"""D37 phase 3 §11.1: a test task's tests run on the Session's baseline, in place (its untracked dependencies stay),
and the workspace always returns to the candidate."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from octopus import sources
from octopus.checks import Checks
from tests.test_archive import RID, make_worker


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node, self.project = make_worker(Path(self.tmp.name))
        self.base = sources.read_record(self.node.path(RID))["commit"]

    def candidate(self, files, remove=(), parent=None):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)
        for name in remove:
            (self.project / name).unlink()
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "commit", "message": "候选",
                                    **({"parent": parent} if parent else {})})

    def test_the_candidate_names_its_files(self):
        result = self.candidate({"src/a.py": "a = 1\n", "tests/test_a.py": "x\n"})
        self.assertEqual(sorted(result["names"]), ["src/a.py", "tests/test_a.py"])
        self.assertNotIn("names_truncated", result)

    def test_the_overlay_follows_the_test_tasks_changes(self):
        code = self.candidate({"src/a.py": "a = 1\n", "tests/old_test.py": "old\n"})
        tests = self.candidate({"tests/test_a.py": "new\n"}, remove=["tests/old_test.py"], parent=code["sha"])
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules/dep.js").write_text("dep")   # untracked: stays through both switches
        sources.baseline_apply(self.project, self.base, tests["parent"], tests["sha"])
        self.assertFalse((self.project / "src/a.py").exists())            # the implementation is not on the baseline
        self.assertEqual((self.project / "tests/test_a.py").read_text(), "new\n")   # the new test is laid on top
        self.assertFalse((self.project / "tests/old_test.py").exists())   # what the test task deleted is gone
        self.assertTrue((self.project / "node_modules/dep.js").exists())
        sources.baseline_restore(self.project, tests["sha"])
        self.assertEqual((self.project / "src/a.py").read_text(), "a = 1\n")
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), tests["sha"])
        self.assertTrue((self.project / "node_modules/dep.js").exists())

    def test_the_workspace_is_back_on_the_candidate_whatever_happens(self):
        result = self.candidate({"src/a.py": "a = 1\n", "tests/test_a.py": "t\n"})
        cases = (["exit 0"], ["exit 3"], ["sleep 30"])
        for commands in cases:
            with self.subTest(commands=commands):
                checks = Checks(per_command=1, total=2)
                applied = []
                job = checks.start(RID, "attempt-" + commands[0][:4], self.project, commands, kind="baseline",
                                   around=(lambda: (applied.append(1), sources.baseline_apply(
                                       self.project, self.base, self.base, result["sha"])),
                                           lambda: sources.baseline_restore(self.project, result["sha"])))["job"]
                while checks.status(job)["state"] != "done":
                    time.sleep(0.05)
                self.assertEqual(applied, [1])
                self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")
                self.assertTrue((self.project / "src/a.py").exists())

    def test_a_switch_that_fails_is_an_error_not_a_failing_baseline(self):
        result = self.candidate({"src/a.py": "a = 1\n"})
        checks = Checks()
        def broken():
            raise ValueError("无法切换")
        job = checks.start(RID, "attempt-x", self.project, ["exit 1"], kind="baseline",
                           around=(broken, lambda: sources.baseline_restore(self.project, result["sha"])))["job"]
        while checks.status(job)["state"] != "done":
            time.sleep(0.05)
        status = checks.status(job)
        self.assertIn("无法切换", status["error"])
        self.assertEqual(status["results"], [])

    def test_a_baseline_job_is_separate_from_the_attempts_checks(self):
        checks = Checks()
        first = checks.start(RID, "attempt-1", self.project, ["exit 0"])["job"]
        second = checks.start(RID, "attempt-1", self.project, ["exit 0"], kind="baseline")["job"]
        self.assertNotEqual(first, second)

    def baseline(self, tests, commands):
        return self.node.verify_start({"run_id": RID, "attempt_id": "attempt-t", "commands": commands,
                                       "baseline": {"candidate": tests["sha"], "parent": tests["parent"]}})

    def test_the_worker_runs_a_baseline_job_and_says_so(self):
        code = self.candidate({"src/a.py": "a = 1\n"})
        tests = self.candidate({"tests/test_a.py": "t\n"}, parent=code["sha"])
        started = self.baseline(tests, ["test -f src/a.py"])   # the implementation is missing on the baseline
        self.assertTrue(started["baseline"])
        while (status := self.node.verify_status({"job": started["job"]}))["state"] != "done":
            time.sleep(0.05)
        self.assertFalse(status["passed"])
        self.assertTrue((self.project / "src/a.py").exists())
        self.assertFalse((self.node.path(RID) / "baseline.json").exists())
        self.assertTrue(self.node.health()["baseline_checks"])

    def test_a_leftover_baseline_is_undone_before_the_next_dispatch(self):
        code = self.candidate({"src/a.py": "a = 1\n"})
        sources.baseline_apply(self.project, self.base, self.base, code["sha"])   # a Worker that died mid-run
        (self.node.path(RID) / "baseline.json").write_text('{"candidate": "%s"}' % code["sha"])
        self.node._undo_baseline(self.node.path(RID))
        self.assertTrue((self.project / "src/a.py").exists())
        self.assertFalse((self.node.path(RID) / "baseline.json").exists())

    def test_a_baseline_needs_full_commit_ids(self):
        with self.assertRaises(Exception):
            self.node.verify_start({"run_id": RID, "attempt_id": "attempt-t", "commands": ["true"],
                                    "baseline": {"candidate": "abc", "parent": "def"}})

    def test_a_running_baseline_is_not_undone_by_another_request(self):
        code = self.candidate({"src/a.py": "a = 1\n"})
        tests = self.candidate({"tests/test_a.py": "t\n"}, parent=code["sha"])
        started = self.baseline(tests, ["sleep 1"])
        time.sleep(0.3)
        self.node._undo_baseline(self.node.path(RID))   # e.g. the console polling the preview meanwhile
        self.assertFalse((self.project / "src/a.py").exists())   # still on the baseline: the job puts it back itself
        while self.node.verify_status({"job": started["job"]})["state"] != "done":
            time.sleep(0.05)
        self.assertTrue((self.project / "src/a.py").exists())
