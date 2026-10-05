#!/usr/bin/env python3
"""
Anki through AnkiConnect: filling cards with TTS audio and building audio for
listening out of notes that already exist.

Usage:
    uv run anki-tts-filler cards <preset>
    uv run anki-tts-filler audio '<query>'
    uv run anki-tts-filler pdf '<query>'

Anki has to be running with the AnkiConnect add-on enabled. The field schema
(which deck/notetype, which fields hold the target language, the translation
and the audio) is shared by every mode in presets/<preset>.toml, while the
voices and the AnkiConnect address live in settings.toml. No specific language
appears in the code: the sides are called target (the language being learnt)
and native (the language already known).

cards mode: the card list is pasted into cards.txt (in the launch directory)
and new notes are added to Anki. Tags come from a "tags: a b" line in a card
or from the --tag flag, which takes every word up to the next flag.

audio mode: query is an Anki query (the same syntax as in the Browser, for
example tag:no\\_familie or deck:language-no); out of the notes it matched, one
mp3 is glued together (target language -> gap -> translation -> gap -> target
language again). Voice and gap settings live in settings.toml.

pdf mode: the same selection as audio, but for printing - every card field on
its own line.

mirror mode: note duplicates in a reverse-direction notetype - the field pair
from the preset [mirror] section is swapped. The target is given the same way
as in resync.

resync mode: the argument is a preset name (every note of its notetype) or the
same Anki query as in audio/pdf; with no argument the list of presets is
printed. The audio fields from the preset [cards] section are re-synthesised
from the current text of the matching fields of notes that already exist - for
a card edited by hand after it was created. From resync.confirm_from notes on
it asks for confirmation.

"""

import asyncio
import sys
import os

from . import config, audio_build, draft, mirror, pdf_build, resync
from .parser import split_cards, build_fields
from .audio import cache_path, sound_tag, ffmpeg_available, sync_media
from .note_cards import clean_text
from .ankiconnect import AnkiConnect, AnkiConnectError
from .audio_build import AudioError
from .mirror import MirrorError
from .pdf_build import PdfError
from .resync import ResyncError
from .config import SettingsError
from .tts_cache import TtsError

MODES = ('cards', 'audio', 'pdf', 'resync', 'mirror', 'draft')


def usage():
    print('Використання:')
    print('  anki cards <preset> [--tag <назва>]...')
    print("  anki audio '<query>'")
    print("  anki pdf '<query>'")
    print("  anki resync <preset> | '<query>'")
    print("  anki mirror <preset> | '<query>' [--tag <назва>]...")
    print('  anki draft')
    print()
    print('Приклади:')
    print('  anki cards base --tag no_fargene kapittel_9')
    print("  anki audio 'tag:no\\_fargene'")
    print("  anki pdf 'deck:language-no::no-verb'")
    print('  anki resync base')
    print("  anki resync 'nid:1790316425552'")
    print("  anki mirror 'tag:no\\_fargene' --tag mirrored")
    print()
    print("Запит: tag:no\\_fargene, deck:language-no::no-verb, nid:<id>, note:<notetype>")
    print('Теги в cards.txt: рядок "tags: no_fargene kapittel_9" усередині картки')


def connect(url):
    client = AnkiConnect(url)
    try:
        client.call('version')
    except Exception:
        print('Не вдалося підключитись до Anki (%s). Anki має бути відкритий з увімкненим аддоном AnkiConnect.' % url)
        sys.exit(1)
    return client


def parse_cards_args(rest, mode):
    """Arguments of the cards and mirror modes -> (positional ones, tags).

    ``--tag`` takes every following word up to the next flag, so tags can be
    written the way Anki itself separates them - by spaces. That makes the flag
    greedy: a positional argument placed after it is read as one more tag, and
    the missing target is then reported by the caller.
    """
    positional = []
    tags = []
    index = 0
    while index < len(rest):
        item = rest[index]
        if item == '--tag':
            index += 1
            start = index
            while index < len(rest) and not rest[index].startswith('-'):
                tags.append(rest[index])
                index += 1
            if index == start:
                print('Після --tag потрібна назва тегу.')
                sys.exit(1)
            continue
        if item.startswith('-'):
            print("Невідомий прапорець %s. Використання: anki %s <ціль> [--tag <назва>]..." % (item, mode))
            sys.exit(1)
        positional.append(item)
        index += 1
    return positional, tags


def single_target(positional, mode):
    """The one positional argument of a mode, with extras reported.

    Without this check a second positional argument was dropped in silence:
    ``--tag a b`` left ``b`` as a positional, and only the first tag was applied.
    """
    if len(positional) > 1:
        print('Зайвий аргумент %s. Ціль одна, а теги йдуть після --tag: anki %s <ціль> --tag a b' % (
            positional[1], mode,
        ))
        sys.exit(1)
    return positional


def require_query(rest, mode):
    if len(rest) < 1:
        print("Запит не вказано. Використання: anki %s '<query>'" % mode)
        sys.exit(1)
    return rest[0]


def resolve_preset(rest):
    # With no argument, list the available presets: the field schema is no longer one per project
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
    # Settings are shared by every mode, so they are created the same way.
    # The path is absolute: the file is read from the launch directory, and
    # without the full path it is invisible that a run from the wrong directory
    # created another template
    if not os.path.exists(config.SETTINGS_FILE):
        config.write_settings_template()
        print(f'Створено {os.path.abspath(config.SETTINGS_FILE)}. Налаштування потрібно перевірити і запустити скрипт повторно.')
        sys.exit(0)
    return config.load_settings()


def card_texts(valid, preset):
    """Texts of every field that audio has to be generated from for the new cards."""
    texts = set()
    for _, card, _ in valid:
        for src in set(preset.cards.values()):
            text = clean_text(card.get(src, ''))
            if text:
                texts.add(text)
    return texts


def card_label(card, text_fields):
    return next((card[f] for f in text_fields if card[f]), '')


def main_cards(rest):
    positional, cli_tags = parse_cards_args(rest, 'cards')
    preset = resolve_preset(single_target(positional, 'cards'))
    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if preset.cards and not ffmpeg_available():
        print('Не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо.')
        sys.exit(1)

    # Anki creates a missing deck silently, so a preset with an unfilled
    # deck_name would otherwise pour notes into a freshly made "MyDeck::MySubdeck"
    if preset.deck_name not in client.call('deckNames'):
        print(f'Колоди "{preset.deck_name}" немає в Anki – треба або створити її, або виправити deck_name у presets/{preset.name}.toml.')
        sys.exit(1)

    model_fields = client.call('modelFieldNames', modelName=preset.model_name)
    # A typo in a field name would otherwise leave that field empty in every new note
    missing = config.missing_fields(preset, model_fields, 'cards')
    if missing:
        print(f'presets/{preset.name}.toml: нотетайп {preset.model_name} не має полів: {", ".join(missing)}')
        sys.exit(1)

    # Fields typed by hand in cards.txt - every notetype field except the generated audio ones
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
            src_text = clean_text(card.get(src, ''))
            audio_tags[audio_field] = sound_tag(audio_map[src_text]) if src_text else ''
        notes.append({
            'deckName': preset.deck_name,
            'modelName': preset.model_name,
            'fields': build_fields(card, audio_tags),
            # Tags from ``--tag`` go to every card of the run, those from a "tags:" line to their own
            'tags': sorted(set(cli_tags) | set(tags)),
        })

    # Duplicates and other refusals are determined before adding, with a reason per note
    checks = client.call('canAddNotesWithErrorDetail', notes=notes)
    # Anki compares duplicates by the first notetype field; repeats inside cards.txt are caught by it too
    first_field = model_fields[0]

    ok = 0
    duplicate = 0
    seen = set()
    # Lines are collected up front so the output stays in card order, even
    # though the notes themselves are added in one request at the end
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

    # Settings are read before connecting: the AnkiConnect address comes from them
    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    out_path = asyncio.run(audio_build.run(query, settings, client))
    if out_path is None:
        sys.exit(1)


def read_draft():
    """The raw list from draft.txt."""
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


def main_draft(rest):
    if rest:
        print('anki draft не приймає аргументів.')
        sys.exit(1)
    if draft.PRESET_NAME not in config.list_presets():
        print(f'Пресет {draft.PRESET_NAME} не знайдено в presets/.')
        sys.exit(1)

    # Anki is not needed here: the mode only rewrites a text file, and field
    # names are checked against the notetype on the cards run anyway
    raw = read_draft()
    cards, problems = draft.generate(raw)

    lines = len([line for line in raw.splitlines() if line.strip()])
    print(f'карток: {len(cards)} | рядків на вході: {lines}', file=sys.stderr)
    for problem in problems:
        print(f'увага: {problem}', file=sys.stderr)
    if problems:
        print('деякі рядки не розібрались – нічого не записано', file=sys.stderr)
        sys.exit(1)

    path = os.path.abspath(config.INPUT_FILE)
    replaced = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(draft.format_cards(cards))
    print(
        f'{"перезаписано" if replaced else "записано"} в {path}',
        file=sys.stderr,
    )


def resolve_target(rest, mode):
    """The argument of the resync and mirror modes: a preset name or an Anki query.

    With no argument the list of presets is printed - the same way cards does
    with a preset name.
    """
    if rest:
        return rest[0]

    names = config.list_presets()
    if not names:
        path = config.create_template()
        print(f'Створено {path}. Пресет потрібно заповнити і запустити скрипт повторно.')
        sys.exit(0)
    print('Ціль не вказано. Доступні пресети:')
    for name in names:
        print(f'  {name}')
    print(f'\nЗапуск: anki {mode} {names[0]}')
    print(f"Або Anki-запит: anki {mode} 'nid:1234567890'")
    sys.exit(1)


def main_resync(rest):
    query = resolve_target(rest, 'resync')

    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if not resync.run(query, settings, client):
        sys.exit(1)


def main_mirror(rest):
    positional, cli_tags = parse_cards_args(rest, 'mirror')
    query = resolve_target(single_target(positional, 'mirror'), 'mirror')

    settings = load_settings_or_exit()
    client = connect(settings.anki_url)

    if not mirror.run(query, settings, client, cli_tags):
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
    # Expected failures (an error in settings.toml or a preset, no ffmpeg, an
    # edge-tts failure, a lost AnkiConnect link) print as a line, not a traceback
    try:
        if mode == 'cards':
            main_cards(rest)
        elif mode == 'audio':
            main_audio(rest)
        elif mode == 'pdf':
            main_pdf(rest)
        elif mode == 'resync':
            main_resync(rest)
        elif mode == 'mirror':
            main_mirror(rest)
        else:
            main_draft(rest)
    except (SettingsError, AnkiConnectError, AudioError, MirrorError, PdfError, ResyncError, TtsError) as exc:
        print('помилка: %s' % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
