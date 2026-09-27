"""Синтез мовлення через edge-tts із кешем на диску.

Спільне ядро обох режимів: запис через тимчасовий ``.part``, відсів порожніх
відповідей edge-tts, повтори і спільний семафор. Режим cards підставляє свою
схему імен і обрізання тиші через підклас у audio.py.
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

    def postprocess(self, path):
        """Обробка щойно синтезованого файлу перед тим, як він стане кешем."""

    async def synthesize(self, text, voice, rate, volume):
        path = self.path_for(text, voice, rate, volume)
        # Порожній файл лишається від обірваних запусків старих версій -
        # це не кеш, його треба озвучити наново
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.hits += 1
            return path

        # Один і той самий текст трапляється в кількох картках: тримаємо
        # спільну задачу, щоб не ходити в мережу двічі за той самий файл.
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
                # У потоці, бо postprocess - це синхронний ffmpeg: у самому
                # loop він блокував би всі інші синтези, зводячи concurrency
                # до одного файлу за раз
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
