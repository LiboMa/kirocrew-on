"""D74: the Workers' half of a takeover. The old boundary gives its Session's branch (D58's full bundle) and the files of
its attempts the next tasks read (handoffs, outcomes); the new boundary adopts both as the same workspace and a native
parent of its own. What arrives is checked: one branch of this Session, from its baseline; only those attempt files."""
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from octopus import gateway_client, takeover
from octopus.security import Error
from octopus.worker import Worker

RID = "run-" + "6" * 32
AID = "attempt-" + "7" * 32


def stub(node):
    def native(path, body=None, method=None, timeout=None):
        if path == "/api/chat/slots/octopus-" + RID and body is None:
            raise gateway_client.GatewayError(404, '{"error":"not found"}')
        if path == "/api/chat/slots":
            return {"key": "octopus-" + RID}
        return {"ok": True}
    node.native = native


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.old = Worker(root / "alice", "a" * 40, crew_home=root / "crew-a")
        self.new = Worker(root / "bob", "b" * 40, crew_home=root / "crew-b")
        stub(self.old)
        stub(self.new)
        self.record = self.old.prepare({"run_id": RID, "source": {"type": "new"}})["project"]
        project = Path(self.old.prepare({"run_id": RID, "source": {"type": "new"}})["workspace"])
        (project / "app.py").write_text("print('hi')\n")
        subprocess.run(["git", "add", "app.py"], cwd=project, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "实现"], cwd=project, check=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip()
        attempt = self.old.path(RID) / "attempts" / AID
        attempt.mkdir(parents=True)
        (attempt / "handoff.md").write_text("# 交接\n已实现 app.py\n")
        (attempt / "outcome.json").write_text(json.dumps({"status": "completed"}))
        (attempt / "dispatch.json").write_text(json.dumps({"native_id": "old-gateway-spawn"}))   # stays behind

    def moved(self):
        plan = self.old.pull_plan({"run_id": RID, "full": True})
        bundle = self.old.pull_bundle(plan).read_bytes()
        files = self.old.pull_attempts({"run_id": RID})["files"]
        return takeover.pack(bundle, files)

    def test_the_new_boundary_adopts_the_same_workspace_and_attempt_files(self):
        files = self.old.pull_attempts({"run_id": RID})["files"]
        self.assertEqual(sorted(files), [f"{AID}/handoff.md", f"{AID}/outcome.json"])   # not the old Gateway's ids
        answer = self.new.adopt({"run_id": RID, "project": self.record, "knowledge_scope": None}, self.moved())
        project = Path(answer["workspace"])
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip(),
                         self.head)
        self.assertEqual(answer["native_parent"], "octopus-" + RID)
        self.assertEqual((answer["project"]["commit"], answer["project"]["branch"]), (self.record["commit"], self.record["branch"]))
        self.assertEqual((self.new.path(RID) / "attempts" / AID / "handoff.md").read_text(), "# 交接\n已实现 app.py\n")
        self.assertTrue((self.new.path(RID) / "parent.json").is_file())

    def test_taking_it_back_replaces_what_the_boundary_held_before(self):
        # alice takes it back: her boundary still holds the workspace as it was before bob took it over
        bob = self.new.adopt({"run_id": RID, "project": self.record, "knowledge_scope": None}, self.moved())
        project = Path(bob["workspace"])
        (project / "more.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "more.py"], cwd=project, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "续作"], cwd=project, check=True)
        plan = self.new.pull_plan({"run_id": RID, "full": True})
        back = takeover.pack(self.new.pull_bundle(plan).read_bytes(), self.new.pull_attempts({"run_id": RID})["files"])
        answer = self.old.adopt({"run_id": RID, "project": self.record, "knowledge_scope": None}, back)
        self.assertTrue((Path(answer["workspace"]) / "more.py").is_file())
        self.assertTrue(any(p.name.startswith(RID + ".before-") for p in self.old.path(RID).parent.iterdir()))   # kept aside

    def test_only_the_branch_and_those_attempt_files_are_accepted(self):
        bundle = self.old.pull_bundle(self.old.pull_plan({"run_id": RID, "full": True})).read_bytes()
        for name in ("../escape.txt", f"{AID}/../../escape.txt", f"{AID}/dispatch.json", "attempt-x/handoff.md"):
            with self.subTest(name=name):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    for member, data in (("session.bundle", bundle), ("attempts/" + name, b"x")):
                        info = tarfile.TarInfo(member)
                        info.size = len(data)
                        archive.addfile(info, io.BytesIO(data))
                with self.assertRaises(Error) as caught:
                    self.new.adopt({"run_id": RID, "project": self.record, "knowledge_scope": None}, buffer.getvalue())
                self.assertEqual(caught.exception.code, "adopt_invalid")
                self.assertFalse((self.new.path(RID) / "workspace" / "project").exists())

    def test_links_and_anything_but_plain_files_are_refused(self):
        bundle = self.old.pull_bundle(self.old.pull_plan({"run_id": RID, "full": True})).read_bytes()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("session.bundle")
            info.size = len(bundle)
            archive.addfile(info, io.BytesIO(bundle))
            link = tarfile.TarInfo(f"attempts/{AID}/handoff.md")
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(link)
        with self.assertRaises(Error) as caught:
            self.new.adopt({"run_id": RID, "project": self.record, "knowledge_scope": None}, buffer.getvalue())
        self.assertEqual(caught.exception.code, "adopt_invalid")

    def test_the_route_takes_the_tar_and_its_header(self):
        import base64
        import threading
        from octopus import transport
        from octopus.worker import serve
        httpd = serve(self.new, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        config = {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "token": self.new.token}
        header = base64.b64encode(json.dumps({"run_id": RID, "project": self.record, "knowledge_scope": None}).encode()).decode()
        answer = transport.worker_upload(config, "/adopt", self.moved(), {"X-Octopus-Adopt": header})
        self.assertEqual(answer["native_parent"], "octopus-" + RID)
        self.assertIn(AID, answer["handoffs"])
        files = transport.worker_request(config, "/pull/attempts", {"run_id": RID})
        self.assertEqual(sorted(files["files"]), [f"{AID}/handoff.md", f"{AID}/outcome.json"])
        self.assertEqual(files["project"]["branch"], "octopus/" + RID)


if __name__ == "__main__":
    unittest.main()
