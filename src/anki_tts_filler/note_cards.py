"""Перетворення нот Anki у картки з окремими сторонами target і native.

Схема полів береться з секції [audio] у presets/*.toml: target - сторона
мови, яку вчать, native - переклад, examples - поле з прикладами. Конкретних
мов тут немає: вони задаються голосами в settings.toml.
"""

import html
import random
import re
from dataclasses import dataclass, field

from . import config

BR_RE = re.compile(r'<br\s*/?>|</div>|</p>', re.IGNORECASE)
TAG_RE = re.compile(r'<[^>]+>')
SOUND_RE = re.compile(r'\[sound:([^\]]+)\]')
CLOZE_RE = re.compile(r'\{\{c\d+::(.*?)(?:::[^}]*?)?\}\}')
NBSP_RE = re.compile(r'[ ​]')
WS_RE = re.compile(r'\s+')
# Інлайновий тег усередині речення лишає по собі пробіл ("du <b>fra</b>?" ->
# "du fra ?"). Три крапки з пробілом перед ними ставлять навмисно, тож
# послідовність крапок не чіпаємо.
SPACE_PUNCT_RE = re.compile(r'\s+(?!\.\.)([.,!?;:])')
DASH = '–'

# Ключ сортування для літер, що йдуть після "z": за кодами символів вони
# опинилися б не там, де стоять в абетці цільової мови
SORT_TAIL = 'zz%02d'


def load_field_map(presets):
    """presets (config.Preset) -> {model_name: {'sides': {...}, 'examples': поле}}.

    Поля беруться з секції [audio] пресету, а готові [sound:...] шукаються за
    мапінгом із [cards]. Пресет без обох сторін audio-режиму не обслуговує -
    ноти такого типу пропускаються (як і невідомий modelName).
    """
    field_map = {}
    for preset in presets:
        if not preset.audio_target or not preset.audio_native:
            continue
        media = preset.media_fields()
        field_map[preset.model_name] = {
            'sides': {
                config.LANG_TARGET: [
                    (text_field, media.get(text_field)) for text_field in preset.audio_target
                ],
                config.LANG_NATIVE: [(text_field, None) for text_field in preset.audio_native],
            },
            'examples': preset.audio_examples,
        }
    return field_map


@dataclass(frozen=True)
class Utterance:
    text: str
    lang: str
    media: str | None = None


@dataclass
class Card:
    note_id: int
    model: str
    sides: dict = field(default_factory=dict)

    def utterances(self):
        for side in self.sides.values():
            yield from side


def clean_text(raw):
    """HTML-поле Anki -> плоский текст, придатний для TTS."""
    text = BR_RE.sub(' ', raw)
    text = SOUND_RE.sub(' ', text)
    text = CLOZE_RE.sub(r'\1', text)
    text = TAG_RE.sub(' ', text)
    text = html.unescape(text)
    text = NBSP_RE.sub(' ', text)
    text = WS_RE.sub(' ', text).strip()
    return SPACE_PUNCT_RE.sub(r'\1', text)


def media_name(raw):
    match = SOUND_RE.search(raw)
    return match.group(1) if match else None


def note_value(note, name):
    """Значення поля ноти з notesInfo; відсутнє поле - порожній рядок."""
    field_data = note['fields'].get(name)
    return field_data['value'] if field_data else ''


def split_lines(raw):
    for line in BR_RE.split(raw):
        text = clean_text(line)
        if text:
            yield text


def note_pairs(raw):
    """Розбирає поле з прикладами на пари 'цільова мова – переклад'.

    Розділювач - перший en dash у рядку: у правій частині він трапляється
    повторно ('En idé er et abstrakt ord. – Ідея – це абстрактне слово.').
    Рядки без en dash - це пояснення рідною мовою, їх пропускаємо.
    """
    for line in split_lines(raw):
        if DASH not in line:
            continue
        left, right = line.split(DASH, 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            yield left, right


def build_card(note, use_anki_media, field_map):
    mapping = field_map.get(note['modelName'])
    if mapping is None:
        return None

    sides = {}
    for lang, specs in mapping['sides'].items():
        utterances = []
        for text_field, audio_field in specs:
            text = clean_text(note_value(note, text_field))
            if not text:
                continue
            media = None
            if use_anki_media and audio_field:
                media = media_name(note_value(note, audio_field))
            utterances.append(Utterance(text, lang, media))
        sides[lang] = utterances

    if not sides.get(config.LANG_TARGET) or not sides.get(config.LANG_NATIVE):
        return None

    return Card(note_id=note['noteId'], model=note['modelName'], sides=sides)


def build_note_cards(note, use_anki_media, field_map):
    """Нота -> основна картка плюс по картці на кожен приклад.

    Поле з прикладами задає ``examples`` у секції [audio] пресету. Приклади
    саме окремими картками: всередині однієї картки спершу звучать усі
    репліки цільової мови і лише потім усі переклади, тож пара
    "приклад - переклад" розсипалася б по різних кінцях картки.
    """
    card = build_card(note, use_anki_media, field_map)
    if card is None:
        return []

    cards = [card]
    examples = field_map[note['modelName']]['examples']
    if examples:
        for left, right in note_pairs(note_value(note, examples)):
            cards.append(Card(
                note_id=note['noteId'],
                model=note['modelName'],
                sides={
                    config.LANG_TARGET: [Utterance(left, config.LANG_TARGET)],
                    config.LANG_NATIVE: [Utterance(right, config.LANG_NATIVE)],
                },
            ))
    return cards


def sort_key(text, language):
    """Ключ абетки цільової мови: без початкового артикля і з її літерами в кінці.

    Артиклі (``sort_prefixes``) і літери після "z" (``sort_extra_letters``)
    задаються в секції [language] settings.toml - у самому коді ніякої
    конкретної мови немає.
    """
    lowered = text.strip().lower()
    for prefix in language.sort_prefixes:
        if lowered.startswith(prefix.lower()):
            lowered = lowered[len(prefix):]
            break
    tail = {
        letter.lower(): SORT_TAIL % index
        for index, letter in enumerate(language.sort_extra_letters)
    }
    return ''.join(tail.get(char, char) for char in lowered)


def apply_order(entries, language):
    """entries - [(елемент, порядок, текст для абетки)] у порядку з Anki.

    Елементи лінійних пресетів лишаються рівно там, де стояли; випадкові
    перемішуються між своїми ж позиціями, впорядковані - сортуються між
    своїми. Тому пресети з різним порядком не заважають один одному.
    """
    ordered = [entry[0] for entry in entries]
    for mode in (config.ORDER_RANDOM, config.ORDER_SORTED):
        positions = [index for index, entry in enumerate(entries) if entry[1] == mode]
        if len(positions) < 2:
            continue
        chosen = [entries[index] for index in positions]
        if mode == config.ORDER_RANDOM:
            random.shuffle(chosen)
        else:
            chosen.sort(key=lambda entry: sort_key(entry[2], language))
        for position, entry in zip(positions, chosen):
            ordered[position] = entry[0]
    return ordered


def build_cards(notes, use_anki_media, field_map):
    """Повертає картки і лічильник пропущених типів нот (без пресету на цей modelName)."""
    cards = []
    skipped = {}
    for note in notes:
        from_note = build_note_cards(note, use_anki_media, field_map)
        if not from_note:
            skipped[note['modelName']] = skipped.get(note['modelName'], 0) + 1
            continue
        cards.extend(from_note)
    return cards, skipped
