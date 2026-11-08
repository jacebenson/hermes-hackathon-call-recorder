"""Readable deletion errors and route exception containment."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from recorder import Recorder
from test_settings import plugin_api as api


class DeleteTests(unittest.TestCase):
    def test_missing_permission_and_unreachable_are_readable(self):
        rec = Recorder(host='fake', recordings_dir='/calls')
        path = '/calls/call-20260101-120000.mkv'
        for reply, expected in [((3, 'Recording not found.'), 'not found'),
                                ((1, 'rm: Permission denied'), 'permission'),
                                ((255, 'ssh: connection refused'), 'reach')]:
            with patch.object(rec, 'run', return_value=reply):
                result = rec.delete_recording(path)
                self.assertFalse(result['ok'])
                self.assertIn(expected, result['error'].lower())
        with patch.object(api, 'recorder', side_effect=OSError('connection refused')):
            result = asyncio.run(api.post_delete({'file': path}))
        self.assertFalse(result['ok'])
        self.assertIn('reach', result['error'].lower())

    def test_success_removes_only_the_pair_and_missing_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder(recordings_dir=directory)
            path = Path(directory) / 'call-20260101-120000.mkv'
            text = path.with_suffix('.transcript.md')
            path.touch(); text.touch()
            other = Path(directory) / 'keep.txt'; other.touch()
            self.assertFalse(rec.delete_recording('/outside/' + path.name)['ok'])
            self.assertTrue(path.exists())
            self.assertTrue(rec.delete_recording(str(path))['ok'])
            self.assertFalse(path.exists()); self.assertFalse(text.exists())
            self.assertTrue(other.exists())
            self.assertIn('not found', rec.delete_recording(str(path))['error'].lower())
