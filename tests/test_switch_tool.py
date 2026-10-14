"""D38 phase 1: tool-level failures go to diagnosis and the KiroCrew Coordinator may recommend another tool; the platform
checks the recommendation against the environment it showed and records the switch. Native calls are substitutes."""
import copy
import json
import tempfile
import unittest

from octopus import recovery
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

TOOLS = {"kiro": {"available": True, "login": "ok"}, "claude": {"available": True}, "codex": {"available": True},
         "opencode": {"available": False, "reason": "opencode 命令不存在"}}
MODELS = {"kiro": ["auto"], "claude": ["auto", "sonnet"], "codex": ["auto"], "opencode": ["auto"]}


def step(sid, tool, kind, **extra):
    return {"id": sid, "name": {"code": "实现", "review": "审查"}[sid], "tool": tool, "prompt": "完成并验证", "kind": kind,
            "acceptance": ["可运行"], **({"verify": ["true"]} if kind == "implement" else {}), **extra}


class SwitchCase(unittest.TestCase):
    capabilities = {"tools": TOOLS, "models": MODELS, "limitations": ["已知问题 K01：…"]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Fixture',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture",
                                            **({"capabilities": self.capabilities} if self.capabilities else {})})))
        self.calls, self.bodies = [], {}
        self.refuse_models, self.mismatch_rounds, self.fail_rounds = set(), set(), set()
        self.decisions = []
        self.engine = Engine(self.db, self.request)

    def start(self, steps, **body):
        return self.service.start(self.actor, {"request_id": "switch-" + str(len(self.calls)) + str(id(steps)),
                                               "input": "修复并验证", "steps": steps, "source": {"type": "new"},
                                               "worker_id": "worker", **body})

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/workspace"}
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        aid = body["attempt_id"]
        if path == "/dispatch":
            if not body.get("coordination") and body["spec"]["model"] in self.refuse_models:
                raise Error(409, "model_unavailable", "所选模型未由此 Worker 后端公布")
            self.bodies.setdefault(aid, copy.deepcopy(body))
            return {"status": "running", "native_id": aid[-8:], "context_hash": "h"}
        as_old_worker(path)
        record = self.bodies[aid]
        result = {"native": {"done": True, "result": "完成"}, "evidence": {"actual_backend": record["spec"]["tool"]},
                  "handoff": "交接"}
        if record.get("coordination"):
            result["decision"] = dumps(self.decisions.pop(0))
            return result
        number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == record["spec"]["id"])
        if (record["spec"]["id"], number) in self.mismatch_rounds:
            result["evidence"]["actual_backend"] = "kiro"
        failed = (record["spec"]["id"], number) in self.fail_rounds
        result["outcome"] = dumps({"decision": "changes_requested" if failed else "pass", "summary": "结论",
                                   "failure_type": "implementation" if failed else "none",
                                   "findings": ["问题"] if failed else []})
        return result

    def tick(self, rid, count=25):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def coordination(self):
        return [body["coordination"] for path, body in self.calls if path == "/dispatch" and body.get("coordination")]


class EnvironmentTests(SwitchCase):
    def test_the_policy_keeps_its_shape_unless_switching_is_turned_off(self):
        self.assertEqual(recovery.policy({"enabled": True, "max_cycles": 2}), {"enabled": True, "max_cycles": 2})
        self.assertEqual(recovery.policy({"switch_tools": False}), {"enabled": True, "max_cycles": 2, "switch_tools": False})
        with self.assertRaises(Error):
            recovery.policy({"switch_tools": "no"})

    def test_the_coordinator_is_shown_the_environment(self):
        self.decisions = [{"action": "wait_for_user", "reason": "需要用户确认", "instructions": "确认后续作"}]
        on = self.start([step("code", "claude", "implement")])
        self.fail_rounds = {("code", 1)}
        self.tick(on["id"])
        off = self.start([step("code", "claude", "implement")], recovery={"enabled": True, "max_cycles": 2, "switch_tools": False})
        self.fail_rounds = {("code", 1), ("code", 2)}
        self.decisions = [{"action": "wait_for_user", "reason": "需要用户确认", "instructions": "确认后续作"}]
        self.tick(off["id"])
        first, second = self.coordination()
        self.assertEqual(first["environment"], {"tools": TOOLS, "models": MODELS, "limitations": ["已知问题 K01：…"],
                                                "switch_tools": True})
        self.assertFalse(second["environment"]["switch_tools"])

    def test_the_coordinator_prompt_offers_a_switch_and_its_rules(self):
        prompt = recovery.coordinator_instructions({"environment": {"tools": TOOLS}}, __import__("pathlib").Path("/f"))
        self.assertNotIn("不换模型", prompt)
        for words in ("switch_tool", "available", "完全相同", "审查", "wait_for_user"):
            self.assertIn(words, prompt)
