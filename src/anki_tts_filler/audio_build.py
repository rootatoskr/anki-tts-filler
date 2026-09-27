"""Оркестрація режиму audio: Anki-ноти -> озвучені сегменти -> один mp3.

Не плутати з audio.py: та частина для режимів cards і resync (коротке аудіо
в поле Anki), тут - довга доріжка для прослуховування (цільова мова -> пауза
-> переклад -> пауза -> цільова мова ще раз) по одному Anki-запиту за раз.
"""

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import config, note_cards
from .tts_cache import TtsCache

PROBE_WORKERS = 8
UNSAFE_RE = re.compile(r'[^\w.-]+', re.UNICODE)
QUERY_PREFIX_RE = re.compile(r'^(?:tag|deck|note):')


class AudioError(Exception):
    pass


def require_tools():
    missing = [name for name in ('ffmpeg', 'ffprobe') if shutil.which(name) is None]
    if missing:
        raise AudioError('не знайдено в PATH: %s' % ', '.join(missing))


def safe_name(query):
    """Імʼя вихідного файлу із запиту: tag:no\\_familie -> no_familie.

    Зворотні слеші - це екранування шаблонів Anki, а не частина назви, тому
    знімаються; провідний tag:/deck: теж, інакше імʼя виходить нечитабельним.

    Небезпечні символи злипаються в "-", тож два різні запити ('deck:x -is:new'
    і 'deck:x -is:due') дали б один файл. Коли заміни справді були, до імені
    додається хвіст із хешу запиту; імена запитів без таких символів незмінні.
    """
    text = QUERY_PREFIX_RE.sub('', query.replace('\\', '').strip()).replace('::', '__')
    name = UNSAFE_RE.sub('-', text).strip('-')
    if not name:
        return 'output'
    if name != text:
        name += '-' + hashlib.sha1(query.encode()).hexdigest()[:6]
    return name


def probe_format(path):
    result = subprocess.run(
        [
            'ffprobe', '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_name,sample_rate,channels',
            '-of', 'csv=p=0',
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AudioError('ffprobe не зміг прочитати %s: %s' % (path, result.stderr.strip()))
    parts = result.stdout.strip().split(',')
    if len(parts) != 3:
        raise AudioError('несподіваний вивід ffprobe для %s: %r' % (path, result.stdout))
    return tuple(parts)


def probe_formats(paths):
    """Формати всіх унікальних файлів. Потоки, бо кожен ffprobe - окремий процес."""
    unique = sorted(set(paths))
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        formats = pool.map(probe_format, unique)
    return dict(zip(unique, formats))


def make_silence(cache_dir, duration, audio_format):
    codec, sample_rate, channels = audio_format
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, 'silence_%s_%s_%s.mp3' % (duration, sample_rate, channels))
    if os.path.exists(path):
        return path
    layout = 'mono' if channels == '1' else 'stereo'
    run_ffmpeg([
        '-f', 'lavfi',
        '-i', 'anullsrc=r=%s:cl=%s' % (sample_rate, layout),
        '-t', str(duration),
        path,
    ])
    return path


def run_ffmpeg(args):
    result = subprocess.run(['ffmpeg', '-y', '-v', 'error', *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise AudioError('ffmpeg завершився з кодом %d: %s' % (result.returncode, result.stderr.strip()))


def _escape(path):
    return path.replace('\\', '\\\\').replace("'", "'\\''")


def write_concat_list(paths, list_path):
    with open(list_path, 'w') as handle:
        for path in paths:
            handle.write("file '%s'\n" % _escape(os.path.abspath(path)))


def concat(paths, out_path, list_path, formats, bitrate):
    """Склеює доріжки. ``-c copy`` тільки коли всі джерела мають однаковий формат.

    Файли з collection.media створені не нами, тож їхній sample rate чи
    кількість каналів можуть відрізнятися від виходу edge-tts. Копіювання
    потоку в такому разі тихо псує склейку, тому перекодовуємо.
    """
    write_concat_list(paths, list_path)
    distinct = set(formats.values())
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    args = ['-f', 'concat', '-safe', '0', '-i', list_path]
    if len(distinct) == 1:
        args += ['-c', 'copy']
        mode = 'copy'
    else:
        # Найнижчий спільний формат саме за числами: sorted() порівнював би
        # рядки, де "8000" виявляється більшим за "48000".
        codec, sample_rate, channels = min(distinct, key=lambda fmt: (int(fmt[1]), int(fmt[2])))
        args += ['-c:a', 'libmp3lame', '-b:a', bitrate, '-ar', sample_rate, '-ac', channels]
        mode = 'encode'
    args.append(out_path)
    run_ffmpeg(args)
    return mode


def duration(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AudioError('ffprobe не зміг прочитати %s: %s' % (path, result.stderr.strip()))
    return float(result.stdout.strip())


def voice_params(settings, lang):
    if lang == config.LANG_TARGET:
        return settings.voice.target, settings.voice.rate_target, settings.voice.volume_target
    return settings.voice.native, settings.voice.rate_native, settings.voice.volume_native


async def resolve_paths(utterances, settings, cache, media_dir):
    """Кожній репліці зіставляє mp3: готовий файл з Anki або свіжий TTS."""
    resolved = {}
    pending = {}
    from_media = 0

    for utterance in utterances:
        if utterance in resolved or utterance in pending:
            continue
        if utterance.media and media_dir:
            candidate = os.path.join(media_dir, utterance.media)
            if os.path.exists(candidate):
                resolved[utterance] = candidate
                from_media += 1
                continue
        voice, rate, volume = voice_params(settings, utterance.lang)
        pending[utterance] = cache.synthesize(utterance.text, voice, rate, volume)

    if pending:
        keys = list(pending)
        results = await asyncio.gather(*[pending[key] for key in keys])
        resolved.update(zip(keys, results))
    return resolved, from_media


def add_silence(sequence, silences, value):
    """Пауза 0 означає "без паузи".

    Файла тиші для неї нема: ffmpeg робить на ``-t 0`` mp3 без жодного фрейму,
    а concat на такому файлі завершується кодом 0 і мовчки відкидає решту
    доріжки - виходить обрізаний результат без жодної помилки.
    """
    path = silences.get(value)
    if path is not None:
        sequence.append(path)


def side_gaps(order, gap):
    """Пауза після кожної сторони, крім останньої.

    Між двома повторами цільової мови пауза своя (between_repeats): after_target
    це час на згадати переклад, і між повторами, що йдуть уже після перекладу,
    така довга пауза зайва.
    """
    values = []
    for index in range(len(order) - 1):
        current, following = order[index], order[index + 1]
        if current != config.LANG_TARGET:
            values.append(gap.after_native)
        elif following == config.LANG_TARGET:
            values.append(gap.between_repeats)
        else:
            values.append(gap.after_target)
    return values


def assemble(card_list, resolved, plan, silences):
    """Розкладає картки в плоский список доріжок із паузами між ними."""
    order = plan['order']
    sequence = []
    for card_index, card in enumerate(card_list):
        last_side = len(order) - 1
        for side_index, side in enumerate(order):
            utterances = card.sides.get(side, [])
            for utterance_index, utterance in enumerate(utterances):
                sequence.append(resolved[utterance])
                if utterance_index < len(utterances) - 1:
                    add_silence(sequence, silences, plan['within_side'])
            if side_index < last_side:
                add_silence(sequence, silences, plan['after'][side_index])
        if card_index < len(card_list) - 1:
            add_silence(sequence, silences, plan['between_cards'])
    return sequence


def order_cards(card_list, orders, language):
    """Розкладає картки за порядком із пресетів, не розриваючи ноту.

    Картки однієї ноти йдуть поспіль (основна форма, далі приклади), тому
    переставляються групи, а не окремі картки. Для абетки ключ - перша
    репліка цільової мови в групі.
    """
    groups = []
    for card in card_list:
        if groups and groups[-1][0].note_id == card.note_id:
            groups[-1].append(card)
        else:
            groups.append([card])

    entries = [
        (
            group,
            orders.get(group[0].model, config.ORDER_LINEAR),
            group[0].sides[config.LANG_TARGET][0].text,
        )
        for group in groups
    ]
    return [card for group in note_cards.apply_order(entries, language) for card in group]


async def build(query, notes, settings, cache, media_dir, field_map, orders):
    card_list, skipped = note_cards.build_cards(
        notes,
        settings.content.use_anki_media,
        field_map,
    )
    base_stats = {'skipped': skipped}
    if not card_list:
        return None, base_stats

    # Порядок карток фіксований порядком нот з Anki, через що послідовність
    # запамʼятовується разом зі словами. Пресет із order = "random" це знімає.
    card_list = order_cards(card_list, orders, settings.language)

    all_utterances = [u for card in card_list for u in card.utterances()]
    resolved, from_media = await resolve_paths(all_utterances, settings, cache, media_dir)

    # Формат тиші має збігатися з форматом доріжок, інакше склейка копіюванням
    # потоку дасть розсинхрон. Беремо найпоширеніший формат серед джерел.
    voice_paths = sorted(set(resolved.values()))
    formats = probe_formats(voice_paths)
    base_format = max(set(formats.values()), key=list(formats.values()).count)

    # target -> native -> target, повторений settings.content.repeat_target разів
    order = [config.LANG_TARGET, config.LANG_NATIVE]
    order += [config.LANG_TARGET] * settings.content.repeat_target
    plan = {
        'order': order,
        'after': side_gaps(order, settings.gap),
        'within_side': settings.gap.within_side,
        'between_cards': settings.gap.between_cards,
    }
    silences = {}
    for value in [*plan['after'], plan['within_side'], plan['between_cards']]:
        if value > 0 and value not in silences:
            silences[value] = make_silence(settings.work_dir, value, base_format)
    formats.update({path: base_format for path in silences.values()})

    sequence = assemble(card_list, resolved, plan, silences)
    label = safe_name(query)
    out_path = os.path.join(settings.output.dir, label + '.mp3')
    list_path = os.path.join(settings.work_dir, label + '.txt')
    mode = concat(sequence, out_path, list_path, formats, settings.output.bitrate)

    stats = dict(base_stats)
    stats.update({
        'cards': len(card_list),
        'segments': len(sequence),
        'from_media': from_media,
        'mode': mode,
        'duration': duration(out_path),
    })
    return out_path, stats


async def run(query, settings, client):
    require_tools()

    media_dir = None
    if settings.content.use_anki_media:
        media_dir = client.media_dir()

    os.makedirs(settings.output.dir, exist_ok=True)
    os.makedirs(settings.work_dir, exist_ok=True)
    cache = TtsCache(settings.cache_dir, settings.concurrency)
    presets = config.load_all_presets()
    field_map = note_cards.load_field_map(presets)

    note_ids = client.find_notes(query)
    print('%s -> нот: %d' % (query, len(note_ids)))
    if not note_ids:
        print('запит нічого не знайшов')
        return None

    notes = client.notes_info(note_ids)
    config.verify_presets(
        presets,
        {note['modelName'] for note in notes},
        lambda model: client.call('modelFieldNames', modelName=model),
        'audio',
    )
    out_path, stats = await build(
        query, notes, settings, cache, media_dir, field_map, config.orders(presets, 'audio'),
    )
    for model, count in sorted(stats['skipped'].items()):
        print('пропущено %d нот типу %s' % (count, model))
    if out_path is None:
        print('жодної придатної картки')
    else:
        print('карток: %d, сегментів: %d, з Anki-медіа: %d, склейка: %s' % (
            stats['cards'], stats['segments'], stats['from_media'], stats['mode'],
        ))
        print('%s, %s' % (out_path, format_duration(stats['duration'])))

    print('TTS: з кешу %d, згенеровано %d' % (cache.hits, cache.misses))
    return out_path


def format_duration(seconds):
    total = int(seconds)
    return '%d:%02d:%02d' % (total // 3600, total % 3600 // 60, total % 60)
