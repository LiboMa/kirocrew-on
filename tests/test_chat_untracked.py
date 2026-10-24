"""D73 fix: the Worker's /candidate check, asked for ``untracked``, names the untracked files the workspace has beyond
those its newest review started with (untracked.json, D57 §4) — what a chat created after the platform last looked. A
review moves out what it creates itself, so the files the platform's checks left before it are in that list; with no
review yet, every untracked file counts."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from octopus import gateway_client
from octopus.worker import Worker

RID = "run-" + "1" * 32


class UntrackedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.node = Worker(root / "worker", "x" * 40, crew_home=root / "crew")

        def native(path, body=None, method=None, timeout=None):
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.run = self.node.path(RID)
        self.project = next(p for p in (self.run / "workspace").iterdir() if p.is_dir())
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, capture_output=True, text=True).stdout.strip()

    def check(self, **extra):
        return self.node.candidate({"run_id": RID, "mode": "check", "expected": self.head, **extra})

    def test_files_created_after_the_newest_review_are_named(self):
        (self.project / "coverage.txt").write_text("left by a check")
        review = self.run / "attempts" / ("attempt-" + "2" * 32)
        review.mkdir(parents=True)
        (review / "untracked.json").write_text(json.dumps({"paths": ["coverage.txt"]}))
        (self.project / "README.md").write_text("# 用法\\n")
        state = self.check(untracked=True)
        self.assertTrue(state["clean"])   # tracked files and HEAD are as the candidate left them
        self.assertEqual(state["untracked"], ["README.md"])

    def test_without_a_review_every_untracked_file_counts(self):
        (self.project / "README.md").write_text("# 用法\\n")
        self.assertEqual(self.check(untracked=True)["untracked"], ["README.md"])

    def finished_checks(self, attempt, command):
        job = self.node.verify_start({"run_id": RID, "attempt_id": attempt, "commands": [command]})["job"]
        for _ in range(200):
            state = self.node.verify_status({"job": job})
            if state["state"] != "running":
                return state
            time.sleep(0.05)
        self.fail("the check never finished")

    def test_files_a_later_check_left_are_the_platforms_not_the_chats(self):
        """D57 whole-branch review: a check after the newest review (a test task's verify) left .coverage and
        reports/junit.xml — counted as chat-created, they refused a diagnosis request and were committed as the person's."""
        review = self.run / "attempts" / ("attempt-" + "2" * 32)
        review.mkdir(parents=True)
        (review / "untracked.json").write_text(json.dumps({"paths": []}))
        self.finished_checks("attempt-" + "3" * 32, "mkdir -p reports && echo x > reports/junit.xml && echo y > .coverage")
        self.assertEqual(self.check(untracked=True)["untracked"], [])
        (self.project / "README.md").write_text("# 用法\n")   # what a chat then writes still counts
        self.assertEqual(self.check(untracked=True)["untracked"], ["README.md"])
        self.assertEqual(json.loads((review / "untracked.json").read_text()), {"paths": []})   # a review's own baseline stays

    def test_a_check_not_asking_says_nothing_of_them(self):
        (self.project / "README.md").write_text("# 用法\\n")
        self.assertNotIn("untracked", self.check())


if __name__ == "__main__":
    unittest.main()
