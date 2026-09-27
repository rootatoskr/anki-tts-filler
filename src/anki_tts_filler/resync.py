"""Оркестрація режиму resync: переозвучення вже створених нот Anki.

Той самий Anki-запит, що й у audio/pdf, і та сама схема [cards] пресету, що й
у режимі cards (аудіополе -> текстове поле). На відміну від cards, текст
береться не з cards.txt, а з поточних значень полів уже існуючої ноти - тому
й аудіо, і кеш (audio_cache/) ті самі, що в cards.
"""

import os

from . import config
from .audio import cache_path, ffmpeg_available, sound_tag, strip_html, sync_media
from .note_cards import note_value


class ResyncError(Exception):
    pass


def source_texts(notes, index):
    """Тексти всіх полів, з яких треба переозвучити аудіо цих нот."""
    texts = set()
    for note in notes:
        for text_field in set(index[note['modelName']].values()):
            text = strip_html(note_value(note, text_field))
            if text:
                texts.add(text)
    return texts


def run(query, settings, client):
    if not ffmpeg_available():
        raise ResyncError('не знайдено ffmpeg – він потрібен для обрізання тиші в згенерованому аудіо')

    presets = config.load_all_presets()
    index = {preset.model_name: preset.cards for preset in presets if preset.cards}

    note_ids = client.find_notes(query)
    print('%s -> нот: %d' % (query, len(note_ids)))
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

    cache_dir = cache_path()
    os.makedirs(cache_dir, exist_ok=True)
    audio_map = sync_media(source_texts(candidates, index), cache_dir, client, settings)

    updated = 0
    unchanged = 0
    for note in candidates:
        fields = {}
        for audio_field, text_field in index[note['modelName']].items():
            text = strip_html(note_value(note, text_field))
            new_value = sound_tag(audio_map[text]) if text else ''
            if new_value != note_value(note, audio_field):
                fields[audio_field] = new_value
        if not fields:
            unchanged += 1
            continue
        client.call('updateNoteFields', note={'id': note['noteId'], 'fields': fields})
        updated += 1

    print('оновлено: %d | без змін: %d' % (updated, unchanged))
    return True
