"""D60 M4-4 part 1: a sub-agent KiroCrew deferred (low memory, critical posture, cap at zero) is queued, not lost — the
Worker recognises it through GET /api/spawn/{id}, and the platform shows it as a queue the watchdog does not count."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.security import Error
from octopus.worker import Worker

RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}


class WorkerQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew")
        self.attempt = "attempt-" + "2" * 32
        self.listed, self.status = [], None

        def native(path, body=None, method=None, timeout=None):
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn" and body is not None:
                return {"id": "aabb1234", "status": "queued", "reason": "low_memory",
                        "reason_detail": "only 2.47 GB available, need 2.50 GB"}
            if path == "/api/spawn":
                return {"agents": self.listed}   # the ordinary list leaves queued runs out
            if path == "/api/spawn/aabb1234":
                if self.status is None:
                    raise gateway_client.GatewayError(404, '{"error":"not found"}')
                return self.status
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.node.dispatch({"run_id": RID, "attempt_id": self.attempt, "input": "需求", "spec": SPEC, "upstream": []})

    def inspect(self):
        return self.node.inspect({"run_id": RID, "attempt_id": self.attempt})

    def queued_row(self, **extra):
        return {"id": "aabb1234", "task": f"[OCTOPUS-ATTEMPT:{self.attempt}] 编码", "done": False, "status": "queued", "queued": True,
                "reason": "low_memory", "reason_detail": "only 2.47 GB available, need 2.50 GB", "elapsed": 1200, **extra}

    def test_a_queued_run_is_queued_not_unknown(self):
        self.status = self.queued_row()
        result = self.inspect()
        self.assertEqual(result["native"], {"done": False, "queued": True})
        self.assertEqual(result["queued"], {"reason": "low_memory", "detail": "only 2.47 GB available, need 2.50 GB",
                                            "elapsed": 1200, "resuming": False})

    def test_a_continuation_said_spawned_but_waits(self):   # /continue answers spawned; the run's own status says queued
        self.status = self.queued_row(resuming=True)
        self.assertTrue(self.inspect()["queued"]["resuming"])

    def test_someone_elses_queued_run_is_not_this_attempt(self):
        self.status = self.queued_row(task="[OCTOPUS-ATTEMPT:attempt-" + "9" * 32 + "] 别的")
        with self.assertRaises(Error) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "native_unavailable")

    def test_neither_listed_nor_queued_is_still_unknown(self):
        with self.assertRaises(Error) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "native_unavailable")


if __name__ == "__main__":
    unittest.main()
