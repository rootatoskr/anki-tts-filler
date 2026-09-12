import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import edge_tts
from .config import TTS_VOICE, TTS_RATE, MEDIA_PREFIX


def strip_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()


def sound_tag(path):
    return f'[sound:{os.path.basename(path)}]'


def ffmpeg_available():
    return shutil.which('ffmpeg') is not None


def media_pattern():
    return f'{MEDIA_PREFIX}*.mp3'


def _fname(text):
    # Голос і темп входять у ключ кешу, щоб зміна TTS_VOICE/TTS_RATE не перевикористовувала старе аудіо
    h = hashlib.md5(f'{text}|{TTS_VOICE}|{TTS_RATE}'.encode()).hexdigest()[:16]
    return f'{MEDIA_PREFIX}{h}.mp3'


def _trim_silence(path):
    tmp = path + '.tmp.mp3'
    subprocess.run(
        [
            'ffmpeg', '-y', '-i', path,
            '-af', 'silenceremove=start_periods=1:start_silence=0.01:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_silence=0.01:start_threshold=-50dB,areverse',
            tmp,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    if os.path.getsize(tmp) == 0:
        os.remove(tmp)
        return
    os.replace(tmp, path)


async def _synthesize(text, path, semaphore):
    async with semaphore:
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
                await communicate.save(path)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
    _trim_silence(path)


async def _run_all(tasks):
    semaphore = asyncio.Semaphore(5)
    await asyncio.gather(*[_synthesize(t, p, semaphore) for t, p in tasks])


def generate(texts, out_dir):
    unique = {t: os.path.join(out_dir, _fname(t)) for t in texts}
    to_gen = [(t, p) for t, p in unique.items() if not os.path.exists(p) or os.path.getsize(p) == 0]
    if to_gen:
        asyncio.run(_run_all(to_gen))
    return unique
