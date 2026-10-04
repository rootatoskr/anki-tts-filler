"""TTS for the cards and resync modes: short audio into an Anki note field.

Synthesis, caching and retries are shared with the audio mode (tts_cache.py);
what lives here is only the file naming scheme (as in the Anki media), silence
trimming and uploading new files into the media.
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
    """Cache of the generated mp3 files - next to cards.txt, in the launch directory."""
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
    """The mp3 name for this text - the single place where it is computed.

    A pure function, so the resync mode can work out in advance which fields
    really change and synthesise only those.
    """
    # Neutral volume stays out of the key: otherwise every ``langdeck_*`` file
    # already uploaded to Anki would be regenerated and duplicated in the media.
    parts = [text, voice, rate] if volume == '+0%' else [text, voice, rate, volume]
    digest = hashlib.md5('|'.join(parts).encode()).hexdigest()[:16]
    return MEDIA_PREFIX + digest + '.mp3'


def media_path(text, cache_dir, voice, rate, volume):
    return os.path.join(cache_dir, media_file_name(text, voice, rate, volume))


def expected_tag(text, settings):
    """The [sound:...] that belongs in the audio field for this text.

    No directory is needed here: only the file name goes into the tag.
    """
    return sound_tag(media_file_name(
        text, settings.voice.target, settings.voice.rate_target, settings.voice.volume_target,
    ))


class MediaCache(TtsCache):
    """Cache of the cards mode: names as in the Anki media, with silence trimmed."""

    def path_for(self, text, voice, rate, volume):
        return media_path(text, self.cache_dir, voice, rate, volume)

    def postprocess(self, path):
        _trim_silence(path)


def generate(texts, cache_dir, voice, rate, volume, concurrency):
    """Texts -> {text: path to mp3}. Files already cached are not synthesised again."""
    ordered = list(texts)
    if not ordered:
        return {}

    cache = MediaCache(cache_dir, concurrency)

    async def run_all():
        return await asyncio.gather(*[cache.synthesize(t, voice, rate, volume) for t in ordered])

    return dict(zip(ordered, asyncio.run(run_all())))


def sync_media(texts, cache_dir, client, settings):
    """Texts -> {text: path to mp3}, with new files uploaded into the Anki media.

    The step shared by the cards and resync modes: synthesis in the target
    language voice plus the upload. The only difference between the modes is
    where the texts themselves come from.
    """
    audio_map = generate(
        texts,
        cache_dir,
        settings.voice.target,
        settings.voice.rate_target,
        settings.voice.volume_target,
        settings.concurrency,
    )

    # Files already present in the Anki media are not uploaded again
    existing = set(client.call('getMediaFilesNames', pattern=media_pattern()))
    for path in audio_map.values():
        name = os.path.basename(path)
        if name in existing:
            continue
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        client.call('storeMediaFile', filename=name, data=data)

    return audio_map
