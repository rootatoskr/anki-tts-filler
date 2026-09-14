import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass

INPUT_FILE = 'cards.txt'
PRESETS_DIR = 'presets'
SETTINGS_FILE = 'settings.toml'

TTS_VOICE = 'nb-NO-FinnNeural'
TTS_RATE = '-40%'

# Префікс імен згенерованих mp3 у медіатеці Anki
MEDIA_PREFIX = 'langdeck_'

ANKICONNECT_URL = 'http://127.0.0.1:8765'

ERROR_LINES = {
    'Невідомий вхід.',
    'Граматична функція, картка не потрібна.',
}

PRESET_TEMPLATE = '''# Мають точно збігатися з назвами вже створених у Anki колоди й notetype
deck_name = "MyDeck::MySubdeck"
model_name = "MyNoteType"

# Поле перекладу - потрібне лише режиму audio (озвучення норвезька -> переклад -> норвезька).
# Режим cards його ігнорує.
translation_field = "field_translation"

# Аудіо-поле: з якого текстового поля згенерувати аудіо. Решта полів нотетайпу
# вводяться вручну в cards.txt, у будь-якому порядку; відсутнє поле лишається порожнім.
# Той самий список використовує й режим audio для норвезької сторони.
[audio_fields]
audio_field_one = "field_one"
'''

SETTINGS_TEMPLATE = '''# Налаштування режиму audio. anki_url збігається з ANKICONNECT_URL у config.py.
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
# Пауза після норвезької: час на згадати переклад.
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
# Поле note містить і пари "no - uk", і суцільні українські пояснення.
# Пояснення пропускаються, але розбір лишається евристичним.
include_note = false

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
    rate_no: str = '+0%'
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
    include_note: bool = False


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


def _apply_section(target, values, prefix):
    known = {f.name for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            raise SettingsError('невідомий ключ %s%s' % (prefix, key))
        setattr(target, key, value)


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
    for key, value in raw.items():
        current = getattr(settings, key, None)
        if is_dataclass(current):
            _apply_section(current, value, '%s.' % key)
        elif hasattr(settings, key):
            setattr(settings, key, value)
        else:
            raise SettingsError('невідомий ключ %s' % key)

    if settings.content.repeat_no < 0:
        raise SettingsError('content.repeat_no має бути >= 0')
    if settings.concurrency < 1:
        raise SettingsError('concurrency має бути >= 1')

    return settings

# markdown-екранування "\_" ламає TOML-парсинг рядків виду key = "value", тому знімається ще до tomllib.loads
_ESCAPED_VALUE_RE = re.compile(r'(?m)^(\w+\s*=\s*")(.*)(")$')

# Дозволяє задати deck_name окремим рядком "deck:значення" без лапок і "="
_DECK_LINE_RE = re.compile(r'(?m)^deck\s*:\s*(.+)$')

_DECK_PREFIX_RE = re.compile(r'^deck\s*:\s*')


class Preset:
    def __init__(self, name, deck_name, model_name, audio_fields, translation_field=''):
        self.name = name
        self.deck_name = deck_name
        self.model_name = model_name
        self.audio_fields = audio_fields
        self.translation_field = translation_field


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
        schema.get('audio_fields', {}),
        schema.get('translation_field', ''),
    )


def load_all_presets():
    return [load_preset(name) for name in list_presets()]
