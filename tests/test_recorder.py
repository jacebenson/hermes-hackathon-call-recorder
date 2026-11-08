"""Unit tests for the recorder core — no PipeWire, no SSH, no GPU needed.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import recorder as recorder_mod  # noqa: E402
from recorder import (  # noqa: E402
    Recorder,
    is_call_app,
    parse_sink_inputs,
    parse_sinks,
    parse_source_outputs,
    parse_transcript,
    pick_monitor,
)

# Trimmed from real `pactl` output on a PipeWire box mid-call.
SINK_INPUTS = """Sink Input #865
\tMute: no
\tVolume: front-left: 65536 / 100% / 0.00 dB
\tSink: 233
\t\tapplication.name = "Chromium"

Sink Input #870
\tMute: no
\tSink: 57
\t\tapplication.name = "mpv"
"""

SINKS = "233\teasyeffects_sink\tPipeWire\tfloat32le 2ch 48000Hz\tSUSPENDED\n" \
        "57\talsa_output.usb-headset\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"

SOURCE_OUTPUTS = """Source Output #986
\tMute: no
\t\tapplication.name = "Chromium input"

Source Output #999
\tMute: no
\t\tapplication.name = "Lavf63.1.102"
"""


class FakeRecorder(Recorder):
    """Recorder with the shell replaced by fixtures."""

    def __init__(self, source_outputs=SOURCE_OUTPUTS, **kwargs):
        super().__init__(state_file="/nonexistent/callrec-test-state.json", **kwargs)
        self.source_outputs = source_outputs
        self._state = {}
        self.commands = []
        self.active = False
        self.started = 0

    def ok(self, command: str, timeout: int = 30) -> str:
        if "sink-inputs" in command:
            return SINK_INPUTS
        if "short sinks" in command:
            return SINKS
        if "source-outputs" in command:
            return self.source_outputs
        if "get-default-sink" in command:
            return "alsa_output.usb-headset\n"
        if "get-default-source" in command:
            return "easyeffects_source\n"
        return ""

    def run(self, command: str, timeout: int = 30):
        self.commands.append(command)
        if "is-active" in command:
            return 0, ("active" if self.active else "inactive")
        return 0, ""

    def state(self) -> dict:
        return dict(self._state)

    def save_state(self, state: dict) -> None:
        self._state = dict(state)

    def start(self, live: bool = False) -> dict:
        self.started += 1
        self.active = True
        self._state.update({"unit": "callrec-test", "file": "/tmp/call-test.mkv",
                            "started": time.time()})
        return {"ok": True, "recording": True}

    def stop(self) -> dict:
        self.active = False
        self._state["unit"] = None
        return {"ok": True, "recording": False, "file": "/tmp/call-test.mkv", "duration": 61.5}


class ParserTests(unittest.TestCase):
    def test_sink_inputs_and_sinks(self):
        inputs = parse_sink_inputs(SINK_INPUTS)
        self.assertEqual([i["app"] for i in inputs], ["Chromium", "mpv"])
        self.assertEqual(inputs[0]["sink"], "233")
        self.assertEqual(parse_sinks(SINKS)["233"], "easyeffects_sink")

    def test_monitor_prefers_the_call_app_sink(self):
        inputs = parse_sink_inputs(SINK_INPUTS)
        sinks = parse_sinks(SINKS)
        # default sink is the headset, but the call plays into easyeffects_sink
        self.assertEqual(pick_monitor(inputs, sinks, "alsa_output.usb-headset"),
                         "easyeffects_sink.monitor")

    def test_monitor_falls_back_to_default_sink(self):
        self.assertEqual(pick_monitor([], {}, "alsa_output.usb-headset"),
                         "alsa_output.usb-headset.monitor")
        with self.assertRaises(RuntimeError):
            pick_monitor([], {}, "")

    def test_call_apps(self):
        for name in ("Chromium", "Google Chrome", "firefox", "Zoom", "Microsoft Teams"):
            self.assertTrue(is_call_app(name), name)
        for name in ("mpv", "spotify", "Lavf63.1.102", "easyeffects"):
            self.assertFalse(is_call_app(name), name)

    def test_source_outputs(self):
        self.assertEqual(parse_source_outputs(SOURCE_OUTPUTS), ["Chromium input", "Lavf63.1.102"])

    def test_transcript_line(self):
        self.assertEqual(parse_transcript("[19:47:09] them> hello there"),
                         {"time": "19:47:09", "speaker": "them", "text": "hello there"})
        self.assertIsNone(parse_transcript("=== live started ==="))


class CallDetectionTests(unittest.TestCase):
    def test_call_requires_mic_and_playback(self):
        rec = FakeRecorder()
        state = rec.call_state()
        self.assertTrue(state["in_call"])
        self.assertIn("Chromium", state["call_apps"])

    def test_browser_playing_a_video_is_not_a_call(self):
        # Chromium plays audio but nothing captures the mic: this was a YouTube tab.
        rec = FakeRecorder(source_outputs="")
        self.assertFalse(rec.call_state()["in_call"])
        self.assertEqual(rec.call_state()["playing"], ["Chromium", "mpv"])


class AutomationTests(unittest.TestCase):
    def test_auto_start_on_a_call(self):
        rec = FakeRecorder(auto_start=True)
        decision = rec.tick()
        self.assertEqual(decision["action"], "started")
        self.assertEqual(rec.started, 1)

    def test_no_auto_start_when_disabled(self):
        rec = FakeRecorder(auto_start=False)
        self.assertEqual(rec.tick()["action"], "call_detected")
        self.assertEqual(rec.started, 0)

    def test_stop_is_delayed_not_immediate(self):
        rec = FakeRecorder(auto_stop_seconds=180)
        rec.start()
        rec.source_outputs = ""       # call window closed
        first = rec.tick()
        self.assertEqual(first["action"], "pending_stop")
        self.assertGreater(first["remaining"], 170)
        self.assertTrue(rec.active, "must keep recording during the countdown")

    def test_stops_after_the_grace_period(self):
        rec = FakeRecorder(auto_stop_seconds=180)
        rec.start()
        rec.source_outputs = ""
        rec.tick()                                    # stamps ended_at
        rec._state["ended_at"] = time.time() - 181    # pretend 3 minutes passed
        decision = rec.tick()
        self.assertEqual(decision["action"], "stopped")
        self.assertFalse(rec.active)

    def test_keep_cancels_the_countdown(self):
        rec = FakeRecorder(auto_stop_seconds=1)
        rec.start()
        rec.source_outputs = ""
        rec.tick()
        rec.keep()
        rec._state["ended_at"] = time.time() - 600
        self.assertEqual(rec.tick()["action"], "kept")
        self.assertTrue(rec.active)

    def test_call_returning_cancels_the_countdown(self):
        rec = FakeRecorder(auto_stop_seconds=180)
        rec.start()
        rec._state["ended_at"] = time.time() - 30
        self.assertEqual(rec.tick()["action"], "recording")
        self.assertNotIn("ended_at", rec._state)


class StatusTests(unittest.TestCase):
    def test_status_reports_countdown(self):
        rec = FakeRecorder(auto_stop_seconds=180)
        rec.start()
        rec._state["ended_at"] = time.time() - 60
        status = rec.status()
        self.assertTrue(status["recording"])
        self.assertEqual(status["auto_stop_seconds"], 180)
        self.assertLessEqual(status["pending_stop"], 120)
        self.assertGreater(status["pending_stop"], 100)


class DetectionTests(unittest.TestCase):
    """Which machine gets recorded, and can we explain the choice."""

    def test_a_configured_machine_wins(self):
        choice = recorder_mod.choose_source("desktop-1")
        self.assertEqual(choice["host"], "desktop-1")
        self.assertEqual(choice["mode"], "configured")

    def test_transcription_paths_are_detected_not_typed(self):
        rec = Recorder(host="desk", state_file="/nonexistent/x.json")
        output = 'PY=/home/u/whisper/.venv/bin/python\nLIVE=/home/u/whisper/live.py\n'
        original = Recorder.run
        Recorder.run = lambda self, cmd, timeout=30: (0, output)
        try:
            found = recorder_mod.detect_transcription("desk")
        finally:
            Recorder.run = original
        self.assertTrue(found["ok"])
        self.assertEqual(found["python"], "/home/u/whisper/.venv/bin/python")
        self.assertEqual(found["live_script"], "/home/u/whisper/live.py")

    def test_half_a_runtime_is_not_transcription(self):
        """faster_whisper without the scripts records fine and transcribes nothing."""
        original = Recorder.run
        Recorder.run = lambda self, cmd, timeout=30: (0, "PY=/usr/bin/python3\n")
        try:
            found = recorder_mod.detect_transcription(None)
        finally:
            Recorder.run = original
        self.assertFalse(found["ok"])


    def test_tilde_expands_on_the_target_not_literally(self):
        """systemd-run does not expand `~`; a literal one created a directory named `~`."""
        rec = FakeRecorder()
        rec.recordings_dir = "~/recordings"
        rec._home_dir = "/home/someone"
        self.assertEqual(rec.rec_dir(), "/home/someone/recordings")

    def test_an_absolute_directory_is_left_alone(self):
        rec = FakeRecorder()
        rec.recordings_dir = "/srv/calls"
        self.assertEqual(rec.rec_dir(), "/srv/calls")


    def test_delete_refuses_anything_that_is_not_one_of_ours(self):
        rec = FakeRecorder()
        rec.recordings_dir = "/srv/calls"
        for bad in ("/etc/passwd", "/srv/calls/notes.txt", "/srv/calls/../call-1.mkv",
                    "/srv/calls/sub/call-1.mkv", ""):
            self.assertFalse(rec.delete_recording(bad)["ok"], bad)

    def test_delete_removes_the_file_and_its_transcript(self):
        rec = FakeRecorder()
        rec.recordings_dir = "/srv/calls"
        seen = {}
        rec.commands = []

        def fake_run(command, timeout=30):
            seen["command"] = command
            return 0, "deleted"

        original = rec.run
        rec.run = fake_run
        try:
            result = rec.delete_recording("/srv/calls/call-20260101-120000.mkv")
        finally:
            rec.run = original
        self.assertTrue(result["ok"])
        self.assertIn("/srv/calls/call-20260101-120000.mkv", seen["command"])
        self.assertIn("call-20260101-120000.transcript.md", seen["command"])

    def test_ssh_commands_are_quoted_once(self):
        """ssh joins argv with spaces; an unquoted multi-word command word-splits remotely."""
        rec = Recorder(host="desk", state_file="/nonexistent/x.json")
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        original = recorder_mod.subprocess.run
        recorder_mod.subprocess.run = fake_run
        try:
            rec.run("pactl get-default-sink && echo done")
        finally:
            recorder_mod.subprocess.run = original
        command = captured["argv"][-1]
        # ssh joins argv and re-parses remotely: the command must arrive as one
        # quoted argument, or the remote shell word-splits the pipeline.
        self.assertTrue(command.startswith("bash -lc "))
        inner = command[len("bash -lc "):]
        self.assertEqual(shlex.split(inner), ["pactl get-default-sink && echo done"])



if __name__ == "__main__":
    unittest.main()
