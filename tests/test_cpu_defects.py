"""Regressions for CPU live launch and complete archive publication."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from recorder import Recorder, detect_transcription


class CpuDefects(unittest.TestCase):
    def test_live_cpu_launch_includes_device_compute_and_audio_session(self):
        rec = Recorder(python='/python', live_script='/live.py')
        with patch.object(rec, 'state', return_value={'file': '/calls/call.mkv', 'monitor': 'mon', 'mic': 'mic'}), patch.object(rec, 'run', return_value=(0, '')) as run:
            rec.start_live()
        command = run.call_args.args[0]
        for value in ('-E WHISPER_DEVICE=cpu', '-E WHISPER_COMPUTE=int8',
                      '-E XDG_RUNTIME_DIR', '-E PULSE_SERVER', '-E MON_SRC=mon', '-E MIC_SRC=mic'):
            self.assertIn(value, command)

    def test_cpu_does_not_select_small_cached_model(self):
        with patch.object(Recorder, 'run', return_value=(0, 'PY=/p\nLIVE=/live.py\nMODEL=/models-be-small\n')):
            found = detect_transcription()
        self.assertEqual(found['model'], 'tiny.en')
        self.assertEqual(found['device'], 'cpu')

    def test_gpu_keeps_cached_small_model(self):
        with patch.object(Recorder, 'run', return_value=(0, 'PY=/p\nLIVE=/live.py\nMODEL=/models-be-small\nLD=/cuda/lib\n')):
            found = detect_transcription()
        self.assertEqual(found['model'], '/models-be-small')
        self.assertEqual(found['device'], 'cuda')

    def archive(self):
        spec = importlib.util.spec_from_file_location('archive_under_test', Path(__file__).resolve().parents[1] / 'scripts/archive.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'numpy': types.ModuleType('numpy'), 'faster_whisper': types.SimpleNamespace(WhisperModel=object)}):
            spec.loader.exec_module(module)
        return module

    def test_archive_never_publishes_header_without_rows(self):
        archive = self.archive()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'call.mkv'
            output = source.with_suffix('.transcript.md')
            output.write_text('previous complete transcript')
            class BrokenText:
                def __format__(self, spec):
                    raise ValueError('serialization failed after header')
            with self.assertRaises(ValueError):
                archive.write_transcript(source, [(1, 'you', BrokenText())])
            self.assertEqual(output.read_text(), 'previous complete transcript')
            archive.write_transcript(source, [(1, 'you', 'hello'), (2, 'them', 'world')])
            text = output.read_text()
            self.assertIn('2 segments', text)
            self.assertEqual(sum(line.startswith('**[') for line in text.splitlines()), 2)

    def test_archive_main_preserves_complete_file_if_segment_rendering_fails(self):
        archive = self.archive()
        class BrokenText:
            def strip(self):
                return self
            def __format__(self, spec):
                raise ValueError('segment rendering failed')
        class Audio(list):
            def __truediv__(self, value):
                return self
        model = types.SimpleNamespace(transcribe=lambda *a, **kw: ([types.SimpleNamespace(start=1, text=BrokenText())], None))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'call.mkv'
            output = source.with_suffix('.transcript.md')
            output.write_text('previous complete transcript')
            numpy = types.SimpleNamespace(int16=int, float32=float, memmap=lambda *a, **kw: [1], asarray=lambda *a, **kw: Audio([1]))
            with patch.object(archive, 'np', numpy), patch.object(archive, 'WhisperModel', return_value=model), patch.object(archive, 'track_to_raw', return_value=Path(directory) / 'raw'), patch.object(sys, 'argv', ['archive.py', str(source)]):
                with self.assertRaisesRegex(ValueError, 'segment rendering failed'):
                    archive.main()
            self.assertEqual(output.read_text(), 'previous complete transcript')

    def test_archive_header_count_must_match_actual_written_segments(self):
        import transcribe
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'call.mkv'
            output = source.with_suffix('.transcript.md')
            output.write_text('# call.mkv\n\n0.3 minutes, 3 segments\n\n')
            result = transcribe.transcribe(Recorder(), str(source), gpu_python='/bin/true', gpu_script='ignored')
        self.assertFalse(result['ok'])
        self.assertIn('count', result['error'])

    def test_empty_pulse_server_gets_a_usable_default(self):
        with patch.dict(os.environ, {'PULSE_SERVER': '', 'XDG_RUNTIME_DIR': '/run/user/1234'}):
            code, output = Recorder().run('printf %s "$PULSE_SERVER"')
        self.assertEqual(code, 0)
        self.assertEqual(output, 'unix:/run/user/1234/pulse/native')

    def test_config_identifies_backend_without_renaming_remote_target(self):
        from test_settings import plugin_api as api
        import socket
        import platform
        for host in ('', 'audio-laptop'):
            with patch.object(api, 'resolved_source', return_value={'host': host, 'mode': 'local' if not host else 'configured', 'why': ''}), patch.object(api, 'resolved_transcription', return_value={}), patch.object(api, 'resolved_readiness', return_value={}), patch.object(Recorder, 'run', return_value=(0, 'ok')):
                result = api._config_payload()
            self.assertEqual(result['backend_machine'], {'hostname': socket.gethostname(), 'os': platform.system().lower()})
            self.assertEqual(result['host_label'], host or 'local (' + socket.gethostname() + ')')

    def test_pane_detail_names_model_and_device(self):
        from test_settings import plugin_api as api
        import asyncio
        with patch.object(api, 'resolved_transcription', return_value={'ok': True, 'python': '/p', 'model': 'tiny.en', 'device': 'cpu'}), patch.object(api, 'resolved_readiness', return_value={}):
            result = asyncio.run(api.get_config())
        self.assertIn('tiny.en', result['transcription_detail'])
        self.assertIn('cpu', result['transcription_detail'])
