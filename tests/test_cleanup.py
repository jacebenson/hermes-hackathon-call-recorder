"""Shipped helper safety and supported entry points."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class CleanupTests(unittest.TestCase):
    def test_only_the_supported_archive_entry_point_remains(self):
        from recorder import Recorder
        self.assertFalse(hasattr(Recorder, "transcribe"))
        self.assertTrue(callable(Recorder.duration))

    def test_live_helper_has_no_listener_or_port_setting(self):
        source = (ROOT / 'scripts/live.py').read_text()
        tree = ast.parse(source)
        self.assertNotIn('http.server', source)
        self.assertNotIn('LIVE_PORT', source)
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        self.assertIsInstance(main.body[-1], ast.While)
        self.assertIn('fh.write(line + "\\n")', source)
        self.assertIn('target=writer', source)
        self.assertIn('queue_in.put', source)
