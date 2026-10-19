"""D46 P5a, the Worker's side: a rework the platform asks to continue resumes the task's native conversation
(KiroCrew POST /api/spawn/{conversation}/continue); a refusal falls back to a fresh spawn, and only this Session's
conversations can be continued."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from octopus import gateway_client as crew_api
from octopus.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
RID, OTHER = "run-" + "1" * 32, "run-" + "9" * 32
FIRST, SECOND = "attempt-" + "a" * 32, "attempt-" + "b" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现加法并运行真实测试"}
REWORK = "这是同一任务的返工"


class ContinueWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        self.calls, self.continue_answer, self.agents = [], None, []

        def native(path, body=None, method=None, timeout=None, headers=None):
            self.calls.append((path, body))
            if path.startswith("/api/chat/slots/octopus-") and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is not None:
                return {"id": "aaaa1111" if not any(p == "/api/spawn" and b for p, b in self.calls[:-1]) else "cccc3333"}
            if path == "/api/spawn":
                return {"agents": self.agents}
            if path.endswith("/continue"):
                answer = self.continue_answer or {"id": "bbbb2222", "conversation": "aaaa1111", "status": "spawned"}
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return {"ok": True}
        self.node.native = native
        for rid in (RID, OTHER):
            self.node.prepare({"run_id": rid, "source": {"type": "new"}})

    def dispatch(self, attempt, rid=RID, **extra):
        return self.node.dispatch({"run_id": rid, "attempt_id": attempt, "input": "需求", "spec": SPEC, "upstream": [], **extra})

    def spawns(self):
        return [(p, b) for p, b in self.calls if (p == "/api/spawn" and b) or p.endswith("/continue")]

    def test_a_rework_continues_its_conversation(self):
        self.dispatch(FIRST)
        value = self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        path, body = self.spawns()[-1]
        self.assertEqual(path, "/api/spawn/aaaa1111/continue")
        self.assertEqual(set(body), {"task", "parent_session", "agent"})   # auto model: none sent
        self.assertEqual((body["parent_session"], body["agent"]), ("dashboard:octopus-" + RID, "poc-claude"))
        self.assertIn("[OCTOPUS-ATTEMPT:" + SECOND + "]", body["task"])
        self.assertIn(REWORK, body["task"])
        self.assertEqual(value["native_id"], "bbbb2222")
        self.assertEqual(value["continued"], {"conversation": "aaaa1111", "from": FIRST})

    def test_an_explicit_model_is_sent(self):
        self.dispatch(FIRST)
        with patch.object(Worker, "health", lambda self: {"models": {"claude": ["auto", "sonnet-x"]}}):
            self.node.dispatch({"run_id": RID, "attempt_id": SECOND, "input": "需求", "spec": {**SPEC, "model": "sonnet-x"},
                                "upstream": [], "continue": {"conversation": "aaaa1111", "from": FIRST}})
        self.assertEqual(self.spawns()[-1][1]["model"], "sonnet-x")

    def test_a_refused_continue_spawns_afresh_with_its_own_words(self):
        self.dispatch(FIRST)
        for attempt, status, code in (("attempt-" + "c" * 32, 404, "conversation_gone"),
                                      ("attempt-" + "d" * 32, 409, "conversation_busy"),
                                      ("attempt-" + "e" * 32, 400, "spawn_rejected")):
            with self.subTest(code):
                self.continue_answer = crew_api.GatewayError(status, json.dumps({"error": "no", "code": code}))
                value = self.dispatch(attempt, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
                path, body = self.spawns()[-1]
                self.assertEqual(path, "/api/spawn")
                self.assertNotIn(REWORK, body["task"])
                self.assertEqual(value["continue_refused"], code)
                self.assertNotIn("continued", value)
                self.assertEqual(value["status"], "running")

    def test_a_conversation_of_another_session_is_refused(self):
        self.dispatch(FIRST, rid=OTHER)   # aaaa1111 is the other Session's
        with self.assertRaises(Exception):
            self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        for bad in ({"conversation": "../x", "from": FIRST}, {"conversation": "aaaa1111", "from": "attempt-x"}, "aaaa1111"):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                self.dispatch("attempt-" + "d" * 32, **{"continue": bad})

    def test_a_lost_continue_receipt_is_reconciled(self):
        self.dispatch(FIRST)
        self.continue_answer = TimeoutError("accepted but response lost")
        self.assertEqual(self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})["status"],
                         "dispatch_unknown")
        task = next(b["task"] for p, b in self.spawns() if p.endswith("/continue"))
        self.agents = [{"id": "bbbb2222", "parent": "dashboard:octopus-" + RID, "task": task}]
        value = self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        self.assertEqual(value["native_id"], "bbbb2222")
        self.assertEqual(len([1 for p, _ in self.spawns() if p.endswith("/continue")]), 1)   # never sent twice

    def test_the_worker_says_it_continues(self):
        with patch.object(Worker, "tools", lambda self: {}), patch.object(Worker, "tool_auth", lambda self: "ok"):
            self.assertIs(self.node.health()["continue"], True)


class ContractTests(unittest.TestCase):
    def test_the_contract_lists_the_continue_endpoint(self):
        http = json.loads((ROOT / "tests/kirocrew_contract.json").read_text())["http"]
        entry = http["POST /api/spawn/{}/continue"]
        self.assertEqual(entry["module"], "dashboard/messaging_api/spawn.py")
        self.assertTrue({"task", "parent_session", "agent", "model"} <= set(entry["reads"]))


if __name__ == "__main__":
    unittest.main()
