"""Оркестрація режиму resync: переозвучення вже створених нот Anki.

Та сама схема [cards] пресету, що й у режимі cards (аудіополе -> текстове
поле). На відміну від cards, текст береться не з cards.txt, а з поточних
значень полів уже існуючої ноти - тому й аудіо, і кеш (audio_cache/) ті самі,
що в cards.

Ціль задається назвою пресету (усі ноти його нотетайпу) або Anki-запитом -
тим самим, що в audio/pdf. Без аргументу режим друкує перелік пресетів, як
це робить cards.

Спершу складається план (які поля справді зміняться) і лише потім
озвучуються тексти саме з плану: порівняння імені файлу синтезу не потребує.
"""

import os
import sys

from . import config
from .audio import cache_path, expected_tag, ffmpeg_available, sound_tag, sync_media
from .note_cards import clean_text, note_value


class ResyncError(Exception):
    pass


def escape_query_value(value):
    """Значення для Anki-запиту в лапках.

    У пошуку Anki ``_`` і ``*`` - шаблони, а ``:`` розділяє поле й значення,
    тож назва нотетайпу з такими символами без екранування знайшла б не те.
    Зворотний слеш іде першим, інакше він подвоїв би вже додані слеші.
    """
    for char in ('\\', '"', '*', '_', ':'):
        value = value.replace(char, '\\' + char)
    return '"%s"' % value


def resolve_targets(client, target, presets):
    """Назва пресету або Anki-запит -> (note_ids, підпис для виводу).

    Аргумент, що збігається з назвою пресету, означає всі ноти його
    нотетайпу; будь-який інший іде в Anki як запит.
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
    """Ноти -> [(нота, {аудіополе: текст})] лише для полів, що зміняться.

    Синтезу тут немає: потрібне імʼя файлу рахується з тексту, тому план
    відомий ще до того, як щось озвучено чи залито в Anki. У плані лишається
    сам текст, а не очікуваний тег: тег усе одно беруть з файлу, який
    фактично озвучився.
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
    """Короткий опис плану: по одному рядку на ноту, максимум кілька рядків."""
    lines = []
    for note, changes in plan[:5]:
        text_field = next(iter(index[note['modelName']].values()))
        label = clean_text(note_value(note, text_field))[:50]
        lines.append('  nid:%d %s (%d поле)' % (note['noteId'], label, len(changes)))
    if len(plan) > 5:
        lines.append('  ... ще %d нот' % (len(plan) - 5))
    return lines


def confirm(plan, index):
    """Питає підтвердження перед записом у багато нот.

    Без термінала відповіді взяти нізвідки, тому вважаємо відмовою: інакше
    запуск зі скрипта переозвучив би все мовчки.
    """
    fields = sum(len(changes) for _, changes in plan)
    print('зміниться нот: %d (аудіополів: %d)' % (len(plan), fields))
    for line in describe(plan, index):
        print(line)
    if not sys.stdin.isatty():
        # Без flush рядок у stderr випереджає щойно надрукований план
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
    # Озвучуються тільки тексти з плану, а не всі поля всіх знайдених нот
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
