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
    def setUp(self, **worker):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew", **worker)
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
        # EC2 2026-10-20: with no model KiroCrew continued on its global default (claude-opus-5.5): a claude "default" run
        # became Opus, a codex one was asked for a Claude model. "auto" is sent as on the first spawn.
        self.assertEqual(set(body), {"task", "parent_session", "agent", "model"})
        self.assertEqual(body["model"], "auto")
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
        self.dispatch(FIRST, rid=OTHER)   # aaaa1111 is the other Session's (or this Session moved to another Worker)
        value = self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        self.assertEqual(self.spawns()[-1][0], "/api/spawn")   # a fresh spawn, never the other Session's conversation
        self.assertEqual((value["status"], value["continue_refused"]), ("running", "not_this_session"))
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
        self.assertEqual(value["continued"], {"conversation": "aaaa1111", "from": FIRST})   # the chain is kept
        self.assertEqual(len([1 for p, _ in self.spawns() if p.endswith("/continue")]), 1)   # never sent twice

    def test_the_rework_words_follow_the_kind_of_work(self):
        self.dispatch(FIRST)
        cases = (("attempt-" + "c" * 32, {"kind": "review"}, {"recheck": True}, "不修改代码", "在已有改动上修复"),
                 ("attempt-" + "d" * 32, {"kind": "design"}, {"instruction": "补充"}, "不修改业务代码", "在已有改动上修复"),
                 ("attempt-" + "e" * 32, {"kind": "implement"}, {"instruction": "修复"}, "在已有改动上修复", "不修改代码"))
        for attempt, contract, repair, said, not_said in cases:
            with self.subTest(contract["kind"]):
                self.dispatch(attempt, contract=contract, repair=repair, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
                task = self.spawns()[-1][1]["task"]
                self.assertIn(said, task)
                self.assertNotIn(not_said, task)
                self.assertIn("instructions.md", task)

    def test_the_worker_says_it_continues(self):
        with patch.object(Worker, "tools", lambda self: {}), patch.object(Worker, "tool_auth", lambda self: "ok"):
            self.assertIs(self.node.health()["continue"], True)


class UnattendedTests(ContinueWorkerTests):
    """approval_mode auto with the boundary's standing YOLO off: a continuation cannot ask for auto approval."""

    def setUp(self):
        super().setUp(approval_mode="auto", crew_yolo=False)

    def test_a_rework_spawns_afresh_with_auto_approval(self):
        self.dispatch(FIRST)
        value = self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        path, body = self.spawns()[-1]
        self.assertEqual((path, body["approval_mode"]), ("/api/spawn", "auto"))
        self.assertEqual(value["continue_refused"], "approval_mode")

    # the inherited cases that expect a continuation do not apply here
    test_a_rework_continues_its_conversation = test_an_explicit_model_is_sent = None
    test_a_lost_continue_receipt_is_reconciled = test_the_rework_words_follow_the_kind_of_work = None
    test_a_refused_continue_spawns_afresh_with_its_own_words = None


class ContractTests(unittest.TestCase):
    def test_the_contract_lists_the_continue_endpoint(self):
        http = json.loads((ROOT / "tests/kirocrew_contract.json").read_text())["http"]
        entry = http["POST /api/spawn/{}/continue"]
        self.assertEqual(entry["module"], "dashboard/messaging_api/spawn.py")
        self.assertTrue({"task", "parent_session", "agent", "model"} <= set(entry["reads"]))


class RoutingEvidenceTests(unittest.TestCase):
    """KiroCrew writes one routing line per run, at its start, under its conversation's key: a continued run's is the
    first run's (EC2 2026-10-19: every continued rework failed identity_match,「实际 ACP 后端未核实」)."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root, self.parent = Path(tmp.name), None
        self.home, log = root / "crew", root / "provider-routing.jsonl"
        self.node = Worker(root / "worker", "x" * 40, crew_home=self.home, routing_log=log)

        def native(path, body=None, method=None, timeout=None, headers=None):
            if path.startswith("/api/chat/slots/octopus-") and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is not None:
                return {"id": "aaaa1111"}
            if path.endswith("/continue"):
                return {"id": "bbbb2222", "conversation": "aaaa1111", "status": "spawned"}
            if path == "/api/spawn":
                return {"agents": [{"id": run, "parent": self.parent, "done": False} for run in ("aaaa1111", "bbbb2222")]}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.parent = self.node.binding(RID)[1]["parent"]
        for attempt, extra in ((FIRST, {}), (SECOND, {"continue": {"conversation": "aaaa1111", "from": FIRST}})):
            self.node.dispatch({"run_id": RID, "attempt_id": attempt, "input": "需求", "spec": SPEC, "upstream": [], **extra})
        for run, started in (("aaaa1111", 1000.0), ("bbbb2222", 2000.0)):   # what KiroCrew wrote for each run
            folder = self.home / "subagents" / run
            folder.mkdir(parents=True)
            (folder / "state.json").write_text(json.dumps({"id": run, "parent_session": self.parent, "started": started,
                                                           "conversation_key": "subagent:aaaa1111", "resolved_model": ""}))
        log.write_text("".join(json.dumps({"time": at, "session_key": "subagent:aaaa1111", "actual_backend": "claude",
                                           "requested_model": model}) + "\n"
                               for at, model in ((1000.02, "auto"), (2000.03, "claude-opus-5.5"))))

    def evidence(self, attempt):
        return self.node.inspect({"run_id": RID, "attempt_id": attempt})["evidence"]

    def test_a_continued_run_is_found_under_its_conversation(self):
        evidence = self.evidence(SECOND)
        self.assertEqual(evidence["actual_backend"], "claude")
        self.assertEqual(evidence["routing"]["time"], 2000.03)

    def test_the_first_run_keeps_its_own_line(self):
        self.assertEqual(self.evidence(FIRST)["routing"]["time"], 1000.02)   # not its continuation's, written later

    def test_without_state_the_dispatch_names_the_conversation(self):
        (self.home / "subagents" / "bbbb2222" / "state.json").unlink()
        self.assertEqual(self.evidence(SECOND)["actual_backend"], "claude")


if __name__ == "__main__":
    unittest.main()
