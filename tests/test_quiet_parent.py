"""BACKLOG (performance, EC2 2026-10-22): every finished task's result was injected into the platform's parent chat slot,
where KiroCrew ran a coordinator turn (a Kiro process and its credits) nobody reads — ``silent`` only mutes notifications.
KiroCrew's POST /api/spawn/mark-collected (0.10, kiro_crew/dashboard/messaging_api/run_control.py) is what its own
blocking spawn tool calls to say the parent has the result already: the turn and the wave's synthesis are skipped, the
result stays readable at GET /api/spawn/{id}. The Worker marks each run it starts right after the receipt; a refusal
(an older Gateway) changes nothing about the dispatch."""
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.worker import Worker

RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}
NATIVE = "aabb1234"


class QuietParentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew")
        self.calls, self.refuse = [], None

        def native(path, body=None, method=None, timeout=None):
            self.calls.append((path, body))
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn/mark-collected":
                if self.refuse:
                    raise self.refuse
                return {"status": "ok", "marked": len(body["ids"])}
            if path == "/api/spawn" and body is not None:
                return {"id": NATIVE}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.parent = self.node.binding(RID)[1]["parent"]

    def dispatch(self, n=2):
        return self.node.dispatch({"run_id": RID, "attempt_id": "attempt-" + str(n) * 32, "input": "需求", "spec": SPEC,
                                   "upstream": []})

    def marks(self):
        return [body for path, body in self.calls if path == "/api/spawn/mark-collected"]

    def test_a_started_run_is_marked_collected_on_its_parent(self):
        result = self.dispatch()
        self.assertEqual(result["status"], "running")
        self.assertEqual(self.marks(), [{"ids": [NATIVE], "parent_session": self.parent}])
        spawned = [i for i, (path, body) in enumerate(self.calls) if path == "/api/spawn" and body]
        marked = [i for i, (path, _) in enumerate(self.calls) if path == "/api/spawn/mark-collected"]
        self.assertLess(spawned[0], marked[0])   # after the receipt, before anything else waits on the run
        self.assertTrue(result.get("collected"))

    def test_a_gateway_that_refuses_the_mark_still_runs_the_task(self):
        for refusal in (gateway_client.GatewayError(404, '{"error":"not found"}'), OSError("reset")):
            with self.subTest(refusal=type(refusal).__name__):
                self.calls.clear()
                self.refuse = refusal
                result = self.dispatch(3 if isinstance(refusal, OSError) else 4)
                self.assertEqual((result["status"], result.get("native_id")), ("running", NATIVE))
                self.assertNotIn("collected", result)


if __name__ == "__main__":
    unittest.main()
