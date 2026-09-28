"""TTS для режимів cards і resync: коротке аудіо в поле ноти Anki.

Синтез, кеш і повтори спільні з режимом audio (tts_cache.py); тут лише своя
схема імен файлів (як у медіатеці Anki), обрізання тиші й заливання нових
файлів у медіатеку.
"""

import asyncio
import base64
import hashlib
import os
import shutil
import subprocess

from .config import AUDIO_CACHE_DIR, MEDIA_PREFIX
from .tts_cache import TtsCache


def sound_tag(path):
    return f'[sound:{os.path.basename(path)}]'


def ffmpeg_available():
    return shutil.which('ffmpeg') is not None


def media_pattern():
    return f'{MEDIA_PREFIX}*.mp3'


def cache_path():
    """Кеш згенерованих mp3 - поруч із cards.txt, у директорії запуску."""
    return os.path.join(os.getcwd(), AUDIO_CACHE_DIR)


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


def media_file_name(text, voice, rate, volume):
    """Імʼя mp3 для цього тексту - єдине місце, де воно обчислюється.

    Чиста функція, тому режим resync може заздалегідь порахувати, які поля
    справді зміняться, і озвучити лише їх.
    """
    # Нейтральна гучність у ключ не входить: інакше всі вже залиті в Anki
    # langdeck_* файли перегенерувалися б і продублювалися в медіатеці.
    parts = [text, voice, rate] if volume == '+0%' else [text, voice, rate, volume]
    digest = hashlib.md5('|'.join(parts).encode()).hexdigest()[:16]
    return MEDIA_PREFIX + digest + '.mp3'


def media_path(text, cache_dir, voice, rate, volume):
    return os.path.join(cache_dir, media_file_name(text, voice, rate, volume))


def expected_tag(text, settings):
    """[sound:...], який має стояти в аудіополі для цього тексту.

    Каталог тут не потрібен: у тег іде лише імʼя файлу.
    """
    return sound_tag(media_file_name(
        text, settings.voice.target, settings.voice.rate_target, settings.voice.volume_target,
    ))


class MediaCache(TtsCache):
    """Кеш режиму cards: імена як у медіатеці Anki, з обрізанням тиші."""

    def path_for(self, text, voice, rate, volume):
        return media_path(text, self.cache_dir, voice, rate, volume)

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


def sync_media(texts, cache_dir, client, settings):
    """Тексти -> {текст: шлях до mp3}, нові файли залиті в медіатеку Anki.

    Спільний крок режимів cards і resync: озвучення голосом цільової мови
    плюс заливання. Різниця між режимами лишається тільки в тому, звідки
    беруться самі тексти.
    """
    audio_map = generate(
        texts,
        cache_dir,
        settings.voice.target,
        settings.voice.rate_target,
        settings.voice.volume_target,
        settings.concurrency,
    )

    # Уже наявні в медіатеці Anki файли повторно не заливаються
    existing = set(client.call('getMediaFilesNames', pattern=media_pattern()))
    for path in audio_map.values():
        name = os.path.basename(path)
        if name in existing:
            continue
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        client.call('storeMediaFile', filename=name, data=data)

    return audio_map
