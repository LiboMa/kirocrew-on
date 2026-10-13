"""D36 §4: downloading a session's source — what the Worker packs (with .git, without dependencies, secrets or
symlinks), the streamed zip, and the platform's signed download link."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
import time

from octopus import gateway_client, sources as workspace
from octopus.db import dumps
from octopus.security import Error, authenticate, issue_token
from octopus.server import archive_run, archive_ticket, preview_key, preview_run, preview_ticket, required_scope, server
from octopus.worker import Worker, serve
from tests.test_access import AccessCase

RID = "run-" + "1" * 32
TOKEN = "x" * 40


def make_worker(root):
    node = Worker(root / "worker", TOKEN, crew_home=root / "crew")

    def native(path, body=None, method=None):
        if path == "/api/chat/slots/octopus-" + RID and body is None:
            raise gateway_client.GatewayError(404, '{"error":"not found"}')
        return {"key": "octopus-" + RID} if path == "/api/chat/slots" else {"ok": True}
    node.native = native
    node.prepare({"run_id": RID, "source": {"type": "new"}})
    return node, workspace.project_dir(node.path(RID))


def write(project, files):
    for name, text in files.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text(text)


class WorkerArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.node, self.project = make_worker(self.root)
        write(self.project, {"src/a.js": "a", "node_modules/x/index.js": "x", ".venv/bin/python": "p", ".env": "SECRET=1",
                             ".env.example": "SECRET=", "dist/app.js": "d", "nested/node_modules/y.js": "y",
                             "web/.next/cache/z": "z", "web/.next/server.js": "s", "pkg/__pycache__/m.pyc": "c"})
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
                       cwd=self.project, check=True)
        (self.project / "link_out").symlink_to("/etc/passwd")
        (self.project / "link_in").symlink_to(self.project / "src/a.js")
        (self.project / "link_dir").symlink_to("/etc")

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return {relative for relative, _ in self.node.archive_plan(RID)["files"]}

    def test_source_and_git_without_dependencies_secrets_or_links(self):
        names = self.names()
        for kept in ("src/a.js", ".git/HEAD", ".env.example", "dist/app.js", "web/.next/server.js"):
            self.assertIn(kept, names)
        for left in ("node_modules/x/index.js", ".venv/bin/python", ".env", "nested/node_modules/y.js",
                     "web/.next/cache/z", "pkg/__pycache__/m.pyc", "link_out", "link_in"):
            self.assertNotIn(left, names)
        plan = self.node.archive_plan(RID)
        self.assertEqual(plan["links"], ["link_dir", "link_in", "link_out"])
        self.assertIn("node_modules", plan["excluded"])
        self.assertIn(".env", plan["excluded"])

    def test_symlinks_are_never_followed(self):
        self.assertFalse(any(name.startswith("link_dir/") or name.endswith("passwd") for name in self.names()))

    def test_too_large_says_where_the_bytes_are(self):
        write(self.project, {"big/blob.bin": "b" * 2000})
        with patch("octopus.worker.ARCHIVE_LIMIT", 1000), self.assertRaises(Error) as caught:
            self.node.archive_plan(RID)
        self.assertEqual((caught.exception.status, caught.exception.code), (413, "archive_too_large"))
        self.assertIn("big", str(caught.exception))

    def call(self, body, token=TOKEN):
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/archive", method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers, exc.read()

    def test_the_zip_is_streamed_with_a_readme(self):
        (self.project / ".octopus").mkdir()
        (self.project / ".octopus/preview.json").write_text(json.dumps({"processes": [{"command": "npm run dev", "port": "$PORT"}]}))
        status, headers, data = self.call({"run_id": RID, "meta": {"name": "订单系统", "creator": "alice"}})
        self.assertEqual((status, headers["Content-Type"]), (200, "application/zip"))
        self.assertIsNone(headers.get("Content-Length"))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            self.assertTrue({"src/a.js", ".git/HEAD", ".octopus/preview.json", "OCTOPUS-ARCHIVE.txt"} <= names)
            readme = archive.read("OCTOPUS-ARCHIVE.txt").decode()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, capture_output=True, text=True).stdout.strip()
        for part in ("订单系统", "alice", head, "node_modules", "link_out", "npm run dev", "localhost:5173"):
            self.assertIn(part, readme)

    def test_errors_before_streaming_stay_json_and_auth_is_required(self):
        status, _, body = self.call({"run_id": "run-" + "2" * 32})
        self.assertEqual(json.loads(body)["code"] if status != 200 else None, json.loads(body).get("code"))
        self.assertNotEqual(status, 200)
        self.assertEqual(self.call({"run_id": RID}, token="y" * 40)[0], 401)


def fake_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("src/a.js", "a" * 5000)
        archive.writestr("OCTOPUS-ARCHIVE.txt", "readme")
    return buffer.getvalue()


class FakeWorker(BaseHTTPRequestHandler):
    mode, received, data = "ok", [], fake_zip()

    def do_POST(self):
        FakeWorker.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(self.data if self.mode == "ok" else self.data[: len(self.data) // 2])

    def log_message(self, *args):
        pass


class PlatformArchiveTests(AccessCase):
    def setUp(self):
        super().setUp()
        FakeWorker.mode, FakeWorker.received = "ok", []
        self.worker_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWorker)
        threading.Thread(target=self.worker_server.serve_forever, daemon=True).start()
        self.addCleanup(self.worker_server.server_close)
        self.addCleanup(self.worker_server.shutdown)
        tid = self.team(environment=False)
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,team_id,config) VALUES('w-z','z',?,?)",
                       (tid, dumps({"url": f"http://127.0.0.1:{self.worker_server.server_address[1]}", "token": TOKEN,
                                    "capabilities": {"isolation": "dedicated"}})))
        self.tid = tid
        self.rid = self.start(self.a, team_id=tid, worker_id="w-z", name="订单系统")["id"]
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET workspace='/w/x',native_parent='octopus-x',status='completed' WHERE id=?", (self.rid,))

    @contextmanager
    def platform(self):
        app = server(self.db, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{app.server_address[1]}"
        finally:
            app.shutdown()
            app.server_close()

    def post(self, base, token, rid=None):
        request = urllib.request.Request(f"{base}/v1/runs/{rid or self.rid}/archive", method="POST", data=b"{}",
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def get(self, base, url):
        try:
            with urllib.request.urlopen(base + url, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers, json.load(exc)

    def downloads(self):
        with self.db.read() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind='run.downloaded'",
                                                         (self.rid,))]

    def test_tickets_have_a_purpose_and_expire(self):
        key = preview_key(self.tmp.name)
        ticket = archive_ticket(self.rid, self.a["id"], "console", key)
        self.assertEqual(archive_run(ticket, key), (self.rid, self.a["id"], "console"))
        for wrong in (lambda: archive_run(preview_ticket(self.rid, self.a["id"], key), key),
                      lambda: preview_run(ticket, key)):
            with self.assertRaises(Error) as caught:
                wrong()
            self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(Error) as caught:
            archive_run(archive_ticket(self.rid, self.a["id"], "console", key, now=time.time() - 3600), key)
        self.assertEqual(caught.exception.code, "archive_expired")

    def test_workflow_archive_still_needs_manage(self):
        self.assertEqual(required_scope("POST", "/v1/workflows/w1/archive"), "manage")
        self.assertEqual(required_scope("POST", "/v1/runs/r1/archive"), "read")

    def test_who_may_ask_for_a_link(self):
        with self.platform() as base:
            self.assertEqual(self.post(base, self.tokens["alice"])[0], 200)
            status, body = self.post(base, self.tokens["bob"])
            self.assertEqual((status, body["url"].startswith("/archive/"), body["expires_in"]), (200, True, 600))
            self.assertEqual(self.post(base, self.tokens["eve"])[0], 404)
            self.assertEqual(self.post(base, self.token("bob", ["read"]))[0], 200)

    def test_the_download_streams_the_workers_zip_and_is_recorded(self):
        with self.platform() as base:
            url = self.post(base, self.tokens["bob"])[1]["url"]
            status, headers, data = self.get(base, url)
        self.assertEqual((status, headers["Content-Type"]), (200, "application/zip"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])
        self.assertEqual(data, FakeWorker.data)
        self.assertEqual(FakeWorker.received[0]["meta"], {"name": "订单系统", "creator": "alice"})
        (record,) = self.downloads()
        self.assertEqual((record["bytes"], record["actor"]["username"]), (len(FakeWorker.data), "bob"))

    def test_a_broken_stream_is_not_recorded_as_downloaded(self):
        FakeWorker.mode = "broken"
        with self.platform() as base:
            url = self.post(base, self.tokens["bob"])[1]["url"]
            with self.assertRaises(Exception):
                with urllib.request.urlopen(base + url, timeout=10) as response:
                    response.read()
        self.assertEqual(self.downloads(), [])

    def test_a_member_removed_after_the_link_was_issued_is_refused(self):
        with self.platform() as base:
            url = self.post(base, self.tokens["bob"])[1]["url"]
            self.service.membership(self.a, self.tid, {"username": "bob"}, remove=True)
            status, _, body = self.get(base, url)
        self.assertEqual((status, body["code"]), (403, "archive_forbidden"))

    def test_a_session_without_source_has_nothing_to_download(self):
        other = self.start(self.a, "start-0000000099")["id"]
        with self.platform() as base:
            status, body = self.post(base, self.tokens["alice"], other)
        self.assertEqual((status, body["code"]), (409, "no_archive"))


if __name__ == "__main__":
    unittest.main()
