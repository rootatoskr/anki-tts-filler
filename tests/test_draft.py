"""Режим draft: межа латиниця/кирилиця, тире, дужки, числівники, регістр і крапки."""

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

    def test_only_sentence_start_lowercased(self):
        # власна назва в середині речення лишається як була
        self.assertEqual(
            self.split('Jeg bor i Oslo Я живу в Осло'),
            {'recognition': 'jeg bor i Oslo', 'production': 'я живу в Осло'},
        )

    def test_each_sentence_start_lowercased(self):
        card = self.split('Hva heter du? Så bra! Як тебе звати? Як добре!')
        self.assertEqual(card['recognition'], 'hva heter du? så bra!')
        self.assertEqual(card['production'], 'як тебе звати? як добре!')

    def test_note_case_untouched(self):
        card = self.split('En Kokk Кухар ((Професія))')
        self.assertEqual(card['note'], 'Професія')
        self.assertEqual(card['recognition'], 'en Kokk')

    def test_trailing_dot_removed_from_both_sides(self):
        self.assertEqual(
            self.split('en bok книга.'),
            {'recognition': 'en bok', 'production': 'книга'},
        )

    def test_inner_dot_kept_between_sentences(self):
        card = self.split('Jeg heter Ola. Jeg bor i Oslo. Мене звати Ола. Я живу в Осло.')
        self.assertEqual(card['recognition'], 'jeg heter Ola. jeg bor i Oslo')
        self.assertEqual(card['production'], 'мене звати Ола. я живу в Осло')

    def test_ellipsis_kept(self):
        card = self.split('Jeg heter ... Мене звати ...')
        self.assertEqual(card['recognition'], 'jeg heter ...')
        self.assertEqual(card['production'], 'мене звати ...')

    def test_question_and_exclamation_kept(self):
        self.assertEqual(self.split('Hva heter du? Як тебе звати?')['production'], 'як тебе звати?')
        self.assertEqual(self.split('Så bra! Як добре!')['production'], 'як добре!')

    def test_trailing_dot_removed_before_number_split(self):
        # крапка в кінці не має заважати регулярці хвоста рядка
        self.assertEqual(
            self.split('førti 40.'),
            {'recognition': 'førti', 'production': '40'},
        )

    def test_trailing_dot_removed_after_note_taken_out(self):
        # крапка стоїть перед ремаркою, тож знімається вже з готового значення
        card = self.split('en kokk кухар. ((професія))')
        self.assertEqual(card['note'], 'професія')
        self.assertEqual(card['production'], 'кухар')

    def test_dot_inside_abbreviation_kept(self):
        card = self.split('kl. 10 о десятій.')
        self.assertEqual(card['recognition'], 'kl. 10')
        self.assertEqual(card['production'], 'о десятій')

    def test_double_parens_go_to_note(self):
        self.assertEqual(
            self.split('en kokk кухар ((професія))'),
            {'recognition': 'en kokk', 'production': 'кухар', 'note': 'професія'},
        )

    def test_single_parens_stay_in_text(self):
        self.assertEqual(
            self.split('en kokk (et yrke) кухар (професія)'),
            {'recognition': 'en kokk (et yrke)', 'production': 'кухар (професія)'},
        )

    def test_note_taken_from_any_position(self):
        card = self.split('en kokk ((et yrke)) кухар')
        self.assertEqual(card['recognition'], 'en kokk')
        self.assertEqual(card['note'], 'et yrke')

    def test_several_note_groups_joined(self):
        card = self.split('glatt på veien слизька дорога ((glatt – слизький)) ((veien – дорога))')
        self.assertEqual(card['note'], 'glatt – слизький veien – дорога')

    def test_html_inside_note_kept(self):
        card = self.split('det blåser дує вітер ((torden – грім<br>lyn – блискавка))')
        self.assertEqual(card['note'], 'torden – грім<br>lyn – блискавка')

    def test_paren_text_without_note_marker(self):
        card = self.split('En Kokk (Et Yrke) Кухар (Професія)')
        self.assertEqual(card['recognition'], 'en Kokk (Et Yrke)')
        self.assertEqual(card['production'], 'кухар (Професія)')
        self.assertNotIn('note', card)

    def test_number_translation(self):
        self.assertEqual(
            self.split('førti 40'),
            {'recognition': 'førti', 'production': '40'},
        )

    def test_digits_inside_target_part_are_not_a_split(self):
        card, error = draft.split_line('T-bane 2 линия')
        self.assertIsNone(error)
        self.assertEqual(card['recognition'], 't-bane 2')
        self.assertEqual(card['production'], 'линия')

    def test_no_cyrillic_and_no_number_is_error(self):
        card, error = draft.split_line('bare norsk her')
        self.assertIsNone(card)
        self.assertIn('кирилич', error)

    def test_no_latin_part_is_error(self):
        card, error = draft.split_line('тільки українською')
        self.assertIsNone(card)
        self.assertIn('латин', error)

    def test_number_translation_keeps_digits(self):
        self.assertEqual(
            self.split('Førti 40.'),
            {'recognition': 'førti', 'production': '40'},
        )


class GenerateTest(unittest.TestCase):
    def test_line_numbers_count_blank_lines(self):
        cards, problems = draft.generate('en bok книга\n\nbare norsk\n')
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(problems), 1)
        self.assertIn('рядок 3', problems[0])

    def test_format_cards_blocks(self):
        cards, problems = draft.generate('en bok книга\nen kokk кухар ((професія))\n')
        self.assertEqual(problems, [])
        self.assertEqual(
            draft.format_cards(cards),
            'recognition: en bok\nproduction: книга\n\n'
            'recognition: en kokk\nproduction: кухар\nnote: професія\n',
        )


if __name__ == '__main__':
    unittest.main()
