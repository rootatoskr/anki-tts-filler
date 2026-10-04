"""A cards.txt draft from a list of "Latin + Cyrillic on one line".

The content is taken as is, with four mechanical fixes: the first letter of
every sentence is lowercased (case in note is left alone), a dot at the end of
a value is removed, a dash becomes an en dash (with a space on each side), and
whatever is wrapped in double parentheses goes into note.

Parsing follows the Cyrillic/Latin script boundary, so it works instantly and
deterministically, with no AI. The mode is deliberately narrow - only the base
preset, with the fields production, recognition and note.
"""

import re

SOURCE_FIELD = 'recognition'
TARGET_FIELD = 'production'
NOTE_FIELD = 'note'
FIELDS = (SOURCE_FIELD, TARGET_FIELD, NOTE_FIELD)

# The mode is bound to a single preset: the field names above are the concrete
# fields of base, and for another notetype they would simply be untrue
PRESET_NAME = 'base'

CYRILLIC_CHAR_RE = re.compile(r'[Ѐ-ӿ]')
WS_RE = re.compile(r'\s+')
_DASHES = '-‐‑‒–—―'
# A dash becomes " – " (en dash, one space on each side) even where there was
# no space ("disse -ці" -> "disse – ці"), but only if a space is present on one
# side at least. A dash inside a word is part of the word itself ("T-bane",
# "e-post", "смс-повідомлення") and must not be broken up
DASH_RE = re.compile(r'\s+[%s]\s*|\s*[%s]\s+' % (_DASHES, _DASHES))
# The separator between the languages, left over as a stray "– " at the end of
# the Latin part once the Cyrillic is cut off (for example "en kunde – один клієнт")
TRAILING_DASH_RE = re.compile(r'\s*–\s*$')
# A remark for note is marked by double parentheses, and its place in the line
# does not matter. Single parentheses stay part of the card text itself
# ("en kokk (et yrke)"), because the marker is explicit and nothing is guessed
DOUBLE_PAREN_RE = re.compile(r'\(\((.+?)\)\)')
# Translation in digits instead of Cyrillic: "førti 40", "to tusen og ti 2010"
TRAILING_NUMBER_RE = re.compile(r'\s+(\d[\d\s]*)$')
# A dot at the end of a value is redundant on a card. Only a single one is
# removed - "..." is written on purpose ("jeg heter ..."), while a question or
# exclamation mark carries meaning. Dots inside are left alone, otherwise two
# sentences would run together
TRAILING_DOT_RE = re.compile(r'(?<!\.)\.$')
# Start of a sentence: the very start of the value, or after . ? ! plus a space.
# Only that one letter is lowercased, so proper names in the middle of a sentence
# ("jeg bor i Oslo") stay as they were
SENTENCE_START_RE = re.compile(r'(^|[.!?]\s+)(\w)')


def collapse(text):
    return WS_RE.sub(' ', text).strip()


def normalize_dashes(text):
    return DASH_RE.sub(' – ', text)


def strip_dot(text):
    return TRAILING_DOT_RE.sub('', text).rstrip()


def lower_sentences(text):
    """Only the first letter of every sentence is lowercased, the rest is untouched."""
    return SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).lower(), text)


def make_card(recognition, production, note_raw):
    """Card values from the already split parts.

    Case is fixed only at sentence starts and only in the two main fields: note
    is a remark from double parentheses, taken from the line as is, and there is
    nothing to normalise its case for.
    """
    card = {
        SOURCE_FIELD: strip_dot(lower_sentences(recognition)),
        TARGET_FIELD: strip_dot(lower_sentences(production)),
    }
    if note_raw is not None:
        card[NOTE_FIELD] = strip_dot(note_raw)
    return card


def split_line(line):
    """One line -> a card, or (None, reason) if the boundary was not found.

    note receives the content of double parentheses, wherever they stand; several
    such groups are joined with a space. They are removed before the line is
    split, because a remark usually contains both languages and would throw off
    the Cyrillic/Latin boundary.
    """
    # The trailing dot is removed here, not only when the card is assembled:
    # otherwise it blocks the line-tail regex and "førti 40." would not be
    # recognised as a numeral. Case is left alone here: it is fixed per field,
    # because note is the exception
    body = strip_dot(collapse(normalize_dashes(line)))

    note_raw = None
    groups = DOUBLE_PAREN_RE.findall(body)
    if groups:
        note_raw = ' '.join(group.strip() for group in groups)
        body = collapse(DOUBLE_PAREN_RE.sub(' ', body))

    cyr_match = CYRILLIC_CHAR_RE.search(body)
    if not cyr_match:
        # Numerals: the translation is written in digits and there is no Cyrillic
        # in the line at all ("førti 40"). Digits in the middle of the Latin part
        # do not qualify - only a solid tail of digits at the end of the line
        number = TRAILING_NUMBER_RE.search(body)
        if not number:
            return None, 'немає кириличної (української) частини'
        recognition = body[:number.start()].strip()
        if not recognition:
            return None, 'немає латинської (норвезької) частини'
        return make_card(recognition, number.group(1).strip(), note_raw), None

    recognition = TRAILING_DASH_RE.sub('', body[:cyr_match.start()].rstrip())
    production = body[cyr_match.start():].strip()
    if not recognition:
        return None, 'немає латинської (норвезької) частини'

    return make_card(recognition, production, note_raw), None


def generate(raw):
    """Raw list -> (cards, problems). A line that did not parse yields no card -
    the problem about it is what stops the write in the caller above."""
    cards = []
    problems = []
    # Numbering follows the actual file lines, not the non-empty ones: otherwise
    # the number in an error would not match the number in the editor
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        card, error = split_line(line)
        if error:
            problems.append('рядок %d: %s - %r' % (number, error, line.strip()))
        else:
            cards.append(card)
    return cards, problems


def format_cards(cards):
    blocks = []
    for card in cards:
        lines = ['%s: %s' % (name, card[name]) for name in FIELDS if card.get(name)]
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks) + '\n'
