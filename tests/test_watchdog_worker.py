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
        self.calls, self.stop_answer, self.timeouts = [], None, {}
        self.row = {"id": "aabb1234", "parent": "dashboard:octopus-" + RID, "done": False, "turns": 9,
                    "last_tool": "npm test --token=[REDACTED]"}

        def native(path, body=None, method=None, timeout=None):
            self.calls.append((path, body, method))
            self.timeouts[path] = timeout
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn" and body is not None:
                return {"id": "aabb1234"}
            if path == "/api/spawn":
                return {"agents": [self.row]}
            if path == "/api/spawn/aabb1234" and method == "DELETE":
                if isinstance(self.stop_answer, Exception):
                    raise self.stop_answer
                return self.stop_answer or {"ok": True, "cancelled": True}
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
        progress = self.node.inspect({"run_id": RID, "attempt_id": self.attempt})["progress"]
        self.assertEqual((progress["updated_at"], progress["turns"], progress["status"]), (1000.0, 9, "running"))
        self.assertEqual(progress["last_tool"], "npm test --token=[REDACTED]")   # KiroCrew's redacted row, not state.json's raw title
        self.assertGreater(progress["idle_seconds"], 1e9 - 2000)                # measured on the Worker's own clock

    def test_the_last_tool_is_masked_before_it_is_cut(self):
        # D57 §3.3: KiroCrew's last tool, cut to 200 characters for the platform, is masked first (its own token too)
        token = self.node.token
        self.row["last_tool"] = "curl -H 'Authorization: Bearer " + token + "' " + "y" * 200
        progress = self.node.inspect({"run_id": RID, "attempt_id": self.attempt})["progress"]
        self.assertEqual(progress["last_tool"], ("curl -H 'Authorization: Bearer " + token[:4] + "…" + token[-2:] + "' "
                                                 + "y" * 200)[:200])
        self.row["last_tool"] = "y" * 180 + " Bearer " + token
        self.assertEqual(self.node.inspect({"run_id": RID, "attempt_id": self.attempt})["progress"]["last_tool"],
                         "y" * 180 + " Bearer " + token[:4] + "…" + token[-2:])

    def test_without_state_there_is_no_idle_signal(self):
        (self.node.home / "subagents/aabb1234/state.json").unlink()
        progress = self.node.inspect({"run_id": RID, "attempt_id": self.attempt}).get("progress") or {}
        self.assertNotIn("idle_seconds", progress)

    def test_one_native_task_is_stopped(self):
        self.assertEqual(self.node.attempt_stop({"run_id": RID, "attempt_id": self.attempt}), {"stopped": True})
        self.assertIn(("/api/spawn/aabb1234", None, "DELETE"), self.calls)
        self.assertGreaterEqual(self.timeouts["/api/spawn/aabb1234"], 120)   # a wedged process takes KiroCrew 30 s or more

    def test_a_task_that_finished_on_its_own_is_not_stopped(self):
        self.stop_answer = {"ok": True, "cancelled": False}
        self.assertEqual(self.node.attempt_stop({"run_id": RID, "attempt_id": self.attempt}), {"stopped": False, "finished": True})

    def test_another_gateway_error_is_passed_on(self):
        self.stop_answer = gateway_client.GatewayError(500, '{"error":"boom"}')
        with self.assertRaises(gateway_client.GatewayError):
            self.node.attempt_stop({"run_id": RID, "attempt_id": self.attempt})

    def test_a_task_already_gone_counts_as_stopped(self):
        self.stop_answer = gateway_client.GatewayError(404, '{"error":"not found"}')
        self.assertEqual(self.node.attempt_stop({"run_id": RID, "attempt_id": self.attempt}), {"stopped": True})

    def test_health_says_it_can_stop_one_task(self):
        self.assertTrue(self.node.health()["attempt_stop"])


if __name__ == "__main__":
    unittest.main()
