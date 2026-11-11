"""Distribution boundary, inspected without importing legacy plugin code."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DistributionTests(unittest.TestCase):
    def test_distribution_has_no_agent_payload_or_backend_dependencies(self):
        package = ROOT / 'packages/call-recorder'
        self.assertEqual({p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}, {'plugin.yaml', 'desktop/plugin.js'})
        self.assertEqual((package / 'desktop/plugin.js').read_bytes(), (ROOT / 'desktop/plugin.js').read_bytes())
        bundle = (package / 'desktop/plugin.js').read_text()
        self.assertNotIn('plugin_api.py', bundle)
        self.assertNotIn('ctx.rest(', bundle)
        self.assertNotIn('getAll()', bundle)
        self.assertFalse((package / '__init__.py').exists())
        self.assertFalse((package / 'dashboard').exists())

    def test_clean_manual_install_copies_only_reviewed_desktop_artifact(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            subprocess.run(['sh', 'scripts/install-desktop.sh', tmp], cwd=ROOT, check=True, capture_output=True)
            files = list(Path(tmp).rglob('*'))
            self.assertEqual([p.relative_to(tmp).as_posix() for p in files if p.is_file()], ['desktop-plugins/call-recorder/plugin.js'])
            self.assertEqual((Path(tmp) / 'desktop-plugins/call-recorder/plugin.js').read_bytes(), (ROOT / 'packages/call-recorder/desktop/plugin.js').read_bytes())

if __name__ == '__main__':
    unittest.main()
