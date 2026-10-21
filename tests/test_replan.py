"""D41: the Coordinator may decide the plan itself is wrong (replan): the failed task and those after it are replaced by
what a KiroCrew planning task plans for the remaining work; accepted tasks stay; once per session."""
import copy
import json
import tempfile
import unittest

from octopus import planning, recovery
from octopus.db import Database, dumps
from octopus.domain import Service, steps
from octopus.security import Error
from octopus.engine import Engine
from octopus.security import authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["可运行"]},
         {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]},
         {"id": "deliver", "name": "交付", "tool": "claude", "prompt": "交付", "kind": "deliver", "acceptance": ["可交付"]}]
NEW_PLAN = """重新规划如下：
```json
{"intent": "补上缺少的测试", "summary": "剩余工作", "name": "剩余工作", "steps": [
 {"id": "review", "name": "补测试", "tool": "claude", "prompt": "补测试", "kind": "test", "acceptance": ["测试覆盖"], "verify": ["true"]},
 {"id": "check", "name": "复审", "tool": "codex", "prompt": "复审", "kind": "review", "acceptance": ["无阻断问题"]}]}
```"""
REPLAN = {"action": "replan", "reason": "计划缺少测试步骤，审查无法通过", "instructions": "加入测试任务后复审"}


class ReplanCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','W',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture", "capabilities": {}})))
        self.calls, self.bodies, self.plan = [], {}, NEW_PLAN
        self.decisions = [REPLAN] + [{"action": "wait_for_user", "reason": "等待", "instructions": "检查"}] * 3
        self.failing = {("review", 1)}
        self.engine = Engine(self.db, self.request)

    def start(self, steps=STEPS, **body):
        return self.service.start(self.actor, {"request_id": "replan-fixture-1", "input": "实现登录并验证", "steps": steps,
                                               "source": {"type": "new"}, "worker_id": "worker", **body})["id"]

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/w"}
        if path == "/dispatch":
            if not body.get("coordination"):
                try:   # what the real Worker checks before anything is spawned
                    steps([body["spec"]])
                except Error as exc:
                    raise Error(400, "invalid_request", str(exc)) from None
            self.bodies.setdefault(body["attempt_id"], copy.deepcopy(body))
            return {"status": "running", "native_id": body["attempt_id"][-8:], "context_hash": "h"}
        as_old_worker(path)
        record = self.bodies[body["attempt_id"]]
        spec = record["spec"]
        result = {"native": {"done": True, "result": "完成"}, "evidence": {"actual_backend": spec["tool"],
                  "handoff_path": "/w/" + spec["id"] + "/handoff.md"}, "handoff": "交接"}
        if record.get("coordination"):
            result["decision"] = dumps(self.decisions.pop(0))
            return result
        number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == spec["id"])
        failed = (spec["id"], number) in self.failing
        if spec["id"].startswith("replan-"):
            result["native"]["result"] = self.plan
        result["outcome"] = dumps({"decision": "changes_requested" if failed else "pass",
                                   "summary": spec["id"] + " 的结论" + ("：空输入用例失败" if failed else ""),
                                   "failure_type": "implementation" if failed else "none", "findings": ["缺少测试"] if failed else []})
        return result

    def tick(self, rid, count=30):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def dispatched(self, task):
        return [b for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == task]


class ReplanTests(ReplanCase):
    def test_a_replan_replaces_the_failed_and_later_tasks(self):
        self.plan = "没有计划"            # stop after the replan task, to look at what it was told
        rid = self.start()
        run = self.tick(rid)
        ids = [t["id"] for t in run["tasks"]]
        self.assertEqual(ids[:2], ["code", "replan-1"])
        code = run["tasks"][0]
        self.assertEqual((code["status"], len(code["attempts"])), ("accepted", 1))   # accepted work stays as it was
        prompt = self.dispatched("replan-1")[0]["spec"]["prompt"]
        for words in ("审查", "交付", "空输入用例失败", "计划缺少测试步骤", "只规划剩余的工作"):
            self.assertIn(words, prompt)
        dispatch = self.dispatched("replan-1")[0]
        self.assertEqual(dispatch["input"], "实现登录并验证")                      # the requirement travels as input,
        self.assertEqual([u["id"] for u in dispatch["upstream"]], ["code"])        # the accepted work as upstream
        self.assertEqual(dispatch["upstream"][0]["summary"], "code 的结论")
        summaries = [r["summary"] for r in self.service.revisions(self.actor, rid)]
        self.assertTrue(any(s.startswith("自动纠错：重新规划，替换「审查、交付」") for s in summaries), summaries)
        snapshot_replan = run.get("replan") or {}
        self.assertEqual([r["id"] for r in snapshot_replan.get("replaced", [])], ["review", "deliver"])
        replaced = [e["payload"] for e in self.service.events(self.actor, rid) if e["kind"] == "task.replaced"]
        self.assertEqual(replaced[0]["removed"], ["review", "deliver"])   # the change log says which tasks went

    def test_the_replan_adds_its_tasks_and_the_session_completes(self):
        rid = self.start()
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "completed")
        self.assertEqual([t["id"] for t in run["tasks"]], ["code", "replan-1", "review-2", "check"])
        self.assertTrue(run["replan"]["applied"])
        summaries = [r["summary"] for r in self.service.revisions(self.actor, rid)]
        self.assertIn("重新规划：追加 2 个任务", summaries)

    def test_new_tasks_do_not_take_the_ids_of_replaced_ones(self):
        rid = self.start()
        run = self.tick(rid, 40)
        new = {t["id"]: t for t in run["tasks"]}["review-2"]
        self.assertEqual([a["spec"]["name"] for a in new["attempts"]], ["补测试"])   # the old review's Attempts are not here

    def test_an_invalid_replan_blocks_the_replan_task(self):
        self.plan, self.decisions = "没有计划", [REPLAN, {"action": "wait_for_user", "reason": "规划失败", "instructions": "检查"}]
        rid = self.start()
        run = self.tick(rid)
        replan = {t["id"]: t for t in run["tasks"]}["replan-1"]
        self.assertEqual(replan["status"], "blocked")
        self.assertEqual([t["id"] for t in run["tasks"]], ["code", "replan-1"])

    def test_a_second_replan_is_refused(self):
        self.decisions = [REPLAN, REPLAN]
        self.failing = {("review", 1), ("check", 1)}
        rid = self.start(recovery={"enabled": True, "max_cycles": 3})
        run = self.tick(rid, 50)
        self.assertEqual(run["status"], "blocked")
        self.assertIn("已重新规划过", run["error"])

    def test_the_planning_task_itself_is_not_replanned(self):
        rid = self.service.start(self.actor, {"request_id": "replan-fixture-plan", "input": "实现登录并验证",
                                              "source": {"type": "new"}, "worker_id": "worker"})["id"]   # Kiro plans it
        run = self.tick(rid)            # the plan task answers "完成": no plan, blocked; the Coordinator asks to replan
        self.assertEqual(run["status"], "blocked")
        self.assertIn("规划任务本身", run["error"])

    def test_a_task_named_like_a_replan_is_an_ordinary_task(self):
        self.failing = {("replan-x", 1)}
        rid = self.start([{**STEPS[0], "id": "replan-x", "name": "用户的任务"}] + STEPS[1:])
        run = self.tick(rid, 40)
        self.assertEqual(run["replan"]["replaced"][0]["id"], "replan-x")   # replaced like any other task

    def test_the_coordinator_is_told_whether_it_has_replanned(self):
        self.decisions = [REPLAN, {"action": "wait_for_user", "reason": "再看", "instructions": "检查"}]
        self.failing = {("review", 1), ("check", 1)}
        rid = self.start(recovery={"enabled": True, "max_cycles": 3})
        self.tick(rid, 50)
        contexts = [b["coordination"]["environment"].get("replanned") for b in self.bodies.values() if b.get("coordination")]
        self.assertEqual(contexts[:2], [False, True])


class ReplanReviewTests(ReplanCase):
    def test_the_replan_prompt_fits_the_worker_limit(self):
        long = "很长的内容" * 3000
        spec = planning.replan_step("replan-1",
                                    [{"id": "t%d" % i, "name": "任务" * 40, "kind": "implement"} for i in range(12)], long,
                                    {"reason": long, "instructions": long}, {"claude": ["m" * 60] * 60},
                                    {"kiro": {"available": True, "reason": long}}, [long] * 5,
                                    {"available": ["c" * 40] * 60, "missing": ["d" * 40] * 60})   # every input at its clip
        steps([spec])   # the Worker refuses prompts over 8000 characters
        self.assertLessEqual(len(spec["prompt"]), 7400)   # room for a few more skills or roles (BACKLOG D50)
        for name in ("test-driven-development", "evaluator"):   # the compact catalogues still name every choice
            self.assertIn(name, spec["prompt"])

    def test_the_planners_are_told_which_commands_a_check_can_run(self):
        commands = {"available": ["python3", "git"], "missing": ["python", "npm"]}
        for spec in (planning.plan_step(commands=commands), planning.replan_step("replan-1", [], "审查未通过", REPLAN, commands=commands)):
            self.assertIn("可用：python3、git", spec["prompt"])
            self.assertIn("没有：python、npm", spec["prompt"])
        self.assertNotIn("没有：", planning.plan_step()["prompt"])   # an older Worker that does not say

    def test_the_replan_prompt_shows_every_field_a_planned_task_carries(self):
        # EC2: without the example the planner left out each task's prompt and the plan failed validation
        prompt = planning.replan_step("replan-1", [], "审查未通过", REPLAN)["prompt"]
        for field in planning.EXAMPLE["steps"][0]:
            self.assertIn(f'"{field}"', prompt, field)

    def test_depends_on_follows_a_renamed_task_and_may_name_a_kept_one(self):
        self.plan = """```json
{"intent": "i", "summary": "s", "name": "n", "steps": [
 {"id": "review", "name": "补测试", "tool": "claude", "prompt": "补测试", "kind": "test", "acceptance": ["a"], "verify": ["true"],
  "depends_on": ["code"]},
 {"id": "check", "name": "复审", "tool": "codex", "prompt": "复审", "kind": "review", "acceptance": ["a"], "depends_on": ["review"]}]}
```"""
        rid = self.start()
        run = self.tick(rid, 40)
        tasks = {t["id"]: t for t in run["tasks"]}
        self.assertEqual(tasks["review-2"]["spec"]["depends_on"], ["code"])        # a kept, accepted task
        self.assertEqual(tasks["check"]["spec"]["depends_on"], ["review-2"])       # the renamed one
        self.assertEqual([u["id"] for u in self.dispatched("check")[0]["upstream"]], ["review-2"])

    def test_tasks_after_a_replan_are_not_told_to_repair(self):
        rid = self.start()
        self.tick(rid, 40)
        for task in ("replan-1", "review-2", "check"):
            with self.subTest(task):
                self.assertNotIn("repair", self.dispatched(task)[0])

    def test_a_rework_after_a_replan_does_not_rerun_the_planner(self):
        self.failing = {("review", 1), ("check", 1)}
        self.decisions = [REPLAN, {"action": "rework", "target_task_id": "code", "reason": "实现有缺陷", "instructions": "修复"}] + \
            [{"action": "wait_for_user", "reason": "等待", "instructions": "检查"}] * 2
        rid = self.start(recovery={"enabled": True, "max_cycles": 3})
        run = self.tick(rid, 60)
        self.assertEqual(len(self.dispatched("replan-1")), 1)
        self.assertEqual({t["id"]: t["status"] for t in run["tasks"]}["replan-1"], "accepted")
        self.assertEqual(run["status"], "completed")

    def test_a_new_task_reusing_a_replaced_id_is_labelled_replanned(self):
        self.plan = """```json
{"intent": "i", "summary": "s", "name": "n", "steps": [
 {"id": "deliver", "name": "新的交付", "tool": "codex", "prompt": "交付", "kind": "deliver", "acceptance": ["a"]}]}
```"""
        rid = self.start()
        run = self.tick(rid, 40)
        new = [t for t in run["tasks"] if t["spec"]["name"] == "新的交付"][0]
        self.assertNotEqual(new["id"], "deliver")                                  # the replaced deliver never ran, but was there
        self.assertEqual(new["mapping"]["label"], "KiroCrew 重新规划")

    def test_the_review_after_a_replan_is_judged_against_the_codes_backend(self):
        rid = self.start()
        self.tick(rid, 40)
        with self.db.read() as db:
            check = db.execute("SELECT ordinal FROM tasks WHERE run_id=? AND id='check'", (rid,)).fetchone()[0]
            self.assertEqual(recovery.reviewed_backend(db, rid, check), "claude")   # not the planner's kiro

    def test_a_user_task_named_replan_1_does_not_break_the_replan(self):
        steps_with = [{**STEPS[0], "id": "replan-1", "name": "用户的任务"}] + STEPS[1:]
        rid = self.start(steps_with)
        run = self.tick(rid, 40)
        self.assertEqual(run["replan"]["task"], "replan-2")
        self.assertEqual(run["status"], "completed")


if __name__ == "__main__":
    unittest.main()
