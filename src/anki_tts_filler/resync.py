"""Orchestration of the resync mode: re-synthesising audio for existing Anki notes.

The same [cards] preset schema as in the cards mode (audio field -> text
field). Unlike cards, the text comes not from cards.txt but from the current
field values of an existing note - which is why the audio and the cache
(audio_cache/) are the same ones the cards mode uses.

The target is given either as a preset name (every note of its notetype) or as
an Anki query - the same one as in audio/pdf. With no argument the mode prints
the list of presets, the way cards does.

A plan is built first (which fields really change) and only then are the texts
from that plan synthesised: comparing a file name needs no synthesis.
"""

import os
import sys

from . import config
from .audio import cache_path, expected_tag, ffmpeg_available, sound_tag, sync_media
from .note_cards import clean_text, note_value


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


def resolve_targets(client, target, presets):
    """Preset name or Anki query -> (note_ids, label for the output).

    An argument that matches a preset name means every note of its notetype;
    any other argument goes to Anki as a query.
    """
    preset = next((item for item in presets if item.name == target), None)
    if preset is None:
        return client.find_notes(target), target
    if not preset.cards:
        raise ResyncError(
            'пресет %s не має секції [cards], тобто аудіополів для переозвучення' % target
        )
    query = 'note:%s' % escape_query_value(preset.model_name)
    return client.find_notes(query), 'пресет %s (%s)' % (target, preset.model_name)


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
            text = clean_text(note_value(note, text_field))
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


def confirm(plan, index):
    """Asks for confirmation before writing to many notes.

    With no terminal there is nowhere to get an answer, so it counts as a
    refusal: otherwise a run from a script would re-synthesise everything
    silently.
    """
    fields = sum(len(changes) for _, changes in plan)
    print('зміниться нот: %d (аудіополів: %d)' % (len(plan), fields))
    for line in describe(plan, index):
        print(line)
    if not sys.stdin.isatty():
        # Without a flush the stderr line jumps ahead of the plan just printed
        sys.stdout.flush()
        print('це більше за resync.confirm_from, а підтвердити ніде - звузь запит', file=sys.stderr)
        return False
    return input('переозвучити? [y/N] ').strip().lower() in ('y', 'yes')


def run(query, settings, client):
    if not ffmpeg_available():
        raise ResyncError('не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо')

    presets = config.load_all_presets()
    index = {preset.model_name: preset.cards for preset in presets if preset.cards}

    note_ids, label = resolve_targets(client, query, presets)
    print('%s -> нот: %d' % (label, len(note_ids)))
    if not note_ids:
        print('запит нічого не знайшов')
        return False

    notes = client.notes_info(note_ids)
    config.verify_presets(
        presets,
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
        print('пропущено %d нот типу %s – пресет без [cards]' % (count, model))
    if not candidates:
        print('жодної придатної ноти')
        return False

    plan = build_plan(candidates, index, settings)
    if not plan:
        print('оновлено: 0 | без змін: %d' % len(candidates))
        return True

    if len(plan) >= settings.resync.confirm_from and not confirm(plan, index):
        print('скасовано')
        return False

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
