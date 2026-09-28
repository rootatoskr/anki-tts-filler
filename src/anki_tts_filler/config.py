import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass

INPUT_FILE = 'cards.txt'
# Сирий список для режиму draft: звідти читаємо, у INPUT_FILE пишемо
DRAFT_FILE = 'draft.txt'
PRESETS_DIR = 'presets'
SETTINGS_FILE = 'settings.toml'

# Префікс імен згенерованих mp3 у медіатеці Anki
MEDIA_PREFIX = 'langdeck_'
# Кеш режимів cards і resync: та сама схема імен, той самий MEDIA_PREFIX
AUDIO_CACHE_DIR = 'audio_cache'

ANKICONNECT_URL = 'http://127.0.0.1:8765'

# Сторони картки: мова, яку вчать, і мова, якою знають. Конкретні мови ніде
# в коді не зашиті - вони задаються голосами в settings.toml
LANG_TARGET = 'target'
LANG_NATIVE = 'native'

# Порядок карток: як у Anki, перемішаний або за абеткою
ORDER_LINEAR = 'linear'
ORDER_RANDOM = 'random'
ORDER_SORTED = 'sorted'
ORDERS = (ORDER_LINEAR, ORDER_RANDOM, ORDER_SORTED)

ERROR_LINES = {
    'Невідомий вхід.',
    'Граматична функція, картка не потрібна.',
}

PRESET_TEMPLATE = '''# Мають точно збігатися з назвами вже створених у Anki колоди й notetype
deck_name = "MyDeck::MySubdeck"
model_name = "MyNoteType"

# Поля кожен режим бере зі своєї секції. Секції немає - режим цей тип ноти
# не обслуговує. Назви полів звіряються з нотетайпом у Anki при запуску.

# Режим cards: аудіополе = текстове поле, з якого згенерувати аудіо. Решта
# полів нотетайпу вводяться вручну в cards.txt, у будь-якому порядку.
[cards]
audio_field_one = "field_one"

# Режим audio: target - сторона мови, яку вчать, native - переклад. Порядок
# полів у списку = черга озвучення. Готові [sound:...] беруться за мапінгом
# із секції [cards].
# examples - поле з прикладами "цільова мова - переклад" через en dash;
# кожна пара стає окремою карткою. Без ключа приклади не озвучуються.
# order - порядок карток: "linear" (як у Anki), "random" (для фонового
# слухання, щоб не вчилася послідовність) або "sorted" (за абеткою).
[audio]
order = "linear"
target = ["field_one"]
native = ["field_translation"]
examples = "note"

# Режим pdf: рядки картки в цьому ж порядку. Порожнє поле рядка не дає.
# order - для шпаргалки зазвичай "sorted": картки за абеткою першого рядка.
[pdf]
order = "sorted"
fields = ["field_one", "field_translation", "note"]
'''

SETTINGS_TEMPLATE = '''# Налаштування усіх режимів. Режими cards і resync беруть звідси anki_url,
# concurrency і голос цільової мови (voice.target, voice.rate_target,
# voice.volume_target); секції gap, content і output стосуються лише режиму
# audio, секція language - сортування за абеткою й мови сторінки на друк.
anki_url = "http://127.0.0.1:8765"
cache_dir = ".cache/tts"
work_dir = ".cache/work"
concurrency = 8

[voice]
# target - мова, яку вчать, native - мова, якою знають
target = "nb-NO-FinnNeural"
native = "uk-UA-PolinaNeural"
rate_target = "-40%"
rate_native = "+0%"
volume_target = "+0%"
volume_native = "+0%"

[language]
# Код мови для атрибута lang сторінки на друк
code = "no"
# Артиклі й частки, які не враховуються при сортуванні за абеткою:
# "en bok" стає на "b", "å lese" на "l"
sort_prefixes = ["å ", "en ", "ei ", "et ", "den ", "det "]
# Літери, які в абетці цільової мови йдуть після "z" - у тому ж порядку,
# що й у самій абетці. Працює лише для абеток на латиниці, що дописують
# літери в кінець (норвезька æøå, шведська åäö): літера підставляється в
# ключ сортування як "після z". Для абетки не на латиниці або для літери,
# місце якої всередині (ґ після г), цей ключ не годиться.
sort_extra_letters = ["æ", "ø", "å"]

[gap]
# Пауза після цільової мови: час на згадати переклад. 0 - без паузи.
after_target = 1.2
# Пауза після перекладу: коротка, далі йде повтор цільової мови.
after_native = 0.4
# Пауза між формами однієї сторони (infinitiv/presens/preteritum).
within_side = 0.5
# Пауза між повторами цільової мови (content.repeat_target > 1).
between_repeats = 0.4
between_cards = 1.5

[content]
# Скільки разів цільова мова повторюється після перекладу:
# target -> native -> target (repeat_target разів)
repeat_target = 1
use_anki_media = true

[resync]
# Від скількох нот режим resync питає підтвердження. Типовий випадок -
# виправив одну картку і переозвучив її; більша кількість майже завжди
# означає, що запит вийшов ширшим, ніж хотілося. Величезне число вимикає
# запитання.
confirm_from = 5

[output]
dir = "out"
bitrate = "48k"
'''

# Перейменовані ключі settings.toml: повний шлях старого -> новий
RENAMED_SETTINGS = {
    'voice.no': 'voice.target',
    'voice.uk': 'voice.native',
    'voice.rate_no': 'voice.rate_target',
    'voice.rate_uk': 'voice.rate_native',
    'voice.volume_no': 'voice.volume_target',
    'voice.volume_uk': 'voice.volume_native',
    'gap.after_no': 'gap.after_target',
    'gap.after_uk': 'gap.after_native',
    'content.repeat_no': 'content.repeat_target',
}


class SettingsError(Exception):
    pass


@dataclass
class VoiceConfig:
    target: str = 'nb-NO-FinnNeural'
    native: str = 'uk-UA-PolinaNeural'
    rate_target: str = '-40%'
    rate_native: str = '+0%'
    volume_target: str = '+0%'
    volume_native: str = '+0%'


@dataclass
class LanguageConfig:
    """Те, що залежить від конкретної цільової мови, а не від схеми карток.

    ``sort_extra_letters`` розраховане на абетки на латиниці, які дописують
    літери в кінець: кожна така літера отримує ключ "після z". Абетку не на
    латиниці цим не впорядкувати - там усі літери й так мають вищі коди, ніж
    підставлений хвіст, тож перелічені опинилися б перед рештою.
    """

    code: str = 'no'
    sort_prefixes: list = field(default_factory=lambda: ['å ', 'en ', 'ei ', 'et ', 'den ', 'det '])
    sort_extra_letters: list = field(default_factory=lambda: ['æ', 'ø', 'å'])


@dataclass
class GapConfig:
    after_target: float = 1.2
    after_native: float = 0.4
    within_side: float = 0.5
    between_repeats: float = 0.4
    between_cards: float = 1.5


@dataclass
class ContentConfig:
    # Скільки разів цільова мова повторюється після перекладу:
    # target -> native -> target*repeat_target
    repeat_target: int = 1
    use_anki_media: bool = True


@dataclass
class ResyncConfig:
    # Від скількох нот питати підтвердження перед записом
    confirm_from: int = 5


@dataclass
class OutputConfig:
    dir: str = 'out'
    bitrate: str = '48k'


@dataclass
class Settings:
    anki_url: str = ANKICONNECT_URL
    cache_dir: str = '.cache/tts'
    work_dir: str = '.cache/work'
    concurrency: int = 8
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    language: LanguageConfig = field(default_factory=LanguageConfig)
    gap: GapConfig = field(default_factory=GapConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    resync: ResyncConfig = field(default_factory=ResyncConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _checked(value, expected, path):
    """Значення з TOML проти типу поля дата-класу.

    ``bool`` у Python - підклас ``int``, тому ``repeat_target = true`` без цієї
    перевірки тихо перетворилося б на 1. Для float приймається і ціле з TOML
    (``after_target = 2``), але одразу зводиться до float, щоб однакові паузи
    не давали двох різних файлів тиші. Список приймається тільки з рядків:
    решта типів у назвах літер і артиклів сенсу не має.
    """
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError('%s: очікується число, а не %s' % (path, type(value).__name__))
        return float(value)
    if expected is int and isinstance(value, bool):
        raise SettingsError('%s: очікується ціле число, а не bool' % path)
    if expected is list:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SettingsError('%s: очікується список рядків' % path)
        return value
    if not isinstance(value, expected):
        raise SettingsError('%s: очікується %s, а не %s' % (path, expected.__name__, type(value).__name__))
    return value


def _unknown_key(path):
    renamed = RENAMED_SETTINGS.get(path)
    if renamed:
        raise SettingsError('ключ %s перейменовано на %s' % (path, renamed))
    raise SettingsError('невідомий ключ %s' % path)


def _apply_section(target, values, prefix):
    known = {f.name: f.type for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            _unknown_key(prefix + key)
        setattr(target, key, _checked(value, known[key], prefix + key))


def write_settings_template():
    with open(SETTINGS_FILE, 'w') as handle:
        handle.write(SETTINGS_TEMPLATE)


def load_settings(path=None):
    if path is None:
        path = SETTINGS_FILE
    if not os.path.exists(path):
        raise SettingsError('налаштування не знайдено: %s' % path)

    with open(path, 'rb') as handle:
        raw = tomllib.load(handle)

    settings = Settings()
    known = {f.name: f.type for f in fields(settings)}
    for key, value in raw.items():
        if key not in known:
            _unknown_key(key)
        current = getattr(settings, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise SettingsError('%s: очікується секція [%s]' % (key, key))
            _apply_section(current, value, '%s.' % key)
        else:
            setattr(settings, key, _checked(value, known[key], key))

    if settings.content.repeat_target < 0:
        raise SettingsError('content.repeat_target має бути >= 0')
    if settings.concurrency < 1:
        raise SettingsError('concurrency має бути >= 1')
    if settings.resync.confirm_from < 1:
        raise SettingsError('resync.confirm_from має бути >= 1')
    # Відʼємна пауза для ffmpeg - помилка, нульова означає "без паузи"
    for name in ('after_target', 'after_native', 'within_side', 'between_repeats', 'between_cards'):
        if getattr(settings.gap, name) < 0:
            raise SettingsError('gap.%s не може бути відʼємним' % name)

    return settings


# markdown-екранування "\_" ламає TOML-парсинг рядків виду key = "value", тому знімається ще до tomllib.loads
_ESCAPED_VALUE_RE = re.compile(r'(?m)^(\w+\s*=\s*")(.*)(")$')

# Дозволяє задати deck_name окремим рядком "deck:значення" без лапок і "="
_DECK_LINE_RE = re.compile(r'(?m)^deck\s*:\s*(.+)$')

_DECK_PREFIX_RE = re.compile(r'^deck\s*:\s*')

# Ключі верхнього рівня пресету і секції, які розбирає _parse_sections
PRESET_KEYS = ('deck_name', 'model_name')
PRESET_SECTIONS = ('cards', 'audio', 'pdf')

# Перейменовані ключі пресетів: шлях старого -> новий
RENAMED_PRESET_KEYS = {
    'audio.no': 'audio.target',
    'audio.uk': 'audio.native',
}


class PresetError(SettingsError):
    pass


class Preset:
    def __init__(self, name, deck_name, model_name, cards, audio_target, audio_native, audio_examples, audio_order, pdf_fields, pdf_order):
        self.name = name
        self.deck_name = deck_name
        self.model_name = model_name
        # cards: {аудіополе: текстове поле}; решта - списки полів у порядку виводу
        self.cards = cards
        # target - сторона мови, яку вчать, native - переклад
        self.audio_target = audio_target
        self.audio_native = audio_native
        # поле з прикладами "цільова мова - переклад"; порожнє - прикладів нема
        self.audio_examples = audio_examples
        self.pdf_fields = pdf_fields
        # порядок карток окремо для кожного режиму: слухання й друк
        # потребують різного - рандом проти стабільної абетки
        self.audio_order = audio_order
        self.pdf_order = pdf_order

    def order_for(self, mode):
        return self.audio_order if mode == 'audio' else self.pdf_order

    def media_fields(self):
        """Текстове поле -> аудіополе, щоб режим audio знаходив готові [sound:...]."""
        return {text_field: audio_field for audio_field, text_field in self.cards.items()}

    def mode_fields(self, mode):
        """Поля, потрібні конкретному режиму - тільки їх і має сенс звіряти.

        Режим audio додає ще й аудіополя з [cards]: без них готові [sound:...]
        не знайдуться і все тихо озвучиться заново через TTS.
        """
        if mode == 'cards':
            return set(self.cards) | set(self.cards.values())
        if mode == 'pdf':
            return set(self.pdf_fields)
        media = self.media_fields()
        used = set(self.audio_target) | set(self.audio_native)
        if self.audio_examples:
            used.add(self.audio_examples)
        return used | {media[name] for name in self.audio_target if name in media}


def presets_path():
    return os.path.abspath(PRESETS_DIR)


def list_presets():
    path = presets_path()
    if not os.path.isdir(path):
        return []
    return sorted(f[:-len('.toml')] for f in os.listdir(path) if f.endswith('.toml'))


def create_template():
    # Створює presets/example.toml, коли жодного пресету ще нема
    path = presets_path()
    os.makedirs(path, exist_ok=True)
    target = os.path.join(path, 'example.toml')
    with open(target, 'w', encoding='utf-8') as f:
        f.write(PRESET_TEMPLATE)
    return target


def _normalize_name(value):
    # Знімає префікс "deck:" і markdown-екранування "\_" -> "_" у назвах колоди й нотетайпу
    return _DECK_PREFIX_RE.sub('', value.strip()).replace('\\_', '_')


def _string_list(raw, path, preset_name):
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise PresetError('%s: %s має бути списком назв полів' % (preset_name, path))
    return raw


def _section(schema, name, preset_name):
    value = schema.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PresetError('%s: [%s] має бути секцією' % (preset_name, name))
    return value


def _unknown_preset_keys(found, known, path, preset_name):
    """Невідомі ключі як помилка, з окремим текстом для перейменованих."""
    unknown = sorted(set(found) - set(known))
    if not unknown:
        return
    renamed = [
        '%s -> %s' % (name, RENAMED_PRESET_KEYS['%s.%s' % (path, name)])
        for name in unknown
        if '%s.%s' % (path, name) in RENAMED_PRESET_KEYS
    ]
    if renamed:
        raise PresetError('%s: у [%s] перейменовані ключі: %s' % (preset_name, path, ', '.join(renamed)))
    raise PresetError('%s: у [%s] невідомі ключі: %s' % (preset_name, path, ', '.join(unknown)))


def _parse_order(section, path, preset_name):
    # Без ключа - лінійно: порядок з Anki передбачуваний, решту вмикають свідомо
    order = section.get('order', ORDER_LINEAR)
    if order not in ORDERS:
        raise PresetError('%s: %s має бути одним із %s, а не %r' % (
            preset_name, path, ', '.join('"%s"' % value for value in ORDERS), order,
        ))
    return order


def _required_name(schema, key, preset_name):
    """Обовʼязковий рядковий ключ верхнього рівня.

    Без цієї перевірки відсутній deck_name падав би KeyError, тобто
    трейсбеком замість повідомлення про помилку в пресеті.
    """
    value = schema.get(key)
    if value is None:
        raise PresetError('%s: немає обовʼязкового ключа %s' % (preset_name, key))
    if not isinstance(value, str):
        raise PresetError('%s: %s має бути рядком, а не %s' % (preset_name, key, type(value).__name__))
    return value


def _parse_sections(schema, preset_name):
    """Пресет -> порядок карток і поля для cards, audio і pdf.

    Порожня чи відсутня секція означає, що режим цей тип ноти не обслуговує.
    Старі ключі схеми відхиляються явно, а не ігноруються мовчки.
    """
    for old in ('audio_fields', 'translation_field'):
        if old in schema:
            raise PresetError(
                '%s: ключ %s зі старої схеми; поля тепер задаються секціями [cards], [audio], [pdf]'
                % (preset_name, old)
            )

    if 'order' in schema:
        raise PresetError(
            '%s: order тепер задається окремо для кожного режиму - у секціях [audio] і [pdf]'
            % preset_name
        )

    # Одруківка в назві ключа верхнього рівня (modle_name) інакше лишалася б
    # непоміченою: model_name узявся б з-за замовчування, тобто нізвідки
    unknown = sorted(set(schema) - set(PRESET_KEYS) - set(PRESET_SECTIONS))
    if unknown:
        raise PresetError('%s: невідомі ключі: %s' % (preset_name, ', '.join(unknown)))

    cards = _section(schema, 'cards', preset_name)
    for audio_field, text_field in cards.items():
        if not isinstance(text_field, str):
            raise PresetError('%s: [cards] %s має бути назвою текстового поля' % (preset_name, audio_field))

    audio = _section(schema, 'audio', preset_name)
    _unknown_preset_keys(audio, ('target', 'native', 'examples', 'order'), 'audio', preset_name)
    audio_target = _string_list(audio.get('target', []), 'audio.target', preset_name)
    audio_native = _string_list(audio.get('native', []), 'audio.native', preset_name)
    audio_examples = audio.get('examples', '')
    if not isinstance(audio_examples, str):
        raise PresetError('%s: audio.examples має бути назвою поля' % preset_name)

    pdf = _section(schema, 'pdf', preset_name)
    _unknown_preset_keys(pdf, ('fields', 'order'), 'pdf', preset_name)
    pdf_fields = _string_list(pdf.get('fields', []), 'pdf.fields', preset_name)

    return (
        cards,
        audio_target,
        audio_native,
        audio_examples,
        _parse_order(audio, 'audio.order', preset_name),
        pdf_fields,
        _parse_order(pdf, 'pdf.order', preset_name),
    )


def field_roles(preset):
    """Поле нотетайпу -> ролі, які йому дає пресет.

    Звіряння з Anki ловить назву, якої нема в нотетайпі, але не зворотне:
    поле, яке існує, а в пресеті не згадане, просто ніде не зʼявиться.
    """
    roles = {}
    for audio_field, text_field in preset.cards.items():
        roles.setdefault(text_field, []).append('cards')
        roles.setdefault(audio_field, []).append('cards:аудіо')
    for text_field in preset.audio_target:
        roles.setdefault(text_field, []).append('audio:target')
    for text_field in preset.audio_native:
        roles.setdefault(text_field, []).append('audio:native')
    if preset.audio_examples:
        roles.setdefault(preset.audio_examples, []).append('audio:examples')
    for text_field in preset.pdf_fields:
        roles.setdefault(text_field, []).append('pdf')
    return roles


def orders(presets, mode):
    """Тип ноти -> порядок, заданий пресетом для цього режиму."""
    return {preset.model_name: preset.order_for(mode) for preset in presets}


def missing_fields(preset, model_fields, mode):
    """Назви полів режиму, яких немає в нотетайпі."""
    known = set(model_fields)
    return sorted(name for name in preset.mode_fields(mode) if name not in known)


def verify_presets(presets, models, fetch_fields, mode):
    """Звіряє пресети потрібних типів нот із нотетайпами в Anki.

    fetch_fields - функція model_name -> список полів; так config лишається
    без залежності від AnkiConnect.
    """
    for preset in presets:
        if preset.model_name not in models or not preset.mode_fields(mode):
            continue
        missing = missing_fields(preset, fetch_fields(preset.model_name), mode)
        if missing:
            raise PresetError('presets/%s.toml: нотетайп %s не має полів: %s' % (
                preset.name, preset.model_name, ', '.join(missing),
            ))


def load_preset(name):
    with open(os.path.join(presets_path(), name + '.toml'), encoding='utf-8') as f:
        raw = f.read()

    raw = _ESCAPED_VALUE_RE.sub(lambda m: m.group(1) + m.group(2).replace('\\_', '_') + m.group(3), raw)

    deck_override = None
    match = _DECK_LINE_RE.search(raw)
    if match:
        deck_override = match.group(1)
        raw = raw[:match.start()] + raw[match.end():]

    schema = tomllib.loads(raw)
    label = 'presets/%s.toml' % name
    if deck_override is not None:
        deck_name = deck_override
        schema.pop('deck_name', None)
    else:
        deck_name = _required_name(schema, 'deck_name', label)

    return Preset(
        name,
        _normalize_name(deck_name),
        _normalize_name(_required_name(schema, 'model_name', label)),
        *_parse_sections(schema, label),
    )


def load_all_presets():
    """Усі пресети з presets/, з перевіркою на повтор нотетайпу.

    Режими audio, pdf і resync зіставляють пресет із нотою саме за
    model_name, тож два пресети на один нотетайп - це не два варіанти на
    вибір, а тихе затирання одного одним. Найпростіший шлях до цього -
    скопіювати пресет як основу для нового й забути змінити model_name.
    """
    presets = [load_preset(name) for name in list_presets()]
    owners = {}
    for preset in presets:
        first = owners.get(preset.model_name)
        if first is not None:
            raise PresetError(
                'presets/%s.toml і presets/%s.toml заявляють той самий нотетайп %s; '
                'режими audio, pdf і resync зіставляють пресети за model_name, '
                'тож він має бути різний' % (first, preset.name, preset.model_name)
            )
        owners[preset.model_name] = preset.name
    return presets
