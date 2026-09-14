"""Перетворення нот Anki у картки з окремими норвезькою та українською сторонами.

Схема полів береться з presets/*.toml (той самий файл, що й для режиму
cards) - audio_fields дає норвезьку сторону, translation_field - українську.
"""

import html
import re
from dataclasses import dataclass, field

BR_RE = re.compile(r'<br\s*/?>|</div>|</p>', re.IGNORECASE)
TAG_RE = re.compile(r'<[^>]+>')
SOUND_RE = re.compile(r'\[sound:([^\]]+)\]')
CLOZE_RE = re.compile(r'\{\{c\d+::(.*?)(?:::[^}]*?)?\}\}')
NBSP_RE = re.compile(r'[ ​]')
WS_RE = re.compile(r'\s+')
DASH = '–'


def load_field_map(presets):
    """presets (config.Preset) -> {model_name: {'no': [...], 'uk': [...]}}.

    Пресети без translation_field не дають уk-сторони - ноти такого типу
    просто пропускаються в audio-режимі (як і невідомий modelName).
    """
    field_map = {}
    for preset in presets:
        no_side = [(text_field, audio_field) for audio_field, text_field in preset.audio_fields.items()]
        uk_side = [(preset.translation_field, None)] if preset.translation_field else []
        field_map[preset.model_name] = {'no': no_side, 'uk': uk_side}
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
    return WS_RE.sub(' ', text).strip()


def media_name(raw):
    match = SOUND_RE.search(raw)
    return match.group(1) if match else None


def split_lines(raw):
    for line in BR_RE.split(raw):
        text = clean_text(line)
        if text:
            yield text


def note_pairs(raw):
    """Розбирає поле ``note`` на пари 'норвезька – українська'.

    Розділювач - перший en dash у рядку: у правій частині він трапляється
    повторно ('En idé er et abstrakt ord. – Ідея – це абстрактне слово.').
    Рядки без en dash - це українські граматичні пояснення, їх пропускаємо.
    """
    for line in split_lines(raw):
        if DASH not in line:
            continue
        left, right = line.split(DASH, 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            yield left, right


def _value(note, name):
    field_data = note['fields'].get(name)
    return field_data['value'] if field_data else ''


def build_card(note, use_anki_media, include_note, field_map):
    mapping = field_map.get(note['modelName'])
    if mapping is None:
        return None

    sides = {}
    for lang, specs in mapping.items():
        utterances = []
        for text_field, audio_field in specs:
            text = clean_text(_value(note, text_field))
            if not text:
                continue
            media = None
            if use_anki_media and audio_field:
                media = media_name(_value(note, audio_field))
            utterances.append(Utterance(text, lang, media))
        sides[lang] = utterances

    if not sides.get('no') or not sides.get('uk'):
        return None

    if include_note:
        for left, right in note_pairs(_value(note, 'note')):
            sides['no'].append(Utterance(left, 'no'))
            sides['uk'].append(Utterance(right, 'uk'))

    return Card(note_id=note['noteId'], model=note['modelName'], sides=sides)


def build_cards(notes, use_anki_media, include_note, field_map):
    """Повертає картки і лічильник пропущених типів нот (без пресету на цей modelName)."""
    cards = []
    skipped = {}
    for note in notes:
        card = build_card(note, use_anki_media, include_note, field_map)
        if card is None:
            skipped[note['modelName']] = skipped.get(note['modelName'], 0) + 1
            continue
        cards.append(card)
    return cards, skipped
