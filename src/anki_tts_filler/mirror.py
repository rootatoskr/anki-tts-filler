"""Orchestration of the mirror mode: note duplicates in a reverse-direction notetype.

The source preset states in its [mirror] section where to write (preset) and
which field pair to swap (swap). The duplicate goes into a separate notetype on
purpose: lying in the same one, it would be matched by the resync and audio
modes under the same model_name, and they would see the target language where
the translation now is.

The audio field is copied as is. That is no accident: the file name is a
fingerprint of the text, and after the swap the text simply sits in another
field, so the receiving preset (whose [cards] points at that other field)
considers such audio up to date.
"""

import sys

from . import config
from .note_cards import note_value


class MirrorError(Exception):
    pass


def escape_query_value(value):
    """A value for an Anki query, in quotes; the same rules as in resync."""
    for char in ('\\', '"', '*', '_', ':'):
        value = value.replace(char, '\\' + char)
    return '"%s"' % value


def resolve_targets(client, target, presets):
    """Preset name or Anki query -> (note_ids, label for the output)."""
    preset = next((item for item in presets if item.name == target), None)
    if preset is None:
        return client.find_notes(target), target
    if not preset.mirror_preset:
        raise MirrorError('пресет %s не має секції [mirror], тобто дублікати нема куди писати' % target)
    query = 'note:%s' % escape_query_value(preset.model_name)
    return client.find_notes(query), 'пресет %s (%s)' % (target, preset.model_name)


def mirrored_fields(note, swap, target_fields):
    """Note fields for the duplicate: a copy by name plus the swapped pair.

    Only fields that exist in the receiving notetype are copied: the rest simply
    have nowhere to go.
    """
    first, second = swap
    fields = {}
    for name in target_fields:
        fields[name] = note_value(note, name)
    if first in fields:
        fields[first] = note_value(note, second)
    if second in fields:
        fields[second] = note_value(note, first)
    return fields


def build_notes(notes, sources, targets, client, extra_tags):
    """Notes -> (drafts, skipped types, first field of each notetype).

    The first field is needed separately: Anki determines duplicates by it, so
    repeats inside the selection itself are caught by it too.
    """
    prepared = []
    skipped = {}
    fields_cache = {}
    for note in notes:
        source = sources.get(note['modelName'])
        if source is None:
            skipped[note['modelName']] = skipped.get(note['modelName'], 0) + 1
            continue
        target = targets[source.name]
        if target.model_name not in fields_cache:
            fields_cache[target.model_name] = client.call('modelFieldNames', modelName=target.model_name)
        prepared.append((note, {
            'deckName': target.deck_name,
            'modelName': target.model_name,
            'fields': mirrored_fields(note, source.mirror_swap, fields_cache[target.model_name]),
            'tags': sorted(set(note.get('tags', [])) | set(extra_tags)),
        }))
    first_fields = {model: fields[0] for model, fields in fields_cache.items() if fields}
    return prepared, skipped, first_fields


def select_addable(prepared, checks, first_fields):
    """Splits the drafts into the addable ones and the reasons for refusal.

    canAddNotesWithErrorDetail checks each note against the collection but not
    against the rest of the same request, while addNotes adds them one by one -
    so two duplicates with the same first field bring down the whole request.
    That is why repeats inside the selection are filtered out here.
    """
    fresh = []
    duplicate = 0
    repeated = []
    rejected = []
    seen = set()
    for (note, item), check in zip(prepared, checks):
        if not check['canAdd']:
            reason = check.get('error', 'нотатку не можна додати')
            if 'duplicate' in reason:
                duplicate += 1
            else:
                rejected.append((note, reason))
            continue
        key = item['fields'].get(first_fields.get(item['modelName'], ''), '')
        if key in seen:
            repeated.append((note, key))
            continue
        seen.add(key)
        fresh.append((note, item))
    return fresh, duplicate, repeated, rejected


def confirm(prepared, swap_by_model):
    """Asks for confirmation: mirror creates notes, and undoing that is harder than an edit."""
    print('буде створено нот: %d' % len(prepared))
    for note, prepared_note in prepared[:5]:
        first = swap_by_model[note['modelName']][0]
        print('  %s -> %s' % (
            note_value(note, first)[:40] or '(порожньо)',
            prepared_note['fields'].get(first, '')[:40] or '(порожньо)',
        ))
    if len(prepared) > 5:
        print('  ... ще %d' % (len(prepared) - 5))
    if not sys.stdin.isatty():
        sys.stdout.flush()
        print('підтвердити ніде - запусти в терміналі', file=sys.stderr)
        return False
    return input('створити? [y/N] ').strip().lower() in ('y', 'yes')


def run(target, settings, client, extra_tags):
    presets = config.load_all_presets()
    by_name = {preset.name: preset for preset in presets}

    sources = {}
    targets = {}
    for preset in presets:
        if not preset.mirror_preset:
            continue
        receiver = by_name.get(preset.mirror_preset)
        if receiver is None:
            raise MirrorError('presets/%s.toml: mirror.preset вказує на %s, а такого пресету немає' % (
                preset.name, preset.mirror_preset,
            ))
        if receiver.model_name == preset.model_name:
            raise MirrorError('presets/%s.toml: mirror.preset %s має той самий нотетайп %s - дублікати мають іти в окремий' % (
                preset.name, receiver.name, receiver.model_name,
            ))
        sources[preset.model_name] = preset
        targets[preset.name] = receiver

    if not sources:
        raise MirrorError('жоден пресет не має секції [mirror]')

    note_ids, label = resolve_targets(client, target, presets)
    print('%s -> нот: %d' % (label, len(note_ids)))
    if not note_ids:
        print('запит нічого не знайшов')
        return False

    notes = client.notes_info(note_ids)
    config.verify_presets(
        presets,
        {note['modelName'] for note in notes},
        lambda model: client.call('modelFieldNames', modelName=model),
        'mirror',
    )

    # The receiving deck has to exist: Anki creates a missing one silently, and
    # the duplicates would settle in a newly made deck named after a placeholder
    decks = set(client.call('deckNames'))
    for receiver in set(targets.values()):
        if receiver.deck_name not in decks:
            raise MirrorError('колоди "%s" з presets/%s.toml немає в Anki' % (receiver.deck_name, receiver.name))

    prepared, skipped, first_fields = build_notes(notes, sources, targets, client, extra_tags)
    for model, count in sorted(skipped.items()):
        print('пропущено %d нот типу %s – пресет без [mirror]' % (count, model))
    if not prepared:
        print('жодної придатної ноти')
        return False

    # A repeat run on the same selection duplicates nothing: Anki rejects a note
    # whose first field already exists
    checks = client.call('canAddNotesWithErrorDetail', notes=[item for _, item in prepared])
    fresh, duplicate, repeated, rejected = select_addable(prepared, checks, first_fields)

    if duplicate:
        print('уже існують: %d' % duplicate)
    if repeated or rejected:
        # Without a flush the stderr lines jump ahead of the report just printed
        sys.stdout.flush()
    for note, key in repeated:
        print('дубль усередині вибірки: nid:%d – %s' % (note['noteId'], key[:50]), file=sys.stderr)
    for note, reason in rejected:
        print('пропущено nid:%d – %s' % (note['noteId'], reason), file=sys.stderr)
    if not fresh:
        print('створювати нема чого')
        return True

    swap_by_model = {preset.model_name: preset.mirror_swap for preset in sources.values()}
    if not confirm(fresh, swap_by_model):
        print('скасовано')
        return False

    added = client.call('addNotes', notes=[item for _, item in fresh])
    created = sum(1 for note_id in added if note_id is not None)
    failed = len(added) - created
    print('створено: %d | уже існували: %d | повтори у вибірці: %d | не додалось: %d' % (
        created, duplicate, len(repeated), failed,
    ))
    return failed == 0
