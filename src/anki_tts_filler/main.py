#!/usr/bin/env python3
"""
Заповнення карток у вже створеній колоді/notetype в Anki через AnkiConnect.

Використання:
    uv run anki-tts-filler

Anki має бути запущений з увімкненим аддоном AnkiConnect.
Поля, ключ старту картки і мапу аудіо-полів задаєте у fields.toml.
Вставте список карток у cards.txt (в директорії, звідки запускаєте) і запустіть скрипт.
"""

import sys
import os
import base64
from .config import DECK_NAME, MODEL_NAME, ERROR_LINES, INPUT_FILE, AUDIO_FIELDS, START_KEY
from .parser import split_cards, validate, build_fields
from .audio import generate, strip_html, sound_tag
from .ankiconnect import invoke

INPUT_PATH = os.path.abspath(INPUT_FILE)


def main():
    try:
        invoke('version')
    except Exception:
        print('Не вдалося підключитись до Anki (http://127.0.0.1:8765). Переконайтеся, що Anki відкритий і аддон AnkiConnect увімкнений.')
        sys.exit(1)

    if not os.path.isfile(INPUT_PATH):
        open(INPUT_PATH, 'w').close()
        print(f'Створено {INPUT_FILE}. Вставте картки і запустіть знову.')
        sys.exit(0)

    with open(INPUT_PATH, encoding='utf-8') as f:
        text = f.read()

    if not text.strip():
        print(f'{INPUT_FILE} порожній. Вставте картки і запустіть знову.')
        sys.exit(0)

    if text.strip() in ERROR_LINES:
        print(f'Асистент повернув: {text.strip()}')
        sys.exit(0)

    cards = split_cards(text)
    if not cards:
        print('Карток не знайдено. Перевір формат вводу.')
        sys.exit(1)

    valid = []
    failed = 0
    for i, card in enumerate(cards, 1):
        missing = validate(card)
        if missing:
            print(f'Картка {i}: пропущено — {", ".join(missing)}', file=sys.stderr)
            failed += 1
        else:
            valid.append((i, card))

    if not valid:
        print('Жодної валідної картки.')
        sys.exit(1)

    cache_dir = os.path.join(os.path.dirname(INPUT_PATH), 'audio_cache')
    os.makedirs(cache_dir, exist_ok=True)

    source_fields = set(AUDIO_FIELDS.values())
    texts = set()
    for _, card in valid:
        for src in source_fields:
            texts.add(strip_html(card[src]))
    audio_map = generate(texts, cache_dir)

    for path in audio_map.values():
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        invoke('storeMediaFile', filename=os.path.basename(path), data=data)

    ok = 0
    duplicate = 0
    for i, card in valid:
        audio_tags = {
            audio_field: sound_tag(audio_map[strip_html(card[src])])
            for audio_field, src in AUDIO_FIELDS.items()
        }
        fields = build_fields(card, audio_tags)
        try:
            invoke('addNote', note={
                'deckName': DECK_NAME,
                'modelName': MODEL_NAME,
                'fields': fields,
                'tags': [],
            })
        except Exception as e:
            if 'duplicate' in str(e):
                print(f'Картка {i}: вже існує — {card[START_KEY]}')
                duplicate += 1
                continue
            raise
        print(f'Картка {i}: OK — {card[START_KEY]}')
        ok += 1

    print(f'\nСтворено: {ok} | Вже існували: {duplicate} | Пропущено: {failed}')


if __name__ == '__main__':
    main()
