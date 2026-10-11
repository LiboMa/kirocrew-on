"""Native entry authorization and binding; no real Gateway or credentials."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import urlencode

import api
from octopus import crew_entry
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import Error, authenticate, create_user, issue_token
from octopus.server import server
from octopus.worker import Worker, write


class CrewEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        self.actors, self.tokens = {}, {}
        with self.db.transaction() as db:
            for username in ("alice", "bob"):
                user = create_user(db, username, "Test-password-123!", username == "alice")
                self.tokens[username] = issue_token(db, user["id"])["token"]
                self.actors[username] = authenticate(db, self.tokens[username])
        self.actor = self.actors["alice"]
        self.health = {"boundary": "fixture", "host_principal": "fixture-host", "isolation": "trusted-local",
                       "crew_entry": True, "crew_origin": "http://localhost:5678"}
        self.config = {"url": "http://127.0.0.1:1", "token": "x"*40, "capabilities": dict(self.health)}
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Fixture',?,?)",
                       (self.actor["id"], dumps(self.config)))
        run = self.service.start(self.actor, {"request_id": "fixture-entry-001", "worker_id": "worker",
            "input": "只测试原生入口，不执行研发", "source": {"type": "new"},
            "steps": [{"id": "task", "name": "测试", "tool": "kiro", "prompt": "测试入口"}]})
        self.rid, self.parent = run["id"], "native-existing-session"
        self.update(native_parent=self.parent, status="stopped")
        self.reply = {"native_parent": self.parent, "url": self.health["crew_origin"] + "/chat?" +
                      urlencode({"sid": self.parent, "token": "fixture-link-only"})}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def update(self, **fields):
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET " + ",".join(k+"=?" for k in fields) + " WHERE id=?",
                       [*fields.values(), self.rid])

    def request(self, config, path, body=None):
        self.calls.append((path, body))
        return self.health if path == "/health" else self.reply

    def create(self, actor=None):
        with patch("octopus.crew_entry.worker_request", side_effect=self.request):
            return crew_entry.create(self.service, actor or self.actor, self.rid)

    def test_entry_uses_saved_identity_without_mutating_or_persisting_credentials(self):
        with self.db.read() as db:
            before = "\n".join(db.iterdump())
        result = self.create()
        self.assertEqual(result["native_parent"], self.parent)
        self.assertEqual(self.calls[-1], ("/crew-entry", {"run_id": self.rid, "native_parent": self.parent}))
        with self.db.read() as db:
            self.assertEqual(before, "\n".join(db.iterdump()))
        self.assertNotIn("fixture-link-only", dumps(self.service.run(self.actor, self.rid)))

    def test_private_session_closed_missing_and_disabled_worker(self):
        with self.assertRaises(Error) as denied:
            self.create(self.actors["bob"])
        self.assertEqual(denied.exception.status, 404)
        self.assertEqual(self.calls, [])
        for fields in ({"native_parent": None}, {"lifecycle": "closed"}, {"desired": "close"},
                       {"desired": "delete"}, {"status": "reopening"}):
            self.update(native_parent=self.parent, lifecycle="open", desired="active", status="stopped")
            self.update(**fields)
            with self.assertRaises(Error):
                self.create()
        self.update(status="stopped")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0")
        with self.assertRaises(Error):
            self.create()
        self.assertEqual(self.calls, [])

    def test_team_membership_and_revocation_during_entry(self):
        team = self.service.create_team(self.actor, {"name": "Entry Team"})
        self.service.membership(self.actor, team["id"], {"username": "bob"})
        self.health["isolation"] = "dedicated"
        self.config["capabilities"] = dict(self.health)
        self.update(team_id=team["id"])
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET owner_id=NULL,team_id=?,config=?",
                       (team["id"], dumps(self.config)))
        self.assertEqual(self.create(self.actors["bob"])["native_parent"], self.parent)
        def revoke(config, path, body=None):
            result = self.request(config, path, body)
            if path == "/crew-entry":
                with self.db.transaction() as db:
                    db.execute("DELETE FROM members WHERE user_id=?", (self.actors["bob"]["id"],))
            return result
        with patch("octopus.crew_entry.worker_request", side_effect=revoke), self.assertRaises(Error) as denied:
            crew_entry.create(self.service, self.actors["bob"], self.rid)
        self.assertEqual(denied.exception.status, 404)

    def test_upgrade_boundary_and_remote_origin_gates(self):
        for changes, code in (({"crew_entry": False}, "worker_upgrade_required"),
                              ({"boundary": "different"}, "worker_changed"),
                              ({"isolation": "dedicated"}, "worker_changed")):
            original = dict(self.health)
            self.health.update(changes)
            with self.assertRaises(Error) as caught:
                self.create()
            self.assertEqual(caught.exception.code, code)
            self.health = original
        self.config["url"] = "https://worker.example.test"
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=?", (dumps(self.config),))
        with self.assertRaises(Error) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "crew_origin_required")
        self.assertTrue(all(path == "/health" for path, _ in self.calls))

    def test_wrong_redirect_and_changed_lifecycle_never_return_link(self):
        for url in ("https://untrusted.example/chat?sid=x&token=bad",
                    "http://localhost:5678/chat?sid=another&token=bad",
                    self.reply["url"]+"&token=duplicate", self.reply["url"]+"#fragment"):
            old = self.reply["url"]
            self.reply["url"] = url
            with self.assertRaises(Error):
                self.create()
            self.reply["url"] = old
        def close(config, path, body=None):
            result = self.request(config, path, body)
            if path == "/crew-entry":
                self.update(desired="close")
            return result
        with patch("octopus.crew_entry.worker_request", side_effect=close), self.assertRaises(Error):
            crew_entry.create(self.service, self.actor, self.rid)

    @contextmanager
    def http(self):
        app = server(self.db, 0)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        def call(method, token, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{app.server_port}/v1/runs/{self.rid}/crew-entry",
                data=None if body is None else dumps(body).encode(), method=method,
                headers={"Authorization": "Bearer "+token, "Content-Type": "application/json"})
            try:
                response = urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, response.headers, json.load(response)
        try:
            yield call
        finally:
            app.shutdown();app.server_close();thread.join(2)

    def test_http_execute_scope_no_store_and_no_arbitrary_target(self):
        with self.db.transaction() as db:
            read_token = issue_token(db, self.actor["id"], ["read"])["token"]
        with self.http() as call, patch("octopus.crew_entry.worker_request", side_effect=self.request):
            self.assertEqual(call("POST", "", {})[0], 401)
            self.assertEqual(call("POST", read_token, {})[0], 403)
            self.assertEqual(call("POST", self.tokens["bob"], {})[0], 404)
            self.assertEqual(call("GET", self.tokens["alice"])[0], 405)
            self.assertEqual(call("POST", self.tokens["alice"], {"sid": "other"})[0], 400)
            self.assertEqual(self.calls, [])
            status, headers, result = call("POST", self.tokens["alice"], {})
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(result["native_parent"], self.parent)

    def test_worker_reuses_native_bootstrap_with_its_own_home_and_port(self):
        node = Worker(Path(self.tmp.name)/"worker", "x"*40, 5678, Path(self.tmp.name)/"crew",
                      crew_url="https://crew.example.test")
        root = node.path(self.rid)
        write(root/"parent.json", {"slot": self.parent, "parent": "dashboard:"+self.parent})
        calls = []
        def native(path, body=None, method=None):
            calls.append((path, body, method))
            return {"key": self.parent}
        node.native = native
        body = {"run_id": self.rid, "native_parent": self.parent}
        with patch("api.chat_entry_url", return_value="test-url") as mint:
            result = node.crew_entry(body)
            mint.assert_called_once_with(self.parent, 5678, home=node.home, origin="https://crew.example.test", ttl="8h")
            self.assertEqual(result["native_parent"], self.parent)
            self.assertEqual(calls, [("/api/chat/slots/"+self.parent, None, None)])
            mint.reset_mock()
            with self.assertRaises(Error):
                node.crew_entry({**body, "native_parent": "another-session"})
            write(root/"lifecycle.json", {"status": "closed"})
            with self.assertRaises(Error):
                node.crew_entry(body)
            mint.assert_not_called()

    def test_public_entry_uses_the_configured_shorter_login_lifetime(self):
        # K03: the native link logs the browser into the whole Gateway; behind CloudFront it is kept short.
        node = Worker(Path(self.tmp.name)/"worker", "x"*40, 5678, Path(self.tmp.name)/"crew",
                      crew_url="https://dnative.cloudfront.net", crew_entry_ttl="1h")
        write(node.path(self.rid)/"parent.json", {"slot": self.parent, "parent": "dashboard:"+self.parent})
        node.native = lambda path, body=None, method=None: {"key": self.parent}
        with patch("api.chat_entry_url", return_value="test-url") as mint:
            node.crew_entry({"run_id": self.rid, "native_parent": self.parent})
            self.assertEqual(mint.call_args.kwargs["ttl"], "1h")
        for invalid in ("9h", "1d", "0h", "", "1h; x"):
            with self.subTest(ttl=invalid), self.assertRaises(Error):
                Worker(Path(self.tmp.name)/"worker2", "x"*40, 5678, Path(self.tmp.name)/"crew", crew_entry_ttl=invalid)

    def test_native_url_helper_preserves_legacy_default_and_encodes_identity(self):
        self.assertEqual(crew_entry.browser_origin("https://CREW.example.test:443/"), "https://crew.example.test")
        self.assertEqual(crew_entry.browser_origin("http://[::1]:5476/"), "http://[::1]:5476")
        with patch("api.token", return_value="fixture-link") as mint:
            self.assertEqual(api.chat_entry_url("old-session"),
                             "http://localhost:5476/chat?sid=old-session&token=fixture-link")
            url = api.chat_entry_url("session/a b", 5678, home=Path("/fixture"), origin="https://crew.example.test/")
            self.assertEqual(url, "https://crew.example.test/chat?sid=session%2Fa+b&token=fixture-link")
            mint.assert_called_with(5678, Path("/fixture"), ttl="8h")
        for invalid in ("http://remote.example", "javascript:alert(1)", "https://u:p@crew.example",
                        "https://crew.example/chat", "https://crew.example?token=bad"):
            with self.assertRaises(Error):
                crew_entry.browser_origin(invalid)
