"""Settings precedence: env > Settings → Plugins (config.yaml) > settings.json > defaults.

The routes module imports fastapi, which is the dashboard host's dependency, not
ours — stub it so these tests run anywhere.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))


def _stub_fastapi() -> None:
    if "fastapi" in sys.modules:
        return

    class _Router:
        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

    module = types.ModuleType("fastapi")
    module.APIRouter = _Router  # type: ignore[attr-defined]
    sys.modules["fastapi"] = module


_stub_fastapi()
with patch.dict(os.environ, {"CALLREC_AUTO_STOP_SECONDS": "0", "CALLREC_AUTO_START": "false"}):
    from dashboard import plugin_api  # noqa: E402


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "hermes"
        self.home.mkdir()
        self.settings_json = root / "settings.json"
        os.environ["HERMES_HOME"] = str(self.home)
        self._old_file = plugin_api._SETTINGS_FILE
        plugin_api._SETTINGS_FILE = self.settings_json
        for key in list(os.environ):
            if key.startswith("CALLREC_"):
                del os.environ[key]

    def tearDown(self):
        plugin_api._SETTINGS_FILE = self._old_file
        os.environ.pop("HERMES_HOME", None)
        for key in list(os.environ):
            if key.startswith("CALLREC_"):
                del os.environ[key]
        self.tmp.cleanup()

    def _write_manifest_settings(self, values: dict) -> None:
        config = {
            "plugins": {
                "enabled": ["call-recorder"],
                "entries": {"call-recorder": {"settings": values}},
            }
        }
        # JSON is valid YAML, which is all the reader needs.
        (self.home / "config.yaml").write_text(json.dumps(config))

    def test_defaults_when_nothing_is_configured(self):
        cfg = plugin_api.settings()
        self.assertEqual(cfg["recordings_dir"], "~/recordings")
        self.assertIs(cfg["auto_start"], False, "nothing records without a click")
        self.assertEqual(cfg["auto_stop_seconds"], 180)

    def test_no_config_means_local_without_any_discovery(self):
        from unittest.mock import patch
        self.assertFalse((self.home / "config.yaml").exists())
        self.assertFalse(self.settings_json.exists())
        self.assertEqual(plugin_api.settings()["host"], "")
        with patch.object(plugin_api.Recorder, "run", side_effect=AssertionError("must not probe hosts")):
            choice = plugin_api.resolved_source(force=True)
        self.assertEqual((choice["host"], choice["mode"]), ("", "local"))
        self.assertIn("Hermes runs on", choice["why"])
        self._write_manifest_settings({"host": "laptop"})
        self.assertEqual(plugin_api.resolved_source()["host"], "laptop")
        self._write_manifest_settings({"host": ""})
        self.assertEqual(plugin_api.resolved_source()["mode"], "local")

    def test_settings_page_values_win_over_defaults(self):
        self._write_manifest_settings({"host": "desktop-1", "auto_stop_seconds": 45,
                                       "auto_start": False})
        cfg = plugin_api.settings()
        self.assertEqual(cfg["host"], "desktop-1")
        self.assertEqual(cfg["auto_stop_seconds"], 45)
        self.assertIs(cfg["auto_start"], False)

    def test_settings_json_fills_gaps_but_the_page_wins(self):
        self._write_manifest_settings({"host": "from-page"})
        self.settings_json.write_text(json.dumps({"host": "from-file",
                                                  "recordings_dir": "~/calls"}))
        cfg = plugin_api.settings()
        self.assertEqual(cfg["host"], "from-page")
        self.assertEqual(cfg["recordings_dir"], "~/calls")

    def test_env_wins_over_everything(self):
        self._write_manifest_settings({"auto_stop_seconds": 45})
        os.environ["CALLREC_AUTO_STOP_SECONDS"] = "0"
        self.assertEqual(plugin_api.settings()["auto_stop_seconds"], 0)

    def test_string_booleans_from_a_form_are_coerced(self):
        self._write_manifest_settings({"auto_start": "false", "watch_seconds": "30"})
        cfg = plugin_api.settings()
        self.assertIs(cfg["auto_start"], False)
        self.assertEqual(cfg["watch_seconds"], 30)

    def test_the_two_switches_drive_the_advanced_values(self):
        self._write_manifest_settings({"transcribe": False, "stop_when_call_ends": False})
        cfg = plugin_api.settings()
        self.assertEqual(cfg["python"], "", "transcribe off must disable the interpreter")
        self.assertEqual(cfg["live_script"], "")
        self.assertEqual(cfg["auto_stop_seconds"], 0, "stop off must disable the countdown")

    def test_active_profile_config_without_home_override(self):
        root = self.home / '.hermes'
        profile = root / 'profiles' / 'work'
        profile.mkdir(parents=True)
        values = {'plugins': {'entries': {'call-recorder': {'settings': {'host': 'profile-host'}}}}}
        (profile / 'config.yaml').write_text(json.dumps(values))
        for env in ({'HERMES_PROFILE': 'work'}, {'HERMES_PROFILE_NAME': 'work'}, {}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), \
                 patch.object(plugin_api.Path, 'home', return_value=self.home):
                if not env:
                    (root / 'active_profile').write_text('work\n')
                self.assertEqual(plugin_api.manifest_settings(), {'host': 'profile-host'})

    def test_home_override_wins_over_active_profile(self):
        self._write_manifest_settings({'host': 'explicit-home'})
        with patch.dict(os.environ, {'HERMES_PROFILE': 'work'}):
            self.assertEqual(plugin_api.manifest_settings(), {'host': 'explicit-home'})

    def test_default_home_without_profile(self):
        root = self.home / '.hermes'
        root.mkdir()
        (root / 'config.yaml').write_text(json.dumps({'plugins': {'entries': {'call-recorder': {'settings': {'host': 'default-host'}}}}}))
        for profile in ('', 'default'):
            with self.subTest(profile=profile), patch.dict(os.environ, {'HERMES_PROFILE': profile}, clear=True), \
                 patch.object(plugin_api.Path, 'home', return_value=self.home):
                self.assertEqual(plugin_api.manifest_settings(), {'host': 'default-host'})

    def test_unreadable_config_is_not_fatal(self):
        (self.home / "config.yaml").write_text("plugins: [this is not a mapping\n")
        self.assertEqual(plugin_api.settings()["recordings_dir"], "~/recordings")


class ManifestSchemaTests(unittest.TestCase):
    def test_defaults_never_auto_start(self):
        """Recording someone else's call without a click is not a default."""
        self.assertIs(plugin_api.settings()["auto_start"], False)

    def test_every_schema_key_is_a_setting_the_plugin_reads(self):
        """A schema key nobody reads is a form field that does nothing."""
        import yaml

        manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text())
        declared = set((manifest.get("config_schema") or {}).keys())
        self.assertFalse(declared, "v2 client-local UI requires no backend machine/settings form")
        self.assertEqual(manifest["version"], "2.0.0")
        self.assertIs(plugin_api.DEFAULTS["auto_start"], False)


if __name__ == "__main__":
    unittest.main()
