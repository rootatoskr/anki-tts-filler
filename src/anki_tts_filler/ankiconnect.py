"""Клієнт AnkiConnect на stdlib, без зовнішніх залежностей."""

import json
import urllib.error
import urllib.request

NOTES_CHUNK = 500


class AnkiConnectError(Exception):
    pass


class AnkiConnect:
    def __init__(self, url, timeout=20):
        self.url = url
        self.timeout = timeout

    def call(self, action, **params):
        payload = json.dumps({'action': action, 'version': 6, 'params': params}).encode()
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except urllib.error.URLError as exc:
            raise AnkiConnectError(
                'немає звʼязку з AnkiConnect на %s (Anki запущений?): %s' % (self.url, exc)
            ) from exc
        if body.get('error'):
            raise AnkiConnectError('%s: %s' % (action, body['error']))
        return body['result']

    def media_dir(self):
        return self.call('getMediaDirPath')

    def find_notes(self, query):
        return self.call('findNotes', query=query)

    def notes_info(self, note_ids):
        """Тягне ноти пачками: один запит на 3000+ id дає надто великий відповідь."""
        result = []
        for start in range(0, len(note_ids), NOTES_CHUNK):
            chunk = note_ids[start:start + NOTES_CHUNK]
            result.extend(self.call('notesInfo', notes=chunk))
        return result
