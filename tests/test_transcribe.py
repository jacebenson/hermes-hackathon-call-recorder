"""One transcription engine: which one runs, and what it does when it cannot."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from recorder import Recorder

import transcribe as tx


class FakeRecorder:
    def __init__(self, run_code=0, segments=3):
        self.commands = []
        self.run_code = run_code
        self.segments = segments

    def run(self, command, timeout=30):
        self.commands.append(command)
        if command.startswith("grep -c"):
            return 0, str(self.segments)
        return self.run_code, "boom" if self.run_code else ""


class EngineChoiceTests(unittest.TestCase):
    def test_whisper_on_the_recording_machine_is_the_engine(self):
        self.assertEqual(tx.detect_engine(True), tx.ENGINE_GPU)

    def test_nothing_available(self):
        self.assertEqual(tx.detect_engine(False), tx.ENGINE_NONE)


class TranscribeTests(unittest.TestCase):
    def test_no_engine_explains_itself_instead_of_failing_silently(self):
        rec = FakeRecorder()
        result = tx.transcribe(rec, "/rec/rec.mkv", gpu_python="", gpu_script="")
        self.assertFalse(result["ok"])
        self.assertIn("no Whisper", result["error"])
        self.assertEqual(rec.commands, [])

    def test_the_environment_travels_with_the_command(self):
        rec = FakeRecorder()
        tx.transcribe(rec, "/rec/rec.mkv", gpu_python="/venv/bin/python",
                      gpu_script="/srv/archive.py",
                      gpu_env={"WHISPER_MODEL": "/models/small.en", "WHISPER_DEVICE": "cuda"})
        command = rec.commands[0]
        self.assertIn("WHISPER_MODEL=/models/small.en", command)
        self.assertIn("WHISPER_DEVICE=cuda", command)
        self.assertIn("/venv/bin/python /srv/archive.py /rec/rec.mkv", command)

    def test_an_empty_environment_variable_is_not_written_out(self):
        rec = FakeRecorder()
        tx.transcribe(rec, "/rec/rec.mkv", gpu_python="p", gpu_script="s",
                      gpu_env={"LD_LIBRARY_PATH": "", "WHISPER_MODEL": "/m"})
        self.assertNotIn("LD_LIBRARY_PATH=", rec.commands[0])

    def test_segments_are_counted_on_the_recording_machine(self):
        rec = FakeRecorder(segments=6)
        result = tx.transcribe(rec, "/rec/rec.mkv", gpu_python="p", gpu_script="s")
        self.assertTrue(result["ok"])
        self.assertEqual(result["segments"], 6)
        self.assertTrue(any(command.startswith("grep -c") for command in rec.commands))

    def test_archive_success_without_a_written_file_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder()
            source = str(pathlib.Path(directory) / 'call.mkv')
            result = tx.transcribe(rec, source, gpu_python='/bin/true', gpu_script='ignored')
            self.assertFalse(result['ok'])
            self.assertIn('transcript', result['error'].lower())

    def test_archive_verifies_and_counts_the_actual_written_markdown(self):
        with tempfile.TemporaryDirectory(prefix='archive space ') as directory:
            source = pathlib.Path(directory) / 'call.mkv'
            script = pathlib.Path(directory) / 'archive stub.py'
            script.write_text("import pathlib, sys\np = pathlib.Path(sys.argv[1]).with_suffix('.transcript.md')\np.write_text('**[00:01] you:** one\\n\\n**[00:02] them:** two\\n')\n")
            rec = Recorder()
            result = tx.transcribe(rec, str(source), gpu_python=sys.executable, gpu_script=str(script))
            self.assertTrue(result['ok'])
            self.assertEqual(result['engine'], 'gpu')
            self.assertEqual(result['segments'], 2)
            self.assertTrue(source.with_suffix('.transcript.md').exists())

    def test_a_failed_run_returns_the_error_rather_than_a_guess(self):
        rec = FakeRecorder(run_code=1)
        result = tx.transcribe(rec, "/rec/rec.mkv", gpu_python="p", gpu_script="s")
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


if __name__ == "__main__":
    unittest.main()
