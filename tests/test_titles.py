"""Titles persist beside recordings without renaming media."""
import tempfile
import unittest
from pathlib import Path
from recorder import Recorder


class TitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = self.directory / 'call-20260101-120000.mkv'
        self.path.write_bytes(b'media')
        self.transcript = self.path.with_suffix('.transcript.md')
        self.transcript.write_text('speech')
        self.rec = Recorder(recordings_dir=str(self.directory))

    def test_title_round_trip_trim_clear_and_list(self):
        result = self.rec.set_title(str(self.path), '  Planning café  ')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['title'], 'Planning café')
        other = Recorder(recordings_dir=str(self.directory))
        self.assertEqual(other.get_title(str(self.path))['title'], 'Planning café')
        self.assertEqual(other.recordings()[0]['title'], 'Planning café')
        self.assertTrue((self.directory / '.titles.json').exists())
        self.assertEqual(self.path.read_bytes(), b'media')
        self.assertEqual(self.transcript.read_text(), 'speech')
        self.assertTrue(other.set_title(str(self.path), '')['ok'])
        self.assertEqual(other.get_title(str(self.path))['title'], '')

    def test_invalid_titles_and_paths_do_not_write(self):
        for title in ['x' * 121, 'hello\nthere', '\tbad', 'hidden\x00', 'a\u0085b', None, 42]:
            with self.subTest(title=repr(title)):
                result = self.rec.set_title(str(self.path), title)
                self.assertFalse(result['ok'])
                self.assertTrue(result['error'])
        for path in ['/other/call-20260101-120000.mkv', str(self.directory / '../call-20260101-120000.mkv')]:
            self.assertFalse(self.rec.set_title(path, 'bad')['ok'])
        self.assertFalse((self.directory / '.titles.json').exists())

    def test_missing_recording_and_corrupt_metadata_report_errors(self):
        self.path.unlink()
        self.assertFalse(self.rec.set_title(str(self.path), 'missing')['ok'])
        self.path.touch()
        (self.directory / '.titles.json').write_text('not json')
        result = self.rec.set_title(str(self.path), 'keep old data')
        self.assertFalse(result['ok'])
        self.assertIn('titles', result['error'].lower())
