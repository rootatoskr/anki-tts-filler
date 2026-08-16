from .config import KEY_RE, START_KEY, EXPECTED_SET, FIELDS


def split_cards(text):
    cards = []
    current = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = KEY_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == START_KEY and current:
            cards.append(current)
            current = {}
        current[key] = value
    if current:
        cards.append(current)
    return cards


def validate(card):
    return sorted(EXPECTED_SET - card.keys())


def build_fields(card, audio_tags):
    fields = {f: card[f] for f in FIELDS}
    fields.update(audio_tags)
    return fields
