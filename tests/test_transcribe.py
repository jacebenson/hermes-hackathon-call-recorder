"""The two-engine transcription path: which engine, and the shape of its output."""

from __future__ import annotations

import base64
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import transcribe as tx


class TinyRecorder:
    """Stands in for a Recorder: hands back a one-byte 'wav' for every extract."""

    def __init__(self):
        self.commands = []

    def run(self, command, timeout=30):
        self.commands.append(command)
        if command.startswith("base64"):
            return 0, base64.b64encode(b"\x00\x01").decode()
        # The engine ships the finished markdown to the recording machine; emulate that
        # write locally so the test can read the result back.
        marker = "base64 -d > "
        if marker in command:
            payload = command.split("printf %s ", 1)[1].split(" | ", 1)[0].strip("'")
            target = command.split(marker, 1)[1].strip().strip("'")
            pathlib.Path(target).write_bytes(base64.b64decode(payload))
        return 0, ""


class EngineChoiceTests(unittest.TestCase):
    def test_gpu_wins_when_both_are_available(self):
        self.assertEqual(tx.detect_engine(None, True, True), tx.ENGINE_GPU)

    def test_hermes_is_the_fallback(self):
        self.assertEqual(tx.detect_engine(None, False, True), tx.ENGINE_HERMES)

    def test_nothing_available(self):
        self.assertIsNone(tx.detect_engine(None, False, False))


class HermesEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmp.name) / "rec.transcript.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_track_and_chunk_becomes_a_labelled_line(self):
        rec = TinyRecorder()
        original = tx._hermes_transcribe_file
        calls = []

        def fake(path):
            calls.append(path)
            return "hello there"

        tx._hermes_transcribe_file = fake
        try:
            result = tx.transcribe_with_hermes(rec, "/rec/rec.mkv", duration=240, out_path=self.out)
        finally:
            tx._hermes_transcribe_file = original

        text = self.out.read_text()
        self.assertTrue(result["ok"])
        # 240s at 120s per chunk = two chunks, both tracks = four calls
        self.assertEqual(len(calls), 4)
        self.assertIn("[00:00] them:", text)
        self.assertIn("[00:00] you:", text)
        self.assertIn("[02:00] them:", text)
        self.assertIn("[02:00] you:", text)

    def test_silence_writes_no_line(self):
        rec = TinyRecorder()
        original = tx._hermes_transcribe_file
        tx._hermes_transcribe_file = lambda path: ""
        try:
            tx.transcribe_with_hermes(rec, "/rec/rec.mkv", duration=30, out_path=self.out)
        finally:
            tx._hermes_transcribe_file = original
        self.assertNotIn("them:", self.out.read_text())

    def test_a_failed_chunk_does_not_lose_the_rest(self):
        rec = TinyRecorder()
        original = tx._hermes_transcribe_file
        state = {"n": 0}

        def flaky(path):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("provider hiccup")
            return "still here"

        tx._hermes_transcribe_file = flaky
        try:
            tx.transcribe_with_hermes(rec, "/rec/rec.mkv", duration=240, out_path=self.out)
        finally:
            tx._hermes_transcribe_file = original
        text = self.out.read_text()
        self.assertIn("transcription error: provider hiccup", text)
        self.assertIn("still here", text)


if __name__ == "__main__":
    unittest.main()
