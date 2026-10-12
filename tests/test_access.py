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
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,0)", (tid, name))
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


class SemanticsTests(AccessCase):
    """Cannot see it: 404, revealing nothing. Can see it but may not do this: 403 with the reason."""

    def test_member_cannot_delete_someone_elses_team_session_and_is_told_why(self):
        tid = self.team()
        rid = self.start(self.a, team_id=tid)["id"]
        mine = self.start(self.b, "start-0000000002", team_id=tid)["id"]
        body = {"request_id": "delete-000000001", "expected_version": 1, "confirmation": rid}
        with self.http() as call:
            status, result = call("POST", f"/v1/runs/{rid}/delete", self.tokens["bob"], body)
            self.assertEqual(status, 403)
            self.assertIn("只有 Team owner 可以删除他人创建的会话", result["error"])
            self.assertEqual(call("POST", f"/v1/runs/{rid}/delete", self.tokens["eve"], body)[0], 404)
            # native: False — the Team's shared environment belongs to alice (K03).
            self.assertEqual(call("GET", f"/v1/runs/{rid}", self.tokens["bob"])[1]["permissions"],
                             {"operate": True, "delete": False, "native": False})
            self.assertEqual(call("GET", f"/v1/runs/{mine}", self.tokens["bob"])[1]["permissions"],
                             {"operate": True, "delete": True, "native": False})
            listed = {r["id"]: r["permissions"] for r in call("GET", "/v1/runs", self.tokens["bob"])[1]}
            self.assertEqual(listed[rid], {"operate": True, "delete": False, "native": False})

    def test_viewer_cannot_edit_a_workflow_and_is_told_why(self):
        from octopus.security import Error
        flow = self.service.save_workflow(self.a, {"name": "研发流程", "steps": [
            {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})
        self.service.share(self.a, flow["id"], {"kind": "user", "principal": self.b["id"], "role": "viewer"})
        edit = {"name": "研发流程", "expected_version": 1, "steps": [{"id": "code", "name": "实现", "tool": "codex", "prompt": "实现"}]}
        with self.assertRaises(Error) as caught:
            self.service.save_workflow(self.b, edit, flow["id"])
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("editor", str(caught.exception))
        with self.assertRaises(Error) as caught:
            self.service.save_workflow(self.e, edit, flow["id"])
        self.assertEqual(caught.exception.status, 404)

    def test_non_member_does_not_learn_a_team_name(self):
        from octopus.security import Error
        tid = self.team("保密项目组", environment=False)
        with self.assertRaises(Error) as caught:
            self.start(self.e, team_id=tid)
        self.assertEqual(caught.exception.status, 404)
        self.assertNotIn("保密项目组", str(caught.exception))
        with self.assertRaises(Error) as caught:   # a member still learns why
            self.start(self.b, team_id=tid)
        self.assertEqual(caught.exception.code, "team_without_worker")

class OversightTests(AccessCase):
    """A2: an elevated admin sees and operates every Session, except its native entry; oversight is audited."""

    def setUp(self):
        super().setUp()
        self.shared_env(users=("bob",))
        self.rid = self.start(self.b)["id"]   # bob's personal Session on bob's environment

    def test_admin_reads_and_operates_every_session_but_not_its_native_entry(self):
        from unittest.mock import patch
        from octopus import crew_entry
        with self.http() as call:
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}", self.tokens["alice"])[0], 200)
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/events", self.tokens["alice"])[0], 200)
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}", self.tokens["eve"])[0], 404)
            status, result = call("POST", f"/v1/runs/{self.rid}/stop", self.tokens["alice"],
                                  {"request_id": "stop-0000000001", "expected_version": 1})
            self.assertEqual((status, result["status"]), (200, "accepted"), result)
            self.assertEqual(call("POST", f"/v1/runs/{self.rid}/crew-entry", self.tokens["alice"], {})[0], 403)
            audit = call("GET", "/v1/admin/audit", self.tokens["alice"])[1]
            # The stop, and (F5) the views of someone else's Session that preceded it.
            self.assertEqual({(e["action"], e["kind"], e["target"], e["actor"]["username"]) for e in audit},
                             {("run.stop", "run", self.rid, "alice"), ("run.view", "run", self.rid, "alice")})
            self.assertEqual(call("GET", "/v1/admin/audit", self.tokens["bob"])[0], 403)
        with patch("octopus.crew_entry.worker_request", return_value={"messages": []}):
            with self.db.transaction() as db:
                db.execute("UPDATE runs SET native_parent='octopus-x' WHERE id=?", (self.rid,))
            self.assertEqual(crew_entry.observe(self.service, self.a, self.rid), {"messages": []})
        events = self.service.events(self.a, self.rid)
        self.assertEqual(next(e for e in events if e["kind"] == "command.stop")["payload"]["actor"], self.a["id"])

    def test_bobs_own_operations_are_not_audited(self):
        self.service.operate(self.b, self.rid, "stop", {"request_id": "stop-0000000002", "expected_version": 1})
        self.assertEqual(self.service.audit_log(self.a), [])

    def test_admin_lists_all_sessions_only_on_request(self):
        with self.http() as call:
            self.assertNotIn(self.rid, {r["id"] for r in call("GET", "/v1/runs", self.tokens["alice"])[1]})
            self.assertIn(self.rid, {r["id"] for r in call("GET", "/v1/runs?all=1", self.tokens["alice"])[1]})
            ordinary = self.token("alice", ["read", "execute"])
            self.assertNotIn(self.rid, {r["id"] for r in call("GET", "/v1/runs?all=1", ordinary)[1]})
            self.assertEqual({r["id"] for r in call("GET", "/v1/runs?all=1", self.tokens["eve"])[1]}, set())

    def test_admin_assigns_a_worker_from_the_sessions_own_space(self):
        waiting = self.start(self.e, "start-0000000004")["id"]   # eve has no environment yet
        self.assertEqual(self.service.run(self.e, waiting)["status"], "waiting_worker")
        self.shared_env("env-eve", users=("eve",))
        result = self.service.operate(self.a, waiting, "assign-worker",
                                      {"request_id": "assign-000000001", "expected_version": 1, "worker_id": "env-eve"})
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.service.run(self.e, waiting)["status"], "preparing")

class UserLifecycleTests(AccessCase):
    NEW = "Another-password-456!"

    def login(self, call, name, password=PASSWORD):
        return call("POST", "/v1/login", body={"username": name, "password": password})

    def test_changing_my_password_revokes_my_other_tokens(self):
        other = self.token("bob", ["read"])
        with self.http() as call:
            status, result = call("POST", "/v1/me/password", self.tokens["bob"], {"current": "wrong-password-0", "password": self.NEW})
            self.assertEqual((status, result["error"]), (403, "原密码不正确"))
            self.assertEqual(call("POST", "/v1/me/password", self.tokens["bob"], {"current": PASSWORD, "password": "short"})[0], 400)
            self.assertEqual(call("POST", "/v1/me/password", self.tokens["bob"], {"current": PASSWORD, "password": self.NEW})[0], 200)
            self.assertEqual(call("GET", "/v1/me", other)[0], 401)
            self.assertEqual(call("GET", "/v1/me", self.tokens["bob"])[0], 200)
            self.assertEqual(self.login(call, "bob")[0], 401)
            self.assertEqual(self.login(call, "bob", self.NEW)[0], 200)

    def test_admin_resets_a_password_and_every_token_of_that_user_stops(self):
        with self.http() as call:
            self.assertEqual(call("POST", f"/v1/users/{self.b['id']}/password", self.tokens["eve"], {"password": self.NEW})[0], 403)
            self.assertEqual(call("POST", f"/v1/users/{self.b['id']}/password", self.tokens["alice"], {"password": self.NEW})[0], 200)
            self.assertEqual(call("GET", "/v1/me", self.tokens["bob"])[0], 401)
            self.assertEqual(self.login(call, "bob", self.NEW)[0], 200)
            self.assertEqual(call("POST", "/v1/users/user-missing/password", self.tokens["alice"], {"password": self.NEW})[0], 404)
        self.assertEqual([(e["action"], e["target"]) for e in self.service.audit_log(self.a)],
                         [("user.reset_password", self.b["id"])])

    def test_deactivation_revokes_tokens_but_leaves_sessions_running(self):
        self.shared_env(users=("bob",))
        rid = self.start(self.b)["id"]
        with self.http() as call:
            self.assertEqual(call("POST", f"/v1/users/{self.b['id']}/active", self.tokens["alice"], {"active": False})[0], 200)
            self.assertEqual(call("GET", "/v1/me", self.tokens["bob"])[0], 401)
            self.assertEqual(self.login(call, "bob")[0], 401)
            self.assertEqual(call("GET", f"/v1/runs/{rid}", self.tokens["alice"])[1]["status"], "preparing")
            self.assertEqual(call("POST", f"/v1/runs/{rid}/stop", self.tokens["alice"],
                                  {"request_id": "stop-0000000003", "expected_version": 1})[0], 200)
            self.assertNotIn("bob", {u["username"] for u in call("GET", "/v1/users", self.tokens["eve"])[1]})
            self.assertEqual(call("POST", f"/v1/users/{self.b['id']}/active", self.tokens["alice"], {"active": True})[0], 200)
            self.assertEqual(self.login(call, "bob")[0], 200)

    def test_last_active_owner_and_last_admin_are_protected(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-bob','bob 的 Team',0)")
            db.execute("INSERT INTO members VALUES('team-bob',?,'owner')", (self.b["id"],))
        with self.http() as call:
            status, result = call("POST", f"/v1/users/{self.b['id']}/active", self.tokens["alice"], {"active": False})
            self.assertEqual((status, result["code"]), (409, "last_owner"))
            self.assertIn("bob 的 Team", result["error"])
            status, result = call("POST", f"/v1/users/{self.a['id']}/admin", self.tokens["alice"], {"admin": False})
            self.assertEqual((status, result["code"]), (409, "last_admin"))
            status, result = call("POST", f"/v1/users/{self.a['id']}/active", self.tokens["alice"], {"active": False})
            self.assertEqual((status, result["code"]), (409, "last_admin"))
            self.assertEqual(call("POST", f"/v1/users/{self.e['id']}/admin", self.tokens["alice"], {"admin": True})[0], 200)
            self.assertEqual(call("POST", f"/v1/users/{self.a['id']}/admin", self.tokens["alice"], {"admin": False})[0], 200)
            # The demoted admin's token still carries the admin scope, but elevation needs the account to be admin.
            self.assertEqual(call("GET", "/v1/admin/users", self.tokens["alice"])[0], 403)

    def test_user_creation_is_audited_and_new_routes_are_documented(self):
        from octopus.schema import openapi
        with self.http() as call:
            status, created = call("POST", "/v1/users", self.tokens["alice"], {"username": "carol", "password": PASSWORD})
            self.assertEqual(status, 201)
        self.assertEqual([(e["action"], e["target"]) for e in self.service.audit_log(self.a)], [("user.create", created["id"])])
        paths = openapi()["paths"]
        for path, method in (("/v1/users/{id}/password", "post"), ("/v1/users/{id}/active", "post"),
                             ("/v1/users/{id}/admin", "post"), ("/v1/admin/users", "get"),
                             ("/v1/admin/users/{id}/tokens", "get")):
            self.assertEqual(paths[path][method].get("tags"), ["用户与权限（管理员）"], path)
        self.assertNotIn("tags", paths["/v1/me/password"]["post"])

    def test_admin_user_list_and_token_revocation(self):
        tid = self.team()
        with self.http() as call:
            self.login(call, "bob")
            status, users = call("GET", "/v1/admin/users", self.tokens["alice"])
            self.assertEqual(status, 200)
            bob = next(u for u in users if u["username"] == "bob")
            self.assertEqual({k: bob[k] for k in ("admin", "active", "tokens")}, {"admin": False, "active": True, "tokens": 2})
            self.assertIsNotNone(bob["last_login"])
            self.assertEqual(bob["teams"], [{"id": tid, "name": "研发 Team", "role": "member"}])
            self.assertEqual(call("GET", "/v1/admin/users", self.tokens["bob"])[0], 403)
            status, tokens = call("GET", f"/v1/admin/users/{self.b['id']}/tokens", self.tokens["alice"])
            self.assertEqual(set(tokens[0]), {"id", "label", "scopes", "expires", "last_used"})
            target = next(t for t in tokens if t["label"] != "Web login")
            self.assertEqual(call("DELETE", f"/v1/admin/users/{self.b['id']}/tokens/{target['id']}", self.tokens["alice"], {})[0], 200)
            self.assertEqual(call("GET", "/v1/me", self.tokens["bob"])[0], 401)
            self.assertEqual(call("DELETE", f"/v1/admin/users/{self.b['id']}/tokens/{target['id']}", self.tokens["alice"], {})[0], 404)
        self.assertIn(("user.revoke_token", self.b["id"]), [(e["action"], e["target"]) for e in self.service.audit_log(self.a)])

class TokenTests(AccessCase):
    def last_used(self, name="bob"):
        with self.db.read() as db:
            return db.execute("SELECT last_used FROM tokens WHERE user_id=? AND label='API'",
                              (self.users[name]["id"],)).fetchone()[0]

    def test_last_used_is_written_at_most_once_a_minute(self):
        import time
        from unittest.mock import patch
        start = time.time()
        with self.http() as call:
            with patch("octopus.server.time.time", return_value=start):
                self.assertEqual(call("GET", "/v1/me", self.tokens["bob"])[0], 200)
            self.assertEqual(self.last_used(), start)
            with patch("octopus.server.time.time", return_value=start + 30):
                call("GET", "/v1/me", self.tokens["bob"])
            self.assertEqual(self.last_used(), start)
            with patch("octopus.server.time.time", return_value=start + 61):
                status, me = call("GET", "/v1/me", self.tokens["bob"])
            self.assertEqual(self.last_used(), start + 61)
            self.assertNotIn("last_used", me)

    def test_token_list_hides_expired_and_shows_last_used(self):
        stale = self.token("bob", ["read"])
        with self.db.transaction() as db:
            db.execute("UPDATE tokens SET expires=1 WHERE user_id=? AND scopes=?", (self.b["id"], '["read"]'))
        with self.http() as call:
            status, tokens = call("GET", "/v1/tokens", self.tokens["bob"])
            self.assertEqual(status, 200)
            self.assertEqual(len(tokens), 1)
            self.assertIn("last_used", tokens[0])
            self.assertEqual(call("GET", "/v1/me", stale)[0], 401)

    def test_delete_needs_no_body(self):
        spare = self.token("bob", ["read"])
        with self.db.read() as db:
            tid = db.execute("SELECT id FROM tokens WHERE user_id=? AND scopes=?", (self.b["id"], '["read"]')).fetchone()[0]
        with self.http() as call:
            status, result = call("DELETE", f"/v1/tokens/{tid}", self.tokens["bob"])
            self.assertEqual((status, result), (200, {"ok": True}))
            self.assertEqual(call("GET", "/v1/me", spare)[0], 401)

class PreviewAccessTests(AccessCase):
    """B6: a preview link lasts 10 minutes and every file it serves re-checks its holder's access."""

    def test_revoked_access_stops_an_issued_preview_link(self):
        import base64
        import http.client
        from unittest.mock import patch
        from octopus.server import preview_key, preview_ticket
        tid = self.team()
        rid = self.start(self.b, team_id=tid)["id"]
        ticket = preview_ticket(rid, self.b["id"], preview_key(self.db.root))
        file = {"content": base64.b64encode(b"<h1>ok</h1>").decode(), "size": 11}
        app = server(self.db, 0)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        def fetch():
            connection = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=5)
            connection.request("GET", f"/preview/{ticket}/index.html")
            response = connection.getresponse()
            data = response.read()
            connection.close()
            return response.status, data
        try:
            with patch("octopus.transport.worker_request", return_value=file):
                self.assertEqual(fetch(), (200, b"<h1>ok</h1>"))
                with self.db.transaction() as db:
                    db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, self.b["id"]))
                status, data = fetch()
                self.assertEqual((status, json.loads(data)["code"]), (403, "preview_forbidden"))
                with self.db.transaction() as db:
                    db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.b["id"]))
                    db.execute("UPDATE users SET active=0 WHERE id=?", (self.b["id"],))
                status, data = fetch()
                self.assertEqual((status, json.loads(data)["code"]), (403, "preview_forbidden"))
        finally:
            app.shutdown()
            app.server_close()
            thread.join(2)

    def test_a_new_preview_link_says_it_lasts_ten_minutes(self):
        from unittest.mock import patch
        self.shared_env(users=("bob",))
        rid = self.start(self.b)["id"]
        with self.http() as call, patch("octopus.transport.worker_request", return_value={"entry": "index.html"}):
            status, result = call("POST", f"/v1/runs/{rid}/preview", self.tokens["bob"], {})
            self.assertEqual((status, result["expires_in"]), (200, 600))
            self.assertEqual(result["url"].split("/")[2].count("."), 3)

class ReviewFixTests(AccessCase):
    """Findings of the phase-1 review, each reproduced first."""

    def test_a_team_always_keeps_an_active_owner(self):
        from octopus.security import Error
        with self.db.transaction() as db:
            carol = create_user(db, "carol", PASSWORD)
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-bc','BC',0)")
            db.execute("INSERT INTO members VALUES('team-bc',?,'owner')", (self.b["id"],))
            db.execute("INSERT INTO members VALUES('team-bc',?,'owner')", (carol["id"],))
        self.service.set_active(self.a, carol["id"], {"active": False})       # bob is still an active owner
        with self.assertRaises(Error) as caught:
            self.service.membership(self.b, "team-bc", {"username": "bob", "role": "member"})
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(Error):
            self.service.membership(self.b, "team-bc", {"username": "bob"}, remove=True)

    def test_a_failed_last_used_write_never_fails_the_request(self):
        import sqlite3
        from unittest.mock import patch
        with self.http() as call, patch.object(self.db, "transaction", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertEqual(call("GET", "/v1/me", self.tokens["bob"])[0], 200)

    def test_password_checks_are_throttled_per_user(self):
        with self.http() as call:
            for _ in range(5):
                self.assertEqual(call("POST", "/v1/me/password", self.tokens["bob"],
                                      {"current": "wrong-password-0", "password": "Another-password-456!"})[0], 403)
            status, result = call("POST", "/v1/me/password", self.tokens["bob"],
                                  {"current": PASSWORD, "password": "Another-password-456!"})
            self.assertEqual((status, result["code"]), (429, "rate_limited"))
            self.assertEqual(call("POST", "/v1/me/password", self.tokens["eve"],     # another user is not affected
                                  {"current": PASSWORD, "password": "Another-password-456!"})[0], 200)

    def oversight_run(self):
        self.shared_env(users=("bob",))
        return self.start(self.b)["id"]

    def test_the_native_entry_tells_an_admin_why_it_stays_closed(self):
        from octopus import crew_entry
        from octopus.security import Error
        rid = self.oversight_run()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET native_parent='octopus-x', status='stopped' WHERE id=?", (rid,))
            # bob's own dedicated Worker: he may open the native entry; alice only oversees.
            db.execute("UPDATE workers SET scope='dedicated', owner_id=? WHERE id='env'", (self.b["id"],))
        with self.assertRaises(Error) as caught:
            crew_entry.create(self.service, self.a, rid)
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("管理员", str(caught.exception))
        self.assertFalse(self.service.run(self.a, rid)["permissions"]["native"])
        self.assertTrue(self.service.run(self.b, rid)["permissions"]["native"])

    def test_an_admin_action_names_the_admin_in_the_sessions_events(self):
        rid = self.oversight_run()
        self.service.operate(self.a, rid, "stop", {"request_id": "stop-0000000009", "expected_version": 1})
        event = next(e for e in self.service.events(self.b, rid) if e["kind"] == "command.stop")
        self.assertEqual((event["payload"]["oversight"], event["payload"]["by"]), (True, {"id": self.a["id"], "username": "alice"}))

    def test_admin_reads_of_someone_elses_session_are_audited_once_an_hour(self):
        from unittest.mock import patch
        from octopus import crew_entry
        rid = self.oversight_run()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET native_parent='octopus-x' WHERE id=?", (rid,))
        reply = {"entry": "index.html", "messages": []}
        with self.http() as call, patch("octopus.transport.worker_request", return_value=reply), \
                patch("octopus.crew_entry.worker_request", return_value=reply):
            for _ in range(2):
                self.assertEqual(call("GET", f"/v1/runs/{rid}", self.tokens["alice"])[0], 200)
                self.assertEqual(call("GET", f"/v1/runs/{rid}/observe", self.tokens["alice"])[0], 200)
            self.assertEqual(call("GET", f"/v1/runs/{rid}/export", self.tokens["alice"])[0], 200)
            self.assertEqual(call("POST", f"/v1/runs/{rid}/preview", self.tokens["alice"], {})[0], 200)
            call("GET", f"/v1/runs/{rid}", self.tokens["bob"])               # the owner's own reads are not audited
        actions = sorted(e["action"] for e in self.service.audit_log(self.a))
        self.assertEqual(actions, ["run.export", "run.observe", "run.preview", "run.view"])

class PermissionMatrixTests(AccessCase):
    """Spec §10 phase-1 acceptance: role × route → status. Writes carry a wrong expected_version, so an allowed caller
    gets 409 and nothing changes."""

    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            self.carol = create_user(db, "carol", PASSWORD)
            self.tokens["carol"] = issue_token(db, self.carol["id"])["token"]
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-m','矩阵 Team',0)")
            db.execute("INSERT INTO members VALUES('team-m',?,'owner')", (self.carol["id"],))
            db.execute("INSERT INTO members VALUES('team-m',?,'member')", (self.b["id"],))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('env-m','env',?,?,'shared')",
                       (self.a["id"], dumps({"capabilities": {}})))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('env-m','team-m')")
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('env-m',?)", (self.b["id"],))
            carol = authenticate(db, self.tokens["carol"])
        self.team_run = self.start(carol, "start-matrix-001", team_id="team-m")["id"]
        self.bob_run = self.start(self.b, "start-matrix-002")["id"]
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET native_parent='octopus-m' WHERE id=?", (self.bob_run,))
        self.flow = self.service.save_workflow(carol, {"name": "矩阵流程", "steps": [
            {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})
        self.service.share(carol, self.flow["id"], {"kind": "user", "principal": self.b["id"], "role": "viewer"})
        self.tokens["alice_plain"] = self.token("alice", ["read", "execute", "manage"])

    def test_matrix(self):
        wrong = {"request_id": "matrix-0000000001", "expected_version": 999}
        steps = [{"id": "code", "name": "实现", "tool": "codex", "prompt": "实现"}]
        roles = ("alice", "alice_plain", "carol", "bob", "eve")   # admin, admin without admin scope, owner, member, outsider
        matrix = [
            ("GET", f"/v1/runs/{self.team_run}", None, (200, 404, 200, 200, 404)),
            ("POST", f"/v1/runs/{self.team_run}/stop", wrong, (409, 404, 409, 409, 404)),
            ("POST", f"/v1/runs/{self.team_run}/delete", {**wrong, "confirmation": self.team_run}, (409, 404, 409, 403, 404)),
            ("GET", f"/v1/runs/{self.bob_run}", None, (200, 404, 404, 200, 404)),
            ("POST", f"/v1/runs/{self.bob_run}/crew-entry", {}, (403, 404, 404, 403, 404)),
            ("GET", "/v1/admin/users", None, (200, 403, 403, 403, 403)),
            ("GET", "/v1/admin/audit", None, (200, 403, 403, 403, 403)),
            ("POST", f"/v1/users/{self.b['id']}/active", {"active": True}, (200, 403, 403, 403, 403)),
            ("POST", "/v1/teams/team-m/members", {"username": "eve", "role": "bogus"}, (400, 404, 400, 403, 404)),
            ("POST", f"/v1/workflows/{self.flow['id']}/versions", {"name": "矩阵流程", "steps": steps, "expected_version": 999},
             (404, 404, 409, 403, 404)),
        ]
        with self.http() as call:
            for method, path, body, expected in matrix:
                for role, status in zip(roles, expected):
                    with self.subTest(route=f"{method} {path}", role=role):
                        self.assertEqual(call(method, path, self.tokens[role], body)[0], status)

if __name__ == "__main__":
    unittest.main()
