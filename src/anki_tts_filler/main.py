#!/usr/bin/env python3
"""
Anki через AnkiConnect: заповнення карток TTS-аудіо і збирання аудіо для
прослуховування з уже створених нот.

Використання:
    uv run anki-tts-filler cards <preset>
    uv run anki-tts-filler audio '<query>'

Anki має бути запущений з увімкненим аддоном AnkiConnect. Схема полів (яка
колода/notetype, які поля норвезькі/переклад/аудіо) задається спільно для
обох режимів у presets/<preset>.toml.

Режим cards: список карток вставляється у cards.txt (у директорії запуску),
до Anki додаються нові ноти.

Режим audio: query - Anki-запит (той самий синтаксис, що й у Навігаторі,
напр. tag:no\\_familie або deck:language-no); з нот, що підійшли під запит,
клеїться один mp3 (норвезька -> пауза -> переклад -> пауза -> норвезька ще
раз). Налаштування голосу й пауз - у settings.toml.
"""

import asyncio
import sys
import os
import base64

from . import config, audio_build
from .parser import split_cards, build_fields
from .audio import generate, strip_html, sound_tag, ffmpeg_available, media_pattern
from .ankiconnect import AnkiConnect, AnkiConnectError
from .audio_build import AudioError
from .config import SettingsError
from .tts_cache import TtsError


def usage():
    print('Використання:')
    print("  anki-tts-filler cards <preset>")
    print("  anki-tts-filler audio '<query>'")


def connect(url):
    client = AnkiConnect(url)
    try:
        client.call('version')
    except Exception:
        print('Не вдалося підключитись до Anki (%s). Anki має бути відкритий з увімкненим аддоном AnkiConnect.' % url)
        sys.exit(1)
    return client


def resolve_preset(rest):
    # Без аргументу список доступних пресетів, бо схема полів більше не одна на проєкт
    names = config.list_presets()
    if not names:
        path = config.create_template()
        rel = os.path.relpath(path)
        print(f'Створено {rel}. Пресет потрібно заповнити і запустити скрипт повторно.')
        sys.exit(0)
    if len(rest) < 1:
        print('Пресет не вказано. Доступні:')
        for name in names:
            print(f'  {name}')
        print(f'\nЗапуск: anki-tts-filler cards {names[0]}')
        sys.exit(1)
    if rest[0] not in names:
        print(f'Пресет "{rest[0]}" не знайдено. Доступні: {", ".join(names)}')
        sys.exit(1)
    return config.load_preset(rest[0])


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


def build_audio_map(valid, preset, cache_dir, client):
    texts = set()
    for _, card in valid:
        for src in set(preset.audio_fields.values()):
            text = strip_html(card.get(src, ''))
            if text:
                texts.add(text)
    audio_map = generate(texts, cache_dir)

    # Уже наявні в медіатеці Anki файли повторно не заливаються
    existing = set(client.call('getMediaFilesNames', pattern=media_pattern()))
    for path in audio_map.values():
        name = os.path.basename(path)
        if name in existing:
            continue
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        client.call('storeMediaFile', filename=name, data=data)

    return audio_map


def card_label(card, text_fields):
    return next((card[f] for f in text_fields if card[f]), '')


def main_cards(rest):
    preset = resolve_preset(rest)
    client = connect(config.ANKICONNECT_URL)

    if preset.audio_fields and not ffmpeg_available():
        print('Не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо.')
        sys.exit(1)

    model_fields = client.call('modelFieldNames', modelName=preset.model_name)
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
        audio_map = build_audio_map(valid, preset, cache_dir, client)
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
    checks = client.call('canAddNotesWithErrorDetail', notes=notes)
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
        client.call('addNote', note=note)
        print(f'Картка {i}: OK – {label}')
        ok += 1

    print(f'\nСтворено: {ok} | Вже існували: {duplicate} | Пропущено: {failed}')


def main_audio(rest):
    if len(rest) < 1:
        print("Запит не вказано. Використання: anki-tts-filler audio '<query>'")
        sys.exit(1)
    query = rest[0]

    if not os.path.exists(config.SETTINGS_FILE):
        config.write_settings_template()
        print(f'Створено {config.SETTINGS_FILE}. Налаштування потрібно перевірити і запустити скрипт повторно.')
        sys.exit(0)

    # Налаштування читаються до підключення: адреса AnkiConnect береться з них
    settings = config.load_settings()
    client = connect(settings.anki_url)

    out_path = asyncio.run(audio_build.run(query, settings, client))
    if out_path is None:
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('cards', 'audio'):
        usage()
        return 1

    mode = sys.argv[1]
    rest = sys.argv[2:]
    # Очікувані відмови (помилка в settings.toml, немає ffmpeg, збій edge-tts,
    # обрив звʼязку з Anki) друкуються рядком, а не трейсбеком
    try:
        if mode == 'cards':
            main_cards(rest)
        else:
            main_audio(rest)
    except (SettingsError, AnkiConnectError, AudioError, TtsError) as exc:
        print('помилка: %s' % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
