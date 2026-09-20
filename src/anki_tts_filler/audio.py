"""TTS для режиму cards: коротке аудіо в поле ноти Anki.

Синтез, кеш і повтори спільні з режимом audio (tts_cache.py); тут лише своя
схема імен файлів (як у медіатеці Anki) і обрізання тиші.
"""

import asyncio
import hashlib
import os
import re
import shutil
import subprocess

from .config import MEDIA_PREFIX
from .tts_cache import TtsCache


def strip_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()


def sound_tag(path):
    return f'[sound:{os.path.basename(path)}]'


def ffmpeg_available():
    return shutil.which('ffmpeg') is not None


def media_pattern():
    return f'{MEDIA_PREFIX}*.mp3'


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


class MediaCache(TtsCache):
    """Кеш режиму cards: імена як у медіатеці Anki, з обрізанням тиші."""

    def path_for(self, text, voice, rate, volume):
        # Нейтральна гучність у ключ не входить: інакше всі вже залиті в Anki
        # langdeck_* файли перегенерувалися б і продублювалися в медіатеці.
        parts = [text, voice, rate] if volume == '+0%' else [text, voice, rate, volume]
        digest = hashlib.md5('|'.join(parts).encode()).hexdigest()[:16]
        return os.path.join(self.cache_dir, MEDIA_PREFIX + digest + '.mp3')

    def postprocess(self, path):
        _trim_silence(path)


def generate(texts, cache_dir, voice, rate, volume, concurrency):
    """Тексти -> {текст: шлях до mp3}. Наявні в кеші файли не переозвучуються."""
    ordered = list(texts)
    if not ordered:
        return {}

    cache = MediaCache(cache_dir, concurrency)

    async def run_all():
        return await asyncio.gather(*[cache.synthesize(t, voice, rate, volume) for t in ordered])

    return dict(zip(ordered, asyncio.run(run_all())))
