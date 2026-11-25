"""Orchestration of the resync mode: re-synthesising audio for existing Anki notes.

Поля беруться з ``config.PRESET.cards`` (аудіополе -> текстове поле), як і в
режимі cards. На відміну від cards, текст береться не з cards.txt, а з
поточних значень полів наявної ноти - тому аудіо й кеш (audio_cache/) ті самі,
що й у режимі cards.

Без аргументу ціль - всі ноти типу з ``config.PRESET``, інакше Anki-запит, той
самий, що й в audio/pdf.

A plan is built first (which fields really change) and only then are the texts
from that plan synthesised: comparing a file name needs no synthesis.
"""

import os

from . import config
from .audio import cache_path, expected_tag, ffmpeg_available, sound_tag, sync_media
from .note_cards import clean_text, note_value, tts_text


class ResyncError(Exception):
    pass


def escape_query_value(value):
    """A value for an Anki query, in quotes.

    In Anki search ``_`` and ``*`` are wildcards and ``:`` separates field from
    value, so a notetype name with such characters would match the wrong thing
    unescaped. The backslash goes first, otherwise it would double the slashes
    already added.
    """
    for char in ('\\', '"', '*', '_', ':'):
        value = value.replace(char, '\\' + char)
    return '"%s"' % value


def resolve_targets(client, query):
    """Anki-запит або None -> (note_ids, підпис для виводу).

    None означає всі ноти типу з ``config.PRESET``.
    """
    if query is not None:
        return client.find_notes(query), query
    model_name = config.PRESET.model_name
    return client.find_notes('note:%s' % escape_query_value(model_name)), model_name


def build_plan(notes, index, settings):
    """Notes -> [(note, {audio field: text})] for the changing fields only.

    No synthesis happens here: the required file name is computed from the text,
    so the plan is known before anything is synthesised or uploaded to Anki. The
    plan keeps the text itself rather than the expected tag: the tag is taken
    from the file that actually got synthesised anyway.
    """
    plan = []
    for note in notes:
        changes = {}
        for audio_field, text_field in index[note['modelName']].items():
            text = tts_text(note_value(note, text_field))
            new_value = expected_tag(text, settings) if text else ''
            if new_value != note_value(note, audio_field):
                changes[audio_field] = text
        if changes:
            plan.append((note, changes))
    return plan


def describe(plan, index):
    """A short description of the plan: one line per note, a few lines at most."""
    lines = []
    for note, changes in plan[:5]:
        text_field = next(iter(index[note['modelName']].values()))
        label = clean_text(note_value(note, text_field))[:50]
        lines.append('  nid:%d %s (%d поле)' % (note['noteId'], label, len(changes)))
    if len(plan) > 5:
        lines.append('  ... ще %d нот' % (len(plan) - 5))
    return lines


def run(query, settings, client):
    if not ffmpeg_available():
        raise ResyncError('не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо')

    index = {config.PRESET.model_name: config.PRESET.cards}

    note_ids, label = resolve_targets(client, query)
    print('%s -> нот: %d' % (label, len(note_ids)))
    if not note_ids:
        print('запит нічого не знайшов')
        return False

    notes = client.notes_info(note_ids)
    config.verify_presets(
        [config.PRESET],
        {note['modelName'] for note in notes},
        lambda model: client.call('modelFieldNames', modelName=model),
        'cards',
    )

    skipped = {}
    candidates = []
    for note in notes:
        if note['modelName'] not in index:
            skipped[note['modelName']] = skipped.get(note['modelName'], 0) + 1
            continue
        candidates.append(note)

    for model, count in sorted(skipped.items()):
        print('пропущено %d нот типу %s' % (count, model))
    if not candidates:
        print('жодної придатної ноти')
        return False

    plan = build_plan(candidates, index, settings)
    if not plan:
        print('оновлено: 0 | без змін: %d' % len(candidates))
        return True

    fields = sum(len(changes) for _, changes in plan)
    print('зміниться нот: %d (аудіополів: %d)' % (len(plan), fields))
    for line in describe(plan, index):
        print(line)

    cache_dir = cache_path()
    os.makedirs(cache_dir, exist_ok=True)
    # Only texts from the plan are synthesised, not every field of every note found
    texts = {text for _, changes in plan for text in changes.values() if text}
    audio_map = sync_media(texts, cache_dir, client, settings)

    for note, changes in plan:
        fields = {
            audio_field: sound_tag(audio_map[text]) if text else ''
            for audio_field, text in changes.items()
        }
        client.call('updateNoteFields', note={'id': note['noteId'], 'fields': fields})

    print('оновлено: %d | без змін: %d' % (len(plan), len(candidates) - len(plan)))
    return True
