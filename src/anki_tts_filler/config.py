import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass

INPUT_FILE = 'cards.txt'
# Raw list for the draft mode: read from there, written into INPUT_FILE
DRAFT_FILE = 'draft.txt'
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

ERROR_LINES = {
    'Невідомий вхід.',
    'Граматична функція, картка не потрібна.',
}

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
    # A negative gap is an error for ffmpeg; zero means "no gap"
    for name in ('after_target', 'after_native', 'within_side', 'between_repeats', 'between_cards'):
        if getattr(settings.gap, name) < 0:
            raise SettingsError('gap.%s не може бути відʼємним' % name)

    return settings


class PresetError(SettingsError):
    pass


@dataclass(frozen=True)
class Preset:
    """Схема єдиного типу нот: колода, нотетайп і поля кожного режиму.

    ``cards`` - це {аудіополе: текстове поле}, решта - списки полів у порядку
    виводу. Порожній ``audio_examples`` означає, що приклади не озвучуються.
    """

    deck_name: str
    model_name: str
    cards: dict
    audio_target: list
    audio_native: list
    pdf_fields: list
    audio_examples: str = ''
    audio_order: str = ORDER_LINEAR
    pdf_order: str = ORDER_LINEAR

    def order_for(self, mode):
        return self.audio_order if mode == 'audio' else self.pdf_order

    def media_fields(self):
        """Text field -> audio field, so the audio mode can find ready [sound:...] tags."""
        return {text_field: audio_field for audio_field, text_field in self.cards.items()}

    def mode_fields(self, mode):
        """The fields a given mode needs - only those are worth checking.

        The audio mode also adds the audio fields from ``cards``: without them the
        ready [sound:...] tags are not found and everything is quietly
        synthesised again through TTS.
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


# Єдиний тип нот проєкту. Назви мають збігатися з колодою й нотетайпом в Anki.
# extra-поля озвучуються голосом цільової мови, як і recognition
PRESET = Preset(
    deck_name='language-no::no-base',
    model_name='type-language-no-basic',
    cards={
        'audio': 'recognition',
        'audio_extra-1': 'extra-1',
        'audio_extra-2': 'extra-2',
        'audio_extra-3': 'extra-3',
        'audio_extra-4': 'extra-4',
        'audio_extra-5': 'extra-5',
    },
    audio_target=['recognition'],
    audio_native=['production'],
    pdf_fields=['recognition', 'production', 'note'],
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
            raise PresetError('config.py: нотетайп %s не має полів: %s' % (
                preset.model_name, ', '.join(missing),
            ))
