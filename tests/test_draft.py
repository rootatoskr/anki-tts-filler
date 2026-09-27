"""Режим draft: межа латиниця/кирилиця, тире, дужки, числівники."""

import unittest

from anki_tts_filler import draft


class SplitLineTest(unittest.TestCase):
    def split(self, line):
        card, error = draft.split_line(line)
        self.assertIsNone(error, error)
        return card

    def test_plain_pair(self):
        self.assertEqual(
            self.split('en bok книга'),
            {'recognition': 'en bok', 'production': 'книга'},
        )

    def test_dash_separator_removed(self):
        self.assertEqual(
            self.split('en kunde – один клієнт'),
            {'recognition': 'en kunde', 'production': 'один клієнт'},
        )

    def test_dash_without_spaces_normalized(self):
        self.assertEqual(
            self.split('disse -ці'),
            {'recognition': 'disse', 'production': 'ці'},
        )

    def test_inner_hyphen_kept(self):
        card = self.split('en e-post електронний лист')
        self.assertEqual(card['recognition'], 'en e-post')

    def test_single_paren_group_goes_to_note(self):
        self.assertEqual(
            self.split('en kokk кухар (професія)'),
            {'recognition': 'en kokk', 'production': 'кухар', 'note': 'професія'},
        )

    def test_two_paren_groups_stay_in_place(self):
        card = self.split('en kokk (et yrke) кухар (професія)')
        self.assertEqual(card['recognition'], 'en kokk (et yrke)')
        self.assertEqual(card['production'], 'кухар (професія)')
        self.assertNotIn('note', card)

    def test_number_translation(self):
        self.assertEqual(
            self.split('førti 40'),
            {'recognition': 'førti', 'production': '40'},
        )

    def test_digits_inside_target_part_are_not_a_split(self):
        card, error = draft.split_line('T-bane 2 линия')
        self.assertIsNone(error)
        self.assertEqual(card['recognition'], 'T-bane 2')
        self.assertEqual(card['production'], 'линия')

    def test_no_cyrillic_and_no_number_is_error(self):
        card, error = draft.split_line('bare norsk her')
        self.assertIsNone(card)
        self.assertIn('кирилич', error)

    def test_no_latin_part_is_error(self):
        card, error = draft.split_line('тільки українською')
        self.assertIsNone(card)
        self.assertIn('латин', error)

    def test_multiple_sentences_kept_verbatim(self):
        card = self.split('Jeg heter Ola. Jeg bor i Oslo. Мене звати Ола. Я живу в Осло.')
        self.assertEqual(card['recognition'], 'Jeg heter Ola. Jeg bor i Oslo.')
        self.assertEqual(card['production'], 'Мене звати Ола. Я живу в Осло.')


class GenerateTest(unittest.TestCase):
    def test_line_numbers_count_blank_lines(self):
        cards, problems = draft.generate('en bok книга\n\nbare norsk\n')
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(problems), 1)
        self.assertIn('рядок 3', problems[0])

    def test_format_cards_blocks(self):
        cards, problems = draft.generate('en bok книга\nen kokk кухар (професія)\n')
        self.assertEqual(problems, [])
        self.assertEqual(
            draft.format_cards(cards),
            'recognition: en bok\nproduction: книга\n\n'
            'recognition: en kokk\nproduction: кухар\nnote: професія\n',
        )


if __name__ == '__main__':
    unittest.main()
