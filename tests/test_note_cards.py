"""Ноти Anki -> картки: чищення HTML, пари прикладів, абетка, порядок."""

import unittest

from anki_tts_filler import config, note_cards


def note(model, **fields):
    return {
        'noteId': abs(hash(tuple(sorted(fields.items())))) % 10**9,
        'modelName': model,
        'fields': {name: {'value': value, 'order': i} for i, (name, value) in enumerate(fields.items())},
    }


class CleanTextTest(unittest.TestCase):
    def test_strips_tags_and_collapses_space(self):
        self.assertEqual(note_cards.clean_text('<b>Hei</b>  p&aring; deg'), 'Hei på deg')

    def test_removes_sound_tag(self):
        self.assertEqual(note_cards.clean_text('bil [sound:langdeck_abc.mp3]'), 'bil')

    def test_unwraps_cloze(self):
        self.assertEqual(note_cards.clean_text('Jeg {{c1::leser::verb}} boka'), 'Jeg leser boka')

    def test_no_space_before_punctuation(self):
        self.assertEqual(note_cards.clean_text('Hvor er du <b>fra</b>?'), 'Hvor er du fra?')

    def test_keeps_space_before_ellipsis(self):
        self.assertEqual(note_cards.clean_text('Jeg heter ...'), 'Jeg heter ...')

    def test_br_becomes_space(self):
        self.assertEqual(note_cards.clean_text('en bok<br>boka'), 'en bok boka')

    def test_nbsp_is_space(self):
        self.assertEqual(note_cards.clean_text('en bok'), 'en bok')


class NotePairsTest(unittest.TestCase):
    def test_splits_on_first_en_dash(self):
        raw = 'En idé er et ord. – Ідея – це слово.<br>Без тире тут нічого'
        self.assertEqual(
            list(note_cards.note_pairs(raw)),
            [('En idé er et ord.', 'Ідея – це слово.')],
        )

    def test_line_without_dash_skipped(self):
        self.assertEqual(list(note_cards.note_pairs('просто пояснення')), [])

    def test_empty_side_skipped(self):
        self.assertEqual(list(note_cards.note_pairs('– переклад без лівої частини')), [])


class SortKeyTest(unittest.TestCase):
    def setUp(self):
        self.language = config.LanguageConfig()

    def key(self, text):
        return note_cards.sort_key(text, self.language)

    def test_article_ignored(self):
        self.assertEqual(self.key('en bok'), 'bok')
        self.assertEqual(self.key('å lese'), 'lese')

    def test_extra_letters_sort_after_z(self):
        words = ['ønske', 'bil', 'åpne', 'ære', 'zebra']
        self.assertEqual(
            sorted(words, key=self.key),
            ['bil', 'zebra', 'ære', 'ønske', 'åpne'],
        )

    def test_letter_order_follows_config(self):
        # Інша мова - інший хвіст абетки, і сортування йде за ним
        language = config.LanguageConfig(sort_prefixes=['il ', 'la '], sort_extra_letters=['ä', 'ö'])
        words = ['öl', 'äpple', 'bil']
        self.assertEqual(
            sorted(words, key=lambda word: note_cards.sort_key(word, language)),
            ['bil', 'äpple', 'öl'],
        )
        self.assertEqual(note_cards.sort_key('la casa', language), 'casa')

    def test_prefix_matching_is_case_insensitive(self):
        self.assertEqual(self.key('En Bok'), 'bok')


class ApplyOrderTest(unittest.TestCase):
    def setUp(self):
        self.language = config.LanguageConfig()

    def test_linear_keeps_positions(self):
        entries = [(name, config.ORDER_LINEAR, name) for name in 'cab']
        self.assertEqual(note_cards.apply_order(entries, self.language), ['c', 'a', 'b'])

    def test_sorted_only_among_own_positions(self):
        entries = [
            ('lin1', config.ORDER_LINEAR, 'zzz'),
            ('c', config.ORDER_SORTED, 'c'),
            ('lin2', config.ORDER_LINEAR, 'aaa'),
            ('a', config.ORDER_SORTED, 'a'),
        ]
        # лінійні лишились на місцях 0 і 2, впорядковані помінялись між 1 і 3
        self.assertEqual(
            note_cards.apply_order(entries, self.language),
            ['lin1', 'a', 'lin2', 'c'],
        )

    def test_random_keeps_same_multiset(self):
        entries = [(str(i), config.ORDER_RANDOM, str(i)) for i in range(10)]
        result = note_cards.apply_order(entries, self.language)
        self.assertEqual(sorted(result), sorted(str(i) for i in range(10)))

    def test_single_element_untouched(self):
        entries = [('only', config.ORDER_SORTED, 'only')]
        self.assertEqual(note_cards.apply_order(entries, self.language), ['only'])


class BuildCardsTest(unittest.TestCase):
    def setUp(self):
        self.field_map = {
            'M': {
                'sides': {
                    config.LANG_TARGET: [('front', 'audio_front')],
                    config.LANG_NATIVE: [('back', None)],
                },
                'examples': 'note',
            },
        }

    def test_card_sides_and_media(self):
        cards, skipped = note_cards.build_cards(
            [note('M', front='en bok', back='книга', audio_front='[sound:langdeck_1.mp3]', note='')],
            True,
            self.field_map,
        )
        self.assertEqual(skipped, {})
        self.assertEqual(len(cards), 1)
        target = cards[0].sides[config.LANG_TARGET][0]
        self.assertEqual(target.text, 'en bok')
        self.assertEqual(target.media, 'langdeck_1.mp3')
        self.assertEqual(cards[0].sides[config.LANG_NATIVE][0].text, 'книга')

    def test_media_ignored_when_disabled(self):
        cards, _ = note_cards.build_cards(
            [note('M', front='en bok', back='книга', audio_front='[sound:langdeck_1.mp3]', note='')],
            False,
            self.field_map,
        )
        self.assertIsNone(cards[0].sides[config.LANG_TARGET][0].media)

    def test_examples_become_separate_cards(self):
        cards, _ = note_cards.build_cards(
            [note('M', front='en bok', back='книга', audio_front='', note='Jeg leser boka – Я читаю книгу')],
            True,
            self.field_map,
        )
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[1].sides[config.LANG_TARGET][0].text, 'Jeg leser boka')
        self.assertEqual(cards[1].sides[config.LANG_NATIVE][0].text, 'Я читаю книгу')
        self.assertEqual(cards[0].note_id, cards[1].note_id)

    def test_unknown_model_skipped(self):
        cards, skipped = note_cards.build_cards([note('Other', a='b')], True, self.field_map)
        self.assertEqual(cards, [])
        self.assertEqual(skipped, {'Other': 1})

    def test_note_without_translation_skipped(self):
        cards, skipped = note_cards.build_cards(
            [note('M', front='en bok', back='', audio_front='', note='')],
            True,
            self.field_map,
        )
        self.assertEqual(cards, [])
        self.assertEqual(skipped, {'M': 1})

    def test_load_field_map_needs_both_sides(self):
        class FakePreset:
            name = 'p'
            model_name = 'M'
            cards = {'audio_front': 'front'}
            audio_target = ['front']
            audio_native = []
            audio_examples = ''

            def media_fields(self):
                return {'front': 'audio_front'}

        self.assertEqual(note_cards.load_field_map([FakePreset()]), {})
        FakePreset.audio_native = ['back']
        field_map = note_cards.load_field_map([FakePreset()])
        self.assertEqual(
            field_map['M']['sides'][config.LANG_TARGET],
            [('front', 'audio_front')],
        )


if __name__ == '__main__':
    unittest.main()
