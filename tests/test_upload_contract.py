"""Run in a fresh process so the legacy FastAPI stub cannot weaken API tests."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class UploadContractTests(unittest.TestCase):
    def test_real_multipart_auth_validation_persistence(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, 'HERMES_HOME': home, 'CALLREC_AUTO_STOP_SECONDS': '0',
                   'CALLREC_AUTO_START': 'false'}
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('upload_contract.py'))],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
