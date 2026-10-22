"""KiroCrew Agent planning: the Session starts with a Kiro planning task; code validates its plan and appends the tasks."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import planning
from octopus.db import Database
from octopus.domain import Service
from octopus.security import Error, authenticate, create_user, issue_token

PLAN = """规划如下：

```json
{"intent": "为订单退款增加幂等保护（缺陷修复）", "summary": "## 目标\\n重复退款请求只生效一次。\\n## 验收\\n- 并发重复请求只退款一次",
 "name": "退款幂等", "steps": [
  {"id": "plan", "name": "实现幂等", "tool": "claude", "model": "made-up-model", "prompt": "实现幂等键并补充测试",
   "kind": "implement", "acceptance": ["并发重复请求只退款一次"], "verify": ["npm test"]},
  {"name": "独立审查", "tool": "codex", "prompt": "独立审查并复跑测试", "kind": "review", "acceptance": ["并发重复请求只退款一次"]},
  {"name": "整理交付", "tool": "opencode", "prompt": "整理交付说明", "kind": "deliver", "acceptance": ["交付说明列出改动与验证"]}]}
```
"""


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name))
        with self.database.transaction() as db:
            owner = create_user(db, "planner", "Planner-password-123!", True)
            self.actor = authenticate(db, issue_token(db, owner["id"])["token"])
        self.service = Service(self.database)

    def start(self, **body):
        return self.service.start(self.actor, {"request_id": "planning-request-" + str(len(body)), "input": "订单退款需要幂等保护",
                                               "source": {"type": "new"}, **body})

    def test_a_requirement_without_a_plan_is_planned_by_a_kiro_agent(self):
        run = self.service.run(self.actor, self.start()["id"])
        self.assertEqual([t["spec"]["tool"] for t in run["tasks"]], ["kiro"])
        self.assertEqual(run["tasks"][0]["id"], planning.PLAN_ID)
        self.assertIn("意图识别", run["tasks"][0]["spec"]["prompt"])
        self.assertEqual(run["planning"], {"mode": "agent", "applied": False})
        matched = self.service.run(self.actor, self.start(plan="match")["id"])
        self.assertIsNone(matched["planning"])

    def test_the_plan_is_validated_by_code_and_appended_to_the_same_session(self):
        rid = self.start()["id"]
        with self.database.transaction() as db:
            applied = planning.expand(db, rid, PLAN, {"claude": ["auto", "sonnet"]})
        run = self.service.run(self.actor, rid)
        self.assertEqual([t["id"] for t in run["tasks"]], ["plan", "plan-2", "task-2", "task-3"])
        self.assertEqual([t["spec"]["tool"] for t in run["tasks"]], ["kiro", "claude", "codex", "opencode"])
        self.assertFalse(any("K01" in note for note in applied["notes"]))   # no built-in swap (D38 A5)
        self.assertEqual(run["tasks"][1]["spec"]["model"], "auto")
        self.assertEqual(run["version"], 2)
        self.assertTrue(run["planning"]["applied"])
        self.assertIn("幂等", run["planning"]["intent"])
        self.assertIn("验收", run["planning"]["summary"])
        self.assertTrue(any("made-up-model" in note for note in applied["notes"]))

    def test_the_planner_asks_runnable_deliveries_to_declare_how_they_start(self):
        prompt = planning.plan_step()["prompt"]
        self.assertIn(".octopus/preview.json", prompt)
        self.assertIn("验收", prompt[prompt.index(".octopus/preview.json") - 200:prompt.index(".octopus/preview.json") + 200])

    def test_the_planner_sees_which_tools_this_environment_can_start(self):
        tools = {"claude": {"available": True}, "opencode": {"available": False, "reason": "opencode 命令不存在"}}
        prompt = planning.plan_step({"claude": ["auto"]}, tools, ["已知问题 K01：OpenCode …"])["prompt"]
        self.assertIn('"opencode":{"available":false,"reason":"opencode 命令不存在"}', prompt)
        self.assertIn("已知问题 K01", prompt)
        self.assertIn("available 为 false 的工具不要选择", prompt)
        self.assertFalse(hasattr(planning, "REASSIGN"))

    def test_the_planner_knows_the_gates_its_tasks_meet(self):
        prompt = planning.plan_step()["prompt"]
        self.assertIn("会话基线", prompt)       # tests_fail_on_baseline
        self.assertIn("paths 中列出", prompt)   # artifacts_locked
        self.assertIn("记提示", prompt)         # D57 §4: both are warnings now
        self.assertIn("不安装依赖", prompt)
        self.assertIn(".gitignore", prompt)

    def test_planned_tasks_need_their_contract(self):
        rid = self.start()["id"]
        missing = [('{"name": "x", "steps": [{"name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", '
                    '"acceptance": ["可运行"]}]}', "验证命令"),
                   ('{"name": "x", "steps": [{"name": "审查", "tool": "codex", "prompt": "审查", "kind": "review"}]}', "验收项"),
                   ('{"name": "x", "steps": [{"name": "实现", "tool": "claude", "prompt": "实现", "acceptance": ["a"], '
                    '"verify": ["b"]}]}', "类别")]
        for text, word in missing:
            with self.subTest(word=word), self.database.transaction() as db, self.assertRaises(Error) as caught:
                planning.expand(db, rid, text)
            self.assertEqual(caught.exception.code, "plan_invalid")
            self.assertIn(word, str(caught.exception))

    def test_the_plan_keeps_each_tasks_contract(self):
        rid = self.start()["id"]
        with self.database.transaction() as db:
            planning.expand(db, rid, PLAN, {"claude": ["auto"]})
        specs = [t["spec"] for t in self.service.run(self.actor, rid)["tasks"]][1:]
        self.assertEqual(specs[0]["acceptance"], ["并发重复请求只退款一次"])
        self.assertEqual([s["kind"] for s in specs], ["implement", "review", "deliver"])

    def test_an_invalid_plan_explains_why(self):
        rid = self.start()["id"]
        for text in ("", "没有代码块的回复", "```json\n{\"steps\": [{\"name\": \"x\", \"tool\": \"gpt\", \"prompt\": \"p\"}]}\n```"):
            with self.subTest(text=text[:20]), self.database.transaction() as db, self.assertRaises(Error):
                planning.expand(db, rid, text)


if __name__ == "__main__":
    unittest.main()
