#!/usr/bin/env python3
"""
Заповнення карток у вже створеній колоді/notetype в Anki через AnkiConnect.

Використання:
    uv run anki-tts-filler <preset>

Anki має бути запущений з увімкненим аддоном AnkiConnect.
Назва колоди/нотетайпу й мапа аудіо-полів задаються у presets/<preset>.toml.
Список полів береться з нотетайпу в Anki. Список карток вставляється у cards.txt
(у директорії запуску), після чого запускається скрипт.
"""

import sys
import os
import base64
from . import config
from .parser import split_cards, build_fields
from .audio import generate, strip_html, sound_tag, ffmpeg_available, media_pattern
from .ankiconnect import invoke


def resolve_preset(argv):
    # Без аргументу список доступних пресетів, бо схема полів більше не одна на проєкт
    names = config.list_presets()
    if not names:
        path = config.create_template()
        rel = os.path.relpath(path)
        print(f'Створено {rel}. Пресет потрібно заповнити і запустити скрипт повторно.')
        sys.exit(0)
    if len(argv) < 2:
        print('Пресет не вказано. Доступні:')
        for name in names:
            print(f'  {name}')
        print(f'\nЗапуск: anki-tts-filler {names[0]}')
        sys.exit(1)
    if argv[1] not in names:
        print(f'Пресет "{argv[1]}" не знайдено. Доступні: {", ".join(names)}')
        sys.exit(1)
    return config.load_preset(argv[1])


def read_cards(input_path):
    if not os.path.isfile(input_path):
        open(input_path, 'w').close()
        print(f'Створено {config.INPUT_FILE}. Картки потрібно вставити і запустити скрипт повторно.')
        sys.exit(0)

    with open(input_path, encoding='utf-8') as f:
        text = f.read()

    if not text.strip():
        print(f'{config.INPUT_FILE} порожній. Картки потрібно вставити і запустити скрипт повторно.')
        sys.exit(0)

    if text.strip() in config.ERROR_LINES:
        print(f'Асистент повернув: {text.strip()}')
        sys.exit(0)

    return text


def build_audio_map(valid, preset, cache_dir):
    texts = set()
    for _, card in valid:
        for src in set(preset.audio_fields.values()):
            text = strip_html(card.get(src, ''))
            if text:
                texts.add(text)
    audio_map = generate(texts, cache_dir)

    # Уже наявні в медіатеці Anki файли повторно не заливаються
    existing = set(invoke('getMediaFilesNames', pattern=media_pattern()))
    for path in audio_map.values():
        name = os.path.basename(path)
        if name in existing:
            continue
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        invoke('storeMediaFile', filename=name, data=data)

    return audio_map


def card_label(card, text_fields):
    return next((card[f] for f in text_fields if card[f]), '')


def main():
    preset = resolve_preset(sys.argv)

    try:
        invoke('version')
    except Exception:
        print('Не вдалося підключитись до Anki (http://127.0.0.1:8765). Anki має бути відкритий з увімкненим аддоном AnkiConnect.')
        sys.exit(1)

    if preset.audio_fields and not ffmpeg_available():
        print('Не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо.')
        sys.exit(1)

    model_fields = invoke('modelFieldNames', modelName=preset.model_name)
    # Поля, що вводяться вручну в cards.txt – усі поля нотетайпу, крім згенерованих аудіополів
    text_fields = [f for f in model_fields if f not in preset.audio_fields]

    input_path = os.path.abspath(config.INPUT_FILE)
    text = read_cards(input_path)

    parsed = split_cards(text, text_fields)
    if not parsed:
        print('Карток не знайдено. Формат вводу потрібно перевірити.')
        sys.exit(1)

    valid = []
    failed = 0
    for i, (card, error) in enumerate(parsed, 1):
        if error:
            print(f'Картка {i}: пропущено – {error}', file=sys.stderr)
            failed += 1
        else:
            valid.append((i, card))

    if not valid:
        print('Жодної валідної картки.')
        sys.exit(1)

    if preset.audio_fields:
        cache_dir = os.path.join(os.path.dirname(input_path), 'audio_cache')
        os.makedirs(cache_dir, exist_ok=True)
        audio_map = build_audio_map(valid, preset, cache_dir)
    else:
        audio_map = {}

    notes = []
    for _, card in valid:
        audio_tags = {}
        for audio_field, src in preset.audio_fields.items():
            src_text = strip_html(card.get(src, ''))
            audio_tags[audio_field] = sound_tag(audio_map[src_text]) if src_text else ''
        notes.append({
            'deckName': preset.deck_name,
            'modelName': preset.model_name,
            'fields': build_fields(card, audio_tags),
            'tags': [],
        })

    # Дублікати й інші відмови визначаються до додавання, з причиною по кожній нотатці
    checks = invoke('canAddNotesWithErrorDetail', notes=notes)
    # Anki порівнює дублікати за першим полем нотетайпу, за ним же ловляться повтори всередині cards.txt
    first_field = model_fields[0]

    ok = 0
    duplicate = 0
    seen = set()
    for (i, card), note, check in zip(valid, notes, checks):
        label = card_label(card, text_fields)
        if not check['canAdd']:
            reason = check.get('error', 'нотатку не можна додати')
            if 'duplicate' in reason:
                print(f'Картка {i}: вже існує – {label}')
                duplicate += 1
            else:
                print(f'Картка {i}: пропущено – {reason}', file=sys.stderr)
                failed += 1
            continue
        key = note['fields'].get(first_field, '')
        if key in seen:
            print(f'Картка {i}: дубль усередині {config.INPUT_FILE} – {label}')
            duplicate += 1
            continue
        seen.add(key)
        invoke('addNote', note=note)
        print(f'Картка {i}: OK – {label}')
        ok += 1

    print(f'\nСтворено: {ok} | Вже існували: {duplicate} | Пропущено: {failed}')


if __name__ == '__main__':
    main()
