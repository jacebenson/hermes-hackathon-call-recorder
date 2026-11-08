"""Capture-machine routes: validation, durable selection and recent machines."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_settings import plugin_api as api


class CaptureHostTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'settings.json'
        for p in (patch.object(api, '_SETTINGS_FILE', self.path),
                  patch.object(api, 'manifest_settings', return_value={'host': 'framework'}),
                  patch.dict(os.environ, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def get(self):
        return asyncio.run(api.get_capture_host())

    def choose(self, host):
        return asyncio.run(api.post_capture_host({'host': host}))

    def test_read_and_persist_trimmed_choice_without_changing_other_settings(self):
        self.path.write_text(json.dumps({'transcribe': False, 'custom': 42}))
        self.assertEqual(self.get()['host'], 'framework')
        result = self.choose('  audio-laptop.example  ')
        self.assertTrue(result['ok'])
        self.assertEqual(self.get()['host'], 'audio-laptop.example')
        self.assertEqual(api.settings()['host'], 'audio-laptop.example')
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['host'], 'audio-laptop.example')
        self.assertFalse(saved['transcribe'])
        self.assertEqual(saved['custom'], 42)
        # A subsequent explicit change in Settings still takes effect.
        with patch.object(api, 'manifest_settings', return_value={'host': 'from-settings'}):
            self.assertEqual(api.settings()['host'], 'from-settings')

    def test_local_clears_the_setting_and_labels_the_backend(self):
        for host in ('local', ''):
            self.assertTrue(self.choose(host)['ok'])
            self.assertEqual(json.loads(self.path.read_text())['host'], '')
            self.assertEqual(api.settings()['host'], '')
            self.assertEqual(self.get()['host_label'], f'local ({api.socket.gethostname()})')

    def test_invalid_values_do_not_write(self):
        for host in (None, 23, [], 'bad\nhost', '\nframework', 'bad\x7f', 'two words',
                     '-option', 'user@host', 'host;true', 'a..b', 'a' * 64, 'a' * 254):
            with self.subTest(host=host):
                result = self.choose(host)
                self.assertFalse(result['ok'])
                self.assertTrue(result['error'])
                self.assertFalse(self.path.exists())
        self.assertFalse(asyncio.run(api.post_capture_host({}))['ok'])

    def test_recent_machines_are_durable_unique_most_recent_first_and_bounded(self):
        self.choose('laptop')
        self.choose('framework')
        self.choose('laptop')
        self.choose('local')
        self.assertEqual(self.get()['recent_hosts'], ['laptop', 'framework'])
        for n in range(12):
            self.choose(f'machine-{n}')
        self.assertEqual(self.get()['recent_hosts'],
                         ['machine-11', 'machine-10', 'machine-9', 'machine-8', 'machine-7'])
        self.assertEqual(json.loads(self.path.read_text())['recent_hosts'], self.get()['recent_hosts'])

    def test_failed_atomic_write_preserves_choice_and_history(self):
        self.choose('laptop')
        before = self.path.read_bytes()
        with patch.object(api.os, 'replace', side_effect=OSError('disk full')):
            result = self.choose('new-machine')
        self.assertFalse(result['ok'])
        self.assertIn('disk full', result['error'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.get()['host'], 'laptop')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_env_override_and_corrupt_file_report_errors_instead_of_false_success(self):
        with patch.dict(os.environ, {'CALLREC_HOST': 'pinned'}):
            self.assertFalse(self.choose('laptop')['ok'])
            self.assertEqual(self.get()['host'], 'pinned')
            self.assertFalse(self.path.exists())
        for content in ('{broken', '[]'):
            self.path.write_text(content)
            self.assertFalse(self.choose('laptop')['ok'])
            self.assertEqual(self.path.read_text(), content)
