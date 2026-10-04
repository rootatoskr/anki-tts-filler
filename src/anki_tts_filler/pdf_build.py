"""A printed version of the same selection as the audio mode.

The same Anki query and the same presets, but instead of concatenating mp3
files - a page for printing: the fields from the [pdf] fields list, each on its
own line, cards one after another. There is no split into target and native
sides here - the fields follow one another in the order written in the preset.

HTML is an intermediate format here: headless Chrome turns it into a PDF, so
that no dependency is pulled into the project just for printing, after which
the HTML is deleted. It is kept only when Chrome was not found.
"""

import html
import os
import shutil
import subprocess
import time
from datetime import date

from . import config, note_cards
from .audio_build import safe_name

# Headless Chrome can do ``--print-to-pdf``; look for both the macOS app and a
# command in PATH (on Linux it is the latter)
CHROME_APPS = (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
)
CHROME_COMMANDS = ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')

PDF_TIMEOUT = 60
POLL_INTERVAL = 0.4

CSS = '''@page { size: A4; margin: 12mm; }
body { margin: 0; color: #000; background: #fff; font-family: "Helvetica Neue", Arial, sans-serif; font-size: 11pt; line-height: 1.35; }
h1 { margin: 0 0 4mm; font-size: 11pt; font-weight: 600; }
h1 span { color: #888; font-weight: 400; }
.cards { column-count: 2; column-gap: 10mm; }
.card { break-inside: avoid; padding: 0 0 3.5mm; margin: 0 0 3.5mm; border-bottom: 0.4pt solid #ccc; }
.line { white-space: pre-wrap; }
'''


class PdfError(Exception):
    pass


def note_lines(note, fields):
    """Note fields as separate lines, exactly those and in the [pdf] fields order.

    An empty field yields no line, while a field of several lines (<br> in Anki)
    lands as several lines.
    """
    lines = []
    for name in fields:
        lines.extend(note_cards.split_lines(note_cards.note_value(note, name)))
    return lines


def render(cards, query, lang):
    blocks = []
    for lines in cards:
        rows = ''.join('<div class="line">%s</div>' % html.escape(text) for text in lines)
        blocks.append('<div class="card">%s</div>' % rows)

    # The query in the heading keeps no escaping: a backslash is Anki search
    # syntax, not part of the tag name
    title = html.escape(query.replace('\\', ''))
    head = '%s <span>· карток: %d · %s</span>' % (title, len(cards), date.today().isoformat())
    return (
        '<!doctype html>\n<html lang="%s"><head><meta charset="utf-8">'
        '<title>%s</title><style>%s</style></head>\n<body>\n<h1>%s</h1>\n'
        '<div class="cards">\n%s\n</div>\n</body></html>\n'
    ) % (html.escape(lang, quote=True), title, CSS, head, '\n'.join(blocks))


def find_chrome():
    for path in CHROME_APPS:
        if os.path.exists(path):
            return path
    for name in CHROME_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    return None


def pdf_ready(path):
    """The PDF is complete once the %%EOF marker shows up in its tail."""
    size = os.path.getsize(path)
    if size < 32:
        return False
    with open(path, 'rb') as handle:
        handle.seek(-32, os.SEEK_END)
        return b'%%EOF' in handle.read()


def to_pdf(chrome, html_path, profile_dir):
    """HTML -> PDF through headless Chrome.

    A separate --user-data-dir is needed so the user profile is left alone, but
    with it Chrome does not exit by itself after printing - so we wait for the
    finished file and stop the process.
    """
    pdf_path = html_path[:-len('.html')] + '.pdf'
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    process = subprocess.Popen(
        [
            chrome,
            '--headless',
            '--disable-gpu',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-background-networking',
            '--disable-extensions',
            '--no-pdf-header-footer',
            '--user-data-dir=%s' % profile_dir,
            '--print-to-pdf=%s' % pdf_path,
            'file://%s' % os.path.abspath(html_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + PDF_TIMEOUT
        previous = -1
        while time.monotonic() < deadline:
            exited = process.poll() is not None
            if os.path.exists(pdf_path):
                size = os.path.getsize(pdf_path)
                if size > 0 and size == previous and pdf_ready(pdf_path):
                    return pdf_path
                previous = size
            elif exited:
                raise PdfError('Chrome завершився, не створивши %s' % pdf_path)
            time.sleep(POLL_INTERVAL)
        raise PdfError('Chrome не надрукував %s за %d с' % (pdf_path, PDF_TIMEOUT))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def run(query, settings, client):
    presets = config.load_all_presets()
    index = {preset.model_name: preset.pdf_fields for preset in presets if preset.pdf_fields}

    note_ids = client.find_notes(query)
    print('%s -> нот: %d' % (query, len(note_ids)))
    if not note_ids:
        print('запит нічого не знайшов')
        return []

    notes = client.notes_info(note_ids)
    config.verify_presets(
        presets,
        {note['modelName'] for note in notes},
        lambda model: client.call('modelFieldNames', modelName=model),
        'pdf',
    )

    entries = []
    skipped = {}
    orders = config.orders(presets, 'pdf')
    for note in notes:
        fields = index.get(note['modelName'])
        lines = note_lines(note, fields) if fields else []
        if not lines:
            skipped[note['modelName']] = skipped.get(note['modelName'], 0) + 1
            continue
        # for the alphabet the key is the first line of the card
        entries.append((lines, orders.get(note['modelName'], config.ORDER_LINEAR), lines[0]))

    for model, count in sorted(skipped.items()):
        print('пропущено %d нот типу %s' % (count, model))
    if not entries:
        print('жодної придатної картки')
        return []

    cards = note_cards.apply_order(entries, settings.language)

    os.makedirs(settings.output.dir, exist_ok=True)
    os.makedirs(settings.work_dir, exist_ok=True)
    chrome = find_chrome()
    html_path = os.path.join(settings.output.dir, safe_name(query) + '.html')
    with open(html_path, 'w', encoding='utf-8') as handle:
        handle.write(render(cards, query, settings.language.code))

    if chrome:
        out_path = to_pdf(chrome, html_path, os.path.join(settings.work_dir, 'chrome'))
        # The HTML has served its purpose: only the PDF is needed from here on
        os.remove(html_path)
    else:
        out_path = html_path
    print('карток: %d' % len(cards))
    print(out_path)
    if chrome is None:
        print('Chrome не знайдено, тож лишився HTML: відкрий у браузері і друкуй у PDF (Cmd+P).')
    return [out_path]
