"""cards.txt -> поля й теги."""

import unittest

from anki_tts_filler import parser


FIELDS = ['front', 'back', 'note']


class SplitCardsTest(unittest.TestCase):
    def test_blocks_split_on_blank_line(self):
        text = 'front: a\nback: b\n\nfront: c\nback: d\n'
        parsed = parser.split_cards(text, FIELDS)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0][0], {'front': 'a', 'back': 'b', 'note': ''})
        self.assertEqual(parsed[1][0]['front'], 'c')

    def test_value_may_contain_colon(self):
        parsed = parser.split_cards('front: kl. 10:30\n', FIELDS)
        self.assertEqual(parsed[0][0]['front'], 'kl. 10:30')

    def test_unknown_field_is_error(self):
        card, tags, error = parser.split_cards('fornt: a\n', FIELDS)[0]
        self.assertIsNone(card)
        self.assertIn('fornt', error)

    def test_line_without_colon_is_error(self):
        card, tags, error = parser.split_cards('просто рядок\n', FIELDS)[0]
        self.assertIsNone(card)
        self.assertIn('без ":"', error)

    def test_tags_line_collected_separately(self):
        card, tags, error = parser.split_cards('front: a\ntags: kapittel_4 familie\n', FIELDS)[0]
        self.assertIsNone(error)
        self.assertEqual(tags, ['kapittel_4', 'familie'])
        self.assertNotIn('tags', card)

    def test_tags_field_in_notetype_wins(self):
        # Нотетайп із власним полем "tags": рядок лишається полем, а не тегами
        fields = FIELDS + ['tags']
        card, tags, error = parser.split_cards('tags: текст поля\n', fields)[0]
        self.assertIsNone(error)
        self.assertEqual(card['tags'], 'текст поля')
        self.assertEqual(tags, [])

    def test_empty_tags_line_gives_no_tags(self):
        card, tags, error = parser.split_cards('front: a\ntags:\n', FIELDS)[0]
        self.assertIsNone(error)
        self.assertEqual(tags, [])

    def test_build_fields_merges_audio(self):
        merged = parser.build_fields({'front': 'a'}, {'audio_front': '[sound:x.mp3]'})
        self.assertEqual(merged, {'front': 'a', 'audio_front': '[sound:x.mp3]'})


if __name__ == '__main__':
    unittest.main()
