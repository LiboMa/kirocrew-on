"""M2 §6 acceptance 2: every task says where its tool and model came from (submitted steps, a saved workflow, a template,
an imported document, the KiroCrew planner, the user's continue, or the Coordinator's switch)."""
import json
import tempfile
import unittest

from octopus import planning
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import authenticate, create_user, issue_token

STEP = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现并验证"}
PLAN = """```json
{"intent": "实现", "summary": "实现", "name": "计划", "steps": [
 {"name": "实现", "tool": "codex", "prompt": "实现", "kind": "implement", "acceptance": ["可运行"], "verify": ["true"]}]}
```"""


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
        self.count = 0

    def start(self, **body):
        self.count += 1
        rid = self.service.start(self.actor, {"request_id": f"mapping-fixture-{self.count:04d}", "input": "做一个功能",
                                              "source": {"type": "new"}, **body})["id"]
        return rid

    def mapping(self, rid, index=0):
        return self.service.run(self.actor, rid)["tasks"][index]["mapping"]

    def test_submitted_steps(self):
        self.assertEqual(self.mapping(self.start(steps=[STEP])), {"source": "steps", "label": "提交时指定"})

    def test_a_saved_workflow(self):
        flow = self.service.save_workflow(self.actor, {"name": "我的流程", "steps": [STEP]})
        mapping = self.mapping(self.start(workflow_id=flow["id"]))
        self.assertEqual(mapping["source"], "workflow")
        self.assertIn("我的流程", mapping["label"])

    def test_a_template_and_a_rule_matched_one(self):
        self.assertEqual(self.mapping(self.start(template_id="proof"))["source"], "template")
        matched = self.mapping(self.start(plan="match"))
        self.assertEqual(matched["source"], "template")
        self.assertIn("规则匹配", matched["label"])

    def test_an_imported_document(self):
        document = {"content": json.dumps({"name": "导入", "steps": [STEP]}), "format": "json"}
        self.assertEqual(self.mapping(self.start(document=document)), {"source": "document", "label": "导入的工作流文档"})

    def test_the_kirocrew_planner(self):
        rid = self.start()
        self.assertEqual(self.mapping(rid)["source"], "platform")   # the planning task itself is always Kiro
        with self.db.transaction() as db:
            planning.expand(db, rid, PLAN, {"codex": ["auto"]})
        self.assertEqual(self.mapping(rid, 1), {"source": "planner", "label": "KiroCrew 规划"})

    def test_a_users_continue_and_the_coordinators_switch(self):
        rid = self.start(steps=[STEP])
        with self.db.transaction() as db:   # the user changed the tool on continue
            db.execute("UPDATE tasks SET spec=? WHERE run_id=?", (dumps({**STEP, "model": "auto", "effort": "", "tool": "codex"}), rid))
        self.assertEqual(self.mapping(rid), {"source": "continue", "label": "续作时由用户修改"})
        with self.db.transaction() as db:   # then the Coordinator switched it
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                       "VALUES('attempt-x',?,'code',1,'{}','running',?,1,1)",
                       (rid, dumps({"switched_from": {"tool": "codex", "model": "auto", "effort": "", "reason": "codex 不可用"}})))
            db.execute("UPDATE tasks SET spec=?, current_attempt='attempt-x' WHERE run_id=?",
                       (dumps({**STEP, "model": "auto", "effort": "", "tool": "kiro"}), rid))
        mapping = self.mapping(rid)
        self.assertEqual(mapping["source"], "coordinator")
        self.assertIn("codex", mapping["label"])


if __name__ == "__main__":
    unittest.main()
