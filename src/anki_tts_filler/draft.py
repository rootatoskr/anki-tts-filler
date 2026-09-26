"""Чернетка cards.txt зі списку "норвезька + українська в одному рядку".

Ніякого форматування змісту: recognition/production - точно та частина
рядка, що лишилась після відсічення роздільників, буква в букву. Єдині дві
механічні правки - тире стає en dash (з пробілом з обох боків) і дужки, якщо
рядок має рівно одну пару дужок, ідуть у note.

Розбір - по межі скриптів кирилиця/латиниця, тому працює миттєво й
детерміновано, без ШІ. Режим навмисно вузький - тільки пресет ordforrad із
полями production, recognition і note.
"""

import re

SOURCE_FIELD = 'recognition'
TARGET_FIELD = 'production'
NOTE_FIELD = 'note'
FIELDS = (SOURCE_FIELD, TARGET_FIELD, NOTE_FIELD)

# Режим прив'язаний до одного пресету: назви полів вище - конкретні поля
# ordforrad, для іншого нотетайпу вони були б просто неправдою
PRESET_NAME = 'ordforrad'

CYRILLIC_CHAR_RE = re.compile(r'[Ѐ-ӿ]')
WS_RE = re.compile(r'\s+')
_DASHES = '-‐‑‒–—―'
# Тире стає " – " (en dash, по одному пробілу з обох боків) навіть там, де
# пробілу не було ("disse -ці" -> "disse – ці"), але тільки якщо з якогось
# боку пробіл усе-таки є. Тире всередині слова - частина самого слова
# ("T-bane", "e-post", "смс-повідомлення"), і розривати його не можна
DASH_RE = re.compile(r'\s+[%s]\s*|\s*[%s]\s+' % (_DASHES, _DASHES))
# Роздільник між мовами, що лишається зайвим "– " у кінці норвезької частини
# після відсічення кирилиці (наприклад "en kunde – один клієнт")
TRAILING_DASH_RE = re.compile(r'\s*–\s*$')
# Одна пара дужок рівно в кінці рядка - решта тексту вже позаду
PAREN_GROUP_RE = re.compile(r'\([^()]*\)')
TRAILING_PAREN_RE = re.compile(r'\s*\(([^()]*)\)\s*$')
# Переклад цифрами замість кирилиці: "førti 40", "to tusen og ti 2010"
TRAILING_NUMBER_RE = re.compile(r'\s+(\d[\d\s]*)$')


def collapse(text):
    return WS_RE.sub(' ', text).strip()


def normalize_dashes(text):
    return DASH_RE.sub(' – ', text)


def split_line(line):
    """Один рядок -> картка або (None, причина), якщо межу не знайдено.

    Дужки йдуть у note, тільки коли в рядку рівно одна пара: дві й більше -
    ознака, що це не окрема ремарка, а частина самого перекладу
    ("en kokk (et yrke) кухар (професія)" лишається одним цілим).
    """
    body = collapse(normalize_dashes(line))

    note_raw = None
    if len(PAREN_GROUP_RE.findall(body)) == 1:
        match = TRAILING_PAREN_RE.search(body)
        if match:
            note_raw = match.group(1)
            body = body[:match.start()].rstrip()

    cyr_match = CYRILLIC_CHAR_RE.search(body)
    if not cyr_match:
        # Числівники: переклад записаний цифрами, кирилиці в рядку нема
        # зовсім ("førti 40"). Цифри посеред норвезької частини під це не
        # підпадають - береться лише суцільний хвіст із цифр у кінці рядка
        number = TRAILING_NUMBER_RE.search(body)
        if not number:
            return None, 'немає кириличної (української) частини'
        recognition = body[:number.start()].strip()
        if not recognition:
            return None, 'немає латинської (норвезької) частини'
        card = {SOURCE_FIELD: recognition, TARGET_FIELD: number.group(1).strip()}
        if note_raw is not None:
            card[NOTE_FIELD] = note_raw
        return card, None

    recognition = TRAILING_DASH_RE.sub('', body[:cyr_match.start()].rstrip())
    production = body[cyr_match.start():].strip()
    if not recognition:
        return None, 'немає латинської (норвезької) частини'

    card = {SOURCE_FIELD: recognition, TARGET_FIELD: production}
    if note_raw is not None:
        card[NOTE_FIELD] = note_raw
    return card, None


def generate(raw):
    """Сирий список -> (картки, зауваження). Рядок, що не розібрався, картки
    не дає - зауваження про нього і зупиняє запис у виклику вище."""
    cards = []
    problems = []
    # Нумерація за фактичними рядками файлу, а не за непорожніми: інакше з
    # порожніми рядками номер у помилці не збігався б із номером у редакторі
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
