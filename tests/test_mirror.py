"""mirror: які поля потрапляють у дублікат і як обирається ціль."""

import unittest

from anki_tts_filler import mirror


def note(nid, model, tags=(), **fields):
    return {
        'noteId': nid,
        'modelName': model,
        'tags': list(tags),
        'fields': {name: {'value': value, 'order': i} for i, (name, value) in enumerate(fields.items())},
    }


class FakePreset:
    def __init__(self, name, model_name, deck_name='D', mirror_preset='', mirror_swap=()):
        self.name = name
        self.model_name = model_name
        self.deck_name = deck_name
        self.mirror_preset = mirror_preset
        self.mirror_swap = list(mirror_swap)


SOURCE = FakePreset('base', 'M', mirror_preset='base-rev', mirror_swap=['production', 'recognition'])
TARGET = FakePreset('base-rev', 'M-rev', deck_name='D::rev')


class FakeClient:
    def __init__(self, found=(), target_fields=()):
        self.found = list(found)
        self.target_fields = list(target_fields)
        self.queries = []

    def find_notes(self, query):
        self.queries.append(query)
        return self.found

    def call(self, action, **params):
        if action == 'modelFieldNames':
            return self.target_fields
        raise AssertionError('несподіваний виклик %s' % action)


class MirroredFieldsTest(unittest.TestCase):
    def setUp(self):
        self.fields = ['production', 'recognition', 'note', 'audio']
        self.note = note(
            1, 'M',
            production='книга', recognition='en bok', note='примітка', audio='[sound:langdeck_x.mp3]',
        )

    def test_pair_swapped(self):
        out = mirror.mirrored_fields(self.note, ['production', 'recognition'], self.fields)
        self.assertEqual(out['production'], 'en bok')
        self.assertEqual(out['recognition'], 'книга')

    def test_other_fields_copied(self):
        out = mirror.mirrored_fields(self.note, ['production', 'recognition'], self.fields)
        self.assertEqual(out['note'], 'примітка')
        # аудіо копіюється як є: імʼя файлу - відпечаток тексту, а текст
        # просто переїхав у інше поле
        self.assertEqual(out['audio'], '[sound:langdeck_x.mp3]')

    def test_only_fields_of_target_notetype(self):
        out = mirror.mirrored_fields(self.note, ['production', 'recognition'], ['production', 'recognition'])
        self.assertEqual(sorted(out), ['production', 'recognition'])

    def test_missing_source_field_becomes_empty(self):
        out = mirror.mirrored_fields(self.note, ['production', 'recognition'], self.fields + ['extra'])
        self.assertEqual(out['extra'], '')

    def test_swap_is_symmetric(self):
        once = mirror.mirrored_fields(self.note, ['production', 'recognition'], self.fields)
        twin = note(2, 'M', **once)
        twice = mirror.mirrored_fields(twin, ['production', 'recognition'], self.fields)
        self.assertEqual(twice['production'], 'книга')
        self.assertEqual(twice['recognition'], 'en bok')


class BuildNotesTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(target_fields=['production', 'recognition', 'note', 'audio'])
        self.sources = {'M': SOURCE}
        self.targets = {'base': TARGET}

    def test_deck_and_model_from_target_preset(self):
        notes = [note(1, 'M', production='книга', recognition='en bok', note='', audio='')]
        prepared, skipped, _ = mirror.build_notes(notes, self.sources, self.targets, self.client, [])
        self.assertEqual(skipped, {})
        item = prepared[0][1]
        self.assertEqual(item['deckName'], 'D::rev')
        self.assertEqual(item['modelName'], 'M-rev')

    def test_tags_copied_and_extended(self):
        notes = [note(1, 'M', tags=['kapittel_9'], production='книга', recognition='en bok')]
        prepared, _, _ = mirror.build_notes(notes, self.sources, self.targets, self.client, ['rev'])
        self.assertEqual(prepared[0][1]['tags'], ['kapittel_9', 'rev'])

    def test_note_of_unknown_model_skipped(self):
        notes = [note(1, 'Other', a='b')]
        prepared, skipped, _ = mirror.build_notes(notes, self.sources, self.targets, self.client, [])
        self.assertEqual(prepared, [])
        self.assertEqual(skipped, {'Other': 1})

    def test_model_fields_fetched_once(self):
        notes = [note(i, 'M', production='p%d' % i, recognition='r%d' % i) for i in range(4)]
        calls = []
        self.client.call = lambda action, **kw: calls.append(kw) or ['production', 'recognition']
        mirror.build_notes(notes, self.sources, self.targets, self.client, [])
        self.assertEqual(len(calls), 1)


class SelectAddableTest(unittest.TestCase):
    """canAddNotes звіряє з колекцією, але не з рештою того самого запиту."""

    FIRST = {'M-rev': 'production'}

    def prepared(self, *values):
        out = []
        for i, value in enumerate(values, 1):
            out.append((note(i, 'M'), {'modelName': 'M-rev', 'fields': {'production': value}}))
        return out

    def test_repeat_inside_batch_filtered(self):
        prepared = self.prepared('en bok', 'en bil', 'en bok')
        checks = [{'canAdd': True}] * 3
        fresh, duplicate, repeated, rejected = mirror.select_addable(prepared, checks, self.FIRST)
        self.assertEqual([item['fields']['production'] for _, item in fresh], ['en bok', 'en bil'])
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0][1], 'en bok')
        self.assertEqual((duplicate, rejected), (0, []))

    def test_existing_duplicate_counted(self):
        prepared = self.prepared('en bok', 'en bil')
        checks = [{'canAdd': False, 'error': 'cannot create note because it is a duplicate'}, {'canAdd': True}]
        fresh, duplicate, repeated, rejected = mirror.select_addable(prepared, checks, self.FIRST)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(duplicate, 1)
        self.assertEqual((repeated, rejected), ([], []))

    def test_other_rejection_reported_separately(self):
        prepared = self.prepared('en bok')
        checks = [{'canAdd': False, 'error': 'cannot create note because it is empty'}]
        fresh, duplicate, repeated, rejected = mirror.select_addable(prepared, checks, self.FIRST)
        self.assertEqual((fresh, duplicate, repeated), ([], 0, []))
        self.assertIn('empty', rejected[0][1])

    def test_all_distinct_pass_through(self):
        prepared = self.prepared('a', 'b', 'c')
        fresh, duplicate, repeated, rejected = mirror.select_addable(prepared, [{'canAdd': True}] * 3, self.FIRST)
        self.assertEqual(len(fresh), 3)
        self.assertEqual((duplicate, repeated, rejected), (0, [], []))


class ResolveTargetsTest(unittest.TestCase):
    def test_preset_name_becomes_notetype_query(self):
        client = FakeClient(found=[1, 2])
        ids, label = mirror.resolve_targets(client, 'base', [SOURCE, TARGET])
        self.assertEqual(ids, [1, 2])
        self.assertEqual(client.queries, ['note:"M"'])
        self.assertIn('base', label)

    def test_preset_without_mirror_rejected(self):
        client = FakeClient()
        with self.assertRaises(mirror.MirrorError) as caught:
            mirror.resolve_targets(client, 'base-rev', [SOURCE, TARGET])
        self.assertIn('[mirror]', str(caught.exception))

    def test_query_passed_verbatim(self):
        client = FakeClient(found=[7])
        ids, label = mirror.resolve_targets(client, 'tag:no\\_del_3', [SOURCE, TARGET])
        self.assertEqual((ids, label), ([7], 'tag:no\\_del_3'))


if __name__ == '__main__':
    unittest.main()
