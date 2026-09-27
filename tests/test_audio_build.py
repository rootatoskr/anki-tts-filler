"""Складання доріжки: імʼя файлу, паузи між сторонами, плоска послідовність."""

import unittest

from anki_tts_filler import audio_build, config, note_cards


def utterance(text, lang):
    return note_cards.Utterance(text, lang)


def card(note_id, target, native):
    return note_cards.Card(
        note_id=note_id,
        model='M',
        sides={
            config.LANG_TARGET: [utterance(text, config.LANG_TARGET) for text in target],
            config.LANG_NATIVE: [utterance(text, config.LANG_NATIVE) for text in native],
        },
    )


class SafeNameTest(unittest.TestCase):
    def test_tag_prefix_and_escaping_removed(self):
        self.assertEqual(audio_build.safe_name('tag:no\\_familie'), 'no_familie')

    def test_subdeck_separator(self):
        self.assertEqual(audio_build.safe_name('deck:language-no::no-verb'), 'language-no__no-verb')

    def test_queries_differing_only_in_unsafe_chars_get_distinct_names(self):
        first = audio_build.safe_name('deck:x -is:new')
        second = audio_build.safe_name('deck:x -is:due')
        self.assertNotEqual(first, second)

    def test_plain_name_unchanged_by_hash(self):
        self.assertEqual(audio_build.safe_name('tag:familie'), 'familie')

    def test_empty_query_has_fallback(self):
        self.assertEqual(audio_build.safe_name('   '), 'output')


class SideGapsTest(unittest.TestCase):
    def setUp(self):
        self.gap = config.GapConfig(
            after_target=2.0, after_native=0.4, within_side=0.5,
            between_repeats=0.3, between_cards=1.5,
        )

    def test_single_repeat(self):
        order = [config.LANG_TARGET, config.LANG_NATIVE, config.LANG_TARGET]
        self.assertEqual(audio_build.side_gaps(order, self.gap), [2.0, 0.4])

    def test_repeats_use_their_own_gap(self):
        order = [config.LANG_TARGET, config.LANG_NATIVE] + [config.LANG_TARGET] * 3
        # after_target тільки перед перекладом; між повторами - between_repeats
        self.assertEqual(audio_build.side_gaps(order, self.gap), [2.0, 0.4, 0.3, 0.3])

    def test_no_repeat_at_all(self):
        order = [config.LANG_TARGET, config.LANG_NATIVE]
        self.assertEqual(audio_build.side_gaps(order, self.gap), [2.0])


class AssembleTest(unittest.TestCase):
    def build(self, plan, cards, silences):
        resolved = {}
        for item in cards:
            for utt in item.utterances():
                resolved[utt] = utt.text + '.mp3'
        return audio_build.assemble(cards, resolved, plan, silences)

    def test_sequence_with_all_gaps(self):
        order = [config.LANG_TARGET, config.LANG_NATIVE, config.LANG_TARGET]
        plan = {'order': order, 'after': [2.0, 0.4], 'within_side': 0.5, 'between_cards': 1.5}
        silences = {2.0: 'p2.mp3', 0.4: 'p04.mp3', 0.5: 'p05.mp3', 1.5: 'p15.mp3'}
        cards = [card(1, ['a', 'b'], ['ua']), card(2, ['c'], ['uc'])]
        self.assertEqual(
            self.build(plan, cards, silences),
            [
                'a.mp3', 'p05.mp3', 'b.mp3', 'p2.mp3',
                'ua.mp3', 'p04.mp3',
                'a.mp3', 'p05.mp3', 'b.mp3',
                'p15.mp3',
                'c.mp3', 'p2.mp3', 'uc.mp3', 'p04.mp3', 'c.mp3',
            ],
        )

    def test_zero_gap_adds_no_file(self):
        order = [config.LANG_TARGET, config.LANG_NATIVE]
        plan = {'order': order, 'after': [0], 'within_side': 0, 'between_cards': 0}
        sequence = self.build(plan, [card(1, ['a'], ['ua'])], {})
        self.assertEqual(sequence, ['a.mp3', 'ua.mp3'])


class OrderCardsTest(unittest.TestCase):
    def test_note_group_stays_together(self):
        cards = [
            card(1, ['zebra'], ['з']),
            card(1, ['zebra example'], ['приклад']),
            card(2, ['en bok'], ['книга']),
        ]
        ordered = audio_build.order_cards(
            cards, {'M': config.ORDER_SORTED}, config.LanguageConfig(),
        )
        texts = [item.sides[config.LANG_TARGET][0].text for item in ordered]
        # "en bok" -> "bok" стоїть перед "zebra", приклад лишається за своєю нотою
        self.assertEqual(texts, ['en bok', 'zebra', 'zebra example'])


class FormatDurationTest(unittest.TestCase):
    def test_hours_minutes_seconds(self):
        self.assertEqual(audio_build.format_duration(3725.8), '1:02:05')
        self.assertEqual(audio_build.format_duration(59.2), '0:00:59')


if __name__ == '__main__':
    unittest.main()
