"""Синтез мовлення через edge-tts із кешем на диску (для режиму audio).

Не плутати з audio.py: та генерація для режиму cards озвучує коротке
поле й одразу заливає файл в Anki, тут - кеш по (текст, голос, темп,
гучність) для довгих прослуховувальних доріжок.
"""

import asyncio
import hashlib
import os

import edge_tts


class TtsError(Exception):
    pass


class TtsCache:
    """Ключ кешу - хеш від тексту та всіх параметрів голосу.

    Зміна тексту в Anki або швидкості в settings.toml дає новий ключ
    автоматично, тож інвалідація кешу не потрібна.
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

    async def synthesize(self, text, voice, rate, volume):
        path = self.path_for(text, voice, rate, volume)
        if os.path.exists(path):
            self.hits += 1
            return path

        # Один і той самий текст трапляється в кількох картках: тримаємо
        # спільну задачу, щоб не ходити в мережу двічі за той самий файл.
        task = self._inflight.get(path)
        if task is None:
            task = asyncio.create_task(self._render(text, voice, rate, volume, path))
            self._inflight[path] = task
        return await task

    async def _render(self, text, voice, rate, volume, path):
        async with self._semaphore:
            partial = '%s.%d.part' % (path, os.getpid())
            try:
                await edge_tts.Communicate(text, voice, rate=rate, volume=volume).save(partial)
            except Exception as exc:
                if os.path.exists(partial):
                    os.remove(partial)
                raise TtsError('не вдалося озвучити %r голосом %s: %s' % (text, voice, exc)) from exc
            if os.path.getsize(partial) == 0:
                os.remove(partial)
                raise TtsError('edge-tts повернув порожній файл для %r' % text)
            os.replace(partial, path)
        self.misses += 1
        return path
