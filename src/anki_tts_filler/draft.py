"""Чернетка cards.txt зі списку "норвезька + українська в одному рядку".

Зміст беруть як є, з чотирьох механічних правок: перша літера кожного речення
стає малою (у note регістр не чіпається), крапка в кінці значення знімається,
тире стає en dash (з пробілом з обох боків), а те, що взяте в подвійні дужки,
іде в note.

Розбір - по межі скриптів кирилиця/латиниця, тому працює миттєво й
детерміновано, без ШІ. Режим навмисно вузький - тільки пресет base із
полями production, recognition і note.
"""

import re

SOURCE_FIELD = 'recognition'
TARGET_FIELD = 'production'
NOTE_FIELD = 'note'
FIELDS = (SOURCE_FIELD, TARGET_FIELD, NOTE_FIELD)

# Режим прив'язаний до одного пресету: назви полів вище - конкретні поля
# base, для іншого нотетайпу вони були б просто неправдою
PRESET_NAME = 'base'

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
# Ремарка для note позначається подвійними дужками, і місце в рядку значення
# не має. Одинарні дужки лишаються частиною самого тексту картки
# ("en kokk (et yrke)"), бо позначка тепер явна й угадувати нема чого
DOUBLE_PAREN_RE = re.compile(r'\(\((.+?)\)\)')
# Переклад цифрами замість кирилиці: "førti 40", "to tusen og ti 2010"
TRAILING_NUMBER_RE = re.compile(r'\s+(\d[\d\s]*)$')
# Крапка в кінці значення: у картці вона зайва. Знімається лише одинична - "..."
# ставлять навмисно ("jeg heter ..."), а знак питання й оклику несуть зміст.
# Крапки всередині не чіпаються, інакше два речення злиплися б в одне
TRAILING_DOT_RE = re.compile(r'(?<!\.)\.$')
# Початок речення: сам початок значення або після . ? ! з пробілом. З малої
# робиться лише ця одна літера, тому власні назви в середині речення
# ("jeg bor i Oslo") лишаються як були
SENTENCE_START_RE = re.compile(r'(^|[.!?]\s+)(\w)')


def collapse(text):
    return WS_RE.sub(' ', text).strip()


def normalize_dashes(text):
    return DASH_RE.sub(' – ', text)


def strip_dot(text):
    return TRAILING_DOT_RE.sub('', text).rstrip()


def lower_sentences(text):
    """З малої лише перша літера кожного речення, решта тексту без змін."""
    return SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).lower(), text)


def make_card(recognition, production, note_raw):
    """Значення картки з уже поділених частин.

    Регістр правиться тільки на початку речень і тільки в двох основних
    полях: note - це ремарка з подвійних дужок, узята з рядка як є, і
    зводити її регістр нема за чим.
    """
    card = {
        SOURCE_FIELD: strip_dot(lower_sentences(recognition)),
        TARGET_FIELD: strip_dot(lower_sentences(production)),
    }
    if note_raw is not None:
        card[NOTE_FIELD] = strip_dot(note_raw)
    return card


def split_line(line):
    """Один рядок -> картка або (None, причина), якщо межу не знайдено.

    У note іде вміст подвійних дужок, скільком би місцях вони не стояли;
    кілька таких груп склеюються пробілом. Знімаються вони до поділу рядка,
    бо ремарка зазвичай містить обидві мови і межу кирилиця/латиниця збила б.
    """
    # Кінцева крапка знімається тут, а не лише при збиранні картки: інакше
    # вона впирається в регулярку хвоста рядка і "førti 40." не розпізнався б
    # як числівник. Регістр тут не чіпаємо: він правиться по полях, бо note
    # виняток
    body = strip_dot(collapse(normalize_dashes(line)))

    note_raw = None
    groups = DOUBLE_PAREN_RE.findall(body)
    if groups:
        note_raw = ' '.join(group.strip() for group in groups)
        body = collapse(DOUBLE_PAREN_RE.sub(' ', body))

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
        return make_card(recognition, number.group(1).strip(), note_raw), None

    recognition = TRAILING_DASH_RE.sub('', body[:cyr_match.start()].rstrip())
    production = body[cyr_match.start():].strip()
    if not recognition:
        return None, 'немає латинської (норвезької) частини'

    return make_card(recognition, production, note_raw), None


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
