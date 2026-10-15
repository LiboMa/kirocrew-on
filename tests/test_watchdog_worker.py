"""D39 §3.1: the Worker reports a running Attempt's progress (KiroCrew's state.json) and stops one native task."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.worker import Worker

RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}


class WatchdogWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew")
        self.calls, self.stop_answer = [], None

        def native(path, body=None, method=None):
            self.calls.append((path, body, method))
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn" and body is not None:
                return {"id": "aabb1234"}
            if path == "/api/spawn":
                return {"agents": [{"id": "aabb1234", "parent": "dashboard:octopus-" + RID, "done": False}]}
            if path == "/api/spawn/aabb1234" and method == "DELETE":
                if self.stop_answer:
                    raise self.stop_answer
                return {"ok": True}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.attempt = "attempt-" + "2" * 32
        self.node.dispatch({"run_id": RID, "attempt_id": self.attempt, "input": "需求", "spec": SPEC, "upstream": []})
        state = self.node.home / "subagents/aabb1234/state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"id": "aabb1234", "parent_session": "dashboard:octopus-" + RID, "status": "running",
                                     "updated_at": 1000.0, "turns": 7, "last_tool": "npm test " + "x" * 300}))

    def test_a_running_attempt_reports_its_progress(self):
        result = self.node.inspect({"run_id": RID, "attempt_id": self.attempt})
        self.assertEqual(result["progress"], {"updated_at": 1000.0, "turns": 7, "last_tool": ("npm test " + "x" * 300)[:200],
                                              "status": "running"})

    def test_one_native_task_is_stopped(self):
        self.assertEqual(self.node.attempt_stop({"run_id": RID, "attempt_id": self.attempt}), {"stopped": True})
        self.assertIn(("/api/spawn/aabb1234", None, "DELETE"), self.calls)

    def test_a_task_already_gone_counts_as_stopped(self):
        self.stop_answer = gateway_client.GatewayError(404, '{"error":"not found"}')
        self.assertEqual(self.node.attempt_stop({"run_id": RID, "attempt_id": self.attempt}), {"stopped": True})

    def test_health_says_it_can_stop_one_task(self):
        self.assertTrue(self.node.health()["attempt_stop"])


if __name__ == "__main__":
    unittest.main()
