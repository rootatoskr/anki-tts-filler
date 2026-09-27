"""Пресети й settings.toml: те, що має впасти помилкою, а не пройти мовчки."""

import os
import tempfile
import unittest

from anki_tts_filler import config


class PresetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.makedirs(os.path.join(self.tmp.name, config.PRESETS_DIR))
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def write(self, name, body):
        path = os.path.join(self.tmp.name, config.PRESETS_DIR, name + '.toml')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(body)

    def test_full_preset(self):
        self.write('good', '''deck_name = "D::Sub"
model_name = "M"

[cards]
audio_one = "one"

[audio]
order = "random"
target = ["one", "two"]
native = ["translation"]
examples = "note"

[pdf]
order = "sorted"
fields = ["one", "translation"]
''')
        preset = config.load_preset('good')
        self.assertEqual(preset.deck_name, 'D::Sub')
        self.assertEqual(preset.cards, {'audio_one': 'one'})
        self.assertEqual(preset.audio_target, ['one', 'two'])
        self.assertEqual(preset.audio_native, ['translation'])
        self.assertEqual(preset.audio_examples, 'note')
        self.assertEqual(preset.audio_order, config.ORDER_RANDOM)
        self.assertEqual(preset.pdf_order, config.ORDER_SORTED)
        self.assertEqual(preset.media_fields(), {'one': 'audio_one'})

    def test_missing_deck_name_is_preset_error(self):
        self.write('bad', 'model_name = "M"\n')
        with self.assertRaises(config.PresetError) as caught:
            config.load_preset('bad')
        self.assertIn('deck_name', str(caught.exception))

    def test_missing_model_name_is_preset_error(self):
        self.write('bad', 'deck_name = "D"\n')
        with self.assertRaises(config.PresetError):
            config.load_preset('bad')

    def test_typo_in_top_level_key_rejected(self):
        self.write('bad', 'deck_name = "D"\nmodel_name = "M"\nmodle_name = "typo"\n')
        with self.assertRaises(config.PresetError) as caught:
            config.load_preset('bad')
        self.assertIn('modle_name', str(caught.exception))

    def test_old_language_keys_report_new_names(self):
        self.write('old', 'deck_name = "D"\nmodel_name = "M"\n\n[audio]\nno = ["a"]\nuk = ["b"]\n')
        with self.assertRaises(config.PresetError) as caught:
            config.load_preset('old')
        self.assertIn('no -> audio.target', str(caught.exception))

    def test_unknown_audio_key_rejected(self):
        self.write('bad', 'deck_name = "D"\nmodel_name = "M"\n\n[audio]\ntarget = ["a"]\nwat = 1\n')
        with self.assertRaises(config.PresetError) as caught:
            config.load_preset('bad')
        self.assertIn('wat', str(caught.exception))

    def test_bad_order_rejected(self):
        self.write('bad', 'deck_name = "D"\nmodel_name = "M"\n\n[pdf]\norder = "alphabetic"\n')
        with self.assertRaises(config.PresetError):
            config.load_preset('bad')

    def test_deck_line_and_markdown_escaping(self):
        self.write('esc', 'deck: language-no::no\\_verb\nmodel_name = "type\\_m"\n')
        preset = config.load_preset('esc')
        self.assertEqual(preset.deck_name, 'language-no::no_verb')
        self.assertEqual(preset.model_name, 'type_m')

    def test_mode_fields_and_missing(self):
        self.write('m', '''deck_name = "D"
model_name = "M"

[cards]
audio_one = "one"

[audio]
target = ["one"]
native = ["uk"]

[pdf]
fields = ["one", "note"]
''')
        preset = config.load_preset('m')
        self.assertEqual(preset.mode_fields('cards'), {'audio_one', 'one'})
        self.assertEqual(preset.mode_fields('pdf'), {'one', 'note'})
        # audio додає аудіополе, щоб знайшовся готовий [sound:...]
        self.assertEqual(preset.mode_fields('audio'), {'one', 'uk', 'audio_one'})
        self.assertEqual(config.missing_fields(preset, ['one', 'uk'], 'audio'), ['audio_one'])
        self.assertEqual(config.missing_fields(preset, ['one', 'uk', 'audio_one'], 'audio'), [])

    def test_field_roles(self):
        self.write('r', '''deck_name = "D"
model_name = "M"

[cards]
audio_one = "one"

[audio]
target = ["one"]
native = ["uk"]
examples = "note"

[pdf]
fields = ["one"]
''')
        roles = config.field_roles(config.load_preset('r'))
        self.assertEqual(roles['one'], ['cards', 'audio:target', 'pdf'])
        self.assertEqual(roles['audio_one'], ['cards:аудіо'])
        self.assertEqual(roles['uk'], ['audio:native'])
        self.assertEqual(roles['note'], ['audio:examples'])


class SettingsTest(unittest.TestCase):
    def load(self, body):
        with tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False, encoding='utf-8') as handle:
            handle.write(body)
            path = handle.name
        try:
            return config.load_settings(path)
        finally:
            os.remove(path)

    def test_template_is_valid(self):
        settings = self.load(config.SETTINGS_TEMPLATE)
        self.assertEqual(settings.voice.target, 'nb-NO-FinnNeural')
        self.assertEqual(settings.language.sort_extra_letters, ['æ', 'ø', 'å'])
        self.assertEqual(settings.gap.between_repeats, 0.4)

    def test_defaults_when_file_almost_empty(self):
        settings = self.load('concurrency = 2\n')
        self.assertEqual(settings.concurrency, 2)
        self.assertEqual(settings.content.repeat_target, 1)

    def test_renamed_keys_point_to_new_names(self):
        with self.assertRaises(config.SettingsError) as caught:
            self.load('[voice]\nno = "x"\n')
        self.assertIn('voice.target', str(caught.exception))
        with self.assertRaises(config.SettingsError) as caught:
            self.load('[content]\nrepeat_no = 2\n')
        self.assertIn('content.repeat_target', str(caught.exception))

    def test_unknown_key_rejected(self):
        with self.assertRaises(config.SettingsError):
            self.load('[gap]\nafter_everything = 1\n')

    def test_int_field_rejects_bool(self):
        with self.assertRaises(config.SettingsError):
            self.load('[content]\nrepeat_target = true\n')

    def test_float_field_accepts_int(self):
        settings = self.load('[gap]\nafter_target = 2\n')
        self.assertEqual(settings.gap.after_target, 2.0)
        self.assertIsInstance(settings.gap.after_target, float)

    def test_negative_gap_rejected(self):
        with self.assertRaises(config.SettingsError):
            self.load('[gap]\nbetween_repeats = -1\n')

    def test_language_lists_must_be_strings(self):
        with self.assertRaises(config.SettingsError):
            self.load('[language]\nsort_prefixes = [1, 2]\n')
        settings = self.load('[language]\nsort_prefixes = ["il "]\ncode = "it"\n')
        self.assertEqual(settings.language.sort_prefixes, ['il '])
        self.assertEqual(settings.language.code, 'it')

    def test_section_expected_to_be_table(self):
        with self.assertRaises(config.SettingsError):
            self.load('voice = "nb-NO"\n')


if __name__ == '__main__':
    unittest.main()
