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
        transport.configure(self.key)
        self.addCleanup(transport.configure, None)
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


if __name__ == "__main__":
    unittest.main()
