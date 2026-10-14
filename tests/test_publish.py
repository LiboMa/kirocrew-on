"""D35 §5.2: publishing a Session — the Worker commits what is pending (without .env, dependencies or anything that looks
like a credential) and exports a bundle on top of the clone's baseline; the platform pushes one branch and opens a
draft pull / merge request with the bound write credential, which never reaches the execution environment."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

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

    # --- final review: the bundle carries every commit, so everything new since the baseline is checked -----------------
    def agent_commit(self, message="agent"):
        subprocess.run(["git", "-C", str(self.project), "add", "-A", "-f"], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.email=a@a", "-c", "user.name=agent", "commit", "-q",
                        "--allow-empty", "-m", message], check=True)

    def published_bytes(self, bundle):
        """Every object of a bundle, decompressed, as one byte string."""
        saved, bare = self.root / "seen.bundle", self.root / "seen"
        shutil.rmtree(bare, ignore_errors=True)
        saved.write_bytes(bundle)
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run(["git", "-C", str(bare), "fetch", "-q", str(saved), f"refs/heads/octopus/{RID}:refs/heads/x"], check=True)
        return subprocess.run(["git", "-C", str(bare), "cat-file", "--batch-all-objects", "--batch"], capture_output=True,
                              check=True).stdout

    def test_a_refused_secret_is_not_left_in_the_branch(self):
        token = "ghp_" + "A1b2C3d4" * 5
        self.write({"src/config.js": f"const token = '{token}';"})
        self.assertEqual(json.loads(self.call({"run_id": RID})[2])["code"], "publish_secret_found")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)   # nothing committed on refusal
        self.write({"src/config.js": "const token = process.env.TOKEN;"})   # what the refusal tells the person to do
        status, _, bundle = self.call({"run_id": RID})
        self.assertEqual(status, 200, bundle[:300])
        self.assertNotIn(token.encode(), self.published_bytes(bundle))
        self.assertEqual(git(self.project, "status", "--porcelain"), "")   # the workspace's index matches the new HEAD

    def test_what_the_agent_committed_is_checked_too(self):
        token = "glpat-" + "Z9y8X7w6" * 3
        self.write({"src/a.js": f"key = '{token}'"})
        self.agent_commit("add key")
        self.write({"src/a.js": "key = env('KEY')"})
        self.agent_commit("remove key")   # gone from the tree, still in the history the bundle carries
        status, _, body = self.call({"run_id": RID})
        answer = json.loads(body)
        self.assertEqual((status, answer["code"]), (409, "publish_secret_found"), answer)
        self.assertIn("src/a.js", answer["error"])
        self.assertNotIn(token, answer["error"])
        self.assertIn("git reset --soft", answer["error"])   # how to get it out of the history

        subprocess.run(["git", "-C", str(self.project), "reset", "-q", "--soft", self.base], check=True)
        subprocess.run(["git", "-C", str(self.project), "reset", "-q"], check=True)
        self.write({"config/.env": "DB_PASSWORD=hunter2", "web/.env.local": "X=1"})
        self.agent_commit("env files")   # an agent may commit what the automatic commit leaves out
        status, _, body = self.call({"run_id": RID})
        answer = json.loads(body)
        self.assertEqual((status, answer["code"]), (409, "publish_secret_found"), answer)
        self.assertIn("config/.env", answer["error"])
        self.assertIn("web/.env.local", answer["error"])

    def test_every_file_is_read_as_it_is_stored(self):
        token = "github_pat_" + "Q1w2E3r4T5" * 3
        cases = {
            "配置/密钥.txt": token.encode(),                       # a name git quotes in a diff
            "assets/blob.bin": b"\x00\x01binary\x00" + token.encode(),   # a file git calls binary
            "notes/plus.md": b"++ " + token.encode(),               # an added line that reads like a diff header
            "app/settings.cfg": token.encode(),                      # marked -diff below
        }
        (self.project / ".gitattributes").write_text("*.cfg -diff\n")
        subprocess.run(["git", "-C", str(self.project), "config", "diff.noprefix", "true"], check=True)
        for name, data in cases.items():
            with self.subTest(name):
                (self.project / name).parent.mkdir(parents=True, exist_ok=True)
                (self.project / name).write_bytes(data)
                status, _, body = self.call({"run_id": RID})
                answer = json.loads(body)
                self.assertEqual((status, answer["code"]), (409, "publish_secret_found"), answer)
                self.assertIn(name, answer["error"])
                (self.project / name).unlink()

    def test_a_secret_in_a_commit_message_is_refused(self):
        self.write({"src/a.js": "a"})
        key = "AKIA" + "ABCDEFGHIJKLMNOP"   # a made-up key: a secret scanner on this machine may need telling so
        subprocess.run(["git", "-C", str(self.project), "config", "--add", "secrets.allowed", key], check=True)
        self.agent_commit("deploy with " + key)
        status, _, body = self.call({"run_id": RID})
        self.assertEqual((status, json.loads(body)["code"]), (409, "publish_secret_found"))

    def test_folders_left_out_are_never_read_into_git(self):
        content = "module.exports = 'only-in-node-modules-7f3a';\n"
        self.write({"node_modules/pkg/index.js": content, "src/app.js": "app", ".env/credentials": "x",
                    "deploy/.env.prod/key": "k"})
        status, headers, _ = self.call({"run_id": RID})
        self.assertEqual(status, 200)
        files = set(git(self.project, "ls-tree", "-r", "--name-only", headers["X-Publish-Commit"]).splitlines())
        self.assertIn("src/app.js", files)
        self.assertFalse({".env/credentials", "deploy/.env.prod/key"} & files, files)
        blob = subprocess.run(["git", "hash-object", "--stdin"], input=content, capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(subprocess.run(["git", "-C", str(self.project), "cat-file", "-e", blob]).returncode, 0,
                            "an excluded file was hashed into the object store")
        self.assertFalse((self.project / ".git/index.lock").exists())

    def test_a_bundle_too_large_is_not_left_behind(self):
        self.write({"src/app.js": "app" * 100})
        with patch("octopus.worker.PUBLISH_LIMIT", 10):
            status, _, body = self.call({"run_id": RID})
        self.assertEqual((status, json.loads(body)["code"]), (413, "publish_too_large"))
        self.assertFalse((self.node.path(RID) / ".publish.bundle").exists())

    def test_a_failure_after_the_header_only_ends_the_connection(self):
        import socket
        self.write({"src/app.js": "app"})

        def broken(source, target, length=0):
            target.write(source.read(10))
            raise OSError("the disk went away")
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        body = json.dumps({"run_id": RID}).encode()
        with patch("octopus.worker.shutil.copyfileobj", broken), \
                socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=30) as sock:
            sock.sendall(b"POST /publish/bundle HTTP/1.1\r\nHost: w\r\nAuthorization: Bearer " + TOKEN.encode()
                         + b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
                         + b"\r\n\r\n" + body)
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.0 200"), data[:100])
        self.assertEqual(data.count(b"HTTP/1."), 1, data[-300:])   # no error answer written into the bundle
        self.assertFalse((self.node.path(RID) / ".publish.bundle").exists())

    def test_a_git_timeout_is_an_error_not_a_crash(self):
        with patch("octopus.sources.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 180)):
            with self.assertRaises(ValueError):
                sources._git(self.project, "status")


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
    pulls, created, seen, refuse = [], [], [], None

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
        if FakeApi.refuse:
            return self.answer(FakeApi.refuse, {"message": "Resource not accessible by personal access token"})
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
        FakeApi.pulls, FakeApi.created, FakeApi.seen, FakeApi.refuse = [], [], [], None
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
        self.service.credentials.private_hosts = {self.host}   # as an admin allows a self-hosted instance on a private network
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
        self.assertEqual([u["result"] for u in self.uses(rid)], ["published", "publish_conflict"])   # each use audited

    def uses(self, rid):
        with self.db.read() as db:
            rows = db.execute("SELECT detail FROM audit WHERE action='credential.use' ORDER BY id").fetchall()
        return [d for d in (json.loads(r["detail"]) for r in rows) if d.get("run_id") == rid and d.get("access") == "write"]

    def test_a_request_that_fails_after_the_push_is_recorded(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        FakeApi.refuse = 403   # e.g. a fine-grained token without "Pull requests: write"
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {})
        self.assertEqual(caught.exception.code, "credential_permission")
        self.assertIn("octopus/" + rid, str(caught.exception))   # it says the branch is there
        commit = self.remote_head("octopus/" + rid)
        self.assertIsNotNone(commit)
        published = self.service.run(self.b, rid)["publish"]
        self.assertEqual((published["branch"], published["commit"], published["url"]), ("octopus/" + rid, commit, None))
        self.assertIn("403", published["request_error"])
        with self.db.read() as db:
            self.assertIn("run.published", [r[0] for r in db.execute("SELECT kind FROM events WHERE run_id=?", (rid,))])
        self.assertEqual([u["result"] for u in self.uses(rid)], ["pushed"])
        FakeApi.refuse = None
        record = self.service.publish(self.b, rid, {})   # fixed: publishing again opens the request on the same branch
        self.assertEqual((record["commit"], record["number"], record.get("request_error")), (commit, 1, None))

    def verified(self, rid, project, files, review=False):
        """A candidate made by the Worker and recorded as the accepted Attempt of the Session's task (D37)."""
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text)
        candidate = self.node.candidate({"run_id": rid, "attempt_id": "attempt-v", "mode": "commit", "message": "KiroCrew ON：实现"})
        gates = [{"name": n, "status": "pass"} for n in ("native_completed", "identity_match", "handoff_present",
                                                         "outcome_valid", "outcome_pass", "diff_present", "no_secrets")]
        with self.db.transaction() as db:
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                       "VALUES('attempt-v',?,'code',1,'{}','accepted',?,0,0)",
                       (rid, dumps({"candidate": {"sha": candidate["sha"], "parent": candidate["parent"]}, "gates": gates})))
            db.execute("UPDATE tasks SET status='accepted',current_attempt='attempt-v' WHERE run_id=? AND id='code'", (rid,))
        return candidate["sha"]

    def test_only_the_verified_candidate_is_published(self):
        rid, project = self.session(cred=self.cred["id"])
        sha = self.verified(rid, project, {"src/feature.js": "export const x = 1;"})
        self.assertEqual(self.service.run(self.b, rid)["verified"], {"sha": sha, "task_id": "code"})
        record = self.service.publish(self.b, rid, {})
        self.assertEqual((record["commit"], self.remote_head("octopus/" + rid)), (sha, sha))
        self.assertEqual(self.worker_bodies[-1]["commit"], sha)

    def test_changes_after_the_verified_candidate_are_refused(self):
        rid, project = self.session(cred=self.cred["id"])
        self.verified(rid, project, {"src/feature.js": "export const x = 1;"})
        (project / "src/feature.js").write_text("export const x = 2;   // after the gates")
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "publish_unverified"))
        self.assertIn("续作", str(caught.exception))
        self.assertIn("src/feature.js", str(caught.exception))
        self.assertIsNone(self.remote_head("octopus/" + rid))

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


class PublishHardeningTests(PublishCase):
    """The phase-1 review's lessons, applied to publishing."""

    def test_publishing_rechecks_that_the_session_may_still_use_the_credential(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        self.service.membership(self.a, self.tid, {"username": "bob"}, remove=True)   # bob's own credential, bob gone
        with self.assertRaises(Error) as caught:
            self.service.publish(self.a, rid, {})   # the Team owner tries
        self.assertEqual(caught.exception.code, "credential_unavailable")
        self.assertIsNone(self.remote_head("octopus/" + rid))

    def test_a_planted_global_git_config_cannot_redirect_the_push(self):
        caught = []

        class Thief(BaseHTTPRequestHandler):
            def do_GET(self):
                caught.append(self.headers.get("Authorization"))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="x"')
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_POST = do_GET

            def log_message(self, *args):
                pass
        thief = ThreadingHTTPServer(("127.0.0.1", 0), Thief)
        threading.Thread(target=thief.serve_forever, daemon=True).start()
        self.addCleanup(thief.server_close)
        self.addCleanup(thief.shutdown)
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        planted = Path(self.tmp.name) / "planted-gitconfig"
        planted.write_text(f'[url "http://127.0.0.1:{thief.server_address[1]}/"]\n\tinsteadOf = https://{self.host}/\n')
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(planted)}):
            record = self.service.publish(self.b, rid, {})
        self.assertEqual(self.remote_head("octopus/" + rid), record["commit"])
        self.assertEqual(caught, [])

    def test_a_token_that_may_not_push_says_which_permission_is_missing(self):
        subprocess.run(["git", "-C", str(self.remote), "config", "http.receivepack", "false"], check=True)   # 403 to pushes
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {})
        self.assertEqual(caught.exception.code, "credential_permission")
        self.assertIn("Contents", str(caught.exception))
        self.assertIsNone(self.remote_head("octopus/" + rid))

    def test_the_hosts_api_is_never_followed_through_a_redirect(self):
        hits = []

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch("octopus.credentials.api_base", lambda kind, host: f"http://127.0.0.1:{server.server_address[1]}"):
            with self.assertRaises(Error):
                publish.pull_request("github_pat", "github.com", "owner/app", "octopus/run-x", None, "t", "b", SECRET)
        self.assertEqual(len(hits), 1)


class ReceiveTests(unittest.TestCase):
    def test_an_oversized_bundle_is_refused_before_it_is_stored(self):
        class Untouched(io.RawIOBase):
            def read(self, size=-1):
                raise AssertionError("read before the size was checked")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Error) as caught:
                publish.receive(Untouched(), publish.LIMIT + 1, Path(directory) / "bare", "octopus/run-x", "0" * 40, "0" * 40)
            self.assertEqual((caught.exception.status, caught.exception.code), (413, "publish_too_large"))
            self.assertEqual(list(Path(directory).rglob("*.bundle")), [])

    def test_only_the_commit_the_worker_checked_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "octopus/run-x", str(root / "w")], check=True)
            for name in ("one", "two"):
                (root / "w" / name).write_text(name)
                subprocess.run(["git", "-C", str(root / "w"), "add", "."], check=True)
                subprocess.run(["git", "-C", str(root / "w"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                                "-m", name], check=True)
            first = git(root / "w", "rev-parse", "HEAD~1")
            head = git(root / "w", "rev-parse", "HEAD")
            subprocess.run(["git", "-C", str(root / "w"), "bundle", "create", "-q", str(root / "b"), "octopus/run-x"], check=True)
            data = (root / "b").read_bytes()
            self.assertEqual(publish.receive(io.BytesIO(data), len(data), root / "ok", "octopus/run-x", first, head), head)
            with self.assertRaises(Error) as caught:   # the scanned commit was HEAD~1; the bundle says otherwise
                publish.receive(io.BytesIO(data), len(data), root / "other", "octopus/run-x", first, first)
            self.assertEqual(caught.exception.code, "publish_invalid")

    def test_a_broken_bundle_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for data, length in ((b"# v2 git bundle\n" + b"x" * 100, 400), (b"not a bundle at all" * 10, 190)):
                with self.assertRaises(Error) as caught:
                    publish.receive(io.BytesIO(data), length, work / f"bare-{length}", "octopus/run-x", "0" * 40, "0" * 40)
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
