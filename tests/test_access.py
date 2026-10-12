"""Access management phase 1: scopes, 403 / 404 semantics, admin oversight, user lifecycle, tokens and previews."""
from contextlib import contextmanager
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from octopus import server as server_module
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import authenticate, create_user, issue_token
from octopus.server import server

PASSWORD = "Test-password-123!"


class AccessCase(unittest.TestCase):
    """alice is the platform admin; bob and eve are users. alice owns a Team with bob as a member."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        self.users, self.tokens = {}, {}
        with self.db.transaction() as db:
            for name in ("alice", "bob", "eve"):
                user = create_user(db, name, PASSWORD, admin=name == "alice")
                self.tokens[name] = issue_token(db, user["id"])["token"]
                self.users[name] = authenticate(db, self.tokens[name])
        self.a, self.b, self.e = (self.users[k] for k in ("alice", "bob", "eve"))

    def tearDown(self):
        self.tmp.cleanup()

    def token(self, name, scopes):
        with self.db.transaction() as db:
            return issue_token(db, self.users[name]["id"], scopes)["token"]

    def shared_env(self, wid="env", users=("alice", "bob", "eve")):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'shared')",
                       (wid, wid, self.a["id"], dumps({"url": "http://127.0.0.1:1", "token": "x" * 40, "capabilities": {}})))
            for name in users:
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, self.users[name]["id"]))
        return wid

    def team(self, name="研发 Team", environment=True):
        """alice owns the Team, bob is a member, eve is not."""
        tid = "team-" + name.encode().hex()[:12]
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES(?,?,0)", (tid, name))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.a["id"]))
            db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.b["id"]))
            if environment:
                db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'shared')",
                           ("env-" + tid, "env", self.a["id"], dumps({"capabilities": {}})))
                db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", ("env-" + tid, tid))
        return tid

    def start(self, actor, request="start-0000000001", **extra):
        return self.service.start(actor, {"request_id": request, "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                          "recovery": {"enabled": False, "max_cycles": 2}, **extra})

    @contextmanager
    def http(self):
        app = server(self.db, 0)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{app.server_address[1]}"

        def call(method, path, token=None, body=None, headers=None):
            request = urllib.request.Request(
                base + path, method=method, data=dumps(body).encode() if body is not None else None,
                headers={**({"Authorization": "Bearer " + token} if token else {}),
                         **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as response:
                with response:
                    return response.code, json.load(response)
        try:
            yield call
        finally:
            app.shutdown()
            app.server_close()
            thread.join(2)


class ScopeTests(AccessCase):
    def test_required_scope_by_route(self):
        cases = {("GET", "/v1/runs"): "read", ("POST", "/v1/runs"): "execute", ("POST", "/v1/runs/r/stop"): "execute",
                 ("POST", "/v1/workflows"): "manage", ("POST", "/v1/me/password"): "manage",
                 ("POST", "/v1/users"): "admin", ("POST", "/v1/users/u/active"): "admin", ("POST", "/v1/teams"): "admin",
                 ("POST", "/v1/workers"): "admin", ("POST", "/v1/workers/w/share"): "admin",
                 ("GET", "/v1/admin/environments"): "admin", ("GET", "/v1/admin/users"): "admin",
                 ("GET", "/v1/workers"): "read", ("POST", "/v1/workflows/resolve"): "read",
                 ("POST", "/v1/runs/r/preview"): "read", ("DELETE", "/v1/tokens/t"): "manage",
                 ("GET", "/v1/users"): "read", ("POST", "/v1/teams/t/members"): "manage"}
        for (method, path), expected in cases.items():
            self.assertEqual(server_module.required_scope(method, path), expected, (method, path))

    def test_admin_login_includes_admin_scope(self):
        with self.http() as call:
            status, token = call("POST", "/v1/login", body={"username": "alice", "password": PASSWORD})
            self.assertEqual((status, sorted(token["scopes"])), (200, ["admin", "execute", "manage", "read"]))
            status, token = call("POST", "/v1/login", body={"username": "bob", "password": PASSWORD})
            self.assertEqual(sorted(token["scopes"]), ["execute", "manage", "read"])

    def test_admin_token_without_admin_scope_is_an_ordinary_user(self):
        # e.g. the long-lived "claude MCP" token: read + execute only.
        ordinary = self.token("alice", ["read", "execute"])
        tid = self.team()
        other = self.team("别人的 Team")
        with self.db.transaction() as db:
            db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (other, self.a["id"]))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (other, self.e["id"]))
        self.shared_env(users=("alice",))
        with self.http() as call:
            status, result = call("GET", "/v1/admin/environments", ordinary)
            self.assertEqual(status, 403)
            self.assertIn("admin", result["error"])
            self.assertEqual(call("POST", "/v1/teams", ordinary, {"name": "x", "owner": "bob", "worker_id": "env"})[0], 403)
            self.assertEqual({t["id"] for t in call("GET", "/v1/teams", ordinary)[1]}, {tid})
            self.assertEqual({w["id"] for w in call("GET", "/v1/workers", ordinary)[1]}, {"env", "env-" + tid})
            self.assertEqual({t["id"] for t in call("GET", "/v1/teams", self.tokens["alice"])[1]}, {tid, other})
            status, run = call("POST", "/v1/runs", ordinary, {
                "request_id": "start-0000000009", "input": "研发需求与真实验证", "source": {"type": "new"},
                "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})
            self.assertEqual(status, 200, run)

    def test_non_admin_cannot_mint_admin_scope(self):
        with self.http() as call:
            self.assertEqual(call("POST", "/v1/tokens", self.tokens["bob"], {"scopes": ["admin"]})[0], 403)
            status, token = call("POST", "/v1/tokens", self.tokens["alice"], {"scopes": ["read", "admin"]})
            self.assertEqual((status, token["scopes"]), (201, ["read", "admin"]))


if __name__ == "__main__":
    unittest.main()
