"""D59 M4-2 (K10): a Worker's access token is kept sealed with the D35 key (token_sealed), opened only when the platform
calls the Worker; registrations and connection updates seal it, and plaintext left from before is sealed at start."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from unittest import mock

from octopus import transport, vault
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase

TOKEN = "w" * 40


class Stub(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        Stub.seen.append(self.headers.get("Authorization"))
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        data = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = vault.key_file(None, self.tmp.name)
        transport.configure_key(self.key)
        self.addCleanup(transport.configure_key, None)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        Stub.seen.clear()

    def test_a_sealed_token_is_opened_only_for_the_request(self):
        config = {"url": self.url, "token_sealed": vault.seal(self.key, TOKEN)}
        self.assertEqual(transport.worker_request(config, "/x", {}), {"ok": True})
        self.assertEqual(Stub.seen, ["Bearer " + TOKEN])
        self.assertNotIn("token", config)   # the caller's dictionary is left as it was

    def test_a_plaintext_token_still_works(self):
        transport.worker_request({"url": self.url, "token": TOKEN}, "/x", {})
        self.assertEqual(Stub.seen, ["Bearer " + TOKEN])

    def test_a_token_that_cannot_be_opened_is_an_answer_not_a_crash(self):
        other = vault.key_file(None, Path(self.tmp.name) / "other") if (Path(self.tmp.name) / "other").mkdir() is None else None
        config = {"url": self.url, "token_sealed": vault.seal(other, TOKEN)}
        with self.assertRaises(Error) as caught:
            transport.worker_request(config, "/x", {})
        self.assertEqual((caught.exception.status, caught.exception.code), (503, "worker_secret"))
        self.assertEqual(Stub.seen, [])
        with self.assertRaises(Error):
            transport.worker_request({"url": self.url}, "/x", {})

    def test_the_tls_authority_and_the_key_are_configured_apart(self):   # caught before deploy: configure(ca) was replaced
        transport.configure(None)   # the Worker TLS authority, as `octopus serve` sets it
        self.assertEqual(transport._KEY, self.key)
        self.assertIsNone(transport._context)

    def test_a_missing_key_file_is_the_same_answer(self):   # final review I1: FileNotFoundError leaked as a 500
        config = {"url": self.url, "token_sealed": vault.seal(self.key, TOKEN)}
        Path(self.key).unlink()
        transport.forget()
        with self.assertRaises(Error) as caught:
            transport.worker_request(config, "/x", {})
        self.assertEqual((caught.exception.status, caught.exception.code), (503, "worker_secret"))

    def test_a_sealed_token_is_opened_once_per_process(self):   # final review M2: an openssl run per Worker call
        from unittest import mock
        config = {"url": self.url, "token_sealed": vault.seal(self.key, TOKEN)}
        transport.forget()
        with mock.patch.object(vault, "open_", wraps=vault.open_) as opened:
            for _ in range(3):
                transport.worker_request(config, "/x", {})
        self.assertEqual(opened.call_count, 1)

    def test_a_remote_preview_upstream_reads_the_sealed_token(self):   # final review C2: KeyError 'token' on remote Workers
        from octopus import remote_preview
        sealed = {"url": "https://10.42.1.50:8943", "token_sealed": vault.seal(self.key, TOKEN)}
        plain = {"url": "https://10.42.1.50:8943", "token": TOKEN}
        via = {"port": 8945, "name": "w1.worker.kirocrew-on"}
        self.assertEqual(remote_preview.upstream(sealed, 0, via), remote_preview.upstream(plain, 0, via))

    def test_the_connection_is_url_and_token(self):
        sealed = {"url": self.url, "token_sealed": vault.seal(self.key, TOKEN), "capabilities": {"x": 1}}
        self.assertEqual(transport.connection(sealed), {"url": self.url, "token": TOKEN})



class PlatformPassesTheSealedTokenTests(unittest.TestCase):
    def test_the_diff_and_the_sync_keep_token_sealed_in_what_they_pass(self):
        from unittest import mock
        from octopus import diffs
        from octopus.db import dumps
        from tests.test_attempt_diff import PlatformDiffTests
        case = PlatformDiffTests("test_the_worker_is_asked_for_that_candidate")
        case.setUp()
        self.addCleanup(case.tearDown)
        with case.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='w-bob'", (dumps({"url": "https://worker.test", "token_sealed": "v1.x.y"}),))
        with mock.patch.object(diffs, "worker_request", return_value={"files": [], "diff": "", "truncated": False}) as call:
            case.service.attempt_diff(case.b, case.rid, "attempt-c")
        self.assertEqual(call.call_args.args[0], {"url": "https://worker.test", "token_sealed": "v1.x.y"})


class SealedOnWriteTests(AccessCase):
    HEALTH = {"isolation": "dedicated", "boundary": "b-9", "host_principal": "hp-9"}

    def stored(self, wid):
        with self.db.read() as db:
            return json.loads(db.execute("SELECT config FROM workers WHERE id=?", (wid,)).fetchone()[0])

    def test_registering_and_reconnecting_keep_only_the_sealed_token(self):
        body = {"name": "w9", "owner_id": self.b["id"], "connection": {"url": "https://10.0.0.9:8943", "token": "t" * 40}}
        with mock.patch("octopus.server.validate_worker", return_value=self.HEALTH), self.http() as call:
            status, created = call("POST", "/v1/workers", self.tokens["alice"], body)
            self.assertEqual(status, 201)
            config = self.stored(created["id"])
            self.assertNotIn("token", config)
            self.assertEqual(vault.open_(vault.key_file(None, self.db.root), config["token_sealed"]), "t" * 40)
            status, _ = call("POST", f"/v1/workers/{created['id']}/connection", self.tokens["alice"],
                             {"connection": {"url": "https://10.0.0.8:8943", "token": "n" * 40}})
            self.assertEqual(status, 200)
            config = self.stored(created["id"])
            self.assertNotIn("token", config)
            self.assertEqual(vault.open_(vault.key_file(None, self.db.root), config["token_sealed"]), "n" * 40)
            for path in ("/v1/workers", "/v1/admin/environments"):
                listed = json.dumps(call("GET", path, self.tokens["alice"])[1])
                self.assertNotIn("token_sealed", listed, path)
                self.assertNotIn("t" * 40, listed, path)

    def test_a_capability_refresh_keeps_the_seal(self):
        sealed = vault.seal(vault.key_file(None, self.db.root), "t" * 40)
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('w8','w8',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://10.0.0.9:8943", "token_sealed": sealed, "capabilities": self.HEALTH})))
        with mock.patch("octopus.server.worker_request", return_value={**self.HEALTH, "models": {}}), self.http() as call:
            self.assertEqual(call("GET", "/v1/workers/w8/capabilities", self.tokens["bob"])[0], 200)
        config = self.stored("w8")
        self.assertEqual((config.get("token"), config["token_sealed"]), (None, sealed))



class SealedAtStartTests(AccessCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(transport.configure_key, None)   # server() sets the module's key: not for the next test

    def test_an_unreadable_key_does_not_keep_the_platform_down(self):   # final review I2
        from octopus.server import server
        app = server(self.db, 0, credentials_key_file=str(Path(self.db.root) / "missing.key"))
        app.server_close()
        with self.assertRaises(Error) as caught:
            transport.worker_request({"url": "http://127.0.0.1:9", "token_sealed": "v1.a.b"}, "/x", {})
        self.assertEqual(caught.exception.code, "worker_secret")

    def test_no_plaintext_is_left_in_the_database_files(self):   # final review M3: free pages and the WAL kept it
        from octopus.server import server
        secret = "q" * 40
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('older','older',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://10.0.0.9:8943", "token": secret, "capabilities": {}})))
        app = server(self.db, 0)
        app.server_close()
        for name in ("octopus.sqlite3", "octopus.sqlite3-wal"):
            path = Path(self.db.root) / name
            if path.exists():
                self.assertNotIn(secret.encode(), path.read_bytes(), name)

    def test_plaintext_from_before_is_sealed_once(self):
        from octopus import workers
        from octopus.server import server
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('old','old',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://10.0.0.9:8943", "token": "p" * 40, "capabilities": {}})))
        app = server(self.db, 0)   # the platform's start
        app.server_close()
        key = vault.key_file(None, self.db.root)
        with self.db.read() as db:
            config = json.loads(db.execute("SELECT config FROM workers WHERE id='old'").fetchone()[0])
        self.assertNotIn("token", config)
        self.assertEqual(vault.open_(key, config["token_sealed"]), "p" * 40)
        with self.db.transaction() as db:
            self.assertEqual(workers.seal_stored(db, key), 0)   # again: nothing to do, the seal is not changed
            self.assertEqual(json.loads(db.execute("SELECT config FROM workers WHERE id='old'").fetchone()[0]), config)


if __name__ == "__main__":
    unittest.main()
