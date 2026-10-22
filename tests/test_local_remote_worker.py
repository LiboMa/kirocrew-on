"""D58 M3-4 on the Worker: the Session's branch as a git bundle for a local pull (checked like a publish), and a local
fast-forward synced back — the branch and workspace move to it only when it is a fast-forward with nothing that looks
like a credential, and the workspace held nothing that would be lost."""
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from octopus import sources
from octopus.security import Error
from octopus.worker import serve
from tests.test_archive import RID, TOKEN, make_worker
from tests.test_candidates import git

AGENT = ["-c", "user.email=a@a", "-c", "user.name=agent"]


class LocalRemoteWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.node, self.project = make_worker(self.root)
        self.branch = sources.read_record(self.node.path(RID))["branch"]
        (self.project / "src").mkdir(exist_ok=True)
        (self.project / "src/a.js").write_text("one\n")
        self.head = self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "commit", "message": "候选"})["sha"]

    def pull(self, full=True):
        plan = self.node.pull_plan({"run_id": RID, "full": full})
        return plan, self.node.pull_bundle(plan)

    def local(self):
        """A local clone of what a pull brings back, on the Session's branch."""
        _, bundle = self.pull()
        local = self.root / "local"
        subprocess.run(["git", "clone", "-q", "-b", self.branch, str(bundle), str(local)], check=True, capture_output=True)
        return local

    def bundle_of(self, local, since):
        target = self.root / "up.bundle"
        git(local, "bundle", "create", "-q", str(target), f"{since}..HEAD")
        return target.read_bytes()

    def commit(self, local, files, message="本地修改"):
        for name, text in files.items():
            (local / name).parent.mkdir(parents=True, exist_ok=True)
            (local / name).write_text(text)
        git(local, "add", "-A", "-f")
        git(local, *AGENT, "commit", "-q", "-m", message)
        return git(local, "rev-parse", "HEAD")

    def test_a_pull_bundle_carries_the_branch_at_its_head(self):
        plan, bundle = self.pull()
        self.assertEqual(plan["head"], self.head)
        self.assertIn(self.head, git(self.project, "bundle", "list-heads", str(bundle)))

    def test_a_branch_with_a_secret_is_not_pulled(self):
        (self.project / ".env").write_text("SECRET=1\n")
        git(self.project, "add", "-f", ".env")
        git(self.project, *AGENT, "commit", "-q", "-m", "env")
        with self.assertRaises(Error) as caught:
            self.pull()
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "bundle_secret_found"))

    def test_a_fast_forward_moves_the_branch_and_the_workspace(self):
        local = self.local()
        new = self.commit(local, {"src/a.js": "one\ntwo\n", "src/b.js": "b\n"})
        result = self.node.sync({"run_id": RID, "old": self.head, "head": new}, self.bundle_of(local, self.head))
        self.assertEqual((result["sha"], result["parent"], result["files"], result["insertions"], result["deletions"], result["commits"]),
                         (new, self.head, 2, 2, 0, 1))
        self.assertEqual(sorted(result["names"]), ["src/a.js", "src/b.js"])
        self.assertEqual(git(self.project, "rev-parse", "refs/heads/" + self.branch), new)
        self.assertEqual((self.project / "src/a.js").read_text(), "one\ntwo\n")

    def test_not_a_fast_forward_is_refused_and_nothing_moves(self):
        local = self.local()
        new = self.commit(local, {"src/a.js": "local\n"})
        (self.project / "src/a.js").write_text("remote\n")
        moved = self.node.candidate({"run_id": RID, "attempt_id": "attempt-2", "mode": "commit", "message": "远端", "parent": self.head})["sha"]
        with self.assertRaises(Error) as caught:
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, self.bundle_of(local, self.head))
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "sync_not_fast_forward"))
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), moved)

    def test_a_secret_in_the_local_commits_is_refused(self):
        local = self.local()
        new = self.commit(local, {".env": "TOKEN=abc\n", "src/a.js": "x\n"})
        with self.assertRaises(Error) as caught:
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, self.bundle_of(local, self.head))
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "sync_secret_found"))
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.head)

    def test_pending_changes_in_the_workspace_are_not_dropped(self):
        local = self.local()
        new = self.commit(local, {"src/a.js": "two\n"})
        (self.project / "src/a.js").write_text("an agent's unfinished change\n")
        with self.assertRaises(Error) as caught:
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, self.bundle_of(local, self.head))
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "sync_dirty"))
        self.assertEqual((self.project / "src/a.js").read_text(), "an agent's unfinished change\n")

    def test_a_refused_sync_leaves_no_object_behind(self):   # final review: the rejected .env was kept in the object store
        local = self.local()
        new = self.commit(local, {".env": "TOKEN=abc\n", "src/a.js": "x\n"})
        blob = git(local, "rev-parse", new + ":.env")
        with self.assertRaises(Error):
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, self.bundle_of(local, self.head))
        missing = subprocess.run(["git", "-C", str(self.project), "cat-file", "-e", blob], capture_output=True)
        self.assertNotEqual(missing.returncode, 0)

    def test_a_bundle_header_must_name_one_branch_head(self):   # final review: refs from the header reached git fetch
        local = self.local()
        new = self.commit(local, {"src/a.js": "two\n"})
        data = self.bundle_of(local, self.head)
        header, _, pack = data.partition(b"\n\n")
        for extra in (f"{new} refs/heads/x:refs/heads/evil".encode(), f"{new} --upload-pack=touch".encode()):
            hostile = header + b"\n" + extra + b"\n\n" + pack
            with self.assertRaises(Error) as caught:
                self.node.sync({"run_id": RID, "old": self.head, "head": new}, hostile)
            self.assertEqual(caught.exception.status, 409)
        renamed = header.replace(b" HEAD", b" refs/heads/x:refs/heads/evil") + b"\n\n" + pack
        with self.assertRaises(Error):
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, renamed)
        self.assertEqual(git(self.project, "for-each-ref", "--format=%(refname)", "refs/heads/evil"), "")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.head)

    def test_the_same_sync_again_answers_the_same(self):   # final review: a retry after the platform failed to record it
        local = self.local()
        new = self.commit(local, {"src/a.js": "two\n"})
        data = self.bundle_of(local, self.head)
        first = self.node.sync({"run_id": RID, "old": self.head, "head": new}, data)
        again = self.node.sync({"run_id": RID, "old": self.head, "head": new}, data)
        self.assertEqual(again, first)

    def test_an_untracked_file_in_the_way_is_not_overwritten(self):   # final review: reset --hard replaced it silently
        local = self.local()
        new = self.commit(local, {"src/new.js": "from local\n"})
        (self.project / "src/new.js").write_text("an agent's untracked file\n")
        with self.assertRaises(Error) as caught:
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, self.bundle_of(local, self.head))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual((self.project / "src/new.js").read_text(), "an agent's untracked file\n")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.head)

    def test_the_declared_head_must_be_in_the_bundle_and_the_size_is_bounded(self):
        local = self.local()
        new = self.commit(local, {"src/a.js": "two\n"})
        data = self.bundle_of(local, self.head)
        with self.assertRaises(Error) as caught:
            self.node.sync({"run_id": RID, "old": self.head, "head": "c" * 40}, data)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(Error) as caught:
            self.node.sync({"run_id": RID, "old": self.head, "head": new}, b"x" * (20 * 1024 * 1024 + 1))
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(Error):
            self.node.sync({"run_id": RID, "old": "--upload-pack=x", "head": new}, data)


    def test_the_routes_are_served(self):
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def call(path, data, headers):
            request = urllib.request.Request(base + path, method="POST", data=data, headers={"Authorization": "Bearer " + TOKEN, **headers})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, dict(exc.headers), exc.read()
        status, headers, data = call("/pull/bundle", json.dumps({"run_id": RID, "full": True}).encode(), {"Content-Type": "application/json"})
        self.assertEqual((status, headers["X-Pull-Head"]), (200, self.head))
        local = self.root / "via-http"
        (self.root / "got.bundle").write_bytes(data)
        subprocess.run(["git", "clone", "-q", "-b", self.branch, str(self.root / "got.bundle"), str(local)], check=True, capture_output=True)
        new = self.commit(local, {"src/a.js": "via http\n"})
        status, _, answer = call("/sync", self.bundle_of(local, self.head), {"Content-Type": "application/octet-stream",
                                 "X-Octopus-Run": RID, "X-Octopus-Old": self.head, "X-Octopus-Head": new})
        self.assertEqual((status, json.loads(answer)["sha"]), (200, new))
        status, _, answer = call("/sync", b"", {"Content-Type": "application/octet-stream", "X-Octopus-Run": RID})
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()
