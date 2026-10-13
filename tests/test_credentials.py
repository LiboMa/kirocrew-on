"""D35 §3: repository credentials — personal, Team and platform scopes, grants, a write-only sealed secret, the host's
own check, and audit; the secret never comes back through any interface."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from octopus import credentials
from octopus.db import dumps
from octopus.security import Error
from octopus.server import required_scope, server
from tests.test_access import AccessCase

SECRET = "github_pat_11ABCDEFG0123456789_secretvalue1a2b"


class FakeHost(BaseHTTPRequestHandler):
    """GitHub's GET /user and GitLab's GET /api/v4/user (+ personal_access_tokens/self), answering only to SECRET."""
    seen = []

    def do_GET(self):
        FakeHost.seen.append((self.path, dict(self.headers)))
        good = self.headers.get("Authorization") == "Bearer " + SECRET or self.headers.get("PRIVATE-TOKEN") == SECRET
        if not good:
            return self.answer(401, {"message": "Bad credentials"})
        if self.path.endswith("/personal_access_tokens/self"):
            return self.answer(200, {"expires_at": "2026-12-31"})
        if self.path.endswith("/user") and "/api/v4" in self.path:
            return self.answer(200, {"username": "gl-alice"})
        return self.answer(200, {"login": "gh-alice"}, {"github-authentication-token-expiration": "2026-12-31 00:00:00 UTC"})

    def answer(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class CredentialCase(AccessCase):
    def setUp(self):
        super().setUp()
        self.tid = self.team(environment=False)
        self.store = self.service.credentials

    def make(self, actor, **body):
        return self.store.create(actor, {"name": "GitHub Token", "kind": "github_pat", "host": "github.com",
                                         "scope": "user", "secret": SECRET, **body})

    def audits(self):
        with self.db.read() as db:
            return [dict(r) for r in db.execute("SELECT action,target,detail FROM audit WHERE kind='credential' ORDER BY id")]

    @contextmanager
    def http(self):
        app = server(self.db, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{app.server_address[1]}"

        def call(method, path, token, body=None):
            import urllib.error
            import urllib.request
            request = urllib.request.Request(base + path, method=method, data=dumps(body).encode() if body is not None else None,
                                             headers={"Authorization": "Bearer " + token,
                                                      **({"Content-Type": "application/json"} if body is not None else {})})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, exc.read().decode()
        try:
            yield call
        finally:
            app.shutdown()
            app.server_close()


class ScopeTests(CredentialCase):
    def test_each_scope_is_seen_and_managed_by_the_right_people(self):
        mine = self.make(self.b, name="bob 的 GitHub")
        team = self.make(self.a, name="团队 GitLab", kind="gitlab_token", host="gitlab.example.com", scope="team",
                         team_id=self.tid, repos=["platform/*"])
        platform = self.make(self.a, name="平台只读", scope="platform", repos=["kirocrew/*"])
        self.assertEqual((mine["hint"], mine["scope"], mine["owner"]["id"]), ("…1a2b", "user", self.b["id"]))
        self.assertNotIn("secret", mine)
        names = lambda actor: {c["name"]: c["can_manage"] for c in self.store.list(actor)}
        self.assertEqual(names(self.b), {"bob 的 GitHub": True, "团队 GitLab": False})   # a member uses, the owner manages
        self.assertEqual(names(self.a), {"团队 GitLab": True, "平台只读": True})          # alice: owner and admin
        self.assertEqual(names(self.e), {})
        for actor, cid, status in ((self.e, mine["id"], 404), (self.b, team["id"], 403), (self.b, platform["id"], 404),
                                   (self.a, mine["id"], 404)):
            with self.assertRaises(Error) as caught:
                self.store.update(actor, cid, {"name": "改名"})
            self.assertEqual(caught.exception.status, status, (actor["username"], cid))

    def test_who_may_create_team_and_platform_credentials(self):
        with self.assertRaises(Error) as caught:
            self.make(self.b, scope="team", team_id=self.tid)
        self.assertEqual(caught.exception.status, 403)   # a member, not the owner
        with self.assertRaises(Error) as caught:
            self.make(self.e, scope="team", team_id=self.tid)
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(Error) as caught:
            self.make(self.b, scope="platform")
        self.assertEqual(caught.exception.status, 403)

    def test_a_platform_credential_is_used_only_where_it_is_granted(self):
        platform = self.make(self.a, name="平台只读", scope="platform")
        self.assertEqual([c["name"] for c in self.store.list(self.b)], [])
        self.store.grant(self.a, platform["id"], {"team_id": self.tid})
        self.assertEqual([c["name"] for c in self.store.list(self.b)], ["平台只读"])
        self.assertEqual([c["name"] for c in self.store.list(self.e)], [])
        self.store.grant(self.a, platform["id"], {"username": "eve"})
        self.assertEqual([c["name"] for c in self.store.list(self.e)], ["平台只读"])
        self.store.revoke(self.a, platform["id"], {"team_id": self.tid})
        self.assertEqual([c["name"] for c in self.store.list(self.b)], [])
        with self.assertRaises(Error) as caught:
            self.store.grant(self.b, platform["id"], {"username": "bob"})
        self.assertIn(caught.exception.status, {403, 404})

    def test_rotating_keeps_the_id_and_replaces_the_secret(self):
        made = self.make(self.b)
        rotated = self.store.update(self.b, made["id"], {"secret": "github_pat_22ZZZZZZZZZZZZ_rotatedvalue9z9z"})
        self.assertEqual((rotated["id"], rotated["hint"]), (made["id"], "…9z9z"))
        self.assertEqual(self.store.secret(made["id"]), "github_pat_22ZZZZZZZZZZZZ_rotatedvalue9z9z")
        self.assertEqual([a["action"] for a in self.audits()], ["credential.create", "credential.rotate"])

    def test_disabling_and_deleting(self):
        made = self.make(self.b)
        self.assertTrue(self.store.update(self.b, made["id"], {"disabled": True})["disabled"])
        self.assertTrue(self.store.list(self.b)[0]["disabled"])
        self.store.delete(self.b, made["id"])
        self.assertEqual(self.store.list(self.b), [])
        self.assertEqual([a["action"] for a in self.audits()], ["credential.create", "credential.disable", "credential.delete"])

    def test_invalid_input_is_refused(self):
        for body in ({"kind": "svn_password"}, {"host": "https://github.com"}, {"host": "github.com/x"}, {"secret": "short"},
                     {"secret": "has space in it 12345"}, {"repos": ["../etc"]}, {"repos": "owner/repo"}, {"access": "admin"},
                     {"inject": True}, {"name": ""}):
            with self.assertRaises(Error, msg=body) as caught:
                self.make(self.b, **body)
            self.assertIn(caught.exception.status, {400, 409}, body)

    def test_repository_patterns_and_paths(self):
        self.assertEqual(credentials.repo_path("https://github.com/kirocrew/app.git"), ("github.com", "kirocrew/app"))
        self.assertEqual(credentials.repo_path("https://gitlab.example.com/group/sub/app"), ("gitlab.example.com", "group/sub/app"))
        for bad in ("git@github.com:kirocrew/app.git", "ssh://git@github.com/kirocrew/app", "http://github.com/a/b"):
            with self.assertRaises(ValueError):
                credentials.repo_path(bad)
        self.assertEqual(credentials.covers([], "a/b"), 0)
        self.assertEqual(credentials.covers(["a/*"], "a/b"), 1)
        self.assertEqual(credentials.covers(["a/*", "a/sub/*"], "a/sub/c"), 2)
        self.assertEqual(credentials.covers(["a/b"], "a/b"), 3)
        self.assertIsNone(credentials.covers(["a/b"], "a/c"))
        self.assertIsNone(credentials.covers(["a/*"], "ab/c"))


class HostCheckTests(CredentialCase):
    def setUp(self):
        super().setUp()
        FakeHost.seen = []
        self.host = ThreadingHTTPServer(("127.0.0.1", 0), FakeHost)
        threading.Thread(target=self.host.serve_forever, daemon=True).start()
        self.addCleanup(self.host.server_close)
        self.addCleanup(self.host.shutdown)
        base = f"http://127.0.0.1:{self.host.server_address[1]}"
        self.patch = patch("octopus.credentials.api_base", lambda kind, host: base + ("/api/v4" if kind == "gitlab_token" else ""))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_the_host_confirms_the_account_and_the_expiry(self):
        github = self.make(self.b)
        result = self.store.test(self.b, github["id"])
        self.assertEqual((result["ok"], result["account"]), (True, "gh-alice"))
        self.assertEqual(time.strftime("%Y-%m-%d", time.gmtime(result["expires"])), "2026-12-31")
        gitlab = self.make(self.b, name="GitLab", kind="gitlab_token", host="gitlab.example.com")
        result = self.store.test(self.b, gitlab["id"])
        self.assertEqual((result["ok"], result["account"]), (True, "gl-alice"))
        self.assertIsNotNone(self.store.list(self.b)[1]["expires"])
        self.assertEqual(self.audits()[-1]["action"], "credential.test")

    def test_a_refused_secret_is_reported_without_the_secret(self):
        made = self.make(self.b)
        self.store.update(self.b, made["id"], {"secret": "github_pat_wrongwrongwrong_0000"})
        result = self.store.test(self.b, made["id"])
        self.assertEqual(result["ok"], False)
        self.assertIn("401", result["error"])
        self.assertNotIn("wrongwrong", json.dumps(result))


class HttpTests(CredentialCase):
    def test_routes_and_scopes(self):
        self.assertEqual(required_scope("GET", "/v1/credentials"), "read")
        self.assertEqual(required_scope("POST", "/v1/credentials"), "manage")
        self.assertEqual(required_scope("PATCH", "/v1/credentials/c1"), "manage")
        self.assertEqual(required_scope("POST", "/v1/credentials/c1/test"), "manage")
        self.assertEqual(required_scope("POST", "/v1/credentials/c1/grants"), "admin")
        self.assertEqual(required_scope("DELETE", "/v1/credentials/c1/grants"), "admin")

    def test_the_secret_never_comes_back(self):
        with self.http() as call:
            status, created = call("POST", "/v1/credentials", self.tokens["bob"],
                                   {"name": "bob GitHub", "kind": "github_pat", "host": "github.com", "scope": "user",
                                    "secret": SECRET, "repos": ["kirocrew/*"]})
            self.assertEqual(status, 201, created)
            cid = json.loads(created)["id"]
            bodies = [created]
            bodies.append(call("GET", "/v1/credentials", self.tokens["bob"])[1])
            bodies.append(call("PATCH", f"/v1/credentials/{cid}", self.tokens["bob"], {"name": "bob 的 GitHub"})[1])
            bodies.append(call("PATCH", f"/v1/credentials/{cid}", self.tokens["bob"], {"repos": ["../bad"]})[1])
            bodies.append(call("POST", "/v1/credentials", self.tokens["bob"],
                               {"name": "x", "kind": "github_pat", "host": "bad host", "scope": "user", "secret": SECRET})[1])
            bodies.append(call("GET", "/v1/admin/audit", self.tokens["alice"])[1])
            self.assertEqual(call("GET", "/v1/credentials", self.tokens["eve"]), (200, "[]"))
        with self.db.read() as db:
            stored = db.execute("SELECT secret FROM credentials").fetchone()["secret"]
        for body in bodies + [stored]:
            self.assertNotIn(SECRET, body)
            self.assertNotIn(SECRET[10:30], body)


if __name__ == "__main__":
    unittest.main()
