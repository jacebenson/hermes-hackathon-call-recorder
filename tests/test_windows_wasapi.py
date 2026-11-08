"""The Windows launch decisions, without a Windows machine in the loop."""

import pathlib
import sys
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import capture_windows_wasapi as wasapi


class FakeRec:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.commands = []
        self.host = "fake-win"
        self.recordings_dir = "~/recordings"

    def run(self, command, timeout=30):
        self.commands.append(command)
        for needle, response in self.responses.items():
            if needle in command:
                return response
        return 0, ""

    def save_state(self, state):
        self.state_value = state

    def state(self):
        return getattr(self, "state_value", {})


class TestUploads(unittest.TestCase):
    def test_install_and_text_preserve_bytes_chunks_and_cleanup(self):
        import base64
        for text in (None, 'hello café' * 1000):
            rec = FakeRec({'USERPROFILE': (0, r'C:\Users\test'), 'Test-Path': (0, 'True')})
            result = wasapi.install(rec) if text is None else wasapi._write_text(rec, r'C:\calls\launch.ps1', text)
            self.assertTrue(result['ok'])
            self.assertIn('script' if text is None else 'path', result)
            pieces = [c.split("-Value '", 1)[1].split("'", 1)[0]
                      for c in rec.commands if 'Add-Content' in c]
            self.assertTrue(all(len(p) <= 1500 for p in pieces))
            expected = ((pathlib.Path(wasapi.__file__).parent / 'scripts' / wasapi.SCRIPT_NAME).read_bytes()
                        if text is None else text.encode('utf-8-sig'))
            self.assertEqual(base64.b64decode(''.join(pieces)), expected)
            self.assertTrue(any('WriteAllBytes' in c and 'Remove-Item -Force' in c for c in rec.commands))
            self.assertTrue(any('.b64' in c and 'SilentlyContinue' in c for c in rec.commands))

    def test_failed_checks_keep_caller_contracts(self):
        self.assertIn('error', wasapi.install(FakeRec()))
        self.assertEqual(wasapi._write_text(FakeRec(), r'C:\calls\x.ps1', 'x'),
                         {'ok': False, 'path': r'C:\calls\x.ps1'})


class TestInteractiveSession(unittest.TestCase):
    def test_an_active_console_session_is_ready(self):
        rec = FakeRec({"quser": (0, "USERNAME  SESSIONNAME  ID  STATE\n jaceb  console  1  Active")})
        self.assertTrue(wasapi.interactive_session(rec)["ok"])

    def test_no_active_session_says_so_instead_of_recording_silence(self):
        rec = FakeRec({"quser": (0, "USERNAME  SESSIONNAME  ID  STATE\n services  0  Disc")})
        result = wasapi.interactive_session(rec)
        self.assertFalse(result["ok"])
        self.assertIn("nobody is logged in", result["error"])
        self.assertIn("Log in", result["fix"])

    def test_start_refuses_before_touching_the_machine(self):
        rec = FakeRec({"import sys": (0, "3"),
                       "import pyaudiowpatch": (0, "ok"),
                       "quser": (0, "services  0  Disc")})
        with unittest.mock.patch("platforms.readiness",
                                 return_value={"state": "ready", "backend": "wasapi"}):
            result = wasapi.start(rec)
        self.assertFalse(result["ok"])
        self.assertIn("nobody is logged in", result["error"])
        self.assertFalse([c for c in rec.commands if "Start-ScheduledTask" in c])


class TestLaunchCommand(unittest.TestCase):
    def test_the_task_is_interactive_so_it_lands_in_the_desktop(self):
        script = wasapi.LAUNCH_PS1
        self.assertIn("-LogonType Interactive", script)
        self.assertIn("WindowsIdentity", script)  # the SID lookup that actually resolves

    def test_the_child_is_the_helper_with_our_paths(self):
        self.assertIn("--out-dir", wasapi.LAUNCH_PS1)
        self.assertIn("--stop-file", wasapi.LAUNCH_PS1)
        self.assertIn("--status-file", wasapi.LAUNCH_PS1)

    def test_the_plugin_names_the_file_not_the_helper(self):
        # One clock for both platforms: the name arrives from the plugin, in UTC.
        self.assertIn("--name", wasapi.LAUNCH_PS1)
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "scripts" / "wasapi_capture.py").read_text()
        self.assertIn("--name", source)
        self.assertIn("args.name or", source)


if __name__ == "__main__":
    unittest.main()
