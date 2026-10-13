"""D36 §4: downloading a session's source — what the Worker packs (with .git, without dependencies, secrets or
symlinks), the streamed zip, and the platform's signed download link."""
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
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

    def call(self, body, token=TOKEN, path="/archive"):
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method="POST",
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

    def zip_of(self):
        status, _, data = self.call({"run_id": RID, "meta": {"name": "x", "creator": "alice"}})
        self.assertEqual(status, 200)
        return data

    def test_an_unreadable_or_special_file_is_listed_and_the_zip_still_ends(self):
        from octopus.server import zip_complete
        if os.geteuid() == 0:
            self.skipTest("root reads mode-000 files")
        write(self.project, {"locked.txt": "secret"})
        os.chmod(self.project / "locked.txt", 0)
        os.mkfifo(self.project / "pipe")
        try:
            data = self.zip_of()
        finally:
            os.chmod(self.project / "locked.txt", 0o600)
        self.assertTrue(zip_complete(data[-256:], len(data)))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names, readme = set(archive.namelist()), archive.read("OCTOPUS-ARCHIVE.txt").decode()
        self.assertIn("src/a.js", names)
        self.assertFalse({"locked.txt", "pipe"} & names)
        self.assertIn("locked.txt", readme)
        self.assertIn("pipe", readme)

    def test_a_failure_while_packing_never_finishes_the_zip(self):
        from octopus.server import zip_complete

        class Sink:   # a socket-like stream: no tell(), no seek()
            def __init__(self):
                self.data = bytearray()

            def write(self, chunk):
                self.data += chunk
                return len(chunk)

            def flush(self):
                pass
        original = zipfile.ZipFile.writestr

        def failing(archive, info, data, *args, **kwargs):
            if (info.filename if isinstance(info, zipfile.ZipInfo) else info) == "OCTOPUS-ARCHIVE.txt":
                raise RuntimeError("disk")
            return original(archive, info, data, *args, **kwargs)
        sink = Sink()
        with patch.object(zipfile.ZipFile, "writestr", failing), self.assertRaises(RuntimeError):
            self.node.write_archive(sink, RID, {"name": "x"})
        data = bytes(sink.data)
        self.assertGreater(len(data), 100)   # the files went out before the failure
        self.assertFalse(zip_complete(data[-256:], len(data)))
        with self.assertRaises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(data))

    def test_empty_directories_and_packed_refs_keep_a_working_repository(self):
        (self.project / "assets/empty").mkdir(parents=True)
        subprocess.run(["git", "pack-refs", "--all"], cwd=self.project, check=True)
        target = Path(self.tmp.name) / "out"
        with zipfile.ZipFile(io.BytesIO(self.zip_of())) as archive:
            archive.extractall(target)
        self.assertTrue((target / "assets/empty").is_dir())
        log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=target, capture_output=True, text=True)
        self.assertEqual((log.returncode, log.stdout.strip()), (0, "init"), log.stderr)

    def test_git_settings_that_run_commands_are_not_shipped(self):
        git = lambda *args: subprocess.run(["git", *args], cwd=self.project, check=True)
        git("config", "core.fsmonitor", "touch /tmp/kc-owned")
        git("config", "core.pager", "sh -c 'touch /tmp/kc-owned'")
        git("config", "alias.st", "!touch /tmp/kc-owned")
        git("remote", "add", "origin", "https://example.com/team/app.git")
        hooks = self.project / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "post-checkout").write_text("#!/bin/sh\ntouch /tmp/kc-owned\n")
        (hooks / "pre-commit.sample").write_text("#!/bin/sh\n")
        with zipfile.ZipFile(io.BytesIO(self.zip_of())) as archive:
            config, names = archive.read(".git/config").decode(), set(archive.namelist())
            readme = archive.read("OCTOPUS-ARCHIVE.txt").decode()
        for gone in ("fsmonitor", "pager", "alias", "kc-owned"):
            self.assertNotIn(gone, config)
        shipped = Path(self.tmp.name) / "shipped-config"
        shipped.write_text(config)
        read = lambda key: subprocess.run(["git", "config", "--file", str(shipped), key], capture_output=True,
                                          text=True).stdout.strip()
        self.assertEqual((read("remote.origin.url"), read("core.repositoryformatversion")),
                         ("https://example.com/team/app.git", "0"))
        self.assertNotIn(".git/hooks/post-checkout", names)
        self.assertIn(".git/hooks/pre-commit.sample", names)
        self.assertIn("hooks", readme)

    def test_the_readme_installs_what_the_download_leaves_out(self):
        write(self.project, {"package.json": json.dumps({"scripts": {"dev": "vite"}}), "package-lock.json": "{}"})
        self.assertTrue((self.project / "node_modules").is_dir())   # present on the Worker, not in the zip
        with zipfile.ZipFile(io.BytesIO(self.zip_of())) as archive:
            readme = archive.read("OCTOPUS-ARCHIVE.txt").decode()
        self.assertIn("npm ci", readme)

    def test_an_unusable_package_json_does_not_break_the_download(self):
        write(self.project, {"package.json": json.dumps({"scripts": ["dev"]})})
        with zipfile.ZipFile(io.BytesIO(self.zip_of())) as archive:
            readme = archive.read("OCTOPUS-ARCHIVE.txt").decode()
        self.assertIn("未找到启动方式", readme)

    def test_a_dry_run_answers_the_size_before_any_download(self):
        status, _, body = self.call({"run_id": RID}, path="/archive/plan")
        summary = json.loads(body)
        self.assertEqual(status, 200)
        self.assertGreater(summary["bytes"], 0)
        self.assertGreater(summary["files"], 0)
        self.assertIn("node_modules", summary["excluded"])
        with patch("octopus.worker.ARCHIVE_LIMIT", 10):
            status, _, body = self.call({"run_id": RID}, path="/archive/plan")
        self.assertEqual((status, json.loads(body)["code"]), (413, "archive_too_large"))

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
    plan = (200, {"bytes": 5000, "files": 2, "excluded": [], "links": []})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/archive/plan":
            status, answer = FakeWorker.plan
            data = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        FakeWorker.received.append(body)
        if self.mode == "chunked-cut":
            self.protocol_version = "HTTP/1.1"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"3E8\r\n" + self.data[:10])   # announces 1000 bytes, sends 10, hangs up
            self.wfile.flush()
            self.close_connection = True
            return
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
        FakeWorker.plan = (200, {"bytes": 5000, "files": 2, "excluded": [], "links": []})
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

    def raw_get(self, base, url):
        host, port = base.removeprefix("http://").split(":")
        with socket.create_connection((host, int(port)), timeout=10) as conn:
            conn.sendall(f"GET {url} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while chunk := conn.recv(65536):
                data += chunk
        return data.partition(b"\r\n\r\n")

    def test_the_link_comes_with_an_estimate_and_refusals_come_first(self):
        with self.platform() as base:
            status, body = self.post(base, self.tokens["bob"])
            self.assertEqual((status, body["bytes_estimate"], body["files"]), (200, 5000, 2))
            FakeWorker.plan = (413, {"code": "archive_too_large", "error": "项目超过 200 MB"})
            status, body = self.post(base, self.tokens["bob"])
            self.assertEqual((status, body["code"]), (413, "archive_too_large"))
            FakeWorker.plan = (404, {"code": "not_found", "error": "接口不存在"})
            status, body = self.post(base, self.tokens["bob"])
            self.assertEqual((status, body["code"]), (409, "archive_unsupported"))

    def test_a_chunked_upstream_cut_ends_without_a_second_response(self):
        FakeWorker.mode = "chunked-cut"
        with self.platform() as base:
            url = self.post(base, self.tokens["bob"])[1]["url"]
            head, _, body = self.raw_get(base, url)
        self.assertIn(b" 200 ", head)
        self.assertNotIn(b"HTTP/1.", body)
        self.assertFalse(body.endswith(b"0\r\n\r\n"))
        self.assertEqual(self.downloads(), [])

    def test_a_failed_record_does_not_corrupt_a_finished_download(self):
        with self.platform() as base, patch("octopus.domain.Service.record_download",
                                            side_effect=sqlite3.OperationalError("database is locked")):
            url = self.post(base, self.tokens["bob"])[1]["url"]
            head, _, body = self.raw_get(base, url)
        self.assertIn(b" 200 ", head)
        self.assertTrue(body.endswith(b"0\r\n\r\n"))
        self.assertNotIn(b"HTTP/1.", body)

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


class FakeDownloadClient:
    url = "https://on.example"

    def __init__(self, data):
        self.data, self.calls = data, []

    def request(self, method, path, body=None, **options):
        self.calls.append((method, path))
        return {"url": "/archive/ticket/" + RID + ".zip", "expires_in": 600}

    def download(self, path, file, timeout=300):
        self.calls.append(("GET", path))
        file.write(self.data)
        return len(self.data)


def archive_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


class ZipEndTests(unittest.TestCase):
    def test_only_a_zip_written_to_its_end_counts_as_complete(self):
        from octopus.server import zip_complete
        inner = archive_bytes({"x": "x"})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("lib/inner.jar", inner)
            archive.writestr("after.txt", "a" * 100)
        data = buffer.getvalue()
        self.assertTrue(zip_complete(data[-256:], len(data)))
        nested = data.index(inner) + len(inner)
        for end in (nested, nested + 30, len(data) - 1):
            self.assertFalse(zip_complete(data[:end][-256:], end), end)
        with patch("zipfile.ZIP_FILECOUNT_LIMIT", 2):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for index in range(4):
                    archive.writestr(f"f{index}", "x")
        big = buffer.getvalue()
        self.assertIn(b"PK\x06\x06", big)   # zip64 end records
        self.assertTrue(zip_complete(big[-256:], len(big)))
        self.assertFalse(zip_complete(big[:-1][-256:], len(big) - 1))


class McpDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_download_extracts_and_reports_how_to_start(self):
        from octopus import mcp
        data = archive_bytes({"src/a.js": "a", ".octopus/preview.json": json.dumps({"processes": [{"command": "npm run dev", "port": "$PORT"}]}),
                              "OCTOPUS-ARCHIVE.txt": "会话：x\nHEAD：abc123\n"})
        result = mcp.call(FakeDownloadClient(data), "octopus_download", {"run_id": RID})
        target = Path(self.tmp.name, "kirocrew-" + RID[4:16]).resolve()
        self.assertEqual(Path(result["dir"]), target)
        self.assertEqual((target / "src/a.js").read_text(), "a")
        self.assertEqual((result["files"], result["bytes"], result["head"]), (3, len(data), "abc123"))
        self.assertEqual(result["start"], ["npm run dev", "打开 http://localhost:5173/"])

    def test_a_nonempty_target_is_refused(self):
        from octopus import mcp
        Path("busy").mkdir()
        Path("busy/file").write_text("x")
        client = FakeDownloadClient(archive_bytes({"a": "a"}))
        with self.assertRaisesRegex(ValueError, "非空"):
            mcp.call(client, "octopus_download", {"run_id": RID, "dest": "busy"})
        self.assertEqual(client.calls, [])   # nothing requested

    def test_extraction_stays_inside_the_target(self):
        from octopus import mcp
        data = archive_bytes({"ok.txt": "ok", "../evil.txt": "evil", "/abs.txt": "abs", "a/../../up.txt": "up"})
        result = mcp.call(FakeDownloadClient(data), "octopus_download", {"run_id": RID, "dest": "out"})
        self.assertTrue(Path("out/ok.txt").is_file())
        for outside in ("evil.txt", "up.txt", "/abs.txt"):
            self.assertFalse(Path(outside).exists() and Path(outside).read_text() in {"evil", "up", "abs"}, outside)
        self.assertEqual(sorted(result["skipped"]), ["../evil.txt", "/abs.txt", "a/../../up.txt"])

    def test_extraction_keeps_the_executable_bit(self):
        from octopus import mcp
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, mode in (("gradlew", 0o100755), ("setuid", 0o104755)):
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, "#!/bin/sh\n")
            archive.writestr("plain.txt", "p")
        mcp.call(FakeDownloadClient(buffer.getvalue()), "octopus_download", {"run_id": RID, "dest": "out"})
        self.assertTrue(os.access("out/gradlew", os.X_OK))
        self.assertEqual(os.stat("out/setuid").st_mode & 0o7000, 0)
        self.assertFalse(os.access("out/plain.txt", os.X_OK))


if __name__ == "__main__":
    unittest.main()
