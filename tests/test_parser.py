"""cards.txt -> fields and tags."""

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
        # A notetype with its own "tags" field: the line stays a field, not tags
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


class CliTagsTest(unittest.TestCase):
    """``--tag`` in the cards and mirror modes: how many words it takes."""

    def parse(self, argv):
        from anki_tts_filler.main import parse_cards_args
        return parse_cards_args(argv, 'cards')

    def test_several_tags_after_one_flag(self):
        self.assertEqual(
            self.parse(['base', '--tag', 'no_kapittel_5', 'no_del_2']),
            (['base'], ['no_kapittel_5', 'no_del_2']),
        )

    def test_repeated_flag_still_works(self):
        self.assertEqual(self.parse(['base', '--tag', 'a', '--tag', 'b']), (['base'], ['a', 'b']))

    def test_mixed_forms_accumulate(self):
        self.assertEqual(
            self.parse(['base', '--tag', 'a', 'b', '--tag', 'c']),
            (['base'], ['a', 'b', 'c']),
        )

    def test_no_tags_at_all(self):
        self.assertEqual(self.parse(['base']), (['base'], []))

    def test_greedy_flag_swallows_a_later_target(self):
        # The documented cost of the greedy form: the target has to come first
        self.assertEqual(self.parse(['--tag', 'a', 'b', 'base']), ([], ['a', 'b', 'base']))

    def test_extra_positional_rejected(self):
        import contextlib
        import io
        from anki_tts_filler.main import single_target
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                single_target(['base', 'no_del_2'], 'cards')
        self.assertIn('no_del_2', out.getvalue())
        self.assertEqual(single_target(['base'], 'cards'), ['base'])
