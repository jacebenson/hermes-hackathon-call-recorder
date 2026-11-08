"""Backend library contract: copy bytes, retain sources, and persist retries."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder import Recorder
from test_settings import plugin_api as api
from recording_library import RecordingLibrary


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = RecordingLibrary(self.root / 'plugin-data' / 'call-recorder')
        self.source = self.root / 'capture' / 'call-20260101-120000.mkv'
        self.source.parent.mkdir()
        self.source.write_bytes(b'\x00\xffvideo\x00')
        self.source.with_suffix('.transcript.md').write_text('# transcript')
        self.source.with_suffix('.live.txt').write_text('[12:00:01] you> speech')
        self.remote = Recorder(host='capture-box', recordings_dir=str(self.source.parent))
        # Exercise real byte transfer and shell commands without an SSH server.
        self.local = Recorder(recordings_dir=str(self.source.parent))

    def test_payload_destination_and_upload_once_survive_restart(self):
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            result = self.library.store(self.remote, str(self.source))
            self.assertTrue(result['ok'], result)
            target = self.library.root / 'capture-box' / self.source.name
            for suffix in ('.mkv', '.transcript.md', '.live.txt'):
                self.assertEqual(target.with_suffix(suffix).read_bytes(), self.source.with_suffix(suffix).read_bytes())
                self.assertTrue(self.source.with_suffix(suffix).exists())
        with patch.object(self.remote, 'shell_argv', side_effect=AssertionError('must not copy twice')):
            again = RecordingLibrary(self.library.root).store(self.remote, str(self.source))
            self.assertTrue(again['ok'], again)

    def test_failed_sidecar_stays_remote_only_and_retries_only_missing_files(self):
        real_run = __import__('subprocess').run
        transfers = []
        def transfer(argv, **kwargs):
            transfers.append(argv[-1])
            if len(transfers) == 2:
                raise OSError('disk full')
            return real_run(argv, **kwargs)
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv), \
             patch('recording_library.subprocess.run', side_effect=transfer):
            first = self.library.store(self.remote, str(self.source))
        self.assertFalse(first['ok'])
        with patch.object(self.remote, 'recordings', return_value=[{'file': str(self.source), 'name': self.source.name}]):
            row = self.library.recordings(self.remote)[0]
        self.assertFalse(row['stored'])
        self.assertIn('disk full', row['upload_error'])
        # The video receipt persists through a new library instance.
        retry = RecordingLibrary(self.library.root)
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv), \
             patch('recording_library.subprocess.run', wraps=real_run) as runner:
            self.assertTrue(retry.store(self.remote, str(self.source))['ok'])
            commands = [call.args[0][-1] for call in runner.call_args_list]
            self.assertEqual([c for c in commands if 'cat -- ' in c],
                             [self.local.SESSION_ENV + 'cat -- ' + str(self.source.with_suffix('.transcript.md')),
                              self.local.SESSION_ENV + 'cat -- ' + str(self.source.with_suffix('.live.txt'))])
            self.assertFalse(any('cat -- ' + str(self.source) in c for c in commands))
        with patch.object(self.remote, 'recordings', return_value=[]):
            self.assertTrue(retry.recordings(self.remote)[0]['stored'])

    def test_merged_route_orders_and_deduplicates_across_machine_changes(self):
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            self.assertTrue(self.library.store(self.remote, str(self.source))['ok'])
        newer = {'file': '/other/call-20260102-120000.mkv', 'name': 'call-20260102-120000.mkv'}
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(api, 'recorder', return_value=self.remote), \
             patch.object(self.remote, 'recordings', return_value=[newer, {'file': str(self.source), 'name': self.source.name}]):
            rows = asyncio.run(api.get_recordings())['items']
        self.assertEqual([(r['name'], r['stored']) for r in rows],
                         [('call-20260102-120000.mkv', False), ('call-20260101-120000.mkv', True)])
        self.assertEqual([r['machine'] for r in rows], ['capture-box', 'capture-box'])
        other = Recorder(host='another-machine')
        with patch.object(other, 'recordings', return_value=[newer]):
            rows = self.library.recordings(other)
        self.assertEqual([r['machine'] for r in rows], ['another-machine', 'capture-box'])
        with patch.object(other, 'recordings', side_effect=OSError('offline')):
            self.assertEqual(len(self.library.recordings(other)), 1)

    def test_stored_routes_use_backend_after_capture_setting_changes(self):
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            stored = self.library.store(self.remote, str(self.source))['file']
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(api, 'recorder', side_effect=AssertionError('selected host must not be used')):
            saved = asyncio.run(api.post_title({'file': stored, 'machine': 'capture-box', 'title': 'Stored title'}))
            self.assertTrue(saved['ok'], saved)
            self.assertEqual(asyncio.run(api.get_title(stored))['title'], 'Stored title')
            self.assertEqual(asyncio.run(api.read_transcript(stored[:-4] + '.transcript.md'))['markdown'], '# transcript')
            with patch.object(api, 'detect_transcription', return_value={'python': '/backend/python', 'live_script': '/backend/live.py'}), \
                 patch.object(api, 'resolved_transcription', side_effect=AssertionError('wrong runtime')), \
                 patch.object(api, 'run_transcription', return_value={'ok': True}) as transcribe:
                self.assertTrue(asyncio.run(api.post_transcribe({'file': stored}))['ok'])
                self.assertIsNone(transcribe.call_args.args[0].host)
                self.assertEqual(transcribe.call_args.args[1], stored)
                self.assertEqual(transcribe.call_args.kwargs['gpu_python'], '/backend/python')
                self.assertEqual(transcribe.call_args.kwargs['gpu_script'], str(api._PLUGIN_ROOT / 'scripts' / 'archive.py'))
        self.assertFalse(self.source.parent.joinpath('.titles.json').exists())

    def test_delete_removes_stored_and_source_sidecars_even_after_host_change(self):
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            stored = self.library.store(self.remote, str(self.source))['file']
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(self.library, 'source_recorder', return_value=self.local), \
             patch.object(api, 'recorder', side_effect=AssertionError('wrong capture machine')):
            deleted = asyncio.run(api.post_delete({'file': stored, 'machine': 'capture-box'}))
        self.assertTrue(deleted['ok'], deleted)
        for suffix in ('.mkv', '.transcript.md', '.live.txt'):
            self.assertFalse(self.source.with_suffix(suffix).exists())
            self.assertFalse(Path(stored).with_suffix(suffix).exists())
        self.assertFalse(list(self.library.root.glob('*/*.library.json')))

    def test_stop_stores_only_after_transcription_or_immediately_when_disabled(self):
        api._TRANSCRIPTION_JOBS.clear()
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv), \
             patch.object(api, 'settings', return_value={'transcribe': False}):
            result = api._after_stop(self.remote, {'ok': True, 'file': str(self.source)})
            self.assertTrue(result['upload']['stored'])
        # A different capture host uses a separate destination for the same name.
        self.remote.host = 'second-machine'
        workers = []
        self.source.with_suffix('.transcript.md').unlink()
        def archive(rec, source):
            self.assertFalse((self.library.root / 'second-machine' / self.source.name).exists())
            self.source.with_suffix('.transcript.md').write_text('ready now')
            return {'ok': True}
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv), \
             patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, 'transcribe_from_live', return_value={'ok': False}), \
             patch.object(api, '_transcribe', side_effect=archive), \
             patch.object(api, '_launch_archive', side_effect=workers.append):
            api._after_stop(self.remote, {'ok': True, 'file': str(self.source)})
            self.assertFalse((self.library.root / 'second-machine' / self.source.name).exists())
            workers[0]()
        self.assertEqual((self.library.root / 'second-machine' / self.source.with_suffix('.transcript.md').name).read_text(), 'ready now')

    def test_delete_remote_only_cleans_partial_copy_and_retry_receipt(self):
        real_run = __import__('subprocess').run
        def transfer(argv, **kwargs):
            if 'cat -- ' in argv[-1] and '.transcript.md' in argv[-1]:
                raise OSError('disk full')
            return real_run(argv, **kwargs)
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv), \
             patch('recording_library.subprocess.run', side_effect=transfer):
            self.assertFalse(self.library.store(self.remote, str(self.source))['ok'])
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(self.library, 'source_recorder', return_value=self.local), \
             patch.object(api, 'recorder', return_value=self.remote):
            result = asyncio.run(api.post_delete({'file': str(self.source), 'machine': 'capture-box'}))
        self.assertTrue(result['ok'], result)
        self.assertFalse(self.source.exists())
        self.assertFalse(list(self.library.root.glob('*/*')))

    def test_title_before_upload_survives_and_stored_rename_can_clear_it(self):
        self.assertTrue(self.local.set_title(str(self.source), 'Before upload')['ok'])
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            stored = self.library.store(self.remote, str(self.source))['file']
        with patch.object(self.remote, 'recordings', return_value=[]):
            self.assertEqual(self.library.recordings(self.remote)[0]['title'], 'Before upload')
        target = self.library.target(stored)
        assert target is not None
        local = target[0]
        self.assertTrue(local.set_title(stored, '')['ok'])
        with patch.object(self.remote, 'recordings', return_value=[]):
            self.assertEqual(self.library.recordings(self.remote)[0]['title'], '')
        self.assertEqual(self.local.get_title(str(self.source))['title'], 'Before upload')

    def test_failed_upload_route_returns_error_before_background_retry(self):
        with patch('recording_library.subprocess.run', side_effect=OSError('connection lost')):
            self.assertFalse(self.library.store(self.remote, str(self.source))['ok'])
        workers = []
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(api, 'recorder', return_value=self.remote), \
             patch.object(self.remote, 'recordings', return_value=[{'file': str(self.source), 'name': self.source.name}]), \
             patch.object(self.library, '_launch_retry', side_effect=workers.append), \
             patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            row = asyncio.run(api.get_recordings())['items'][0]
            self.assertFalse(row['stored'])
            self.assertIn('connection lost', row['upload_error'])
            self.assertEqual(len(workers), 1)
            workers[0]()
            row = asyncio.run(api.get_recordings())['items'][0]
            self.assertTrue(row['stored'])
            self.assertFalse(row['upload_error'])
            self.assertEqual(len(workers), 1)

    def test_same_absolute_paths_on_two_machines_do_not_alias_actions(self):
        with patch('recording_library.socket.gethostname', return_value='backend'):
            self.assertTrue(self.library.store(self.local, str(self.source))['ok'])
        with patch.object(api, 'RecordingLibrary', return_value=self.library), \
             patch.object(api, 'recorder', return_value=self.remote), \
             patch.object(self.remote, 'recordings', return_value=[{'file': str(self.source), 'name': self.source.name}]), \
             patch.object(self.remote, 'set_title', return_value={'ok': True}) as rename, \
             patch.object(self.remote, 'read_transcript', return_value='remote text') as read, \
             patch.object(self.remote, 'delete_recording', return_value={'ok': True}) as delete:
            rows = asyncio.run(api.get_recordings())['items']
            self.assertEqual({(r['machine'], r['stored']) for r in rows}, {('backend', True), ('capture-box', False)})
            body = {'file': str(self.source), 'machine': 'capture-box', 'title': 'Remote title'}
            self.assertTrue(asyncio.run(api.post_title(body))['ok'])
            rename.assert_called_once_with(str(self.source), 'Remote title')
            transcript = str(self.source.with_suffix('.transcript.md'))
            self.assertEqual(asyncio.run(api.read_transcript(transcript, 'capture-box'))['markdown'], 'remote text')
            read.assert_called_once_with(transcript)
            self.assertTrue(asyncio.run(api.post_delete(body))['ok'])
            delete.assert_called_once_with(str(self.source))
        self.assertTrue(self.source.exists())
        self.assertIsNotNone(self.library.target(str(self.source), 'backend'))

    def test_delete_failure_keeps_backend_copy_and_provenance_for_retry(self):
        with patch.object(self.remote, 'shell_argv', side_effect=self.local.shell_argv):
            stored = self.library.store(self.remote, str(self.source))['file']
        with patch.object(self.library, 'source_recorder', return_value=self.remote), \
             patch.object(self.remote, 'delete_recording', return_value={'ok': False, 'error': 'Could not reach capture machine.'}):
            result = self.library.delete(stored, 'capture-box')
        assert result is not None
        self.assertFalse(result['ok'])
        self.assertIn('reach', result['error'])
        self.assertTrue(Path(stored).exists())
        self.assertTrue(self.source.exists())
        self.assertIsNotNone(self.library.target(stored, 'capture-box'))

    def test_local_capture_is_indexed_without_copy(self):
        with patch('recording_library.socket.gethostname', return_value='backend'), \
             patch('recording_library.subprocess.run', side_effect=AssertionError('no transfer')):
            result = self.library.store(self.local, str(self.source))
        self.assertTrue(result['ok'], result)
        with patch('recording_library.socket.gethostname', return_value='backend'):
            rows = self.library.recordings(self.local)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['file'], rows[0]['machine'], rows[0]['stored']),
                         (str(self.source), 'backend', True))
        self.assertFalse((self.library.root / 'backend' / self.source.name).exists())
