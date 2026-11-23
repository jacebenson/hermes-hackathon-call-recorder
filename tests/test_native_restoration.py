"""Native defaults and watcher recovery, without a capture device or real home."""
import asyncio
import unittest
from unittest.mock import patch

from recorder import CaptureUnavailable, Recorder
from test_settings import plugin_api as api


class NativeRestorationTests(unittest.TestCase):
    def test_new_install_is_native_without_starting_capture(self):
        with patch.object(api, 'manifest_settings', return_value={}), \
             patch.object(api._SETTINGS_FILE.__class__, 'read_text', side_effect=FileNotFoundError), \
             patch.dict(api.os.environ, {}, clear=True), \
             patch.object(Recorder, 'run') as commands:
            cfg = api.settings()
        self.assertEqual(cfg['capture_mode'], 'computer')
        self.assertFalse(cfg['auto_start'])
        commands.assert_not_called()

    def test_audio_failure_can_recover_on_next_explicit_start(self):
        rec = Recorder()
        cfg = {**api.DEFAULTS, 'capture_mode': 'computer', 'watch_seconds': 3}
        # Execute the real watcher in a controlled thread adapter. It must clear
        # its latch on failure, then a subsequent user start must launch it again.
        with patch.object(api, 'settings', return_value=cfg), \
             patch.object(api, 'recorder', return_value=rec), \
             patch.object(api, '_WATCHER_STARTED', False), \
             patch.object(rec, 'tick', side_effect=CaptureUnavailable('fixture audio absent')), \
             patch.object(api.threading.Thread, 'start', lambda thread: thread.run()):
            api.ensure_watcher()
            self.assertFalse(api._WATCHER_STARTED)
            with patch.object(rec, 'start', return_value={'ok': True, 'file': '/calls/call-20260101-120000.mkv'}) as start, \
                 patch.object(api, '_watch', side_effect=lambda: setattr(api, '_WATCHER_STARTED', False)) as watcher:
                api.ensure_watcher()
                watcher.assert_called_once()
        with patch.object(api, 'settings', return_value=cfg), \
             patch.object(api, 'recorder', return_value=rec), \
             patch.object(api, 'ensure_watcher') as ensure, \
             patch.object(rec, 'start', return_value={'ok': True}) as start:
            result = asyncio.run(api.post_start({'live': True}))
            self.assertTrue(result['ok'])
            start.assert_called_once_with(live=True)
            ensure.assert_called_once()


if __name__ == '__main__':
    unittest.main()
