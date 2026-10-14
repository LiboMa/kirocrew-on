"""D37 §5 (G1): the platform runs a task's verify commands itself — in order, stopping at the first failure, each and all
of them time-limited with their whole process group ended, the output's tail kept with credentials hidden."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from octopus import sources
from octopus.checks import Checks
from octopus.security import Error
from tests.test_archive import RID, make_worker


def finished(checks, job, limit=30):
    deadline = time.time() + limit
    while time.time() < deadline:
        value = checks.status(job)
        if value["state"] == "done":
            return value
        time.sleep(0.1)
    raise AssertionError("checks did not finish")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)

    def test_commands_run_in_order_and_stop_at_the_first_failure(self):
        checks = Checks()
        job = checks.start("run-a", "attempt-1", self.project, ["true", "echo out; exit 3", "touch never"])["job"]
        value = finished(checks, job)
        self.assertEqual([r["exit"] for r in value["results"]], [0, 3])
        self.assertEqual((value["passed"], value["failed"]), (False, 1))
        self.assertIn("out", value["results"][1]["tail"])
        self.assertFalse((self.project / "never").exists())

    def test_all_passing_commands_pass(self):
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["true", "pwd"])["job"])
        self.assertEqual((value["passed"], value["failed"]), (True, None))
        self.assertIn(str(self.project.resolve()), value["results"][1]["tail"])   # runs in the project

    def test_output_tail_is_kept_and_scrubbed(self):
        token = "ghp_" + "A1b2C3d4" * 5
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project,
                                              [f"head -c 10000 /dev/zero | tr '\\0' x; echo; echo token={token}"])["job"])
        tail = value["results"][0]["tail"]
        self.assertLessEqual(len(tail.encode()), 4096)
        self.assertNotIn(token, tail)
        self.assertIn(sources.HIDDEN, tail)

    def test_a_hanging_command_and_its_children_are_ended(self):
        checks = Checks(per_command=1)
        started = time.time()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["sleep 31.5 & sleep 31.6"])["job"])
        self.assertLess(time.time() - started, 10)
        self.assertEqual((value["passed"], value["results"][0]["exit"], value["results"][0].get("timeout")), (False, None, True))
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "sleep 31.[56]"], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [])

    def test_the_total_limit_ends_the_rest(self):
        checks = Checks(per_command=30, total=1)
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["sleep 3", "touch never"])["job"])
        self.assertEqual(len(value["results"]), 1)
        self.assertTrue(value["results"][0].get("timeout"))
        self.assertFalse((self.project / "never").exists())

    def test_cancel_ends_a_running_job(self):
        checks = Checks()
        job = checks.start("run-a", "attempt-1", self.project, ["sleep 30"])["job"]
        time.sleep(0.3)
        checks.cancel("run-a")
        value = finished(checks, job, limit=10)
        self.assertEqual((value["passed"], value["results"][0].get("stopped")), (False, True))

    def test_the_same_attempt_gets_the_same_job_and_unknown_jobs_are_404(self):
        checks = Checks()
        first = checks.start("run-a", "attempt-1", self.project, ["true"])["job"]
        self.assertEqual(checks.start("run-a", "attempt-1", self.project, ["true"])["job"], first)
        self.assertNotEqual(checks.start("run-a", "attempt-2", self.project, ["true"])["job"], first)
        with self.assertRaises(Error) as caught:
            checks.status("nope")
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "check_unknown"))


class WorkerCheckTests(unittest.TestCase):
    def test_the_worker_runs_checks_in_the_project_and_stopping_the_session_ends_them(self):
        with tempfile.TemporaryDirectory() as directory:
            node, project = make_worker(Path(directory))
            job = node.verify_start({"run_id": RID, "attempt_id": "attempt-1", "commands": ["pwd"]})["job"]
            value = finished(node.checks, job)
            self.assertIn(str(project.resolve()), value["results"][0]["tail"])
            for bad in ("npm test", ["x" * 501], ["a"] * 11, [3]):
                with self.subTest(bad=bad), self.assertRaises(Error):
                    node.verify_start({"run_id": RID, "attempt_id": "attempt-2", "commands": bad})
            job = node.verify_start({"run_id": RID, "attempt_id": "attempt-3", "commands": ["sleep 30"]})["job"]
            time.sleep(0.3)
            node.checks.cancel(RID)   # what the session's stop / close / delete does
            self.assertTrue(finished(node.checks, job, limit=10)["results"][0].get("stopped"))
            self.assertEqual(node.verify_status({"job": job})["state"], "done")


if __name__ == "__main__":
    unittest.main()
