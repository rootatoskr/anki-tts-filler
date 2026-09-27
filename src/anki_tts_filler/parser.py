# Рядок "tags: a b" задає теги ноти, а не поле нотетайпу. Якщо в нотетайпі
# справді є поле з такою назвою, воно має пріоритет: тоді теги задаються
# тільки прапорцем --tag
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
    """Блок рядків -> (поля, теги, помилка).

    Відсутнє поле лишається порожнім, зайве/невідоме поле - помилка картки.
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
    # Картки розділяються порожнім рядком, а не повторенням певного ключа
    return [parse_card(block, text_fields) for block in split_blocks(text)]


def build_fields(card, audio_tags):
    fields = dict(card)
    fields.update(audio_tags)
    return fields
