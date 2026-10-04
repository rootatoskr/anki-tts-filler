"""resync: where the target comes from and which fields end up in the plan."""

import unittest

from anki_tts_filler import config, resync


def note(nid, model, **fields):
    return {
        'noteId': nid,
        'modelName': model,
        'fields': {name: {'value': value, 'order': i} for i, (name, value) in enumerate(fields.items())},
    }


class FakeClient:
    """A minimal AnkiConnect: returns what was put in and records the requests."""

    def __init__(self, found=()):
        self.found = list(found)
        self.queries = []

    def find_notes(self, query):
        self.queries.append(query)
        return self.found


class FakePreset:
    def __init__(self, name, model_name, cards):
        self.name = name
        self.model_name = model_name
        self.cards = cards


PRESETS = [
    FakePreset('verb', 'type-language-no-verb', {'audio_infinitiv': 'infinitiv'}),
    FakePreset('tichky', 'Без Аудіо', {}),
]


class EscapeQueryValueTest(unittest.TestCase):
    def test_wraps_in_quotes(self):
        self.assertEqual(resync.escape_query_value('type-language-no-verb'), '"type-language-no-verb"')

    def test_spaces_survive_quoting(self):
        self.assertEqual(resync.escape_query_value('Norwegian Bokmål'), '"Norwegian Bokmål"')

    def test_wildcards_and_colon_escaped(self):
        # _ and * are wildcards in Anki search, : separates field and value
        self.assertEqual(resync.escape_query_value('a_b*c:d'), '"a\\_b\\*c\\:d"')

    def test_backslash_escaped_first(self):
        self.assertEqual(resync.escape_query_value('a\\b'), '"a\\\\b"')


class ResolveTargetsTest(unittest.TestCase):
    def test_preset_name_becomes_notetype_query(self):
        client = FakeClient(found=[7, 8])
        ids, label = resync.resolve_targets(client, 'verb', PRESETS)
        self.assertEqual(ids, [7, 8])
        self.assertEqual(client.queries, ['note:"type-language-no-verb"'])
        self.assertIn('verb', label)
        self.assertIn('type-language-no-verb', label)

    def test_preset_without_cards_rejected(self):
        client = FakeClient(found=[1])
        with self.assertRaises(resync.ResyncError) as caught:
            resync.resolve_targets(client, 'tichky', PRESETS)
        self.assertIn('[cards]', str(caught.exception))

    def test_unknown_name_goes_to_anki_as_query(self):
        client = FakeClient(found=[3])
        ids, label = resync.resolve_targets(client, 'nid:3', PRESETS)
        self.assertEqual((ids, label), ([3], 'nid:3'))

    def test_query_passed_to_anki_verbatim(self):
        client = FakeClient(found=[3])
        ids, label = resync.resolve_targets(client, 'nid:3', PRESETS)
        self.assertEqual((ids, label), ([3], 'nid:3'))
        self.assertEqual(client.queries, ['nid:3'])


class BuildPlanTest(unittest.TestCase):
    def setUp(self):
        self.settings = config.Settings()
        self.index = {'M': {'audio': 'front'}}

    def plan(self, notes):
        return resync.build_plan(notes, self.index, self.settings)

    def expected(self, text):
        from anki_tts_filler.audio import expected_tag
        return expected_tag(text, self.settings)

    def test_stale_field_planned(self):
        plan = self.plan([note(1, 'M', front='en bok', audio='[sound:langdeck_old.mp3]')])
        self.assertEqual(len(plan), 1)
        # the plan keeps the text: the tag comes from the file actually synthesised
        self.assertEqual(plan[0][1], {'audio': 'en bok'})

    def test_matching_field_not_planned(self):
        current = self.expected('en bok')
        self.assertEqual(self.plan([note(1, 'M', front='en bok', audio=current)]), [])

    def test_empty_text_clears_field(self):
        plan = self.plan([note(1, 'M', front='', audio='[sound:langdeck_old.mp3]')])
        self.assertEqual(plan[0][1], {'audio': ''})

    def test_empty_text_and_empty_field_not_planned(self):
        self.assertEqual(self.plan([note(1, 'M', front='', audio='')]), [])

    def test_html_stripped_before_comparison(self):
        current = self.expected('en bok')
        self.assertEqual(self.plan([note(1, 'M', front='<b>en bok</b>', audio=current)]), [])

    def test_nbsp_and_double_space_normalized(self):
        # The same cleanup as in the audio mode: otherwise the same text would
        # produce two different files
        current = self.expected('en bok')
        self.assertEqual(self.plan([note(1, 'M', front='en\u00a0bok', audio=current)]), [])
        self.assertEqual(self.plan([note(1, 'M', front='en  bok', audio=current)]), [])

    def test_entity_unescaped(self):
        current = self.expected('mor & far')
        self.assertEqual(self.plan([note(1, 'M', front='mor &amp; far', audio=current)]), [])

    def test_only_changed_notes_in_plan(self):
        notes = [
            note(1, 'M', front='en bok', audio=self.expected('en bok')),
            note(2, 'M', front='en bil', audio='[sound:langdeck_old.mp3]'),
        ]
        plan = self.plan(notes)
        self.assertEqual([n['noteId'] for n, _ in plan], [2])


class DescribeTest(unittest.TestCase):
    def test_caps_at_five_notes(self):
        index = {'M': {'audio': 'front'}}
        plan = [(note(i, 'M', front='слово %d' % i, audio=''), {'audio': 'x'}) for i in range(8)]
        lines = resync.describe(plan, index)
        self.assertEqual(len(lines), 6)
        self.assertIn('ще 3 нот', lines[-1])


if __name__ == '__main__':
    unittest.main()
