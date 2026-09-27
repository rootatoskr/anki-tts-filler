"""Режим prune: згенеровані mp3, на які вже не посилається жодна нота.

Кеш audio_cache/ і медіатека Anki ростуть назавжди: відредагований текст
картки дає новий файл, а старий лишається обома копіями. Тут шукаються саме
файли з префіксом MEDIA_PREFIX - чужі файли в медіатеці не перевіряються й
не чіпаються.

Типово лише звіт. Видалення - тільки з --apply, бо стирання медіафайлів
Anki не відкотити.
"""

import os

from . import config
from .audio import cache_path, media_pattern
from .note_cards import SOUND_RE, note_value

# Порожній запит AnkiConnect не приймає; "deck:*" охоплює всю колекцію
ALL_NOTES_QUERY = 'deck:*'


class PruneError(Exception):
    pass


def referenced_names(notes):
    """Імена файлів MEDIA_PREFIX, на які посилається хоч одна нота."""
    used = set()
    for note in notes:
        for name in note['fields']:
            for match in SOUND_RE.findall(note_value(note, name)):
                if match.startswith(config.MEDIA_PREFIX):
                    used.add(match)
    return used


def cache_files(cache_dir):
    if not os.path.isdir(cache_dir):
        return []
    return sorted(
        name for name in os.listdir(cache_dir)
        if name.startswith(config.MEDIA_PREFIX) and name.endswith('.mp3')
    )


def total_size(cache_dir, names):
    return sum(os.path.getsize(os.path.join(cache_dir, name)) for name in names)


def human_size(size):
    if size >= 1024 * 1024:
        return '%.1f МБ' % (size / (1024 * 1024))
    return '%.0f КБ' % (size / 1024)


def run(settings, client, apply_changes, include_anki):
    cache_dir = cache_path()

    note_ids = client.find_notes(ALL_NOTES_QUERY)
    notes = client.notes_info(note_ids)
    used = referenced_names(notes)
    print('нот у колекції: %d | посилань на %s*: %d' % (len(notes), config.MEDIA_PREFIX, len(used)))

    local = cache_files(cache_dir)
    orphans = [name for name in local if name not in used]
    print('%s: файлів %d, зайвих %d (%s)' % (
        config.AUDIO_CACHE_DIR, len(local), len(orphans), human_size(total_size(cache_dir, orphans)),
    ))

    media_orphans = []
    if include_anki:
        in_media = set(client.call('getMediaFilesNames', pattern=media_pattern()))
        media_orphans = sorted(in_media - used)
        print('медіатека Anki: файлів %d, зайвих %d' % (len(in_media), len(media_orphans)))

    if not orphans and not media_orphans:
        print('нічого видаляти')
        return True

    if not apply_changes:
        for name in orphans[:10]:
            print('  %s/%s' % (config.AUDIO_CACHE_DIR, name))
        if len(orphans) > 10:
            print('  ... ще %d' % (len(orphans) - 10))
        print('це лише звіт; видалити - той самий запит з --apply')
        return True

    for name in orphans:
        os.remove(os.path.join(cache_dir, name))
    for name in media_orphans:
        client.call('deleteMediaFile', filename=name)
    print('видалено: %s %d | медіатека Anki %d' % (
        config.AUDIO_CACHE_DIR, len(orphans), len(media_orphans),
    ))
    return True
