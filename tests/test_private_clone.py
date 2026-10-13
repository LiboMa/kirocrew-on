"""D35 §5.1: a bound credential clones a private repository — the token goes to git through GIT_ASKPASS and a 0600 file
that is removed at once; nothing of it stays in the workspace, the project record or any error."""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from octopus import sources
from octopus.db import dumps
from octopus.engine import Engine
from octopus.security import Error
from tests.test_access import AccessCase

TOKEN = "github_pat_11PRIVATEclonetoken_9z9z"
BACKEND = Path(subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()) / "git-http-backend"


class GitHost(BaseHTTPRequestHandler):
    """A private repository over HTTPS: git-http-backend behind Basic x-access-token:<TOKEN>."""
    root = None

    def handle_any(self):
        if self.headers.get("Authorization") != "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        env = {"GIT_PROJECT_ROOT": str(GitHost.root), "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query,
               "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "x-access-token", "PATH": os.environ["PATH"]}
        head, _, payload = subprocess.run([str(BACKEND)], input=body, env=env, capture_output=True).stdout.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode().split("\r\n"):
            key, _, value = line.partition(": ")
            if key.lower() == "status":
                status = int(value.split()[0])
            elif key:
                headers.append((key, value))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = handle_any

    def log_message(self, *args):
        pass


@unittest.skipUnless(BACKEND.exists() and shutil.which("openssl"), "git-http-backend and openssl are needed")
class PrivateCloneCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        GitHost.root = self.root / "repos"
        GitHost.root.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(GitHost.root / "app.git")], check=True)
        src = self.root / "src"
        subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
        (src / "README.md").write_text("private")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "first"],
                       check=True)
        subprocess.run(["git", "-C", str(src), "push", "-q", str(GitHost.root / "app.git"), "main"], check=True)
        subprocess.run(["git", "-C", str(GitHost.root / "app.git"), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        cert, key = self.root / "cert.pem", self.root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), GitHost)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"https://127.0.0.1:{self.server.server_address[1]}/app.git"
        trust = patch.dict(os.environ, {"GIT_SSL_CAINFO": str(cert), "GIT_CONFIG_GLOBAL": str(self.root / "no-gitconfig")})
        trust.start()
        self.addCleanup(trust.stop)
        self.run_dir = self.root / ("run-" + "5" * 32)
        self.run_dir.mkdir()
        (self.run_dir / "workflow-run.json").write_text(json.dumps({"id": self.run_dir.name}))

    def tearDown(self):
        self.tmp.cleanup()

    def auth(self, secret=TOKEN):
        return {"kind": "github_pat", "host": f"127.0.0.1:{self.server.server_address[1]}", "username": "x-access-token",
                "secret": secret}

    def everything(self):
        """Every byte under the run's folder, to look for the secret in."""
        return b"".join(p.read_bytes() for p in self.run_dir.rglob("*") if p.is_file() and not p.is_symlink())


class CloneTests(PrivateCloneCase):
    def test_a_private_clone_leaves_no_secret_behind(self):
        record = sources.prepare_project(self.run_dir, source=self.url, auth=self.auth())
        project = self.run_dir / "workspace/project"
        self.assertEqual((record["status"], (project / "README.md").read_text()), ("ready", "private"))
        self.assertNotIn(TOKEN.encode(), self.everything())
        self.assertFalse((self.run_dir / ".askpass").exists())
        git = lambda *args: subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True).stdout.strip()
        self.assertEqual(git("remote", "get-url", "origin"), self.url)          # no credential in the remote
        self.assertEqual(git("remote", "get-url", "--push", "origin"), "DISABLED_NO_PUSH")

    def test_without_a_credential_a_private_clone_fails_plainly(self):
        with self.assertRaises(ValueError) as caught:
            sources.prepare_project(self.run_dir, source=self.url)
        self.assertIn("源码准备失败", str(caught.exception))

    def test_a_wrong_token_fails_without_showing_it(self):
        with self.assertRaises(ValueError) as caught:
            sources.prepare_project(self.run_dir, source=self.url, auth=self.auth("github_pat_wrongWRONGwrong_0000"))
        self.assertNotIn("wrongWRONG", str(caught.exception))
        self.assertNotIn(b"wrongWRONG", self.everything())
        self.assertFalse((self.run_dir / ".askpass").exists())

    def test_global_git_config_cannot_redirect_the_token(self):
        caught = []

        class Thief(BaseHTTPRequestHandler):
            def do_GET(self):
                caught.append(self.headers.get("Authorization"))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="x"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass
        thief = ThreadingHTTPServer(("127.0.0.1", 0), Thief)
        threading.Thread(target=thief.serve_forever, daemon=True).start()
        self.addCleanup(thief.server_close)
        self.addCleanup(thief.shutdown)
        planted = self.root / "planted-gitconfig"   # what an agent of the same OS user could write
        planted.write_text(f'[url "http://127.0.0.1:{thief.server_address[1]}/"]\n\tinsteadOf = {self.url.rsplit("/", 1)[0]}/\n'
                           '[credential]\n\thelper = store\n')
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(planted)}):
            record = sources.prepare_project(self.run_dir, source=self.url, auth=self.auth())
        self.assertEqual(record["status"], "ready")
        self.assertEqual(caught, [])

    def test_askpass_answers_only_its_own_host(self):
        auth = self.auth()
        with sources.askpass(self.root, auth) as env:
            script = env["GIT_ASKPASS"]
            ask = lambda prompt: subprocess.run([script, prompt], capture_output=True, text=True)
            own = ask(f"Password for 'https://x-access-token@{auth['host']}': ")
            other = ask("Password for 'https://x-access-token@evil.example': ")
            user = ask(f"Username for 'https://{auth['host']}': ")
        self.assertEqual((own.returncode, own.stdout), (0, TOKEN))
        self.assertEqual((other.returncode, other.stdout), (1, ""))
        self.assertEqual(user.stdout.strip(), "x-access-token")

    def test_git_errors_never_carry_the_secret(self):
        text = ("fatal: unable to access 'https://x-access-token:ghp_SECRET@github.com/a/b.git/'\n"
                "> Authorization: Basic eC1hY2Nlc3MtdG9rZW46Z2hwX1NFQ1JFVA==\nPRIVATE-TOKEN: glpat-abc\nplain ghp_SECRET here")
        cleaned = sources.scrub(text, "ghp_SECRET")
        for leak in ("ghp_SECRET", "eC1hY2Nl", "glpat-abc", "x-access-token:"):
            self.assertNotIn(leak, cleaned)
        self.assertIn("https://github.com/a/b.git", cleaned)

    def test_the_worker_passes_the_credential_and_reports_failures(self):
        from tests.test_archive import make_worker   # a Worker with its native Gateway stubbed
        node, _ = make_worker(self.root / "w")
        rid = "run-" + "6" * 32
        body = {"run_id": rid, "source": {"type": "git", "url": self.url}, "git_auth": self.auth()}
        result = node.prepare(body)
        self.assertTrue(result["workspace"].endswith("workspace/project"))
        folder = node.path(rid)
        self.assertNotIn(TOKEN.encode(), b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file() and not p.is_symlink()))
        other = "run-" + "7" * 32
        with self.assertRaises(Error) as caught:
            node.prepare({"run_id": other, "source": {"type": "git", "url": self.url}, "git_auth": self.auth("github_pat_bad_00000")})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "source_failed"))
        self.assertNotIn("github_pat_bad", str(caught.exception))
        with self.assertRaises(Error) as caught:
            node.prepare({"run_id": "run-" + "8" * 32, "source": {"type": "git", "url": self.url},
                          "git_auth": {**self.auth(), "host": "evil.example"}})
        self.assertEqual(caught.exception.code, "invalid_request")


class EngineTests(AccessCase):
    """The Engine opens the bound credential at the moment of preparation, and only then."""

    def setUp(self):
        super().setUp()
        self.tid = self.team("凭证 Team")
        self.calls = []

    def fake(self, config, path, body=None, timeout=35):
        self.calls.append((path, body))
        if path == "/prepare":
            return {"native_parent": "octopus-x", "workspace": "/w/x", "project": {}}
        return {}

    def submit(self, request, **extra):
        return self.service.start(self.b, {"request_id": request, "input": "研发需求与真实验证", "team_id": self.tid,
                                           "source": {"type": "git", "url": "https://github.com/kirocrew/app.git"},
                                           "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                           "recovery": {"enabled": False, "max_cycles": 2}, **extra})["id"]

    def advance(self, rid):
        Engine(self.db, request=self.fake)._advance_guarded(rid)   # one cycle, errors handled as the loop does
        with self.db.read() as db:
            return db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()

    def test_a_bound_credential_reaches_the_worker_only_for_preparation(self):
        made = self.service.credentials.create(self.b, {"name": "bob 的", "kind": "github_pat", "host": "github.com",
                                                        "scope": "user", "secret": TOKEN})
        rid = self.submit("start-0000000401", credentials={"source": made["id"]})
        run = self.advance(rid)
        prepare = next(body for path, body in self.calls if path == "/prepare")
        self.assertEqual(prepare["git_auth"], {"kind": "github_pat", "host": "github.com", "username": "x-access-token",
                                               "secret": TOKEN})
        self.assertEqual(run["native_parent"], "octopus-x")
        with self.db.read() as db:
            used = db.execute("SELECT actor_id,detail FROM audit WHERE action='credential.use'").fetchone()
            last = db.execute("SELECT last_used FROM credentials WHERE id=?", (made["id"],)).fetchone()["last_used"]
            payloads = " ".join(r[0] for r in db.execute("SELECT payload FROM events WHERE run_id=?", (rid,)))
        self.assertEqual((used["actor_id"], json.loads(used["detail"])["repo"]), (self.b["id"], "kirocrew/app"))
        self.assertIsNotNone(last)
        self.assertNotIn(TOKEN, payloads)

    def test_no_binding_sends_no_credential(self):
        self.advance(self.submit("start-0000000402"))
        prepare = next(body for path, body in self.calls if path == "/prepare")
        self.assertNotIn("git_auth", prepare)

    def test_a_credential_the_session_may_no_longer_use_is_not_sent(self):
        platform = self.service.credentials.create(self.a, {"name": "平台", "kind": "github_pat", "host": "github.com",
                                                            "scope": "platform", "secret": TOKEN})
        self.service.credentials.grant(self.a, platform["id"], {"team_id": self.tid})
        granted = self.submit("start-0000000410", credentials={"source": platform["id"]})
        self.service.credentials.revoke(self.a, platform["id"], {"team_id": self.tid})
        personal = self.service.credentials.create(self.b, {"name": "bob 的", "kind": "github_pat", "host": "github.com",
                                                            "scope": "user", "secret": TOKEN})
        left = self.submit("start-0000000411", credentials={"source": personal["id"]})
        self.service.membership(self.a, self.tid, {"username": "bob"}, remove=True)
        for rid in (granted, left):
            run = self.advance(rid)
            self.assertEqual(run["status"], "blocked", rid)
        self.assertNotIn("/prepare", [path for path, _ in self.calls])

    def test_removing_the_binding_blocks_the_next_preparation(self):
        made = self.service.credentials.create(self.b, {"name": "bob 的", "kind": "github_pat", "host": "github.com",
                                                        "scope": "user", "secret": TOKEN})
        rid = self.submit("start-0000000412", credentials={"source": made["id"]})
        self.service.rebind(self.b, rid, {"source": None})
        run = self.advance(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertIn("撤下", run["error"])
        self.assertNotIn("/prepare", [path for path, _ in self.calls])
        self.assertEqual(self.service.run(self.b, rid)["attention"]["title"], "仓库凭证不可用")

    def test_a_disabled_credential_blocks_preparation_and_says_so(self):
        made = self.service.credentials.create(self.b, {"name": "bob 的", "kind": "github_pat", "host": "github.com",
                                                        "scope": "user", "secret": TOKEN})
        rid = self.submit("start-0000000403", credentials={"source": made["id"]})
        self.service.credentials.update(self.b, made["id"], {"disabled": True})
        run = self.advance(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertIn("bob 的", run["error"])
        self.assertNotIn("/prepare", [path for path, _ in self.calls])
        attention = self.service.run(self.b, rid)["attention"]
        self.assertEqual(attention["title"], "仓库凭证不可用")


if __name__ == "__main__":
    unittest.main()
