"""Live transcript delivery through recorder, conversion, and stop routes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder import Recorder
import transcribe as tx
from test_settings import plugin_api as api


class LiveTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = patch.dict('os.environ', {'HERMES_HOME': self.tmp.name})
        home.start()
        self.addCleanup(home.stop)
        upload = patch.object(api, '_store', return_value={'ok': True, 'stored': True})
        upload.start()
        self.addCleanup(upload.stop)
        self.source = Path(self.tmp.name) / 'call-20261108-201556.mkv'
        self.live = self.source.with_suffix('.live.txt')
        self.output = self.source.with_suffix('.transcript.md')
        self.rec = Recorder(recordings_dir=self.tmp.name,
                            state_file=str(Path(self.tmp.name) / 'state.json'),
                            python='/python', live_script='/live.py')
        api._TRANSCRIPTION_JOBS.clear()

    def test_conversion_orders_both_speakers_and_skips_markers_and_errors(self):
        text = ('\n=== live started 2026-11-08 20:15:59 ===\n'
                '[20:16:10] you> Later\n[20:16:02] them> First\n'
                '[20:16:03] you>   \n[20:16:04] them> [error broken]\nnoise\n')
        markdown, count = tx.live_to_markdown(str(self.source), text)
        self.assertEqual(count, 2)
        self.assertEqual(markdown, '# call-20261108-201556.mkv\n\n0.2 minutes, 2 segments\n\n'
                         '**[00:06] them:** First\n\n**[00:14] you:** Later\n\n')

    def test_conversion_crosses_midnight(self):
        markdown, count = tx.live_to_markdown('call-20261108-235958.mkv',
                                             '[00:00:03] you> After\n[23:59:59] them> Before\n')
        self.assertEqual(count, 2)
        self.assertIn('**[00:01] them:** Before\n\n**[00:05] you:** After', markdown)

    def test_start_live_sets_recording_specific_output_and_preserves_environment(self):
        self.rec.save_state({'file': str(self.source), 'unit': 'capture'})
        with patch.object(self.rec, 'run', return_value=(0, '')) as run:
            self.rec.start_live(model='model', ld_library_path='/cuda')
        command = run.call_args.args[0]
        self.assertIn('-E LIVE_OUT=' + str(self.live), command)
        self.assertIn('-E WHISPER_MODEL=model', command)
        self.assertIn('-E LD_LIBRARY_PATH=/cuda', command)

    def test_start_passes_new_recording_to_live(self):
        with patch.object(self.rec, 'sources', return_value={'monitor': 'mon', 'mic': 'mic'}), \
             patch.object(self.rec, 'ok', return_value=''), \
             patch.object(self.rec, 'run', return_value=(0, '')) as run:
            result = self.rec.start(live=True)
        self.assertTrue(result['recording'])
        self.assertIn('-E LIVE_OUT=' + str(Path(result['file']).with_suffix('.live.txt')),
                      run.call_args.args[0])

    def test_live_tail_prefers_current_file_and_falls_back_when_missing_or_stopped(self):
        shared = Path(self.tmp.name) / 'live.txt'
        shared.write_text('[20:00:00] you> shared\n')
        self.live.write_text('=== live started ===\n[20:16:00] them> current\n')
        self.rec.save_state({'file': str(self.source), 'unit': 'capture'})
        with patch.object(self.rec, 'unit_active', return_value=True):
            self.assertEqual(self.rec.transcript_tail(), ['[20:16:00] them> current'])
            self.live.unlink()
            self.assertEqual(self.rec.transcript_tail(), ['[20:00:00] you> shared'])
        self.live.write_text('[20:16:00] them> old\n')
        with patch.object(self.rec, 'unit_active', return_value=False):
            self.assertEqual(self.rec.transcript_tail(), ['[20:00:00] you> shared'])

    def stop(self):
        return api._after_stop(self.rec, {'ok': True, 'file': str(self.source)})

    def test_stop_prefers_live_writes_markdown_and_deduplicates(self):
        self.live.write_text('[20:16:02] them> Saved speech\n')
        with patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, '_transcribe') as archive:
            first = self.stop()
            second = self.stop()
        archive.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first['transcription']['state'], 'ready')
        self.assertEqual(first['transcription']['engine'], 'live')
        self.assertEqual(first['transcription']['segments'], 1)
        self.assertEqual(first['transcription']['path'], str(self.output))
        self.assertIn('**[00:06] them:** Saved speech', self.output.read_text())

    def test_missing_empty_and_marker_only_live_fall_back_to_archive(self):
        for content in (None, '', '\n=== live started ===\n', '[20:16:00] you> [error broken]\n'):
            with self.subTest(content=content):
                api._TRANSCRIPTION_JOBS.clear()
                if content is not None:
                    self.live.write_text(content)
                with patch.object(api, 'settings', return_value={'transcribe': True}), \
                     patch.object(api, '_launch_archive', side_effect=lambda fn: fn()), \
                     patch.object(api, '_transcribe', return_value={'ok': True, 'engine': 'gpu', 'segments': 2}) as archive:
                    result = self.stop()
                archive.assert_called_once_with(self.rec, str(self.source))
                self.assertEqual(result['transcription']['engine'], 'gpu')
                self.assertEqual(result['transcription']['state'], 'ready')
                self.assertTrue(result['transcription']['live_error'])

    def test_failed_live_write_is_not_reported_ready_and_archive_failure_is_explicit(self):
        self.live.write_text('[20:16:02] them> Speech\n')
        with patch.object(self.rec, 'run', side_effect=[(0, self.live.read_text()), (1, 'Permission denied')]), \
             patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, '_launch_archive', side_effect=lambda fn: fn()), \
             patch.object(api, '_transcribe', return_value={'ok': False, 'error': 'Whisper failed'}):
            job = self.stop()['transcription']
        self.assertEqual(job['state'], 'failed')
        self.assertTrue(job['both_failed'])
        self.assertFalse(self.output.exists())

    def test_explicit_transcribe_forces_archive_and_overwrites_live_result(self):
        self.live.write_text('[20:16:02] them> Live words\n')
        def archive(rec, path):
            self.output.write_text('# quality\n')
            return {'ok': True, 'engine': 'gpu', 'path': str(self.output), 'segments': 3}
        with patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, 'recorder', return_value=self.rec), \
             patch.object(api, '_transcribe', side_effect=archive) as run:
            self.assertEqual(self.stop()['transcription']['engine'], 'live')
            result = asyncio.run(api.post_transcribe({'file': str(self.source)}))
            self.assertEqual(result['engine'], 'gpu')
            self.assertEqual(self.output.read_text(), '# quality\n')
            self.assertEqual(api._status_payload()['transcriptions'][0]['engine'], 'gpu')
        run.assert_called_once_with(self.rec, str(self.source))

    def test_forced_repass_supersedes_a_queued_automatic_job(self):
        workers = []
        with patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, 'recorder', return_value=self.rec), \
             patch.object(api, '_launch_archive', side_effect=workers.append), \
             patch.object(api, '_transcribe', return_value={'ok': True, 'engine': 'gpu', 'path': str(self.output)}) as archive:
            self.stop()
            asyncio.run(api.post_transcribe({'file': str(self.source)}))
            workers[0]()
        archive.assert_called_once_with(self.rec, str(self.source))

    def test_live_readback_must_match_before_reporting_success(self):
        with patch.object(self.rec, 'run', side_effect=[(0, '[20:16:02] you> Words\n'),
                                                     (0, ''), (0, 'wrong file')]):
            result = tx.transcribe_from_live(self.rec, str(self.source))
        self.assertFalse(result['ok'])
        self.assertIn('verify', result['error'])

    def test_live_remote_commands_quote_paths_and_verify_written_unicode(self):
        text = '[20:16:02] you> café and "quotes"\n'
        source = "/calls with 'quotes'/call-20261108-201556.mkv"
        markdown, _ = tx.live_to_markdown(source, text)
        with patch.object(self.rec, 'run', side_effect=[(0, text), (0, ''), (0, markdown)]) as run:
            result = tx.transcribe_from_live(self.rec, source)
        self.assertTrue(result['ok'])
        self.assertEqual(result['path'], "/calls with 'quotes'/call-20261108-201556.transcript.md")
        self.assertEqual(run.call_args_list[0].args[0],
                         "cat -- '/calls with '\"'\"'quotes'\"'\"'/call-20261108-201556.live.txt'")
        self.assertEqual(run.call_args_list[2].args[0],
                         "cat -- '/calls with '\"'\"'quotes'\"'\"'/call-20261108-201556.transcript.md'")

    def test_forced_repass_waits_for_running_auto_writer(self):
        entered = threading.Event()
        release = threading.Event()
        forced_registered = threading.Event()
        writers = []
        workers = []
        def archive(rec, path):
            writers.append(threading.current_thread().name)
            if len(writers) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
                self.output.write_text('automatic')
            else:
                self.output.write_text('forced quality')
            return {'ok': True, 'engine': 'gpu', 'path': str(self.output)}
        class ObservedLocks(dict):
            def setdefault(inner, key, default=None):
                if entered.is_set():
                    forced_registered.set()
                return super().setdefault(key, default)
        with patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, 'recorder', return_value=self.rec), \
             patch.object(api, '_TRANSCRIPTION_FILE_LOCKS', ObservedLocks()), \
             patch.object(api, '_launch_archive', side_effect=workers.append), \
             patch.object(api, '_transcribe', side_effect=archive), \
             ThreadPoolExecutor(max_workers=2) as pool:
            self.stop()
            auto = pool.submit(workers[0])
            self.assertTrue(entered.wait(5))
            forced = pool.submit(lambda: asyncio.run(api.post_transcribe({'file': str(self.source)})))
            try:
                self.assertTrue(forced_registered.wait(5))
                self.assertEqual(len(writers), 1)
            finally:
                release.set()
            auto.result(timeout=5)
            self.assertTrue(forced.result(timeout=5)['ok'])
        self.assertEqual(self.output.read_text(), 'forced quality')

    def test_windows_live_publish_uses_literal_paths_and_reads_back(self):
        self.rec.os_name = 'windows'
        source = r"C:\Calls\call-20261108-201556.mkv"
        speech = '[20:16:02] them> Windows words\n'
        markdown, _ = tx.live_to_markdown(source, speech)
        with patch.object(self.rec, 'run', side_effect=[(0, speech), (0, ''), (0, markdown)]) as run:
            result = tx.transcribe_from_live(self.rec, source)
        self.assertTrue(result['ok'])
        self.assertEqual(result['segments'], 1)
        self.assertIn("Get-Content -LiteralPath 'C:\\Calls\\call-20261108-201556.live.txt'", run.call_args_list[0].args[0])
        self.assertIn('Move-Item -LiteralPath $tmp', run.call_args_list[1].args[0])
        self.assertIn("Get-Content -LiteralPath 'C:\\Calls\\call-20261108-201556.transcript.md'", run.call_args_list[2].args[0])

    def test_delete_also_removes_live_file(self):
        self.source.touch()
        self.live.write_text('speech')
        self.output.touch()
        self.assertTrue(self.rec.delete_recording(str(self.source))['ok'])
        self.assertFalse(self.live.exists())
        self.assertFalse(self.output.exists())
        self.assertFalse(self.source.exists())
