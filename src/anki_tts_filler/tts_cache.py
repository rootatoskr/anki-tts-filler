"""Speech synthesis through edge-tts with an on-disk cache.

The shared core of both modes: writing through a temporary ``.part`` file,
discarding empty edge-tts responses, retries and a shared semaphore. The cards
mode supplies its own naming scheme and silence trimming via a subclass in
audio.py.
"""

import asyncio
import hashlib
import os

import edge_tts

RETRIES = 3
RETRY_DELAY = 1


class TtsError(Exception):
    pass


class TtsCache:
    """The cache key is a hash of the text and every voice parameter.

    Changing the text in Anki or the rate in settings.toml yields a new key
    automatically, so cache invalidation is not needed.
    """

    def __init__(self, cache_dir, concurrency):
        self.cache_dir = cache_dir
        self._semaphore = asyncio.Semaphore(concurrency)
        self._inflight = {}
        self.hits = 0
        self.misses = 0
        os.makedirs(cache_dir, exist_ok=True)

    def path_for(self, text, voice, rate, volume):
        key = '|'.join([text, voice, rate, volume]).encode()
        return os.path.join(self.cache_dir, hashlib.sha1(key).hexdigest() + '.mp3')

    def postprocess(self, path):
        """Processing of a freshly synthesised file before it becomes a cache entry."""

    async def synthesize(self, text, voice, rate, volume):
        path = self.path_for(text, voice, rate, volume)
        # An empty file is left over from interrupted runs of older versions -
        # that is not a cache entry, it has to be synthesised again
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.hits += 1
            return path

        # The same text shows up in several cards: keep one shared task so the
        # network is not hit twice for the same file.
        task = self._inflight.get(path)
        if task is None:
            task = asyncio.create_task(self._render(text, voice, rate, volume, path))
            self._inflight[path] = task
        else:
            self.hits += 1
        try:
            return await task
        finally:
            self._inflight.pop(path, None)

    async def _render(self, text, voice, rate, volume, path):
        async with self._semaphore:
            partial = '%s.%d.part' % (path, os.getpid())
            for attempt in range(RETRIES):
                try:
                    await edge_tts.Communicate(text, voice, rate=rate, volume=volume).save(partial)
                    break
                except Exception as exc:
                    _remove(partial)
                    if attempt == RETRIES - 1:
                        raise TtsError('не вдалося озвучити %r голосом %s: %s' % (text, voice, exc)) from exc
                    await asyncio.sleep(RETRY_DELAY)
            if os.path.getsize(partial) == 0:
                _remove(partial)
                raise TtsError('edge-tts повернув порожній файл для %r' % text)
            try:
                # In a thread, because ``postprocess`` is a synchronous ffmpeg call:
                # inside the loop it would block every other synthesis, reducing
                # concurrency to one file at a time
                await asyncio.to_thread(self.postprocess, partial)
            except Exception as exc:
                _remove(partial)
                raise TtsError('не вдалося обробити аудіо для %r: %s' % (text, exc)) from exc
            os.replace(partial, path)
        self.misses += 1
        return path


def _remove(path):
    if os.path.exists(path):
        os.remove(path)
