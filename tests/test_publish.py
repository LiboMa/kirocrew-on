"""D35 §5.2: publishing a Session — the Worker commits what is pending (without .env, dependencies or anything that looks
like a credential) and exports a bundle on top of the clone's baseline; the platform pushes one branch and opens a
draft pull / merge request with the bound write credential, which never reaches the execution environment."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from octopus import sources
from octopus.worker import serve
from tests.test_archive import RID, TOKEN, make_worker


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True).stdout.strip()


class WorkerBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.node, self.project = make_worker(self.root)
        self.base = sources.read_record(self.node.path(RID))["commit"]

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, files):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)

    def call(self, body, token=TOKEN):
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/publish/bundle", method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, dict(exc.headers), exc.read()

    def test_pending_changes_are_committed_without_secrets_or_dependencies(self):
        self.write({"src/app.js": "console.log(1)", ".env": "SECRET=1", ".env.example": "SECRET=", "node_modules/x/i.js": "x",
                    "web/.env.local": "KEY=2", "pkg/__pycache__/m.pyc": "c"})
        status, headers, bundle = self.call({"run_id": RID, "message": "KiroCrew ON：订单系统（run-x）"})
        self.assertEqual(status, 200, bundle[:200])
        commit = headers["X-Publish-Commit"]
        self.assertEqual((headers["X-Publish-Base"], headers["X-Publish-Branch"]), (self.base, "octopus/" + RID))
        self.assertEqual(int(headers["Content-Length"]), len(bundle))
        files = set(git(self.project, "ls-tree", "-r", "--name-only", commit).splitlines())
        self.assertTrue({"src/app.js", ".env.example"} <= files, files)
        self.assertFalse({".env", "node_modules/x/i.js", "web/.env.local", "pkg/__pycache__/m.pyc"} & files)
        self.assertEqual(git(self.project, "log", "-1", "--format=%an|%s", commit), "KiroCrew ON|KiroCrew ON：订单系统（run-x）")
        saved = self.root / "out.bundle"
        saved.write_bytes(bundle)
        self.assertIn(f"{commit} refs/heads/octopus/{RID}", git(self.project, "bundle", "list-heads", str(saved)))
        empty = self.root / "empty"
        subprocess.run(["git", "init", "-q", "--bare", str(empty)], check=True)
        verify = subprocess.run(["git", "-C", str(empty), "bundle", "verify", str(saved)], capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stderr)   # complete: it needs nothing the platform does not have
        subprocess.run(["git", "-C", str(empty), "fetch", "-q", str(saved), f"octopus/{RID}:x"], check=True)
        self.assertEqual(subprocess.run(["git", "-C", str(empty), "merge-base", "--is-ancestor", self.base, commit]).returncode, 0)
        self.assertEqual((self.project / ".env").read_text(), "SECRET=1")   # left in the workspace, untouched

    def test_nothing_to_publish(self):
        status, _, body = self.call({"run_id": RID})
        self.assertEqual((status, json.loads(body)["code"]), (409, "nothing_to_publish"))

    def test_secrets_in_the_workspace_are_never_published(self):
        token = "ghp_" + "A1b2C3d4" * 5
        self.write({"src/config.js": f"const token = '{token}';", "src/ok.js": "ok"})
        status, _, body = self.call({"run_id": RID})
        answer = json.loads(body)
        self.assertEqual((status, answer["code"]), (409, "publish_secret_found"))
        self.assertIn("src/config.js", answer["error"])
        self.assertNotIn(token, answer["error"])

    def test_a_worker_needs_its_token(self):
        self.assertEqual(self.call({"run_id": RID}, token="y" * 40)[0], 401)


if __name__ == "__main__":
    unittest.main()


# --- the platform side (Task 2) ---------------------------------------------------------------------------------------
import io
import os
import shutil
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from octopus import publish
from octopus import gateway_client
from octopus.db import dumps
from octopus.security import Error
from octopus.worker import Worker
from tests.test_access import AccessCase
from tests.test_private_clone import BACKEND, GitHost, TOKEN as SECRET   # the token the test git host accepts


class FakeApi(BaseHTTPRequestHandler):
    """GitHub's repository and pulls endpoints, and GitLab's project and merge requests, kept in memory."""
    pulls, created, seen = [], [], []

    def answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return self.headers.get("Authorization") == "Bearer " + SECRET or self.headers.get("PRIVATE-TOKEN") == SECRET

    def do_GET(self):
        FakeApi.seen.append(("GET", self.path))
        if not self.authorized():
            return self.answer(401, {"message": "Bad credentials"})
        path, _, query = self.path.partition("?")
        if path.endswith("/pulls") or path.endswith("/merge_requests"):
            branch = query.split("head=owner%3A")[-1].split("&")[0] if "head=" in query else query.split("source_branch=")[-1].split("&")[0]
            return self.answer(200, [p for p in FakeApi.pulls if p["branch"] == urllib.parse.unquote(branch)])
        return self.answer(200, {"default_branch": "main"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeApi.seen.append(("POST", self.path))
        if not self.authorized():
            return self.answer(401, {"message": "Bad credentials"})
        FakeApi.created.append(body)
        number = len(FakeApi.pulls) + 1
        branch = body.get("head") or body.get("source_branch")
        item = {"branch": branch, "number": number, "iid": number, "html_url": f"https://forge.example/owner/app/pull/{number}",
                "web_url": f"https://forge.example/owner/app/-/merge_requests/{number}"}
        FakeApi.pulls.append(item)
        return self.answer(201, item)

    def log_message(self, *args):
        pass


import urllib.parse


@unittest.skipUnless(BACKEND.exists() and shutil.which("openssl"), "git-http-backend and openssl are needed")
class PublishCase(AccessCase):
    def setUp(self):
        super().setUp()
        FakeApi.pulls, FakeApi.created, FakeApi.seen = [], [], []
        root = Path(self.tmp.name)
        GitHost.root = root / "repos"
        self.remote = GitHost.root / "owner/app.git"
        self.remote.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.remote), "config", "http.receivepack", "true"], check=True)
        seed = root / "seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        (seed / "README.md").write_text("app")
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(["git", "-C", str(seed), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "first"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", str(self.remote), "main"], check=True)
        cert, key = root / "cert.pem", root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True)
        self.git_server = ThreadingHTTPServer(("127.0.0.1", 0), GitHost)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.git_server.socket = context.wrap_socket(self.git_server.socket, server_side=True)
        self.api_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        for server in (self.git_server, self.api_server):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        env = patch.dict(os.environ, {"GIT_SSL_CAINFO": str(cert), "GIT_CONFIG_GLOBAL": str(root / "no-gitconfig")})
        env.start()
        self.addCleanup(env.stop)
        api = f"http://127.0.0.1:{self.api_server.server_address[1]}"
        base = patch("octopus.credentials.api_base", lambda kind, host: api + ("/api/v4" if kind == "gitlab_token" else ""))
        base.start()
        self.addCleanup(base.stop)
        self.host = f"127.0.0.1:{self.git_server.server_address[1]}"
        self.url = f"https://{self.host}/owner/app.git"
        # a real Worker, its native Gateway stubbed
        self.node = Worker(root / "worker", TOKEN, crew_home=root / "crew")
        self.node.native = lambda path, body=None, method=None: (
            (_ for _ in ()).throw(gateway_client.GatewayError(404, '{"error":"not found"}')) if body is None and "/slots/" in path
            else {"key": body["name"]} if path == "/api/chat/slots" else {"ok": True})
        self.worker_bodies = []
        plan = self.node.publish_plan
        self.node.publish_plan = lambda body: (self.worker_bodies.append(body), plan(body))[1]
        self.worker_server = serve(self.node, 0)
        threading.Thread(target=self.worker_server.serve_forever, daemon=True).start()
        self.addCleanup(self.worker_server.server_close)
        self.addCleanup(self.worker_server.shutdown)
        self.tid = self.team(environment=False)
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,team_id,config) VALUES('w-p','p',?,?)",
                       (self.tid, dumps({"url": f"http://127.0.0.1:{self.worker_server.server_address[1]}", "token": TOKEN,
                                         "capabilities": {"isolation": "dedicated"}})))
        self.cred = self.service.credentials.create(self.b, {"name": "bob 写", "kind": "github_pat", "host": self.host,
                                                             "scope": "user", "access": "write", "secret": SECRET})

    def session(self, actor=None, request="start-0000000501", cred=None):
        actor = actor or self.b
        rid = self.service.start(actor, {"request_id": request, "input": "研发需求与真实验证", "team_id": self.tid, "worker_id": "w-p",
                                         "source": {"type": "git", "url": self.url},
                                         "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                         "recovery": {"enabled": False, "max_cycles": 2},
                                         **({"credentials": {"source": cred}} if cred else {})})["id"]
        auth = {"kind": "github_pat", "host": self.host, "username": "x-access-token", "secret": SECRET}
        result = self.node.prepare({"run_id": rid, "source": {"type": "git", "url": self.url}, "git_auth": auth})
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET workspace=?,native_parent='octopus-x',status='completed' WHERE id=?",
                       (result["workspace"], rid))
        return rid, Path(result["workspace"])

    def remote_head(self, branch):
        out = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "--verify", "-q", "refs/heads/" + branch],
                             capture_output=True, text=True)
        return out.stdout.strip() or None


class PublishTests(PublishCase):
    def test_a_session_is_pushed_and_a_draft_pull_request_opened(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "src").mkdir()
        (project / "src/feature.js").write_text("export const x = 1;")
        record = self.service.publish(self.b, rid, {})
        self.assertEqual((record["branch"], record["kind"], record["number"], record["reused"]),
                         ("octopus/" + rid, "pull_request", 1, False))
        self.assertEqual(self.remote_head("octopus/" + rid), record["commit"])
        self.assertIsNone(self.remote_head("main") == record["commit"] or None)   # the default branch untouched
        created = FakeApi.created[0]
        self.assertEqual((created["head"], created["base"], created["draft"]), ("octopus/" + rid, "main", True))
        view = self.service.run(self.b, rid)
        self.assertEqual(view["publish"]["url"], "https://forge.example/owner/app/pull/1")
        with self.db.read() as db:
            kinds = [r[0] for r in db.execute("SELECT kind FROM events WHERE run_id=?", (rid,))]
            use = db.execute("SELECT detail FROM audit WHERE action='credential.use' ORDER BY id DESC").fetchone()["detail"]
        self.assertIn("run.published", kinds)
        self.assertEqual(json.loads(use)["access"], "write")

    def test_publishing_again_reuses_the_pull_request(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        first = self.service.publish(self.b, rid, {})
        (project / "b.txt").write_text("2")
        second = self.service.publish(self.b, rid, {})
        self.assertEqual((second["number"], second["reused"]), (first["number"], True))
        self.assertEqual(len(FakeApi.created), 1)
        self.assertEqual(self.remote_head("octopus/" + rid), second["commit"])

    def test_a_branch_changed_by_someone_else_is_not_overwritten(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        self.service.publish(self.b, rid, {})
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", "-b", "octopus/" + rid, str(self.remote), str(other)], check=True)
        (other / "theirs.txt").write_text("theirs")
        subprocess.run(["git", "-C", str(other), "add", "."], check=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.email=o@o", "-c", "user.name=o", "commit", "-q", "-m", "theirs"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "octopus/" + rid], check=True)
        theirs = self.remote_head("octopus/" + rid)
        (project / "b.txt").write_text("2")
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "publish_conflict"))
        self.assertEqual(self.remote_head("octopus/" + rid), theirs)

    def test_who_and_when_one_may_publish(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        with self.assertRaises(Error) as caught:
            self.service.publish(self.e, rid, {})
        self.assertEqual(caught.exception.status, 404)
        alices, project2 = self.session(self.a, "start-0000000502")
        (project2 / "a.txt").write_text("1")
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, alices, {})   # a member, not the creator
        self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(Error) as caught:
            self.service.publish(self.a, alices, {})   # no credential bound
        self.assertEqual(caught.exception.code, "credential_unavailable")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='running' WHERE id=?", (rid,))
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {})
        self.assertEqual(caught.exception.code, "publish_busy")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (rid,))
        for branch in ("main", "feature/x", "octopus/../main"):
            with self.assertRaises(Error, msg=branch) as caught:
                self.service.publish(self.b, rid, {"branch": branch})
            self.assertEqual(caught.exception.status, 400, branch)
        self.service.credentials.update(self.b, self.cred["id"], {"access": "read"})
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {})
        self.assertEqual(caught.exception.code, "credential_read_only")

    def test_the_write_credential_never_reaches_the_worker(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        self.service.publish(self.b, rid, {})
        self.assertTrue(self.worker_bodies)
        self.assertNotIn(SECRET, json.dumps(self.worker_bodies))
        leaks = [p for p in Path(self.tmp.name).rglob("*") if p.is_file() and not p.is_symlink()
                 and "repos" not in p.parts and SECRET.encode() in p.read_bytes()]
        self.assertEqual(leaks, [])


class ReceiveTests(unittest.TestCase):
    def test_a_broken_bundle_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for data, length in ((b"# v2 git bundle\n" + b"x" * 100, 400), (b"not a bundle at all" * 10, 190)):
                with self.assertRaises(Error) as caught:
                    publish.receive(io.BytesIO(data), length, work / f"bare-{length}", "octopus/run-x", "0" * 40)
                self.assertEqual(caught.exception.code, "publish_invalid")

    def test_gitlab_gets_a_draft_merge_request(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        FakeApi.pulls, FakeApi.created = [], []
        api = f"http://127.0.0.1:{server.server_address[1]}/api/v4"
        with patch("octopus.credentials.api_base", lambda kind, host: api):
            result = publish.pull_request("gitlab_token", "gitlab.example.com", "group/sub/app", "octopus/run-x", None,
                                          "订单系统", "说明", SECRET)
        self.assertEqual((result["number"], result["url"]), (1, "https://forge.example/owner/app/-/merge_requests/1"))
        self.assertEqual(FakeApi.created[0]["title"], "Draft: 订单系统")
        self.assertEqual(FakeApi.created[0]["target_branch"], "main")
        self.assertIn(("POST", "/api/v4/projects/group%2Fsub%2Fapp/merge_requests"), FakeApi.seen)
