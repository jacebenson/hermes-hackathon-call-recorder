"""Browser uploads use disposable backend storage, never capture-machine paths."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
from test_settings import plugin_api as api
from recording_library import RecordingLibrary


class BrowserCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'HERMES_HOME': str(self.root)})
        env.start(); self.addCleanup(env.stop)
        cfg = patch.object(api, '_SETTINGS_FILE', self.root / 'settings.json')
        cfg.start(); self.addCleanup(cfg.stop)
        manifest = patch.object(api, 'manifest_settings', return_value={})
        manifest.start(); self.addCleanup(manifest.stop)

    def test_new_install_browser_explicit_legacy_host_computer_and_override(self):
        self.assertEqual(api.settings()['capture_mode'], 'browser')
        (self.root / 'settings.json').write_text(json.dumps({'host': 'old-host', 'custom': 7}))
        self.assertEqual(api.settings()['capture_mode'], 'computer')
        result = asyncio.run(api.post_capture_mode({'capture_mode': 'browser'}))
        self.assertTrue(result['ok'])
        self.assertEqual(api.settings()['host'], 'old-host')
        self.assertEqual(api.settings()['capture_mode'], 'browser')
        self.assertEqual(json.loads((self.root / 'settings.json').read_text())['custom'], 7)
        self.assertFalse(asyncio.run(api.post_capture_mode({'capture_mode': 'elsewhere'}))['ok'])

    def test_browser_whisper_needs_runtime_not_legacy_live_script(self):
        with patch.object(api, 'detect_transcription', return_value={'python': '/backend/python', 'ok': False, 'live_script': ''}):
            self.assertTrue(api.browser_transcription()['ok'])

    def test_browser_choice_cannot_start_computer_as_fallback(self):
        with patch.object(api, 'recorder') as native:
            result = asyncio.run(api.post_start({'live': True}))
        self.assertFalse(result['ok'])
        native.assert_not_called()

    def test_explicit_empty_legacy_host_preserved(self):
        (self.root / 'settings.json').write_text('{"host":""}')
        self.assertEqual(api.settings()['capture_mode'], 'computer')

    def audio(self):
        path = self.root / 'test.webm'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=330:duration=0.5', '-c:a', 'libopus', str(path)], check=True)
        return path.read_bytes()

    def test_upload_persists_one_track_and_reuses_title_read_delete(self):
        library = RecordingLibrary()
        result = library.store_browser(self.audio(), 'audio/webm')
        self.assertTrue(result['ok'], result)
        path = result['file']
        selected = Mock(host='offline')
        selected.recordings.side_effect = RuntimeError('offline')
        rows = library.recordings(selected)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['capture_mode'], 'browser')
        self.assertEqual(rows[0]['tracks'], ['microphone'])
        self.assertFalse(rows[0]['speaker_separated'])
        self.assertEqual(rows[0]['origin'], 'browser')
        rec = library.target(path, 'browser')[0]
        self.assertTrue(rec.set_title(path, 'Test')['ok'])
        self.assertEqual(rec.get_title(path)['title'], 'Test')
        self.assertTrue(library.delete(path, 'browser')['ok'])
        self.assertFalse(Path(path).exists())

    def test_upload_rejects_empty_invalid_and_oversize_without_files(self):
        library = RecordingLibrary()
        for content, mime in [(b'', 'audio/webm'), (b'not audio', 'audio/webm'), (b'foo', 'text/html')]:
            self.assertFalse(library.store_browser(content, mime)['ok'])
        with patch('recording_library.MAX_BROWSER_BYTES', 2):
            self.assertFalse(library.store_browser(b'xxx', 'audio/webm')['ok'])
        self.assertEqual(list(library.root.glob('*/call-*.library.json')), [])

    def test_upload_cannot_follow_playlist_paths(self):
        segment = self.root / 'private.ts'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=duration=0.3', '-c:a', 'aac', '-f', 'mpegts', str(segment)], check=True)
        playlist = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n'
                    '#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:0.3,\n' + str(segment) + '\n#EXT-X-ENDLIST\n').encode()
        self.assertFalse(RecordingLibrary().store_browser(playlist, 'audio/webm')['ok'])

    def test_browser_transcribe_never_runs_two_track_archive(self):
        library = RecordingLibrary()
        result = library.store_browser(self.audio(), 'audio/webm')
        with patch.object(api, 'detect_transcription', return_value={'ok': False}), \
             patch.object(api, 'run_transcription') as two_track:
            outcome = api._transcribe(library.target(result['file'])[0], result['file'])
        self.assertFalse(outcome['ok'])
        self.assertIn('unavailable', outcome['error'].lower())
        two_track.assert_not_called()
