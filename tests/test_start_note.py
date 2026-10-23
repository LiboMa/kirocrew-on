"""D73: as the platform starts a task, the parent Session's chat gets one line — the person and the Agent there know the
platform is the writer until the task's result note follows. A note KiroCrew does not take never holds the dispatch up."""
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.worker import Worker

RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "实现登录页", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}


class StartNoteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.node = Worker(root / "worker", "x" * 40, crew_home=root / "crew")
        self.calls, self.refuse = [], False

        def native(path, body=None, method=None, timeout=None):
            self.calls.append((path, body))
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path.endswith("/note") and body.get("source") == "octopus-start" and self.refuse:
                raise gateway_client.GatewayError(503, '{"error":"busy"}')
            if path == "/api/spawn" and body is not None:
                return {"id": "aabb1234"}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})

    def dispatch(self, attempt="attempt-" + "2" * 32):
        return self.node.dispatch({"run_id": RID, "attempt_id": attempt, "input": "需求", "spec": SPEC, "upstream": []})

    def notes(self):
        return [body for path, body in self.calls if path.endswith("/note") and body.get("source") == "octopus-start"]

    def test_a_started_task_is_said_in_the_chat_once(self):
        self.assertEqual(self.dispatch()["status"], "running")
        self.dispatch()   # the same Attempt again (a lost receipt's retry): no second line
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("KiroCrew ON 开始执行：实现登录页（Claude Code", notes[0]["content"])
        self.assertIn("完成前请不要修改工作区", notes[0]["content"])

    def test_a_note_kiroCrew_does_not_take_never_holds_the_dispatch_up(self):
        self.refuse = True
        result = self.dispatch()
        self.assertEqual((result["status"], result["native_id"]), ("running", "aabb1234"))


if __name__ == "__main__":
    unittest.main()
