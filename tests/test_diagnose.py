"""Connection checklist at the backend and route boundaries."""
import asyncio
import subprocess
import unittest
from unittest.mock import patch

from recorder import Recorder
import platforms
from test_settings import plugin_api as api


class DiagnoseTests(unittest.TestCase):
    def test_healthy_linux_checks_in_order_and_names_whisper(self):
        def run(rec, command, timeout=30):
            if command == 'echo callrec-connected':
                return 0, 'callrec-connected\n'
            if 'uname -s' in command:
                return 0, 'Linux\n'
            if 'pactl list short sources' in command:
                return 0, '1\tspeaker.monitor\tmodule\n2\tmicrophone\tmodule\n'
            if 'get-default-sink' in command:
                return 0, 'speaker\n'
            if 'get-default-source' in command:
                return 0, 'microphone\n'
            if 'command -v ffmpeg' in command:
                return 0, '/usr/bin/ffmpeg\n'
            if 'import faster_whisper' in command:
                return 0, 'PY=/home/u/whisper/.venv/bin/python\n'
            return 1, 'unexpected command: ' + command
        with patch.object(Recorder, 'run', run), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'callrec-connected', '')):
            result = platforms.diagnose(Recorder, 'desk')
        self.assertTrue(result['ok'], result)
        self.assertEqual([s['id'] for s in result['steps']], ['ssh', 'os', 'audio', 'ffmpeg', 'whisper'])
        self.assertTrue(all(s['status'] == 'ok' for s in result['steps']))
        self.assertIn('/home/u/whisper/.venv/bin/python', result['steps'][-1]['message'])

    def test_dead_ssh_and_unexpected_errors_never_raise(self):
        for failure in [(255, 'Connection refused'), OSError('no ssh')]:
            with self.subTest(failure=failure), patch('subprocess.run') as run:
                if isinstance(failure, Exception):
                    run.side_effect = failure
                else:
                    run.return_value = subprocess.CompletedProcess([], failure[0], failure[1], "")
                result = platforms.diagnose(Recorder, 'dead-host')
            self.assertFalse(result['ok'])
            self.assertEqual(result['steps'][0]['id'], 'ssh')
            self.assertEqual(result['steps'][0]['status'], 'failed')
            self.assertEqual(len(result['steps']), 5)

    def test_windows_without_logged_in_session_fails_audio(self):
        def run(rec, command, timeout=30):
            if command == 'echo callrec-connected':
                return 0, 'callrec-connected'
            if 'uname -s' in command:
                return 1, 'not found'
            if command == 'Write-Output $env:OS':
                return 0, 'Windows_NT'
            if command == 'quser':
                return 1, 'No User exists'
            if 'Get-Command ffmpeg' in command:
                return 0, 'C:\\ffmpeg.exe'
            if 'import faster_whisper' in command:
                return 1, 'missing'
            return 1, 'not available'
        with patch.object(Recorder, 'run', run), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'callrec-connected', '')):
            result = platforms.diagnose(Recorder, 'windows-pc')
        self.assertFalse(result['ok'])
        audio = result['steps'][2]
        self.assertEqual(audio['status'], 'failed')
        self.assertIn('logged in', audio['message'])
        self.assertEqual(result['steps'][3]['status'], 'ok')
        self.assertEqual(result['steps'][4]['status'], 'failed')

    def test_windows_audio_probe_checks_devices_and_cleans_up_its_own_task(self):
        import diagnose_windows
        from unittest.mock import Mock
        rec = Mock()
        rec.run.return_value = (0, '{"ok": true, "message": "WASAPI loopback Speakers and microphone USB are present."}')
        with patch.object(diagnose_windows, '_home', return_value=r'C:\Users\person'), \
             patch.object(diagnose_windows, '_write_bytes', return_value='True') as write:
            ok, message = diagnose_windows.audio_devices(rec)
        self.assertTrue(ok)
        self.assertIn('microphone USB', message)
        self.assertIn(b'pyaudiowpatch', write.call_args.args[2])
        command = rec.run.call_args_list[0].args[0]
        self.assertIn('-LogonType Interactive', command)
        self.assertIn('New-TimeSpan -Seconds 30', command)
        self.assertIn('Get-Content -LiteralPath', command)
        cleanup = rec.run.call_args_list[-1].args[0]
        self.assertIn('Unregister-ScheduledTask', cleanup)
        self.assertIn('Remove-Item -LiteralPath', cleanup)
        self.assertNotIn("'call-recorder-capture'", command + cleanup)
        rec.run.return_value = (1, 'could not launch')
        with patch.object(diagnose_windows, '_home', return_value=r'C:\Users\person'), \
             patch.object(diagnose_windows, '_write_bytes', return_value='True'):
            self.assertFalse(diagnose_windows.audio_devices(rec)[0])
        self.assertIn('Unregister-ScheduledTask', rec.run.call_args_list[-1].args[0])

    def test_windows_device_enumeration_requires_both_real_endpoints(self):
        import importlib.util
        from pathlib import Path
        from unittest.mock import MagicMock
        import sys
        spec = importlib.util.spec_from_file_location('audio_probe', Path(__file__).resolve().parent.parent / 'scripts' / 'diagnose_windows.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        audio = MagicMock()
        p = audio.PyAudio.return_value.__enter__.return_value
        p.get_host_api_info_by_type.return_value = {'defaultOutputDevice': 0, 'defaultInputDevice': 1}
        output = {'name': 'Speakers', 'isLoopbackDevice': False}
        microphone = {'name': 'USB mic', 'maxInputChannels': 1}
        p.get_device_info_by_index.side_effect = lambda index: output if index == 0 else microphone
        p.get_loopback_device_info_generator.return_value = [{'name': 'Speakers loopback', 'isLoopbackDevice': True}]
        with patch.dict(sys.modules, {'pyaudiowpatch': audio}):
            found = module.devices()
            self.assertTrue(found['ok'])
            self.assertIn('USB mic', found['message'])
            microphone['maxInputChannels'] = 0
            self.assertFalse(module.devices()['ok'])
            microphone['maxInputChannels'] = 1
            p.get_loopback_device_info_generator.return_value = []
            self.assertFalse(module.devices()['ok'])

    def test_audio_failure_still_checks_ffmpeg_and_whisper(self):
        def run(rec, command, timeout=30):
            if 'uname -s' in command:
                return 0, 'Linux'
            if 'pactl' in command:
                raise OSError('audio server unavailable')
            if 'command -v ffmpeg' in command:
                return 0, '/usr/bin/ffmpeg'
            if 'import faster_whisper' in command:
                return 0, 'PY=/usr/bin/python3'
            return 0, 'callrec-connected'
        with patch.object(Recorder, 'run', run):
            result = platforms.diagnose(Recorder)
        self.assertEqual([s['status'] for s in result['steps']], ['ok', 'ok', 'failed', 'ok', 'ok'])

    def test_route_checks_explicit_local_and_remote_with_same_shape(self):
        shapes = []
        for host in ['', 'beast-1']:
            with patch.object(api, 'settings', return_value={**api.DEFAULTS, 'host': host}), \
                 patch.object(Recorder, 'run', return_value=(255, 'refused')), \
                 patch('subprocess.run', return_value=subprocess.CompletedProcess([], 255, '', 'refused')):
                result = asyncio.run(api.post_diagnose())
            self.assertEqual(result['target'], host or 'local')
            shapes.append((set(result), [set(s) for s in result['steps']]))
        self.assertEqual(shapes[0], shapes[1])
