"""Headless lifecycle: readiness probing, clean SIGTERM exit, and unit files that match the CLI."""
import configparser
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from octopus import ops
from octopus.__main__ import build_parser
from octopus.config import load

ROOT = Path(__file__).resolve().parent.parent


class ReadinessTests(unittest.TestCase):
    def test_ready_after_transient_failures(self):
        attempts = []

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) < 3:
                raise urllib.error.URLError("refused")
            return open(os.devnull, "rb")

        self.assertEqual(ops.wait_ready([("http://127.0.0.1:1/health", {})], timeout=5, interval=0.01, opener=opener), [])
        self.assertEqual(len(attempts), 3)

    def test_timeout_reports_unready_urls(self):
        def opener(request, timeout):
            raise urllib.error.URLError("refused")

        pending = ops.wait_ready([("http://127.0.0.1:1/health", {})], timeout=0.1, interval=0.02, opener=opener)
        self.assertEqual(pending, ["http://127.0.0.1:1/health"])

    def test_runtime_marker_requires_composition(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "gateway-runtime.json"
            self.assertFalse(ops.runtime_marker_ok(marker))
            marker.write_text(json.dumps({"pid": os.getpid(), "composition": "PocProviderRegistry",
                                          "composition_features": ["codex_effort_pair_v1"]}))
            self.assertTrue(ops.runtime_marker_ok(marker))
            marker.write_text(json.dumps({"pid": os.getpid(), "composition": "default"}))
            self.assertFalse(ops.runtime_marker_ok(marker))


class ServeProcessTests(unittest.TestCase):
    def test_serve_exits_cleanly_on_sigterm(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "octopus.toml"
            config.write_text(f'data_dir = "{temporary}/platform"\n')
            process = subprocess.Popen([sys.executable, "-m", "octopus", "--config", str(config), "serve", "--port", "0"],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                line = process.stdout.readline()
                port = int(line.rsplit(":", 1)[1])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.stdout.close()


class UnitFileTests(unittest.TestCase):
    def units(self):
        for path in sorted((ROOT / "deploy/systemd").glob("*.service")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            parser.read(path)
            yield path.name, parser

    def test_units_use_existing_commands_and_separate_users(self):
        names = {}
        subcommands = build_parser(load(environ={"HOME": "/tmp"}, cwd="/tmp"))._subparsers._group_actions[0].choices
        for name, unit in self.units():
            service = unit["Service"]
            names[name] = service["User"]
            command = service["ExecStart"]
            if "-m octopus" in command:
                self.assertIn(command.split()[-1], subcommands)
            else:
                self.assertIn("-m composition.gateway", command)
                self.assertTrue((ROOT / "composition/gateway.py").is_file())
            self.assertEqual(service["KillSignal"], "SIGTERM")
            self.assertIn("ReadWritePaths", service)
        self.assertEqual(names["octopus-api.service"], "octopus")
        self.assertEqual(names["octopus-worker@.service"], "crew-%i")
        self.assertEqual(names["octopus-gateway@.service"], "crew-%i")


class WaitReadyCommandTests(unittest.TestCase):
    """The Worker writes its token on first start; readiness must wait for it, not crash."""

    def boundary(self, root, port):
        config = root / "boundary.toml"
        config.write_text(f'worker_token_file = "{root}/worker.token"\nworker_port = {port}\n')
        return config

    def test_missing_token_reports_not_ready_instead_of_crashing(self):
        from octopus.__main__ import main
        with tempfile.TemporaryDirectory() as temporary:
            config = self.boundary(Path(temporary), 9)
            with self.assertRaises(SystemExit) as raised:
                main(["--config", str(config), "wait-ready", "--worker", "--timeout", "0.3"])
            self.assertIn("worker.token", str(raised.exception.code))

    def test_token_written_after_start_is_awaited(self):
        import http.server
        import threading
        from octopus.__main__ import main
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            class Health(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_GET(self):
                    token = (root / "worker.token").read_text().strip()
                    ok = self.headers.get("Authorization") == "Bearer " + token
                    self.send_response(200 if ok else 401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            timer = threading.Timer(0.3, lambda: (root / "worker.token").write_text("t" * 40))
            timer.start()
            try:
                config = self.boundary(root, httpd.server_address[1])
                main(["--config", str(config), "wait-ready", "--worker", "--timeout", "5"])
            finally:
                timer.cancel()
                httpd.shutdown()
                httpd.server_close()

class BoundedShutdownTests(unittest.TestCase):
    """I2: SIGTERM with a Worker call still in flight exits 0 within the configured grace period."""

    def test_sigterm_with_slow_inflight_worker_call_exits_within_grace(self):
        import http.server
        import threading
        from octopus.db import Database, dumps
        received = threading.Event()

        class Slow(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                received.set()
                time.sleep(30)

        stub = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        stub.daemon_threads = True
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "octopus.toml"
            config.write_text(f'data_dir = "{root}/platform"\nshutdown_grace = 1.0\nengine_workers = 1\n')
            db = Database(root / "platform")
            now = time.time()
            with db.transaction() as c:
                c.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u','u','x',1,1,?)", (now,))
                c.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w','w','u',?)",
                          (dumps({"url": f"http://127.0.0.1:{stub.server_address[1]}", "token": "t" * 40}),))
                c.execute("INSERT INTO runs(id,owner_id,worker_id,snapshot,input,source,status,created,updated) "
                          "VALUES('run-slow','u','w','{}','goal',?,'preparing',?,?)", (dumps({"type": "new"}), now, now))
            process = subprocess.Popen([sys.executable, "-m", "octopus", "--config", str(config), "serve", "--port", "0"],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                process.stdout.readline()
                self.assertTrue(received.wait(10), "Engine never called the Worker")
                started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=15), 0)
                self.assertLess(time.monotonic() - started, 8)
            finally:
                if process.poll() is None:
                    process.kill()
                process.stdout.close()
                stub.shutdown()
                stub.server_close()

if __name__ == "__main__":
    unittest.main()
