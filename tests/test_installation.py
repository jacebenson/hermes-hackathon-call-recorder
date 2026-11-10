"""Disposable install and exact file-path import; never uses a live Hermes home."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def test_clean_desktop_only_and_unified_legacy_import_is_inert(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            home = Path(tmp)
            subprocess.run(['sh', 'scripts/install-desktop.sh', str(home)], cwd=ROOT, check=True, capture_output=True)
            disk = home / 'desktop-plugins/call-recorder/plugin.js'
            self.assertEqual(disk.read_bytes(), (ROOT / 'desktop/plugin.js').read_bytes())
            self.assertEqual([p.relative_to(home).as_posix() for p in home.rglob('*') if p.is_file()], ['desktop-plugins/call-recorder/plugin.js'])
            package = home / 'plugins/call-recorder'
            package.mkdir(parents=True)
            for source in ROOT.glob('*.py'):
                shutil.copy2(source, package / source.name)
            shutil.copy2(ROOT / 'plugin.yaml', package / 'plugin.yaml')
            for name in ('dashboard', 'scripts', 'desktop'):
                shutil.copytree(ROOT / name, package / name, ignore=shutil.ignore_patterns('__pycache__'))
            code = '''
import importlib.util, sys
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1])
with patch('threading.Thread.start',side_effect=AssertionError('hidden watcher')), patch('subprocess.Popen',side_effect=AssertionError('hidden helper/SSH')):
    spec=importlib.util.spec_from_file_location('isolated_api',root/'dashboard/plugin_api.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.router.routes
    for name in ('recorder','platforms','recording_library','transcribe'):
        assert Path(sys.modules[name].__file__).is_relative_to(root), name
    spec=importlib.util.spec_from_file_location('isolated_registration',root/'__init__.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.register(object())
print('Installed runtime paths only; no watcher/helper/SSH')
'''
            env = {**os.environ, 'HERMES_HOME': str(home)}
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, '-c', code, str(package)], cwd=home, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Installed runtime paths only', result.stdout)
            self.assertFalse((home / 'recordings').exists())
            self.assertFalse((package / 'settings.json').exists())


if __name__ == '__main__':
    unittest.main()
