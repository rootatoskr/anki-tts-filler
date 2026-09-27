#!/usr/bin/env python3
"""
Anki через AnkiConnect: заповнення карток TTS-аудіо і збирання аудіо для
прослуховування з уже створених нот.

Використання:
    uv run anki-tts-filler cards <preset>
    uv run anki-tts-filler audio '<query>'
    uv run anki-tts-filler pdf '<query>'

Anki має бути запущений з увімкненим аддоном AnkiConnect. Схема полів (яка
колода/notetype, які поля цільової мови/перекладу/аудіо) задається спільно
для всіх режимів у presets/<preset>.toml, голоси й адреса AnkiConnect - у
settings.toml. Конкретних мов у коді немає: сторони називаються target
(мова, яку вчать) і native (мова, якою знають).

Режим cards: список карток вставляється у cards.txt (у директорії запуску),
до Anki додаються нові ноти. Теги - рядком "tags: a b" у картці або
прапорцем --tag.

Режим audio: query - Anki-запит (той самий синтаксис, що й у Навігаторі,
напр. tag:no\\_familie або deck:language-no); з нот, що підійшли під запит,
клеїться один mp3 (цільова мова -> пауза -> переклад -> пауза -> цільова
мова ще раз). Налаштування голосів і пауз - у settings.toml.

Режим pdf: та сама вибірка, що й audio, але на друк - кожне поле картки
окремим рядком.

Режим resync: query - той самий Anki-запит, що й у audio/pdf. Аудіополя з
[cards] пресету переозвучуються за поточним текстом відповідних полів уже
існуючих нот - для картки, яку відредагували вручну після створення.

Режим prune: згенеровані mp3, на які вже не посилається жодна нота. Типово
лише звіт, видалення - з --apply.
"""

import asyncio
import sys
import os

from . import config, audio_build, draft, pdf_build, prune, resync
from .parser import split_cards, build_fields
from .audio import cache_path, strip_html, sound_tag, ffmpeg_available, sync_media
from .ankiconnect import AnkiConnect, AnkiConnectError
from .audio_build import AudioError
from .pdf_build import PdfError
from .prune import PruneError
from .resync import ResyncError
from .config import SettingsError
from .tts_cache import TtsError

MODES = ('cards', 'audio', 'pdf', 'resync', 'prune', 'presets', 'draft')


def usage():
    print('Використання:')
    print('  anki cards <preset> [--tag <назва>]')
    print("  anki audio '<query>'")
    print("  anki pdf '<query>'")
    print("  anki resync '<query>'")
    print('  anki prune [--apply] [--anki]')
    print('  anki presets [preset]')
    print('  anki draft [--append] [--print] [--stdin]')


def connect(url):
    client = AnkiConnect(url)
    try:
        client.call('version')
    except Exception:
        print('Не вдалося підключитись до Anki (%s). Anki має бути відкритий з увімкненим аддоном AnkiConnect.' % url)
        sys.exit(1)
    return client


def parse_flags(rest, allowed, usage_line):
    """Аргументи -> (позиційні, прапорці). Невідомий прапорець - помилка."""
    positional = []
    flags = set()
    for item in rest:
        if item in allowed:
            flags.add(item)
        elif item.startswith('--'):
            print('Невідомий прапорець %s. %s' % (item, usage_line))
            sys.exit(1)
        else:
            positional.append(item)
    return positional, flags


def parse_cards_args(rest):
    """Аргументи режиму cards -> (позиційні, теги з --tag)."""
    positional = []
    tags = []
    index = 0
    while index < len(rest):
        item = rest[index]
        if item == '--tag':
            index += 1
            if index >= len(rest):
                print('Після --tag потрібна назва тегу.')
                sys.exit(1)
            tags.append(rest[index])
        elif item.startswith('-'):
            print('Невідомий прапорець %s. Використання: anki cards <preset> [--tag <назва>]' % item)
            sys.exit(1)
        else:
            positional.append(item)
        index += 1
    return positional, tags


def require_query(rest, mode):
    if len(rest) < 1:
        print("Запит не вказано. Використання: anki %s '<query>'" % mode)
        sys.exit(1)
    return rest[0]


def resolve_preset(rest):
    # Без аргументу список доступних пресетів, бо схема полів більше не одна на проєкт
    names = config.list_presets()
    if not names:
        path = config.create_template()
        print(f'Створено {path}. Пресет потрібно заповнити і запустити скрипт повторно.')
        sys.exit(0)
    if len(rest) < 1:
        print('Пресет не вказано. Доступні:')
        for name in names:
            print(f'  {name}')
        print(f'\nЗапуск: anki cards {names[0]}')
        sys.exit(1)
    if rest[0] not in names:
        print(f'Пресет "{rest[0]}" не знайдено. Доступні: {", ".join(names)}')
        sys.exit(1)
    return config.load_preset(rest[0])


def read_cards(input_path):
    if not os.path.isfile(input_path):
        open(input_path, 'w').close()
        print(f'Створено {input_path}. Картки потрібно вставити і запустити скрипт повторно.')
        sys.exit(0)

    with open(input_path, encoding='utf-8') as f:
        text = f.read()

    if not text.strip():
        print(f'{input_path} порожній. Картки потрібно вставити і запустити скрипт повторно.')
        sys.exit(0)

    if text.strip() in config.ERROR_LINES:
        print(f'Асистент повернув: {text.strip()}')
        sys.exit(0)

    return text


def load_settings_or_exit():
    # Налаштування спільні для всіх режимів, тому й створюються однаково.
    # Шлях абсолютний: файл читається з директорії запуску, і без повного
    # шляху не видно, що запуск не з того каталогу створив ще один шаблон
    if not os.path.exists(config.SETTINGS_FILE):
        config.write_settings_template()
        print(f'Створено {os.path.abspath(config.SETTINGS_FILE)}. Налаштування потрібно перевірити і запустити скрипт повторно.')
        sys.exit(0)
    return config.load_settings()


def card_texts(valid, preset):
    """Тексти всіх полів, з яких треба згенерувати аудіо для нових карток."""
    texts = set()
    for _, card, _ in valid:
        for src in set(preset.cards.values()):
            text = strip_html(card.get(src, ''))
            if text:
                texts.add(text)
    return texts


def card_label(card, text_fields):
    return next((card[f] for f in text_fields if card[f]), '')


def main_cards(rest):
    positional, cli_tags = parse_cards_args(rest)
    preset = resolve_preset(positional)
    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if preset.cards and not ffmpeg_available():
        print('Не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо.')
        sys.exit(1)

    # Anki створює відсутню колоду мовчки, тож пресет із незаповненим
    # deck_name інакше насипав би нот у новостворену "MyDeck::MySubdeck"
    if preset.deck_name not in client.call('deckNames'):
        print(f'Колоди "{preset.deck_name}" немає в Anki – треба або створити її, або виправити deck_name у presets/{preset.name}.toml.')
        sys.exit(1)

    model_fields = client.call('modelFieldNames', modelName=preset.model_name)
    # Одруківка в назві поля інакше тихо лишила б поле порожнім у всіх нових нотах
    missing = config.missing_fields(preset, model_fields, 'cards')
    if missing:
        print(f'presets/{preset.name}.toml: нотетайп {preset.model_name} не має полів: {", ".join(missing)}')
        sys.exit(1)

    # Поля, що вводяться вручну в cards.txt – усі поля нотетайпу, крім згенерованих аудіополів
    text_fields = [f for f in model_fields if f not in preset.cards]

    input_path = os.path.abspath(config.INPUT_FILE)
    text = read_cards(input_path)

    parsed = split_cards(text, text_fields)
    if not parsed:
        print('Карток не знайдено. Формат вводу потрібно перевірити.')
        sys.exit(1)

    valid = []
    failed = 0
    for i, (card, tags, error) in enumerate(parsed, 1):
        if error:
            print(f'Картка {i}: пропущено – {error}', file=sys.stderr)
            failed += 1
        else:
            valid.append((i, card, tags))

    if not valid:
        print('Жодної валідної картки.')
        sys.exit(1)

    if preset.cards:
        cache_dir = cache_path()
        os.makedirs(cache_dir, exist_ok=True)
        audio_map = sync_media(card_texts(valid, preset), cache_dir, client, settings)
    else:
        audio_map = {}

    notes = []
    for _, card, tags in valid:
        audio_tags = {}
        for audio_field, src in preset.cards.items():
            src_text = strip_html(card.get(src, ''))
            audio_tags[audio_field] = sound_tag(audio_map[src_text]) if src_text else ''
        notes.append({
            'deckName': preset.deck_name,
            'modelName': preset.model_name,
            'fields': build_fields(card, audio_tags),
            # Теги з --tag дістаються всім карткам запуску, з рядка "tags:" – своїй
            'tags': sorted(set(cli_tags) | set(tags)),
        })

    # Дублікати й інші відмови визначаються до додавання, з причиною по кожній нотатці
    checks = client.call('canAddNotesWithErrorDetail', notes=notes)
    # Anki порівнює дублікати за першим полем нотетайпу, за ним же ловляться повтори всередині cards.txt
    first_field = model_fields[0]

    ok = 0
    duplicate = 0
    seen = set()
    # Рядки збираються наперед, щоб вивід лишився в порядку карток,
    # хоча самі ноти додаються одним запитом у кінці
    lines = []
    to_add = []
    for (i, card, _), note, check in zip(valid, notes, checks):
        label = card_label(card, text_fields)
        if not check['canAdd']:
            reason = check.get('error', 'нотатку не можна додати')
            if 'duplicate' in reason:
                lines.append((f'Картка {i}: вже існує – {label}', sys.stdout))
                duplicate += 1
            else:
                lines.append((f'Картка {i}: пропущено – {reason}', sys.stderr))
                failed += 1
            continue
        key = note['fields'].get(first_field, '')
        if key in seen:
            lines.append((f'Картка {i}: дубль усередині {config.INPUT_FILE} – {label}', sys.stdout))
            duplicate += 1
            continue
        seen.add(key)
        lines.append(None)
        to_add.append((len(lines) - 1, i, label, note))

    if to_add:
        added = client.call('addNotes', notes=[note for _, _, _, note in to_add])
        for (slot, i, label, _), note_id in zip(to_add, added):
            if note_id is None:
                lines[slot] = (f'Картка {i}: пропущено – Anki не додав нотатку', sys.stderr)
                failed += 1
            else:
                lines[slot] = (f'Картка {i}: OK – {label}', sys.stdout)
                ok += 1

    for message, stream in lines:
        print(message, file=stream)

    all_tags = sorted(set(cli_tags) | {tag for _, _, tags in valid for tag in tags})
    if all_tags:
        print('теги: %s' % ', '.join(all_tags))
    print(f'\nСтворено: {ok} | Вже існували: {duplicate} | Пропущено: {failed}')


def main_audio(rest):
    query = require_query(rest, 'audio')

    # Налаштування читаються до підключення: адреса AnkiConnect береться з них
    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    out_path = asyncio.run(audio_build.run(query, settings, client))
    if out_path is None:
        sys.exit(1)


DRAFT_FLAGS = ('--print', '--append', '--stdin')


def read_draft(use_stdin):
    """Сирий список: типово з draft.txt, при --stdin - із потоку."""
    if use_stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            print('Порожній ввід.')
            sys.exit(1)
        return raw

    path = os.path.abspath(config.DRAFT_FILE)
    if not os.path.isfile(path):
        open(path, 'w').close()
        print(f'Створено {path}. Список потрібно вставити і запустити скрипт повторно.')
        sys.exit(0)

    with open(path, encoding='utf-8') as handle:
        raw = handle.read()
    if not raw.strip():
        print(f'{path} порожній. Список потрібно вставити і запустити скрипт повторно.')
        sys.exit(0)
    return raw


def write_draft(path, body, append):
    existing = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as handle:
            existing = handle.read()
    with open(path, 'a' if append else 'w', encoding='utf-8') as handle:
        if append and existing and not existing.endswith('\n\n'):
            handle.write('\n' if existing.endswith('\n') else '\n\n')
        handle.write(body)


def main_draft(rest):
    positional, flags = parse_flags(
        rest, DRAFT_FLAGS, 'Використання: anki draft [--append] [--print] [--stdin]',
    )
    if len(positional) > 1:
        print('Зайвий аргумент %s.' % positional[1])
        sys.exit(1)
    name = positional[0] if positional else None
    # Режим прив'язаний до ordforrad: назви полів у draft.py - конкретні поля
    # цього нотетайпу, для іншого вони були б просто неправдою
    if name is not None and name != draft.PRESET_NAME:
        print(f'draft працює тільки з пресетом {draft.PRESET_NAME}, а не "{name}".')
        sys.exit(1)
    if draft.PRESET_NAME not in config.list_presets():
        print(f'Пресет {draft.PRESET_NAME} не знайдено в presets/.')
        sys.exit(1)

    # Anki тут не потрібен: режим лише переписує текстовий файл, а назви
    # полів усе одно звіряються з нотетайпом при запуску cards
    raw = read_draft('--stdin' in flags)
    cards, problems = draft.generate(raw)

    lines = len([line for line in raw.splitlines() if line.strip()])
    print(f'карток: {len(cards)} | рядків на вході: {lines}', file=sys.stderr)
    for problem in problems:
        print(f'увага: {problem}', file=sys.stderr)
    if problems:
        print('деякі рядки не розібрались – нічого не записано', file=sys.stderr)
        sys.exit(1)

    body = draft.format_cards(cards)
    if '--print' in flags:
        print(body, end='')
        return
    path = os.path.abspath(config.INPUT_FILE)
    replaced = '--append' not in flags and os.path.exists(path) and os.path.getsize(path) > 0
    write_draft(path, body, '--append' in flags)
    print(
        f'{"перезаписано" if replaced else "записано"} в {path}',
        file=sys.stderr,
    )


def main_presets(rest):
    """Друкує, яку роль кожне поле нотетайпу має в пресеті.

    Показує і те, чого не ловить звіряння при запуску: поле нотетайпу, яке в
    пресеті не згадане ніде, тобто ніколи не прозвучить і не надрукується.
    """
    names = config.list_presets()
    if rest:
        if rest[0] not in names:
            print(f'Пресет "{rest[0]}" не знайдено. Доступні: {", ".join(names)}')
            sys.exit(1)
        names = [rest[0]]

    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    for name in names:
        preset = config.load_preset(name)
        print(f'\n{name} ({preset.model_name}, audio: {preset.audio_order}, pdf: {preset.pdf_order})')
        try:
            model_fields = client.call('modelFieldNames', modelName=preset.model_name)
        except AnkiConnectError as exc:
            print(f'  нотетайпу немає в Anki: {exc}')
            continue

        roles = config.field_roles(preset)
        unused = []
        for field_name in model_fields:
            used = roles.pop(field_name, [])
            if used:
                print(f'  {field_name} – {", ".join(used)}')
            else:
                unused.append(field_name)
        if unused:
            print(f'  ніде не згадані: {", ".join(unused)}')
        if roles:
            print(f'  у пресеті є, а в нотетайпі немає: {", ".join(sorted(roles))}')


def main_resync(rest):
    query = require_query(rest, 'resync')

    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if not resync.run(query, settings, client):
        sys.exit(1)


def main_prune(rest):
    positional, flags = parse_flags(
        rest, ('--apply', '--anki'), 'Використання: anki prune [--apply] [--anki]',
    )
    if positional:
        print('Зайвий аргумент %s.' % positional[0])
        sys.exit(1)

    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if not prune.run(settings, client, '--apply' in flags, '--anki' in flags):
        sys.exit(1)


def main_pdf(rest):
    query = require_query(rest, 'pdf')

    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if not pdf_build.run(query, settings, client):
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        usage()
        return 1

    mode = sys.argv[1]
    rest = sys.argv[2:]
    # Очікувані відмови (помилка в settings.toml або пресеті, немає ffmpeg,
    # збій edge-tts, обрив звʼязку з Anki) друкуються рядком, а не трейсбеком
    try:
        if mode == 'cards':
            main_cards(rest)
        elif mode == 'audio':
            main_audio(rest)
        elif mode == 'pdf':
            main_pdf(rest)
        elif mode == 'resync':
            main_resync(rest)
        elif mode == 'prune':
            main_prune(rest)
        elif mode == 'presets':
            main_presets(rest)
        else:
            main_draft(rest)
    except (SettingsError, AnkiConnectError, AudioError, PdfError, PruneError, ResyncError, TtsError) as exc:
        print('помилка: %s' % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
