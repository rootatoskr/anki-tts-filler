# The line "tags: a b" sets the note tags, not a notetype field. If the notetype
# really does have a field with that name, the field wins: tags are then set
# only by the ``--tag`` flag
TAGS_KEY = 'tags'


def split_blocks(text):
    blocks = []
    current = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_card(lines, text_fields):
    """Block of lines -> (fields, tags, error).

    A missing field stays empty; an extra or unknown field is a card error.
    """
    card = {f: '' for f in text_fields}
    tags = []
    for line in lines:
        if ':' not in line:
            return None, None, f'рядок без ":" – {line}'
        key, value = line.split(':', 1)
        key = key.strip()
        if key == TAGS_KEY and key not in card:
            tags.extend(value.split())
            continue
        if key not in card:
            return None, None, f'невідоме поле "{key}"'
        card[key] = value.strip()
    return card, tags, None


def split_cards(text, text_fields):
    # Cards are separated by a blank line, not by a repeated key
    return [parse_card(block, text_fields) for block in split_blocks(text)]


def build_fields(card, audio_tags):
    fields = dict(card)
    fields.update(audio_tags)
    return fields
