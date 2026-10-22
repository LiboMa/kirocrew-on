"""Settings precedence: defaults < TOML file < OCTOPUS_* environment < explicit overrides."""
from pathlib import Path
import tempfile
import unittest

from octopus.config import ConfigError, load


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.env = {"HOME": str(self.home)}

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, text):
        path = self.root / "etc" / "octopus.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return path

    def test_defaults_are_relative_to_cwd_and_home(self):
        s = load(environ=self.env, cwd=self.cwd)
        self.assertEqual(s.data_dir, self.cwd / "state/platform")
        self.assertEqual(s.worker_data_dir, self.cwd / "state/worker")
        self.assertEqual(s.crew_home, self.home / ".kiro/crew")
        self.assertEqual(s.kiro_home, self.home / ".kiro")
        self.assertEqual((s.api_port, s.worker_port, s.gateway_port), (8918, 8928, 5476))
        self.assertEqual(s.engine_workers, 4)

    def test_file_relative_paths_resolve_against_file_directory(self):
        path = self.write('data_dir = "data/platform"\nworker_data_dir = "/srv/worker"\n')
        s = load(path, environ=self.env, cwd=self.cwd)
        self.assertEqual(s.data_dir, path.parent / "data/platform")
        self.assertEqual(s.worker_data_dir, Path("/srv/worker"))

    def test_environment_overrides_file_and_resolves_against_cwd(self):
        path = self.write('data_dir = "data/platform"\napi_port = 9000\n')
        env = {**self.env, "OCTOPUS_DATA_DIR": "env/platform", "OCTOPUS_ALLOWED_HOSTS": "localhost:18918, ops.example:443"}
        s = load(path, environ=env, cwd=self.cwd)
        self.assertEqual(s.data_dir, self.cwd / "env/platform")
        self.assertEqual(s.api_port, 9000)
        self.assertEqual(s.allowed_hosts, ("localhost:18918", "ops.example:443"))

    def test_overrides_win_and_none_is_ignored(self):
        s = load(environ={**self.env, "OCTOPUS_API_PORT": "9001"}, overrides={"api_port": 9002, "data_dir": None},
                 cwd=self.cwd)
        self.assertEqual(s.api_port, 9002)
        self.assertEqual(s.data_dir, self.cwd / "state/platform")

    def test_config_file_from_environment(self):
        path = self.write('worker_port = 9100\n')
        s = load(environ={**self.env, "OCTOPUS_CONFIG": str(path)}, cwd=self.cwd)
        self.assertEqual(s.worker_port, 9100)

    def test_tools_section_and_environment(self):
        path = self.write('[tools]\nclaude = "/opt/tools/claude"\n')
        s = load(path, environ={**self.env, "OCTOPUS_TOOL_CODEX": "/opt/tools/codex"}, cwd=self.cwd)
        self.assertEqual(s.tools, {"claude": Path("/opt/tools/claude"), "codex": Path("/opt/tools/codex")})

    def test_crew_yolo_is_on_unless_turned_off(self):
        self.assertIs(load(environ=self.env, cwd=self.cwd).crew_yolo, True)
        self.assertIs(load(self.write("crew_yolo = false\n"), environ=self.env, cwd=self.cwd).crew_yolo, False)
        self.assertIs(load(environ={**self.env, "OCTOPUS_CREW_YOLO": "false"}, cwd=self.cwd).crew_yolo, False)
        self.assertIs(load(environ={**self.env, "OCTOPUS_CREW_YOLO": "1"}, cwd=self.cwd).crew_yolo, True)
        with self.assertRaises(ConfigError):
            load(self.write('crew_yolo = "maybe"\n'), environ=self.env, cwd=self.cwd)

    def test_invalid_values_are_rejected(self):
        for text in ('api_port = 0\n', 'unknown_key = 1\n', 'worker_isolation = "shared"\n',
                     'engine_workers = 99\n', '[tools]\ngemini = "/x"\n', 'allowed_hosts = ["bad host"]\n'):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load(self.write(text), environ=self.env, cwd=self.cwd)


    def test_unrelated_octopus_environment_variables_are_ignored(self):
        env = {**self.env, "OCTOPUS_ADMIN_PASSWORD": "secret-value-123", "OCTOPUS_SOMETHING_ELSE": "x",
               "OCTOPUS_API_PORT": "9300"}
        s = load(environ=env, cwd=self.cwd)
        self.assertEqual(s.api_port, 9300)
        self.assertNotIn("secret-value-123", repr(s))

    def test_disabled_tools_default_to_opencode_and_are_validated(self):
        self.assertEqual(load(environ=self.env, cwd=self.cwd).disabled_tools, ("opencode",))   # K01
        self.assertEqual(load(self.write('disabled_tools = []\n'), environ=self.env, cwd=self.cwd).disabled_tools, ())
        self.assertEqual(load(self.write('disabled_tools = ["codex"]\n'), environ=self.env, cwd=self.cwd).disabled_tools, ("codex",))
        with self.assertRaises(ConfigError):
            load(self.write('disabled_tools = ["gemini"]\n'), environ=self.env, cwd=self.cwd)

    def test_default_admission_and_acceptance_are_auto_and_validated(self):
        # D68 (owner 2026-10-22): once a workflow is decided no one is asked again; a platform may still opt back in (D55)
        settings = load(environ=self.env, cwd=self.cwd)
        self.assertEqual((settings.default_admission, settings.default_acceptance), ("auto", "auto"))
        chosen = load(self.write('default_admission = "confirm"\ndefault_acceptance = "confirm"\n'), environ=self.env, cwd=self.cwd)
        self.assertEqual((chosen.default_admission, chosen.default_acceptance), ("confirm", "confirm"))
        for bad in ('default_admission = "later"\n', 'default_acceptance = "later"\n'):
            with self.assertRaises(ConfigError):
                load(self.write(bad), environ=self.env, cwd=self.cwd)

    def test_approval_mode_is_optional_and_validated(self):
        self.assertEqual(load(environ=self.env, cwd=self.cwd).approval_mode, "")
        self.assertEqual(load(self.write('approval_mode = "auto"\n'), environ=self.env, cwd=self.cwd).approval_mode, "auto")
        with self.assertRaises(ConfigError):
            load(self.write('approval_mode = "yolo"\n'), environ=self.env, cwd=self.cwd)

    def test_native_entry_lifetime_defaults_to_eight_hours_and_is_validated(self):
        self.assertEqual(load(environ=self.env, cwd=self.cwd).crew_entry_ttl, "8h")
        self.assertEqual(load(self.write('crew_entry_ttl = "1h"\n'), environ=self.env, cwd=self.cwd).crew_entry_ttl, "1h")
        for invalid in ('"9h"', '"1d"', '"0m"'):
            with self.subTest(value=invalid), self.assertRaises(ConfigError):
                load(self.write(f"crew_entry_ttl = {invalid}\n"), environ=self.env, cwd=self.cwd)

    def test_accepted_kirocrew_versions(self):
        s = load(self.write('kirocrew_versions = ["0.10.0"]\n'), environ=self.env, cwd=self.cwd)
        self.assertEqual(s.kirocrew_versions, ("0.10.0",))
        self.assertEqual(load(environ=self.env, cwd=self.cwd).kirocrew_versions, ())
        with self.assertRaises(ConfigError):
            load(self.write('kirocrew_versions = ["0.10 bad"]\n'), environ=self.env, cwd=self.cwd)

if __name__ == "__main__":
    unittest.main()
