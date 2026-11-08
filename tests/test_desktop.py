"""Exercise desktop handlers through the registered plugin contributions."""
from pathlib import Path
import subprocess
import unittest


class DesktopTests(unittest.TestCase):
    def test_sdk_contributions(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ['node', '--experimental-vm-modules', '--no-warnings', 'tests/desktop.test.mjs'],
            cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Desktop behavior checks passed.', result.stdout)
