import os
import re
import sys
import tomllib

INPUT_FILE = "cards.txt"
FIELDS_FILE = "fields.toml"

TTS_VOICE = "nb-NO-FinnNeural"
TTS_RATE = "-30%"

ANKICONNECT_URL = "http://127.0.0.1:8765"

_FIELDS_TEMPLATE = '''# Мають точно збігатися з назвами вже створених у Anki колоди й notetype
deck_name = "MyDeck::MySubdeck"
model_name = "MyNoteType"

# Ключ, з якого починається нова картка в cards.txt
start_key = "field_one"

# Поля, які вводяться вручну в cards.txt (мають збігатися з полями нотетайпу в Anki)
fields = [
    "field_one",
    "field_two",
]

# Аудіо-поле: з якого текстового поля згенерувати аудіо
[audio_fields]
audio_field_one = "field_one"
'''

_fields_path = os.path.abspath(FIELDS_FILE)

if not os.path.isfile(_fields_path):
    with open(_fields_path, 'w', encoding='utf-8') as f:
        f.write(_FIELDS_TEMPLATE)
    print(f'Створено {FIELDS_FILE}. Задайте deck_name/model_name/fields/start_key/audio_fields і запустіть знову.')
    sys.exit(0)

with open(_fields_path, 'rb') as f:
    _schema = tomllib.load(f)

DECK_NAME = _schema['deck_name']
MODEL_NAME = _schema['model_name']
FIELDS = _schema['fields']
START_KEY = _schema['start_key']
AUDIO_FIELDS = _schema.get('audio_fields', {})

EXPECTED_SET = set(FIELDS)

ERROR_LINES = {
    "Невідомий вхід.",
    "Граматична функція, картка не потрібна.",
}

KEY_RE = re.compile(r"^(" + "|".join(FIELDS) + r")\s*:\s*(.*)$")
