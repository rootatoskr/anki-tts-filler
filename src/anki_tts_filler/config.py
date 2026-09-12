import os
import re
import tomllib

INPUT_FILE = 'cards.txt'
PRESETS_DIR = 'presets'

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

# Аудіо-поле: з якого текстового поля згенерувати аудіо. Решта полів нотетайпу
# вводяться вручну в cards.txt, у будь-якому порядку; відсутнє поле лишається порожнім
[audio_fields]
audio_field_one = "field_one"
'''

# markdown-екранування "\_" ламає TOML-парсинг рядків виду key = "value", тому знімається ще до tomllib.loads
_ESCAPED_VALUE_RE = re.compile(r'(?m)^(\w+\s*=\s*")(.*)(")$')

# Дозволяє задати deck_name окремим рядком "deck:значення" без лапок і "="
_DECK_LINE_RE = re.compile(r'(?m)^deck\s*:\s*(.+)$')

_DECK_PREFIX_RE = re.compile(r'^deck\s*:\s*')


class Preset:
    def __init__(self, name, deck_name, model_name, audio_fields):
        self.name = name
        self.deck_name = deck_name
        self.model_name = model_name
        self.audio_fields = audio_fields


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
    )
