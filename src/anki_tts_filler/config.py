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

# Режим audio: no - норвезька сторона, uk - переклад. Порядок полів у списку
# = черга озвучення. Готові [sound:...] беруться за мапінгом із секції [cards].
# examples - поле з прикладами "норвезька - переклад" через en dash; кожна
# пара стає окремою карткою. Без ключа приклади не озвучуються.
# order - порядок карток: "linear" (як у Anki), "random" (для фонового
# слухання, щоб не вчилася послідовність) або "sorted" (за абеткою).
[audio]
order = "linear"
no = ["field_one"]
uk = ["field_translation"]
examples = "note"

# Режим pdf: рядки картки в цьому ж порядку. Порожнє поле рядка не дає.
# order - для шпаргалки зазвичай "sorted": картки за абеткою першого рядка.
[pdf]
order = "sorted"
fields = ["field_one", "field_translation", "note"]
'''

SETTINGS_TEMPLATE = '''# Налаштування обох режимів. Режим cards бере звідси anki_url, concurrency
# і норвезький голос (voice.no, voice.rate_no, voice.volume_no); секції gap,
# content і output стосуються лише режиму audio.
anki_url = "http://127.0.0.1:8765"
cache_dir = ".cache/tts"
work_dir = ".cache/work"
concurrency = 8

[voice]
no = "nb-NO-FinnNeural"
uk = "uk-UA-PolinaNeural"
rate_no = "-40%"
rate_uk = "+0%"
volume_no = "+0%"
volume_uk = "+0%"

[gap]
# Пауза після норвезької: час на згадати переклад. 0 - без паузи.
after_no = 1.2
# Пауза після української: коротка, далі йде повтор норвезької.
after_uk = 0.4
# Пауза між формами однієї сторони (infinitiv/presens/preteritum).
within_side = 0.5
between_cards = 1.5

[content]
# Скільки разів норвезька повторюється після перекладу: no -> uk -> no (repeat_no разів)
repeat_no = 1
use_anki_media = true

[output]
dir = "out"
bitrate = "48k"
'''


class SettingsError(Exception):
    pass


@dataclass
class VoiceConfig:
    no: str = 'nb-NO-FinnNeural'
    uk: str = 'uk-UA-PolinaNeural'
    rate_no: str = '-40%'
    rate_uk: str = '+0%'
    volume_no: str = '+0%'
    volume_uk: str = '+0%'


@dataclass
class GapConfig:
    after_no: float = 1.2
    after_uk: float = 0.4
    within_side: float = 0.5
    between_cards: float = 1.5


@dataclass
class ContentConfig:
    # Скільки разів норвезька повторюється після перекладу: no -> uk -> no*repeat_no
    repeat_no: int = 1
    use_anki_media: bool = True


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
    gap: GapConfig = field(default_factory=GapConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _checked(value, expected, path):
    """Значення з TOML проти типу поля дата-класу.

    ``bool`` у Python - підклас ``int``, тому ``repeat_no = true`` без цієї
    перевірки тихо перетворилося б на 1. Для float приймається і ціле з TOML
    (``after_no = 2``), але одразу зводиться до float, щоб однакові паузи не
    давали двох різних файлів тиші.
    """
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError('%s: очікується число, а не %s' % (path, type(value).__name__))
        return float(value)
    if expected is int and isinstance(value, bool):
        raise SettingsError('%s: очікується ціле число, а не bool' % path)
    if not isinstance(value, expected):
        raise SettingsError('%s: очікується %s, а не %s' % (path, expected.__name__, type(value).__name__))
    return value


def _apply_section(target, values, prefix):
    known = {f.name: f.type for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            raise SettingsError('невідомий ключ %s%s' % (prefix, key))
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
            raise SettingsError('невідомий ключ %s' % key)
        current = getattr(settings, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise SettingsError('%s: очікується секція [%s]' % (key, key))
            _apply_section(current, value, '%s.' % key)
        else:
            setattr(settings, key, _checked(value, known[key], key))

    if settings.content.repeat_no < 0:
        raise SettingsError('content.repeat_no має бути >= 0')
    if settings.concurrency < 1:
        raise SettingsError('concurrency має бути >= 1')
    # Відʼємна пауза для ffmpeg - помилка, нульова означає "без паузи"
    for name in ('after_no', 'after_uk', 'within_side', 'between_cards'):
        if getattr(settings.gap, name) < 0:
            raise SettingsError('gap.%s не може бути відʼємним' % name)

    return settings

# markdown-екранування "\_" ламає TOML-парсинг рядків виду key = "value", тому знімається ще до tomllib.loads
_ESCAPED_VALUE_RE = re.compile(r'(?m)^(\w+\s*=\s*")(.*)(")$')

# Дозволяє задати deck_name окремим рядком "deck:значення" без лапок і "="
_DECK_LINE_RE = re.compile(r'(?m)^deck\s*:\s*(.+)$')

_DECK_PREFIX_RE = re.compile(r'^deck\s*:\s*')


class PresetError(SettingsError):
    pass


class Preset:
    def __init__(self, name, deck_name, model_name, cards, audio_no, audio_uk, audio_examples, audio_order, pdf_fields, pdf_order):
        self.name = name
        self.deck_name = deck_name
        self.model_name = model_name
        # cards: {аудіополе: текстове поле}; решта - списки полів у порядку виводу
        self.cards = cards
        self.audio_no = audio_no
        self.audio_uk = audio_uk
        # поле з прикладами "норвезька - переклад"; порожнє - прикладів нема
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
        used = set(self.audio_no) | set(self.audio_uk)
        if self.audio_examples:
            used.add(self.audio_examples)
        return used | {media[name] for name in self.audio_no if name in media}


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


def _parse_order(section, path, preset_name):
    # Без ключа - лінійно: порядок з Anki передбачуваний, решту вмикають свідомо
    order = section.get('order', ORDER_LINEAR)
    if order not in ORDERS:
        raise PresetError('%s: %s має бути одним із %s, а не %r' % (
            preset_name, path, ', '.join('"%s"' % value for value in ORDERS), order,
        ))
    return order


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

    cards = _section(schema, 'cards', preset_name)
    for audio_field, text_field in cards.items():
        if not isinstance(text_field, str):
            raise PresetError('%s: [cards] %s має бути назвою текстового поля' % (preset_name, audio_field))

    audio = _section(schema, 'audio', preset_name)
    unknown = sorted(set(audio) - {'no', 'uk', 'examples', 'order'})
    if unknown:
        raise PresetError('%s: у [audio] невідомі ключі: %s' % (preset_name, ', '.join(unknown)))
    audio_no = _string_list(audio.get('no', []), 'audio.no', preset_name)
    audio_uk = _string_list(audio.get('uk', []), 'audio.uk', preset_name)
    audio_examples = audio.get('examples', '')
    if not isinstance(audio_examples, str):
        raise PresetError('%s: audio.examples має бути назвою поля' % preset_name)

    pdf = _section(schema, 'pdf', preset_name)
    unknown = sorted(set(pdf) - {'fields', 'order'})
    if unknown:
        raise PresetError('%s: у [pdf] невідомі ключі: %s' % (preset_name, ', '.join(unknown)))
    pdf_fields = _string_list(pdf.get('fields', []), 'pdf.fields', preset_name)

    return (
        cards,
        audio_no,
        audio_uk,
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
    for text_field in preset.audio_no:
        roles.setdefault(text_field, []).append('audio:no')
    for text_field in preset.audio_uk:
        roles.setdefault(text_field, []).append('audio:uk')
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
    deck_name = deck_override if deck_override is not None else schema['deck_name']

    return Preset(
        name,
        _normalize_name(deck_name),
        _normalize_name(schema['model_name']),
        *_parse_sections(schema, 'presets/%s.toml' % name),
    )


def load_all_presets():
    return [load_preset(name) for name in list_presets()]
