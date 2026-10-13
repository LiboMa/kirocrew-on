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
        verify = subprocess.run(["git", "-C", str(self.project), "bundle", "verify", str(saved)], capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn(self.base, verify.stdout + verify.stderr)   # the baseline is its prerequisite
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
