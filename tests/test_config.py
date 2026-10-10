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

if __name__ == "__main__":
    unittest.main()
