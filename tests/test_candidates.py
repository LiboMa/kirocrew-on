"""D37 §3, §6: a task's candidate commit on the Session's branch (secrets keep the branch where it was), and a review's
check that the workspace is still the candidate it reviewed."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from octopus import sources
from octopus.security import Error
from octopus.worker import serve
from tests.test_archive import RID, TOKEN, make_worker


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.node, self.project = make_worker(self.root)
        self.base = sources.read_record(self.node.path(RID))["commit"]

    def write(self, files):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)

    def commit(self, **extra):
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "commit",
                                    "message": "KiroCrew ON：实现登录（run/code 第 1 次）", **extra})

    def agent_commit(self, message):
        git(self.project, "add", "-A")
        git(self.project, "-c", "user.email=a@a", "-c", "user.name=agent", "commit", "-q", "-m", message)

    def test_a_change_becomes_a_candidate_on_the_branch(self):
        self.write({"src/a.js": "export const a = 1;\nexport const b = 2;\n", "node_modules/x/i.js": "x"})
        result = self.commit()
        self.assertTrue(result["changed"])
        self.assertEqual((result["parent"], result["files"], result["insertions"], result["deletions"]), (self.base, 1, 2, 0))
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), result["sha"])
        self.assertEqual(git(self.project, "log", "-1", "--format=%an|%s", result["sha"]),
                         "KiroCrew ON|KiroCrew ON：实现登录（run/code 第 1 次）")
        self.assertNotIn("node_modules/x/i.js", git(self.project, "ls-tree", "-r", "--name-only", result["sha"]))
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")

    def test_no_change_is_the_parent_itself(self):
        first = self.commit()
        self.assertEqual((first["sha"], first["changed"]), (self.base, False))
        self.write({"src/a.js": "a"})
        second = self.commit()
        third = self.commit(parent=second["sha"])
        self.assertEqual((third["sha"], third["changed"], third["files"]), (second["sha"], False, 0))

    def test_agent_commits_are_kept(self):
        self.write({"src/a.js": "a"})
        self.agent_commit("agent: a")
        agents = git(self.project, "rev-parse", "HEAD")
        self.write({"src/b.js": "b"})
        result = self.commit()
        self.assertEqual(git(self.project, "rev-parse", result["sha"] + "^"), agents)
        self.assertEqual(result["files"], 2)

    def test_a_secret_leaves_the_branch_where_it_was(self):
        token = "ghp_" + "A1b2C3d4" * 5
        self.write({"src/x.js": f"const t = '{token}';"})
        result = self.commit()
        self.assertEqual(result["secrets"], ["src/x.js"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertNotIn(token, json.dumps(result))

    def test_a_rewritten_history_is_marked_not_fatal(self):
        self.write({"src/a.js": "a"})
        first = self.commit()
        git(self.project, "reset", "-q", "--hard", self.base)   # the Agent rewrote what came before
        self.write({"src/b.js": "b"})
        result = self.commit(parent=first["sha"])
        self.assertTrue(result["rewritten"])
        self.assertEqual(result["parent"], first["sha"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), result["sha"])

    def test_check_reports_head_and_pending_changes(self):
        self.write({"src/a.js": "a"})
        sha = self.commit()["sha"]
        check = lambda: self.node.candidate({"run_id": RID, "attempt_id": "attempt-2", "mode": "check", "expected": sha})
        self.assertEqual(check(), {"head": sha, "clean": True, "changed_files": []})
        self.write({".env": "SECRET=1", "node_modules/y/i.js": "y"})
        self.assertTrue(check()["clean"])   # what a download and a publish leave out does not count
        self.write({".coverage": "data", "reports/junit.xml": "<x/>", "dist/app.js": "built"})
        self.assertTrue(check()["clean"])   # what checks and test runs leave untracked is not a change to the code
        self.write({"src/a.js": "changed by the review"})
        self.assertEqual(check()["changed_files"], ["src/a.js"])
        self.assertFalse(check()["clean"])

    def test_commit_ids_are_checked_before_git_sees_them(self):
        for bad in ("--output=/tmp/x", "HEAD", "abc"):
            with self.subTest(bad=bad), self.assertRaises(Error):
                self.commit(parent=bad)

    def test_the_route_is_served_and_unknown_routes_stay_404(self):
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def post(path, body):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method="POST",
                                             data=json.dumps(body).encode(),
                                             headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.loads(exc.read())
        status, body = post("/candidate", {"run_id": RID, "attempt_id": "attempt-1", "mode": "commit"})
        self.assertEqual((status, body["sha"]), (200, self.base))
        self.assertEqual(post("/no-such-route", {"run_id": RID})[0], 404)


if __name__ == "__main__":
    unittest.main()
