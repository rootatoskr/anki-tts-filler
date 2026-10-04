import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass

INPUT_FILE = 'cards.txt'
# Raw list for the draft mode: read from there, written into INPUT_FILE
DRAFT_FILE = 'draft.txt'
PRESETS_DIR = 'presets'
SETTINGS_FILE = 'settings.toml'

# Name prefix of the generated mp3 files in the Anki media
MEDIA_PREFIX = 'langdeck_'
# Cache of the cards and resync modes: same naming scheme, same MEDIA_PREFIX
AUDIO_CACHE_DIR = 'audio_cache'

ANKICONNECT_URL = 'http://127.0.0.1:8765'

# Card sides: the language being learnt and the language already known. No
# specific language is hardcoded anywhere - they are set by the voices in
# settings.toml
LANG_TARGET = 'target'
LANG_NATIVE = 'native'

# Card order: as in Anki, shuffled, or alphabetical
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

# Режим mirror: дублікати цих нот у нотетайп зворотного напрямку.
# preset - пресет, куди писати (його deck_name і model_name мають існувати
# в Anki). swap - рівно два поля, що міняються місцями. Секції немає - режим
# цей тип ноти не обслуговує.
# [mirror]
# preset = "example-rev"
# swap = ["field_one", "field_translation"]
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

# Renamed settings.toml keys: full path of the old one -> the new one
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
    """What depends on the particular target language rather than the card schema.

    ``sort_extra_letters`` is meant for Latin alphabets that append letters at
    the end: each such letter gets an "after z" key. A non-Latin alphabet cannot
    be ordered this way - all of its letters already have higher code points
    than the substituted tail, so the listed ones would end up before the rest.
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
    # How many times the target language repeats after the translation:
    # target -> native -> target*repeat_target
    repeat_target: int = 1
    use_anki_media: bool = True


@dataclass
class ResyncConfig:
    # From how many notes on to ask for confirmation before writing
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
    """A value from TOML against the type of a dataclass field.

    ``bool`` is a subclass of ``int`` in Python, so ``repeat_target = true``
    would quietly turn into 1 without this check. For a float an integer from
    TOML is accepted too (``after_target = 2``) but is converted to float right
    away, so that equal gaps do not produce two different silence files. A list
    is accepted only with strings: other types make no sense in letter and
    article names.
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
    # A negative gap is an error for ffmpeg; zero means "no gap"
    for name in ('after_target', 'after_native', 'within_side', 'between_repeats', 'between_cards'):
        if getattr(settings.gap, name) < 0:
            raise SettingsError('gap.%s не може бути відʼємним' % name)

    return settings


# The markdown escape "\_" breaks TOML parsing of key = "value" lines, so it is removed before tomllib.loads
_ESCAPED_VALUE_RE = re.compile(r'(?m)^(\w+\s*=\s*")(.*)(")$')

# Allows deck_name to be given as a separate "deck:value" line, without quotes or "="
_DECK_LINE_RE = re.compile(r'(?m)^deck\s*:\s*(.+)$')

_DECK_PREFIX_RE = re.compile(r'^deck\s*:\s*')

# Top-level preset keys and the sections parsed by ``_parse_sections``
PRESET_KEYS = ('deck_name', 'model_name')
PRESET_SECTIONS = ('cards', 'audio', 'pdf', 'mirror')

# Renamed preset keys: path of the old one -> the new one
RENAMED_PRESET_KEYS = {
    'audio.no': 'audio.target',
    'audio.uk': 'audio.native',
}


class PresetError(SettingsError):
    pass


class Preset:
    def __init__(self, name, deck_name, model_name, cards, audio_target, audio_native, audio_examples, audio_order, pdf_fields, pdf_order, mirror_preset, mirror_swap):
        self.name = name
        self.deck_name = deck_name
        self.model_name = model_name
        # cards: {audio field: text field}; the rest are field lists in output order
        self.cards = cards
        # target - the side in the language being learnt, native - the translation
        self.audio_target = audio_target
        self.audio_native = audio_native
        # field with "target language - translation" examples; empty means no examples
        self.audio_examples = audio_examples
        self.pdf_fields = pdf_fields
        # card order separately per mode: listening and printing need different
        # things - random against a stable alphabet
        self.audio_order = audio_order
        self.pdf_order = pdf_order
        # mirror mode: where to write the duplicates and which field pair to swap
        self.mirror_preset = mirror_preset
        self.mirror_swap = mirror_swap

    def order_for(self, mode):
        return self.audio_order if mode == 'audio' else self.pdf_order

    def media_fields(self):
        """Text field -> audio field, so the audio mode can find ready [sound:...] tags."""
        return {text_field: audio_field for audio_field, text_field in self.cards.items()}

    def mode_fields(self, mode):
        """The fields a given mode needs - only those are worth checking.

        The audio mode also adds the audio fields from [cards]: without them the
        ready [sound:...] tags are not found and everything is quietly
        synthesised again through TTS.
        """
        if mode == 'cards':
            return set(self.cards) | set(self.cards.values())
        if mode == 'pdf':
            return set(self.pdf_fields)
        if mode == 'mirror':
            return set(self.mirror_swap)
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
    # Creates presets/example.toml when no preset exists yet
    path = presets_path()
    os.makedirs(path, exist_ok=True)
    target = os.path.join(path, 'example.toml')
    with open(target, 'w', encoding='utf-8') as f:
        f.write(PRESET_TEMPLATE)
    return target


def _normalize_name(value):
    # Strips the "deck:" prefix and the markdown escape "\_" -> "_" in deck and notetype names
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
    """Unknown keys as an error, with separate wording for renamed ones."""
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
    # Without the key it is linear: the order from Anki is predictable, the rest is opted into
    order = section.get('order', ORDER_LINEAR)
    if order not in ORDERS:
        raise PresetError('%s: %s має бути одним із %s, а не %r' % (
            preset_name, path, ', '.join('"%s"' % value for value in ORDERS), order,
        ))
    return order


def _required_name(schema, key, preset_name):
    """A required top-level string key.

    Without this check a missing deck_name would fail with a KeyError, that is,
    with a traceback instead of a message about an error in the preset.
    """
    value = schema.get(key)
    if value is None:
        raise PresetError('%s: немає обовʼязкового ключа %s' % (preset_name, key))
    if not isinstance(value, str):
        raise PresetError('%s: %s має бути рядком, а не %s' % (preset_name, key, type(value).__name__))
    return value


def _parse_mirror(section, preset_name):
    """The [mirror] section -> (name of the receiving preset, field pair).

    Exactly two fields in the pair: swapping three or more makes no sense, and
    one would mean there is nothing to swap.
    """
    if not section:
        return '', []

    target = section.get('preset')
    if not isinstance(target, str) or not target:
        raise PresetError('%s: mirror.preset має бути назвою пресету' % preset_name)

    swap = _string_list(section.get('swap', []), 'mirror.swap', preset_name)
    if len(swap) != 2:
        raise PresetError('%s: mirror.swap має містити рівно два поля, а не %d' % (preset_name, len(swap)))
    if swap[0] == swap[1]:
        raise PresetError('%s: mirror.swap містить те саме поле двічі' % preset_name)
    return target, swap


def _parse_sections(schema, preset_name):
    """A preset -> card order and the fields for cards, audio and pdf.

    An empty or missing section means the mode does not serve this note type.
    Keys from the old schema are rejected explicitly rather than ignored.
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

    # A typo in a top-level key name (modle_name) would otherwise go unnoticed:
    # model_name would come from the default, that is, from nowhere
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

    mirror = _section(schema, 'mirror', preset_name)
    _unknown_preset_keys(mirror, ('preset', 'swap'), 'mirror', preset_name)
    mirror_preset, mirror_swap = _parse_mirror(mirror, preset_name)

    return (
        cards,
        audio_target,
        audio_native,
        audio_examples,
        _parse_order(audio, 'audio.order', preset_name),
        pdf_fields,
        _parse_order(pdf, 'pdf.order', preset_name),
        mirror_preset,
        mirror_swap,
    )


def orders(presets, mode):
    """Note type -> the order set by its preset for this mode."""
    return {preset.model_name: preset.order_for(mode) for preset in presets}


def missing_fields(preset, model_fields, mode):
    """Names of the mode fields that the notetype does not have."""
    known = set(model_fields)
    return sorted(name for name in preset.mode_fields(mode) if name not in known)


def verify_presets(presets, models, fetch_fields, mode):
    """Checks the presets of the needed note types against the notetypes in Anki.

    fetch_fields is a function model_name -> list of fields; that keeps config
    free of any dependency on AnkiConnect.
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
    """Every preset from presets/, with a check for a repeated notetype.

    The audio, pdf and resync modes match a preset to a note by model_name, so
    two presets for one notetype are not two options to choose from but one
    quietly overwriting the other. The easiest way into that is to copy a preset
    as the basis for a new one and forget to change model_name.
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
