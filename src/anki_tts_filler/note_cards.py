"""Turning Anki notes into cards with separate target and native sides.

The field schema comes from the [audio] section in presets/*.toml: target is
the side in the language being learnt, native is the translation, examples is
the field with examples. No specific language appears here: they are set by the
voices in settings.toml.
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
# An inline tag inside a sentence leaves a space behind ("du <b>fra</b>?" ->
# "du fra ?"). An ellipsis with a space before it is written on purpose, so a
# run of dots is left alone.
SPACE_PUNCT_RE = re.compile(r'\s+(?!\.\.)([.,!?;:])')
DASH = '–'

# Sort key for letters that come after "z": by code point they would land
# somewhere other than their place in the target alphabet. The substitution goes
# to the tail, so it works for Latin alphabets; see ``LanguageConfig`` in config.py
SORT_TAIL = 'zz%02d'


def load_field_map(presets):
    """presets (config.Preset) -> {model_name: {'sides': {...}, 'examples': field}}.

    The fields come from the [audio] section of a preset, while ready
    [sound:...] tags are looked up through the mapping from [cards]. A preset
    without both sides does not serve the audio mode - notes of that type are
    skipped, as is an unknown modelName.
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
    """An Anki HTML field -> flat text fit for TTS."""
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
    """A note field value from notesInfo; a missing field gives an empty string."""
    field_data = note['fields'].get(name)
    return field_data['value'] if field_data else ''


def split_lines(raw):
    for line in BR_RE.split(raw):
        text = clean_text(line)
        if text:
            yield text


def note_pairs(raw):
    """Parses the examples field into 'target language – translation' pairs.

    The separator is the first en dash in the line: in the right-hand part it
    occurs again ('En idé er et abstrakt ord. – Ідея – це абстрактне слово.').
    Lines without an en dash are explanations in the native language and are
    skipped.
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
    """A note -> the main card plus one card per example.

    The examples field is named by ``examples`` in the [audio] section of a
    preset. Examples become separate cards on purpose: inside one card every
    target-language line is spoken first and only then every translation, so an
    "example - translation" pair would be scattered to opposite ends of the card.
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
    """Alphabet key of the target language: no leading article, its letters last.

    The articles (``sort_prefixes``) and the letters after "z"
    (``sort_extra_letters``) are set in the [language] section of settings.toml -
    no specific language lives in the code itself.
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
    """entries - [(item, order, text for the alphabet)] in the order from Anki.

    Items of linear presets stay exactly where they were; random ones are
    shuffled among their own positions, sorted ones are sorted among theirs.
    That is why presets with different orders do not interfere with each other.
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
    """Returns the cards and a counter of skipped note types (no preset for that modelName)."""
    cards = []
    skipped = {}
    for note in notes:
        from_note = build_note_cards(note, use_anki_media, field_map)
        if not from_note:
            skipped[note['modelName']] = skipped.get(note['modelName'], 0) + 1
            continue
        cards.extend(from_note)
    return cards, skipped
