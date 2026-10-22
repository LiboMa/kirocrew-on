"""EC2 2026-10-22 13:25–13:30: the owner boundary's Gateway event loop stalled for up to 18 s and its watchdog ended it
(SIGALRM), cutting two sub-agents off mid-turn. Every inspect of a running Attempt read the whole /api/spawn listing
(58 runs, 310 KB, ~150 ms of the Gateway's single loop thread each); one run's own GET /api/spawn/{id} is 7 KB / 16 ms.
A running Attempt whose ownership its state.json proves is now inspected by its own row; the listing is still read when
it ended (it alone says a person stopped it), is queued, or has no state to prove it is ours."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.worker import Worker

RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}
NATIVE = "aabb1234"


class InspectCostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew")
        self.attempt = "attempt-" + "2" * 32
        self.calls, self.single = [], {"id": NATIVE, "done": False, "turns": 3, "last_tool": "shell", "result": "进行中"}
        self.listed = None

        def native(path, body=None, method=None, timeout=None):
            self.calls.append(path)
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn" and body is not None:
                return {"id": NATIVE}
            if path == "/api/spawn":
                return {"agents": self.listed if self.listed is not None else [self.row()]}
            if path == "/api/spawn/" + NATIVE:
                return dict(self.single)
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.node.dispatch({"run_id": RID, "attempt_id": self.attempt, "input": "需求", "spec": SPEC, "upstream": []})
        self.parent = self.node.binding(RID)[1]["parent"]

    def row(self, **extra):
        return {"id": NATIVE, "task": f"[OCTOPUS-ATTEMPT:{self.attempt}] 编码", "done": False, "parent": self.parent,
                "turns": 3, "last_tool": "shell", "result": "进行中", **extra}

    def state(self):
        folder = self.root / "crew" / "subagents" / NATIVE
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(json.dumps({"id": NATIVE, "parent_session": self.parent, "updated_at": 1}))

    def inspect(self):
        self.calls.clear()
        return self.node.inspect({"run_id": RID, "attempt_id": self.attempt})

    def test_a_running_attempt_it_owns_is_read_by_its_own_row(self):
        self.state()
        result = self.inspect()
        self.assertEqual((result["native"]["done"], result["native"]["turns"]), (False, 3))
        self.assertNotIn("/api/spawn", self.calls)
        self.assertIn("/api/spawn/" + NATIVE, self.calls)
        self.assertEqual(result["progress"]["turns"], 3)

    def test_an_ended_attempt_still_reads_the_listing_for_who_stopped_it(self):
        self.state()
        self.single = {"id": NATIVE, "done": True, "result": "完整结果", "error": "stopped"}
        self.listed = [self.row(done=True, stopped=True, result="截断的结果")]
        result = self.inspect()
        self.assertIn("/api/spawn", self.calls)
        self.assertTrue(result["native"]["stopped"])
        self.assertEqual(result["native"]["result"], "完整结果")   # the full one, as before

    def test_without_state_to_prove_it_is_ours_the_listing_decides(self):
        result = self.inspect()
        self.assertIn("/api/spawn", self.calls)
        self.assertEqual(result["native"]["turns"], 3)

    def test_a_queued_row_goes_the_way_it_always_did(self):
        self.state()
        self.single = {"id": NATIVE, "done": False, "status": "queued", "queued": True,
                       "task": f"[OCTOPUS-ATTEMPT:{self.attempt}] 编码", "reason": "low_memory"}
        self.listed = []   # the ordinary list leaves queued runs out
        result = self.inspect()
        self.assertTrue(result["native"].get("queued"))


if __name__ == "__main__":
    unittest.main()
