"""Native routes with real two-track lavfi media, never physical audio.

Only the audio/service boundary is replaced. State, start/status/stop, ffprobe,
live-text finalization, transcript reads and backend library use production code.
This is not live hardware or live Whisper acceptance.
"""
import asyncio
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from recorder import Recorder
from test_settings import plugin_api as api


class SyntheticNativeTests(unittest.TestCase):
    def test_native_routes_save_two_tracks_and_build_readable_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = Recorder(recordings_dir=str(root / 'calls'), state_file=str(root / 'state.json'),
                           auto_start=False, python='fixture', live_script='fixture/live.py')
            real_run = rec.run
            active = set()
            commands = []

            def run(command, timeout=30):
                commands.append(command)
                if command.startswith('systemd-run'):
                    self.assertIn('-f pulse -i fixture-monitor', command)
                    self.assertIn('-f pulse -i fixture-mic', command)
                    argv = shlex.split(command)
                    active.add(next(a.split('=', 1)[1] for a in argv if a.startswith('--unit=')))
                    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=330:duration=1', '-f', 'lavfi', '-i',
                        'sine=frequency=660:duration=1', '-map', '0:a', '-map', '1:a',
                        '-c:a', 'libopus', argv[-1]], capture_output=True, text=True)
                    return result.returncode, result.stderr
                if command.startswith('systemctl'):
                    active.clear()
                    # Never dispatch a service command to the real user session.
                    command = command[command.index('echo __SZ__'):]
                return real_run(command, timeout)

            def start_live(**kwargs):
                active.add('call-recorder-live')
                Path(rec.state()['file']).with_suffix('.live.txt').write_text(
                    '[00:00:01] you> Synthetic microphone speech\n'
                    '[00:00:02] them> Synthetic system speech\n')
                return {'ok': True}

            cfg = {**api.DEFAULTS, 'transcribe': True, 'capture_mode': 'computer'}
            with patch.dict(os.environ, {'HERMES_HOME': str(root)}), \
                 patch.object(api, 'settings', return_value=cfg), \
                 patch.object(api, 'recorder', return_value=rec), \
                 patch.object(api, 'ensure_watcher'), \
                 patch.object(rec, 'sources', return_value={'monitor': 'fixture-monitor', 'mic': 'fixture-mic'}), \
                 patch.object(rec, 'unit_active', side_effect=lambda unit: unit in active), \
                 patch.object(rec, 'mic_level', return_value=-20), \
                 patch.object(rec, 'start_live', side_effect=start_live), \
                 patch.object(rec, 'run', side_effect=run):
                started = asyncio.run(api.post_start({'live': True}))
                self.assertTrue(started['ok'])
                self.assertTrue(api._status_payload()['recording'])
                stopped = asyncio.run(api.post_stop())
                self.assertTrue(stopped['ok'])
                self.assertGreater(stopped['bytes'], 0)
                self.assertEqual(stopped['transcription']['state'], 'ready')
                self.assertFalse(api._status_payload()['recording'])
                audio = Path(started['file'])
                streams = json.loads(subprocess.check_output(['ffprobe', '-v', 'error',
                    '-show_streams', '-of', 'json', str(audio)]))['streams']
                self.assertEqual([s['codec_type'] for s in streams], ['audio', 'audio'])
                transcript = str(audio.with_suffix('.transcript.md'))
                result = asyncio.run(api.read_transcript(transcript))
                self.assertTrue(result['ok'])
                self.assertIn('Synthetic microphone speech', result['markdown'])
                self.assertIn('Synthetic system speech', result['markdown'])
                self.assertTrue(asyncio.run(api.get_recordings())['items'])
            self.assertTrue(commands)
