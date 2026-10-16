"""D45: a tool or model the requirement names is the user's choice — the planner marks it (`requested`), the platform runs
it as asked, and when it is not available here says so and stops instead of using something else."""
import copy
import json
import tempfile
import unittest

from octopus import planning
from octopus.db import Database, dumps
from octopus.domain import Service, steps
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

TOOLS = {"kiro": {"available": True, "login": "ok"}, "claude": {"available": True}, "codex": {"available": True},
         "opencode": {"available": False, "reason": "opencode 命令不存在"}}
MODELS = {"kiro": ["auto"], "claude": ["auto", "sonnet"], "codex": ["auto"], "opencode": ["auto"]}


def plan(*tasks):
    return "```json\n" + json.dumps({"intent": "实现", "summary": "需求", "name": "计划", "steps": list(tasks)}, ensure_ascii=False) + "\n```"


def task(sid, tool, model="auto", **extra):
    return {"id": sid, "name": sid, "tool": tool, "model": model, "prompt": "完成", "kind": "deliver", "acceptance": ["完成"], **extra}


class RequestedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','W',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture",
                                            "capabilities": {"tools": TOOLS, "models": MODELS}})))
        self.bodies, self.plan = {}, plan(task("code", "claude"))
        self.engine = Engine(self.db, self.request)

    def start(self):
        return self.service.start(self.actor, {"request_id": "requested-fixture-1", "input": "实现，审查用 codex", "source": {"type": "new"},
                                               "worker_id": "worker", "recovery": {"enabled": True, "max_cycles": 2}})["id"]

    def request(self, config, path, body, **kwargs):
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/w"}
        if path == "/dispatch":
            self.bodies.setdefault(body["attempt_id"], copy.deepcopy(body))
            return {"status": "running", "native_id": body["attempt_id"][-8:], "context_hash": "h"}
        as_old_worker(path)
        record = self.bodies[body["attempt_id"]]
        spec = record["spec"]
        result = {"native": {"done": True, "result": self.plan if spec["id"] == "plan" else "完成"},
                  "evidence": {"actual_backend": spec["tool"], "handoff_path": "/w/handoff.md"}, "handoff": "交接"}
        if record.get("coordination"):
            result["decision"] = dumps({"action": "wait_for_user", "reason": "等待", "instructions": "检查"})
            return result
        result["outcome"] = dumps({"decision": "pass", "summary": spec["id"] + " 完成", "failure_type": "none", "findings": []})
        return result

    def tick(self, rid, count=30):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def dispatched(self):
        return [(b["spec"]["id"], b["spec"]["tool"], b["spec"]["model"]) for b in self.bodies.values() if not b.get("coordination")]

    def tasks(self, run):
        return {t["id"]: t for t in run["tasks"]}


class RequestedTests(RequestedCase):
    def test_a_requested_tool_is_used_and_labelled(self):
        self.plan = plan(task("code", "claude"), task("review", "codex", requested={"tool": "codex", "quote": "审查用 codex"}))
        rid = self.start()
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertIn(("review", "codex", "auto"), self.dispatched())
        self.assertEqual(self.tasks(run)["review"]["mapping"], {"source": "requested", "label": "需求中指定：「审查用 codex」"})
        self.assertEqual(self.tasks(run)["code"]["mapping"]["label"], "KiroCrew 规划")

    def test_a_planner_that_changed_the_tool_is_corrected(self):
        self.plan = plan(task("review", "claude", requested={"tool": "codex", "quote": "审查用 codex"}))
        rid = self.start()
        run = self.tick(rid)
        self.assertIn(("review", "codex", "auto"), self.dispatched())
        self.assertIn("任务「review」改回需求指定的工具 codex", run["planning"]["notes"])

    def test_an_unavailable_requested_tool_is_refused_not_replaced(self):
        self.plan = plan(task("code", "opencode", requested={"tool": "opencode", "quote": "用 opencode 实现"}))
        rid = self.start()
        run = self.tick(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual([d for d in self.dispatched() if d[0] == "code"], [])     # nothing ran it with another tool
        self.assertEqual(run["recovery"]["history"], [])                          # no Coordinator asked to switch
        attempt = self.tasks(run)["code"]["attempts"][-1]
        self.assertIn("需求指定的 opencode 在此执行环境不可用：opencode 命令不存在", attempt["error"])
        self.assertEqual(run["attention"]["title"], "需求指定的工具或模型不可用")

    def test_an_unpublished_requested_model_is_kept_and_refused(self):
        self.plan = plan(task("code", "claude", model="opus-9", requested={"tool": "claude", "model": "opus-9", "quote": "用 opus-9"}))
        rid = self.start()
        run = self.tick(rid)
        code = self.tasks(run)["code"]
        self.assertEqual(code["spec"]["model"], "opus-9")                         # not quietly made auto
        self.assertIn("需求指定的模型 opus-9 未由执行环境公布", code["attempts"][-1]["error"])
        self.assertIn("任务「code」：需求指定的模型 opus-9 未由执行环境公布", run["planning"]["notes"])
        self.assertEqual(run["recovery"]["history"], [])

    def test_a_continue_with_another_tool_supersedes_the_request(self):
        self.plan = plan(task("code", "opencode", requested={"tool": "opencode", "quote": "用 opencode 实现"}))
        rid = self.start()
        run = self.tick(rid)
        self.service.operate(self.actor, rid, "continue", {"request_id": "requested-continue-1", "expected_version": run["version"],
                                                           "task_id": "code", "execution": {"tool": "claude"}})
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertNotIn("requested", self.tasks(run)["code"]["spec"])
        self.assertEqual(self.tasks(run)["code"]["mapping"]["label"], "续作时由用户修改")

    def test_requested_is_checked(self):
        base = task("code", "claude")
        self.assertEqual(steps([{**base, "requested": {"tool": "claude", "quote": "用 claude"}}])[0]["requested"],
                         {"tool": "claude", "quote": "用 claude"})
        for bad in ({"quote": "只有原文"}, {"tool": "gemini"}, {"tool": "claude", "extra": 1}, "claude", {"model": "", "quote": "x"}):
            with self.subTest(bad), self.assertRaises(Error) as caught:
                steps([{**base, "requested": bad}])
            self.assertIn("requested 须为", str(caught.exception))

    def test_the_planner_is_told_how_to_mark_a_request(self):
        for spec in (planning.plan_step(), planning.replan_step("replan-1", [], "失败", {"reason": "r", "instructions": "i"})):
            self.assertIn("requested", spec["prompt"])
            self.assertIn("需求明确指定", spec["prompt"])


if __name__ == "__main__":
    unittest.main()
