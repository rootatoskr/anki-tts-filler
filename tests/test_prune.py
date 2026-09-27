"""prune: які згенеровані файли вважаються зайвими."""

import os
import tempfile
import unittest

from anki_tts_filler import prune


def note(**fields):
    return {
        'noteId': 1,
        'modelName': 'M',
        'fields': {name: {'value': value, 'order': i} for i, (name, value) in enumerate(fields.items())},
    }


class ReferencedNamesTest(unittest.TestCase):
    def test_collects_only_generated_prefix(self):
        notes = [
            note(audio='[sound:langdeck_aaa.mp3]', other='[sound:my_own_recording.mp3]'),
            note(audio='[sound:langdeck_bbb.mp3] текст'),
        ]
        self.assertEqual(
            prune.referenced_names(notes),
            {'langdeck_aaa.mp3', 'langdeck_bbb.mp3'},
        )

    def test_empty_collection(self):
        self.assertEqual(prune.referenced_names([]), set())


class CacheFilesTest(unittest.TestCase):
    def test_lists_only_generated_mp3(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('langdeck_a.mp3', 'langdeck_b.mp3', 'other.mp3', 'langdeck_c.txt'):
                with open(os.path.join(tmp, name), 'w') as handle:
                    handle.write('x')
            self.assertEqual(prune.cache_files(tmp), ['langdeck_a.mp3', 'langdeck_b.mp3'])

    def test_missing_dir_is_empty(self):
        self.assertEqual(prune.cache_files('/nonexistent/path/for/test'), [])


class HumanSizeTest(unittest.TestCase):
    def test_kilobytes_and_megabytes(self):
        self.assertEqual(prune.human_size(2048), '2 КБ')
        self.assertEqual(prune.human_size(3 * 1024 * 1024), '3.0 МБ')


if __name__ == '__main__':
    unittest.main()
