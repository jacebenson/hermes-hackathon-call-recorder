"""Untrusted dashboard paths must not become reads or archive jobs."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder import Recorder
from test_settings import plugin_api as api


class TranscriptPathTests(unittest.TestCase):
    def test_route_refuses_unowned_paths_without_reading(self):
        rec = Recorder(recordings_dir='/calls')
        cases = [
            ('/outside/call-20260101-120000.transcript.md', 'outside the recordings directory'),
            ('/calls/../call-20260101-120000.live.txt', 'outside the recordings directory'),
            ('/calls/notes.transcript.md', 'not a recording of mine'),
            ('/home/user/.hermes/.env', 'not a recording of mine'),
            ('/home/user/.ssh/id_ed25519', 'not a recording of mine'),
        ]
        for host in (None, 'capture-box'):
            rec.host = host
            for path, error in cases:
                with self.subTest(host=host, path=path), \
                     patch.object(api, 'action_recorder', return_value=rec), \
                     patch.object(rec, 'run', return_value=(0, 'SECRET')) as run:
                    self.assertEqual(asyncio.run(api.read_transcript(path)),
                                     {'ok': False, 'error': error})
                    run.assert_not_called()

    def test_legitimate_transcript_and_live_files_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder(recordings_dir=directory)
            for suffix in ('.transcript.md', '.live.txt'):
                path = Path(directory) / ('call-20260101-120000' + suffix)
                path.write_text('fixture transcript\n')
                with self.subTest(suffix=suffix), patch.object(api, 'action_recorder', return_value=rec):
                    self.assertEqual(asyncio.run(api.read_transcript(str(path))),
                                     {'ok': True, 'markdown': 'fixture transcript\n'})

    def test_direct_read_refuses_with_a_clear_exception(self):
        rec = Recorder(recordings_dir='/calls')
        with patch.object(rec, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'not a recording of mine'):
                rec.read_transcript('/home/user/.env')
            run.assert_not_called()

    def test_windows_uses_resolved_directory_and_case_insensitive_comparison(self):
        rec = Recorder(recordings_dir=r'~\calls', os_name='windows')
        with patch('capture_windows.resolved_dir', return_value=r'C:\Users\User\calls'), \
             patch.object(rec, 'run', return_value=(0, 'fixture')) as run:
            self.assertEqual(rec.read_transcript(r'c:\users\user\calls\call-20260101-120000.live.txt'), 'fixture')
            run.reset_mock()
            with self.assertRaisesRegex(ValueError, 'outside the recordings directory'):
                rec.read_transcript(r'C:\outside\call-20260101-120000.live.txt')
            run.assert_not_called()


class TranscribePathTests(unittest.TestCase):
    def test_route_rejects_paths_before_detection_commands_or_jobs(self):
        cases = [('/outside/call-20260101-120000.mkv', 'outside the recordings directory'),
                 ('/calls/../call-20260101-120000.mkv', 'outside the recordings directory'),
                 ('/calls/notes.mkv', 'not a recording of mine')]
        for path, error in cases:
            with self.subTest(path=path), \
                 patch.object(api, 'settings', return_value={**api.DEFAULTS, 'host': 'capture-box', 'recordings_dir': '/calls'}), \
                 patch.object(api.RecordingLibrary, 'target', return_value=None), \
                 patch.object(api, 'resolved_transcription', return_value={}) as detect, \
                 patch.object(api.Recorder, 'run', return_value=(0, '')) as run, \
                 patch.object(api, '_transcribe', return_value={'ok': True}) as archive, \
                 patch.object(api, '_store') as store, \
                 patch.dict(api._TRANSCRIPTION_JOBS, {}, clear=True):
                self.assertEqual(asyncio.run(api.post_transcribe({'file': path})),
                                 {'ok': False, 'error': error})
                detect.assert_not_called()
                run.assert_not_called()
                archive.assert_not_called()
                store.assert_not_called()
                self.assertEqual(api._TRANSCRIPTION_JOBS, {})

    def test_legitimate_recording_still_runs_archive(self):
        rec = Recorder(recordings_dir='/calls')
        path = '/calls/call-20260101-120000.mkv'
        with patch.object(api, 'action_recorder', return_value=rec), \
             patch.object(api, '_transcribe', return_value={'ok': True, 'engine': 'fixture'}) as archive, \
             patch.object(api, '_store', return_value={'ok': True}):
            self.assertEqual(asyncio.run(api.post_transcribe({'file': path})),
                             {'ok': True, 'engine': 'fixture'})
            archive.assert_called_once_with(rec, path)
