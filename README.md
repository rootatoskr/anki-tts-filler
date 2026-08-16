# anki-tts-filler

Заповнення карток у вже створеній **колоді/notetype** в **Anki** через [AnkiConnect](https://github.com/amikey/anki-connect). Скрипт не визначає вигляд картки чи структуру колоди – тільки дані (**текст + аудіо**). Схема полів і карток задається у файлах (`fields.toml`, `cards.txt`), не в коді – не привʼязаний до конкретної мови чи колоди.

## Як це працює

1. **Колоду** й **поля** створити та редагувати вручну в **Anki** (*поля, шаблон картки, CSS*).
2. Задати ті самі назви **полів** і назви **колоди** в `fields.toml`.
3. Записати список карток у `cards.txt` у форматі `ключ: значення`.
4. **Anki** має бути відкритий з увімкненим аддоном **AnkiConnect**.
5. Запустити скрипт – аудіо генерується через `edge-tts` + `ffmpeg`, і картки додаються напряму в **Anki** через **AnkiConnect**.

## Встановлення

```bash
uv sync --no-editable
```

- Утиліти:
    - `ffmpeg`
    - [AnkiConnect](https://ankiweb.net/shared/info/2055492159) в **Anki** (`2055492159`, `Tools → Add-ons → Browse & Install`)

## Використання

```bash
uv run anki-tts-filler
```

**Anki** має бути запущений. Вхідні картки беруться з `cards.txt` у поточній директорії.

## Схема полів (fields.toml)

Файл лежить поруч із `cards.txt`, у поточній директорії. Якщо його нема – скрипт створить шаблон при першому запуску і вийде. Готовий приклад – `fields.toml.example` та `cards.txt.example`, скопіювати їх у `fields.toml`/`cards.txt` як старт.

```toml
deck_name = "MyDeck::MySubdeck"
model_name = "MyNoteType"

start_key = "field_one"

fields = [
    "field_one",
    "field_two",
]

[audio_fields]
audio_field_one = "field_one"
```

- `deck_name` / `model_name` – мають **дослівно** збігатися з назвами вже створених в **Anki** (для підтек – з `::`, як у самому **Anki**).
- `fields` – текстові поля, які вводяться вручну в `cards.txt`. Мають збігатися з полями в **Anki** (`Tools → Manage Note Types → Fields...`).
- `start_key` – з якого поля починається нова картка в `cards.txt`.
- `[audio_fields]` – `назва_аудіо_поля = "з_якого_текстового_поля_генерувати_аудіо"`. Ці аудіополя теж мають існувати в полях **Anki**.

## Формат вхідного файлу

Кожна картка – блок пар `ключ: значення`, ключі беруться з `fields` у `fields.toml`:

```
field_one: значення першого поля
field_two: значення другого поля
```

Нова картка починається з поля `start_key`. Порожні рядки ігноруються. Усі поля з `fields` обовʼязкові.

На кожну картку створюється одна нотатка напряму в **Anki**. Якщо нотатка з такими даними вже існує – **AnkiConnect** поверне помилку дубліката, скрипт це коректно пропускає (**не падає**) і рахує окремо.

## Структура проєкту

```
src/anki_tts_filler/
    main.py – точка входу (entry point anki-tts-filler)
    config.py – читає fields.toml, статичні налаштування (TTS, AnkiConnect URL)
    ankiconnect.py – HTTP-клієнт для AnkiConnect (localhost:8765)
    parser.py – парсинг cards.txt, побудова словника полів
    audio.py – генерація TTS-аудіо через edge-tts + ffmpeg
fields.toml – схема полів/колоди/notetype (дані, не код)
fields.toml.example – приклад fields.toml
cards.txt – вхідний файл із картками
cards.txt.example – приклад cards.txt
audio_cache/ – локальний кеш згенерованих mp3
```

