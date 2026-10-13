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

from octopus import gateway_client, sources as workspace
from octopus.security import Error
from octopus.worker import Worker, serve

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


if __name__ == "__main__":
    unittest.main()
