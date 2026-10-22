"""D56 M3-5: what each candidate changed — the Worker's read-only ``mode: diff`` (stat and unified diff, clipped) and the
platform's GET /v1/runs/<id>/attempts/<attempt>/diff with the session's access check."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from octopus import diffs, sources
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase
from tests.test_archive import RID, make_worker
from tests.test_candidates import git

SHA, PARENT = "a" * 40, "b" * 40


class WorkerDiffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node, self.project = make_worker(Path(self.tmp.name))
        self.base = sources.read_record(self.node.path(RID))["commit"]

    def write(self, files):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_bytes(text if isinstance(text, bytes) else text.encode())

    def commit(self, **extra):
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "commit", "message": "候选", **extra})["sha"]

    def diff(self, **body):
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "diff", **body})

    def test_the_diff_of_a_candidate_against_the_one_before(self):
        self.write({"src/a.js": "one\ntwo\n", "README.md": "x\n"})
        first = self.commit()
        self.write({"src/a.js": "one\nTWO\nthree\n"})
        second = self.commit(parent=first)
        result = self.diff(expected=second, parent=first)
        self.assertEqual(result["files"], [{"path": "src/a.js", "insertions": 2, "deletions": 1, "binary": False}])
        self.assertIn("+TWO", result["diff"])
        self.assertIn("-two", result["diff"])
        self.assertFalse(result["truncated"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), second)   # read only

    def test_without_a_parent_it_is_against_the_session_baseline(self):
        self.write({"src/a.js": "one\n", "README.md": "x\n"})
        sha = self.commit()
        self.assertEqual(sorted(f["path"] for f in self.diff(expected=sha)["files"]), ["README.md", "src/a.js"])

    def test_a_large_diff_is_clipped_and_says_so(self):
        self.write({"big.txt": "".join(f"line {i}\n" for i in range(6000))})
        result = self.diff(expected=self.commit())
        self.assertTrue(result["truncated"])
        self.assertLessEqual(result["diff"].count("\n"), sources.DIFF_LINES)
        self.assertEqual(result["files"][0]["insertions"], 6000)   # the stat is whole

    def test_a_binary_file_is_listed_without_content(self):
        self.write({"logo.png": b"\x89PNG\x00\x01\x02"})
        result = self.diff(expected=self.commit())
        self.assertEqual(result["files"], [{"path": "logo.png", "insertions": 0, "deletions": 0, "binary": True}])

    def test_a_commit_the_session_does_not_have_is_refused(self):
        with self.assertRaises(Error) as caught:
            self.diff(expected="c" * 40)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(Error):
            self.diff(expected="--output=/tmp/x")
        with self.assertRaises(Error):
            self.diff()


class PlatformDiffTests(AccessCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('w-bob','bob',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://worker.test", "token": "t" * 40, "capabilities": {}})))
        self.rid = self.start(self.b, worker_id="w-bob")["id"]
        with self.db.transaction() as db:
            for number, (aid, evidence) in enumerate((("attempt-c", {"candidate": {"sha": SHA, "parent": PARENT, "files": 1}}),
                                                      ("attempt-n", {}),
                                                      ("attempt-s", {"candidate": {"sha": SHA, "parent": PARENT, "moved": False}})), 1):
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,'code',?,'{}','accepted',?,1,1)", (aid, self.rid, number, dumps(evidence)))

    def test_the_worker_is_asked_for_that_candidate(self):
        answer = {"files": [{"path": "a", "insertions": 1, "deletions": 0, "binary": False}], "diff": "+x", "truncated": False}
        with mock.patch.object(diffs, "worker_request", return_value=answer) as call:
            self.assertEqual(self.service.attempt_diff(self.b, self.rid, "attempt-c"), {**answer, "sha": SHA, "parent": PARENT})
        config, path, body = call.call_args.args
        self.assertEqual((config["url"], path), ("https://worker.test", "/candidate"))
        self.assertEqual(body, {"run_id": self.rid, "attempt_id": "attempt-c", "mode": "diff", "expected": SHA, "parent": PARENT})

    def test_no_candidate_no_session_and_an_old_worker(self):
        with self.assertRaises(Error) as caught:
            self.service.attempt_diff(self.b, self.rid, "attempt-n")
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "no_candidate"))
        with self.assertRaises(Error) as caught:
            self.service.attempt_diff(self.e, self.rid, "attempt-c")
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(Error) as caught:
            self.service.attempt_diff(self.b, self.rid, "attempt-missing")
        self.assertEqual(caught.exception.status, 404)
        with mock.patch.object(diffs, "worker_request") as call, self.assertRaises(Error) as caught:
            self.service.attempt_diff(self.b, self.rid, "attempt-s")   # no_secrets kept the branch: never shown
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "candidate_secret"))
        call.assert_not_called()
        old = Error(400, "invalid_request", "mode 只能是 commit、check 或 restore")
        with mock.patch.object(diffs, "worker_request", side_effect=old), self.assertRaises(Error) as caught:
            self.service.attempt_diff(self.b, self.rid, "attempt-c")
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "worker_outdated"))
        self.assertIn("请平台管理员", str(caught.exception))
        self.assertNotIn("deploy", str(caught.exception))

    def test_the_route(self):
        with mock.patch.object(diffs, "worker_request", return_value={"files": [], "diff": "", "truncated": False}), self.http() as call:
            status, body = call("GET", f"/v1/runs/{self.rid}/attempts/attempt-c/diff", self.tokens["bob"])
            self.assertEqual((status, body["sha"]), (200, SHA))
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/attempts/attempt-c/diff", self.tokens["eve"])[0], 404)
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/attempts/attempt-n/diff", self.tokens["bob"])[0], 404)


if __name__ == "__main__":
    unittest.main()
