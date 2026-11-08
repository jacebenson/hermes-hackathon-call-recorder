"""Dashboard routes, with no network or capture process."""
import asyncio
import unittest
from unittest.mock import Mock, patch
from test_settings import plugin_api as api


class StopTests(unittest.TestCase):
    def setUp(self):
        upload = patch.object(api, '_store', return_value={'ok': True, 'stored': True})
        upload.start()
        self.addCleanup(upload.stop)
        api._TRANSCRIPTION_JOBS.clear() if hasattr(api, '_TRANSCRIPTION_JOBS') else None
        self.rec = Mock(host='test')
        self.rec.status.return_value = {'recording': False}
        self.rec.stop.return_value = {'ok': True, 'file': '/calls/call-20260101-120000.mkv'}

    def test_stop_queues_archive_and_reports_real_completion(self):
        workers = []
        with patch.object(api, 'recorder', return_value=self.rec), \
             patch.object(api, 'settings', return_value={'transcribe': True}), \
             patch.object(api, '_launch_archive', side_effect=lambda fn: workers.append(fn)), \
             patch.object(api, 'resolved_transcription', return_value={'python': 'p', 'live_script': '/x/live.py'}), \
             patch.object(api, 'run_transcription', return_value={'ok': False, 'error': 'Whisper failed'}) as run:
            result = asyncio.run(api.post_stop())
            self.assertEqual(result['transcription']['state'], 'queued')
            workers[0]()
            status = asyncio.run(api.get_status())
            self.assertEqual(status['transcriptions'][0]['state'], 'failed')
            self.assertEqual(status['transcriptions'][0]['error'], 'Whisper failed')
            run.assert_called_once()
            self.rec.duration.assert_not_called()

    def test_disabled_and_failed_stops_never_queue(self):
        for enabled, stopped in [(False, {'ok': True, 'file': 'x'}), (True, {'ok': False, 'file': 'x'})]:
            with patch.object(api, 'recorder', return_value=self.rec), \
                 patch.object(api, 'settings', return_value={'transcribe': enabled}), \
                 patch.object(api, '_launch_archive') as thread:
                self.rec.stop.return_value = stopped
                asyncio.run(api.post_stop())
                thread.assert_not_called()

    def test_success_exception_and_duplicate_stop_jobs(self):
        for outcome in ({'ok': True, 'path': 'transcript.md'}, RuntimeError('offline')):
            api._TRANSCRIPTION_JOBS.clear()
            workers = []
            with patch.object(api, 'settings', return_value={'transcribe': True}), \
                 patch.object(api, '_launch_archive', side_effect=lambda fn: workers.append(fn)), \
                 patch.object(api, '_transcribe', side_effect=outcome if isinstance(outcome, Exception) else None,
                              return_value=outcome):
                first = api._after_stop(self.rec, self.rec.stop.return_value)
                api._after_stop(self.rec, self.rec.stop.return_value)
                self.assertEqual(len(workers), 1)
                workers[0]()
                job = api._TRANSCRIPTION_JOBS[('test', first['file'])]
                self.assertEqual(job['state'], 'failed' if isinstance(outcome, Exception) else 'ready')
                self.assertEqual(job['ok'], not isinstance(outcome, Exception))

    def test_title_routes_validate_and_round_trip(self):
        with patch.object(api, 'recorder', return_value=self.rec):
            bad = asyncio.run(api.post_title({'file': 'f', 'title': 'bad\nname'}))
            self.assertFalse(bad['ok'])
            self.rec.set_title.assert_not_called()
            self.rec.set_title.return_value = {'ok': True, 'title': 'Planning'}
            self.assertTrue(asyncio.run(api.post_title({'file': 'f', 'title': ' Planning '}))['ok'])
            self.rec.set_title.assert_called_once_with('f', 'Planning')
            self.rec.get_title.return_value = {'ok': True, 'title': 'Planning'}
            self.assertEqual(asyncio.run(api.get_title('f'))['title'], 'Planning')

    def test_watcher_uses_the_same_archive_path(self):
        self.rec.tick.return_value = {'action': 'stopped', 'ok': True, 'file': 'x'}
        with patch.object(api, 'recorder', return_value=self.rec), \
             patch.object(api, 'settings', return_value={'watch_seconds': 3, 'transcribe': True}), \
             patch.object(api, '_after_stop', return_value={}) as finish, \
             patch.object(api.time, 'sleep', side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                api._watch()
            finish.assert_called_once_with(self.rec, self.rec.tick.return_value)
