"""Схема нот і settings.toml: що має падати з помилкою, а не проходити."""

import os
import tempfile
import unittest

from anki_tts_filler import config


class PresetTest(unittest.TestCase):
    def test_mode_fields_and_missing(self):
        preset = config.Preset('D', 'M', {'audio_one': 'one'}, ['one'], ['uk'], ['one', 'note'])
        self.assertEqual(preset.mode_fields('cards'), {'audio_one', 'one'})
        self.assertEqual(preset.mode_fields('pdf'), {'one', 'note'})
        # audio adds the audio field so a ready [sound:...] can be found
        self.assertEqual(preset.mode_fields('audio'), {'one', 'uk', 'audio_one'})
        self.assertEqual(config.missing_fields(preset, ['one', 'uk'], 'audio'), ['audio_one'])
        self.assertEqual(config.missing_fields(preset, ['one', 'uk', 'audio_one'], 'audio'), [])


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
