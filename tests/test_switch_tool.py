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
        number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["run_id"] == record["run_id"]
                     and b["spec"]["id"] == record["spec"]["id"])
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


class ToolFailureTests(SwitchCase):
    WAIT = {"action": "wait_for_user", "reason": "需要换用工具", "instructions": "请换用可用的工具后续作"}

    def test_an_unavailable_tool_goes_to_diagnosis_without_being_dispatched(self):
        self.decisions = [self.WAIT]
        run = self.tick(self.start([step("code", "opencode", "implement")])["id"])
        self.assertFalse([b for p, b in self.calls if p == "/dispatch" and not b.get("coordination")])
        attempt = run["tasks"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "blocked")
        self.assertEqual(attempt["error"], "opencode 在此执行环境不可用：opencode 命令不存在")
        self.assertEqual((attempt["evidence"]["failure_type"], attempt["evidence"]["refused"]), ("tool", "tool_unavailable"))
        self.assertEqual(run["recovery"]["used"], 1)
        self.assertEqual(self.coordination()[0]["failure_type"], "tool")

    def test_a_refused_model_goes_to_diagnosis(self):
        self.decisions, self.refuse_models = [self.WAIT], {"unpublished-x"}
        run = self.tick(self.start([step("code", "claude", "implement", model="unpublished-x")])["id"])
        evidence = run["tasks"][0]["attempts"][0]["evidence"]
        self.assertEqual((evidence["failure_type"], evidence["refused"]), ("tool", "model_unavailable"))
        self.assertEqual(run["recovery"]["used"], 1)
        self.assertEqual(run["attention"]["title"], "需要换用工具或模型")

    def test_a_backend_that_is_not_the_requested_one_goes_to_diagnosis(self):
        self.decisions, self.mismatch_rounds = [self.WAIT], {("code", 1)}
        run = self.tick(self.start([step("code", "claude", "implement")])["id"])
        self.assertEqual(run["tasks"][0]["attempts"][0]["evidence"]["failure_type"], "tool")
        self.assertEqual(run["recovery"]["used"], 1)

    def test_an_unprepared_workspace_is_not_a_tool_failure(self):
        def refuse(config, path, body, **kwargs):
            if path == "/dispatch" and not body.get("coordination"):
                raise Error(409, "not_prepared", "工作区未准备")
            return self.request(config, path, body, **kwargs)
        self.engine.request, self.decisions = refuse, [self.WAIT]
        run = self.tick(self.start([step("code", "claude", "implement")])["id"])
        evidence = run["tasks"][0]["attempts"][0]["evidence"]
        self.assertNotIn("refused", evidence)
        self.assertNotEqual(evidence.get("failure_type"), "tool")
        self.assertEqual(self.coordination()[0]["failure_type"], "native_error")   # as before: reconcile diagnoses it


class OldWorkerTests(SwitchCase):
    capabilities = None   # registered before D38: no tools in its capabilities

    def test_an_old_worker_without_tools_dispatches_as_before(self):
        run = self.tick(self.start([step("code", "opencode", "implement")])["id"])
        self.assertTrue([b for p, b in self.calls if p == "/dispatch" and b["spec"]["tool"] == "opencode"])
        self.assertEqual(run["status"], "completed")


class SwitchTests(SwitchCase):
    def switch(self, tool="claude", model="auto", target="code", **extra):
        return {"action": "switch_tool", "target_task_id": target, "tool": tool, "model": model, "effort": "",
                "reason": "原工具在此执行环境不可用", "instructions": "以新工具重新执行", **extra}

    def test_an_unavailable_tool_is_diagnosed_before_dispatch_and_the_switch_completes(self):
        self.decisions = [self.switch()]
        rid = self.start([step("code", "opencode", "implement"), step("review", "codex", "review")])["id"]
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        code = run["tasks"][0]
        self.assertEqual(code["spec"]["tool"], "claude")
        second = code["attempts"][-1]
        self.assertEqual(second["spec"]["tool"], "claude")
        self.assertEqual(second["evidence"]["switched_from"],
                         {"tool": "opencode", "model": "auto", "effort": "", "reason": "原工具在此执行环境不可用",
                          "by": "coordinator", "recovery": 1})
        self.assertNotIn("switched_from", run["tasks"][1]["attempts"][-1]["evidence"])
        revisions = self.service.revisions(self.actor, rid)
        self.assertEqual(revisions[-1]["summary"], "自动纠错：「实现」由 opencode / auto 换为 claude / auto（原工具在此执行环境不可用）")
        self.assertIn("task.tool_switched", [e["kind"] for e in self.service.events(self.actor, rid)])

    def test_a_refused_model_is_fixed_on_the_same_tool(self):
        self.decisions, self.refuse_models = [self.switch(model="auto")], {"unpublished-x"}
        run = self.tick(self.start([step("code", "claude", "implement", model="unpublished-x")])["id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["tasks"][0]["attempts"][-1]["evidence"]["switched_from"]["model"], "unpublished-x")

    def test_switch_validation_explains_each_refusal(self):
        cases = [("unavailable", [step("code", "opencode", "implement")], self.switch(tool="opencode", model="auto"), "不可用"),
                 ("same", [step("code", "claude", "implement")], self.switch(tool="claude"), "相同"),
                 ("model", [step("code", "claude", "implement")], self.switch(tool="codex", model="gpt-x"), "未由 codex 公布"),
                 ("target", [step("code", "claude", "implement"), step("review", "codex", "review")],
                  self.switch(tool="codex", target="code"), "失败的任务"),
                 ("review", [step("code", "claude", "implement"), step("review", "opencode", "review")],
                  self.switch(tool="claude", target="review"), "审查须使用与被审查代码（claude）不同的工具"),
                 ("effort", [step("code", "claude", "implement")], self.switch(tool="codex", model="auto", effort="ultra"), "Effort")]
        for name, steps, decision, words in cases:
            with self.subTest(name):
                self.decisions = [decision]
                self.mismatch_rounds = {("code", 1)} if name in {"same", "model", "effort"} else set()
                self.fail_rounds = {("review", 1)} if name == "target" else set()
                run = self.tick(self.start(steps)["id"])
                self.assertEqual(run["status"], "blocked")
                self.assertTrue(run["error"].startswith("协调者换工具的推荐未通过核对："), run["error"])
                self.assertIn(words, run["error"])

    def test_switching_turned_off_is_refused(self):
        self.decisions = [self.switch()]
        run = self.tick(self.start([step("code", "opencode", "implement")],
                                   recovery={"enabled": True, "max_cycles": 2, "switch_tools": False})["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertIn("已关闭换工具", run["error"])

    def test_switches_are_bounded_by_the_cycle_limit(self):
        self.decisions = [self.switch(tool="claude"), self.switch(tool="codex")]
        self.mismatch_rounds = {("code", 1), ("code", 2), ("code", 3)}
        run = self.tick(self.start([step("code", "codex", "implement")],
                                   recovery={"enabled": True, "max_cycles": 2})["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["recovery"]["used"], 2)
        self.assertIn("2 轮上限", run["attention"]["action"])

    def test_a_continue_after_a_switch_is_not_labelled_a_switch(self):
        rid = self.start([step("code", "claude", "implement")])["id"]   # not ticked: no Attempt yet
        with self.db.transaction() as db:
            for number, aid in enumerate(("attempt-a", "attempt-b"), 1):   # created before the diagnosis was applied
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) "
                           "VALUES(?,?,'code',?,'{}','blocked',0,0)", (aid, rid, number))
            db.execute("INSERT INTO recoveries(id,run_id,number,failed_task_id,failed_attempt_id,attempt_id,status,context,"
                       "decision,created,updated) VALUES('r',?,1,'code','attempt-a','attempt-b','applied','{}',?,1,1)",
                       (rid, dumps({**self.switch(tool="claude"), "switched_from": {"tool": "codex", "model": "auto", "effort": ""}})))
            # the task still runs the Coordinator's choice: labelled; the user picked another since: not
            self.assertEqual(recovery.switched_from(db, rid, "code", {"tool": "claude", "model": "auto", "effort": ""})["tool"], "codex")
            self.assertIsNone(recovery.switched_from(db, rid, "code", {"tool": "codex", "model": "auto", "effort": ""}))


class SwitchWithoutToolsTests(SwitchCase):
    capabilities = {"models": MODELS}   # a Worker from before D38: models but no tools

    def test_switch_is_refused_without_reported_tools(self):
        self.decisions, self.mismatch_rounds = [{"action": "switch_tool", "target_task_id": "code", "tool": "codex",
                                                 "model": "auto", "effort": "", "reason": "r", "instructions": "i"}], {("code", 1)}
        run = self.tick(self.start([step("code", "claude", "implement")])["id"])
        self.assertEqual(run["status"], "blocked")
        self.assertIn("未报告可用性", run["error"])
