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



class BackupCommandTests(unittest.TestCase):
    def test_backup_and_restore_verify_commands(self):
        import contextlib
        import io
        import json
        from octopus.__main__ import main
        from octopus.db import Database
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "c.toml"
            config.write_text(f'data_dir = "{root}/platform"\n')
            Database(root / "platform")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--config", str(config), "backup", "--dest", str(root / "backups")])
            manifest = json.loads(out.getvalue())
            self.assertEqual(manifest["integrity"], "ok")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--config", str(config), "restore-verify",
                      str(root / "backups" / (Path(manifest["file"]).stem + ".json")), "--into", str(root / "restore")])
            self.assertTrue(json.loads(out.getvalue())["ok"])

    def test_a_restored_copy_is_checked_the_same_way(self):
        # MVP 0.2.0 (P §5.2): after `litestream restore -o <file>`, that file is backed up and verified like the live one
        import contextlib
        import io
        import json
        import shutil
        from octopus.__main__ import main
        from octopus.db import Database
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "c.toml"
            config.write_text(f'data_dir = "{root}/platform"\n')
            Database(root / "elsewhere")
            restored = root / "restored.sqlite3"
            shutil.copy2(root / "elsewhere" / "octopus.sqlite3", restored)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--config", str(config), "backup", "--source", str(restored), "--dest", str(root / "check")])
            manifest = json.loads(out.getvalue())
            self.assertEqual((manifest["integrity"], manifest["source"]), ("ok", str(restored)))
            self.assertFalse((root / "platform").exists())   # the live database is not touched


if __name__ == "__main__":
    unittest.main()
