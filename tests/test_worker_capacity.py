"""MVP 0.2.0 P1 Task 4 (H §6.2; design §5.3 #8): a Worker's health says how much room its host has and how much work it
runs, for the control plane to place and scale by. Reading it never breaks the health answer."""
import json
import tempfile
import unittest
from pathlib import Path

from octopus.worker import Worker

MEMINFO = "MemTotal:       16000000 kB\nMemFree:         1000000 kB\nMemAvailable:    6000000 kB\n"


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.node = Worker(root / "worker", "t" * 40, crew_home=root / "crew")
        self.meminfo = root / "meminfo"
        self.meminfo.write_text(MEMINFO)
        self.node.MEMINFO = self.meminfo
        (root / "crew").mkdir(exist_ok=True)
        (root / "crew" / "config.json").write_text(json.dumps({"agent": {"max_subagents": 3}}))
        attempts = root / "worker" / ("run-" + "1" * 32) / "attempts"
        for name, finished in (("attempt-" + "a" * 32, False), ("attempt-" + "b" * 32, True)):
            (attempts / name).mkdir(parents=True)
            (attempts / name / "dispatch.json").write_text("{}")
            if finished:
                (attempts / name / "result-note.json").write_text("{}")

    def test_capacity_reports_memory_cpu_and_running_work(self):
        capacity = self.node.health()["capacity"]
        self.assertEqual((capacity["mem_total_mb"], capacity["mem_available_mb"]), (15625, 5859))
        self.assertIsInstance(capacity["cpus"], int)
        self.assertIsInstance(capacity["load1"], float)
        self.assertEqual(capacity["running_attempts"], 1)   # dispatched, no result yet
        self.assertEqual(capacity["max_subagents"], 3)

    def test_an_attempt_dispatched_long_ago_is_not_counted_as_running(self):   # review 2026-10-26
        import os
        import time
        stale = Path(self.tmp.name) / "worker" / ("run-" + "2" * 32) / "attempts" / ("attempt-" + "c" * 32)
        stale.mkdir(parents=True)
        (stale / "dispatch.json").write_text("{}")   # stopped or failed: it never gets a result note
        old = time.time() - self.node.RUNNING_HORIZON - 60
        os.utime(stale / "dispatch.json", (old, old))
        self.node._capacity_cache = None
        self.assertEqual(self.node.health()["capacity"]["running_attempts"], 1)

    def test_capacity_never_breaks_health(self):
        self.node.MEMINFO = Path(self.tmp.name) / "missing"
        (Path(self.tmp.name) / "crew" / "config.json").write_text("not json")
        capacity = self.node.health()["capacity"]
        self.assertIsNone(capacity["mem_total_mb"])
        self.assertIsNone(capacity["max_subagents"])


if __name__ == "__main__":
    unittest.main()
