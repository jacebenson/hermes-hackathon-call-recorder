"""A machine with no audio server must say so, not return a 500.

The backend offers its own machine as a capture target. On a headless server
that machine has no PipeWire and no `pactl`, so `sources()` failed with a bare
RuntimeError that surfaced as HTTP 500 and the automation watcher retried it
every few seconds forever.
"""
import asyncio
import unittest
from unittest.mock import patch

from recorder import CaptureUnavailable, Recorder, capture_unavailable
from test_settings import plugin_api as api


class SourceFailureTests(unittest.TestCase):
    def test_missing_audio_tools_name_the_machine_and_the_fix(self):
        rec = Recorder(host=None)
        with patch.object(Recorder, 'ok',
                          side_effect=RuntimeError("command failed (127): bash: pactl: command not found")):
            with self.assertRaises(CaptureUnavailable) as caught:
                rec.sources()
        message = str(caught.exception)
        self.assertIn('this machine', message)
        self.assertIn('audio tools', message)
        self.assertIn('pulseaudio-utils', message)

    def test_no_audio_server_is_distinguished_from_missing_tools(self):
        rec = Recorder(host='capture-box')
        with patch.object(Recorder, 'ok',
                          side_effect=RuntimeError("command failed (1): Connection refused")):
            with self.assertRaises(CaptureUnavailable) as caught:
                rec.sources()
        self.assertIn('capture-box', str(caught.exception))
        self.assertIn('no running audio server', str(caught.exception))

    def test_unknown_source_failure_keeps_the_detail(self):
        error = capture_unavailable('something odd happened', 'box')
        self.assertIn('something odd happened', str(error))


class StartRouteTests(unittest.TestCase):
    def test_start_returns_an_actionable_error_instead_of_raising(self):
        rec = Recorder(host=None)
        failure = CaptureUnavailable('this machine has no audio tools installed, so it cannot record.')
        with patch.object(api, 'recorder', return_value=rec), \
             patch.object(rec, 'start', side_effect=failure), \
             patch.object(api, 'settings', return_value={'capture_mode': 'computer'}), \
             patch.object(api, 'ensure_watcher'):
            result = asyncio.run(api.post_start({}))
        self.assertEqual(result['ok'], False)
        self.assertTrue(result['unavailable'])
        self.assertIn('cannot record', result['error'])

    def test_watcher_stops_instead_of_retrying_a_dead_end(self):
        failure = CaptureUnavailable('no audio here')
        rec = Recorder(host=None)
        ticks = {'n': 0}

        def tick():
            ticks['n'] += 1
            raise failure

        with patch.object(api, 'recorder', return_value=rec), \
             patch.object(rec, 'tick', side_effect=tick), \
             patch.object(api, 'settings',
                          return_value={'capture_mode': 'computer', 'watch_seconds': 3}), \
             patch.object(api, 'time') as clock:
            clock.sleep.side_effect = AssertionError('watcher must not loop on a dead end')
            api._watch()
        self.assertEqual(ticks['n'], 1)


if __name__ == '__main__':
    unittest.main()
