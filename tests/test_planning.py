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
  {"id": "plan", "name": "实现幂等", "tool": "claude", "model": "made-up-model", "prompt": "实现幂等键并补充测试"},
  {"name": "独立审查", "tool": "codex", "prompt": "独立审查并复跑测试"},
  {"name": "整理交付", "tool": "opencode", "prompt": "整理交付说明"}]}
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
        self.assertEqual([t["spec"]["tool"] for t in run["tasks"]], ["kiro", "claude", "codex", "claude"])
        self.assertTrue(any("K01" in note for note in applied["notes"]))
        self.assertEqual(run["tasks"][1]["spec"]["model"], "auto")
        self.assertEqual(run["version"], 2)
        self.assertTrue(run["planning"]["applied"])
        self.assertIn("幂等", run["planning"]["intent"])
        self.assertIn("验收", run["planning"]["summary"])
        self.assertTrue(any("made-up-model" in note for note in applied["notes"]))

    def test_an_invalid_plan_explains_why(self):
        rid = self.start()["id"]
        for text in ("", "没有代码块的回复", "```json\n{\"steps\": [{\"name\": \"x\", \"tool\": \"gpt\", \"prompt\": \"p\"}]}\n```"):
            with self.subTest(text=text[:20]), self.database.transaction() as db, self.assertRaises(Error):
                planning.expand(db, rid, text)


if __name__ == "__main__":
    unittest.main()
