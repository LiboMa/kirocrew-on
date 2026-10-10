"""CLI defaults come from Settings; explicit flags still win."""
from pathlib import Path
import tempfile
import unittest

from octopus.__main__ import build_parser, settings_from_argv
from octopus.config import load


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = load(environ={"HOME": str(self.root)}, cwd=self.root,
                             overrides={"api_port": 9918, "worker_port": 9928, "crew_url": "http://localhost:15476"})

    def tearDown(self):
        self.temporary.cleanup()

    def test_serve_and_worker_defaults_follow_settings(self):
        parser = build_parser(self.settings, environ={})
        serve = parser.parse_args(["serve"])
        self.assertEqual((Path(serve.data), serve.port, serve.host), (self.settings.data_dir, 9918, "127.0.0.1"))
        worker = parser.parse_args(["worker"])
        self.assertEqual(Path(worker.data), self.settings.worker_data_dir)
        self.assertEqual(Path(worker.token_file), self.settings.worker_token_file)
        self.assertEqual(worker.port, 9928)
        self.assertEqual(worker.crew_url, "http://localhost:15476")
        self.assertEqual(Path(worker.routing_log), self.settings.routing_log)

    def test_flags_override_settings(self):
        args = build_parser(self.settings, environ={}).parse_args(["serve", "--port", "7000", "--data", "/tmp/x"])
        self.assertEqual((args.port, args.data), (7000, "/tmp/x"))

    def test_client_url_defaults_to_api_address(self):
        args = build_parser(self.settings, environ={}).parse_args(["request", "GET", "/v1/me"])
        self.assertEqual(args.url, "http://127.0.0.1:9918")
        env = {"OCTOPUS_URL": "https://ops.example"}
        self.assertEqual(build_parser(self.settings, environ=env).parse_args(["mcp"]).url, "https://ops.example")

    def test_config_flag_is_read_before_subcommand(self):
        config = self.root / "c.toml"
        config.write_text("api_port = 9500\n")
        settings, rest = settings_from_argv(["--config", str(config), "serve"], environ={"HOME": str(self.root)})
        self.assertEqual(settings.api_port, 9500)
        self.assertEqual(rest, ["serve"])


if __name__ == "__main__":
    unittest.main()
