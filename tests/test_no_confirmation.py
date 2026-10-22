"""D68 (owner 2026-10-22: 「从任何工作流决定开始后，不需要人工再确认」): once a workflow is decided and started, the platform
asks no one again — the plan starts without 「计划待确认」 and what the Evaluator leaves to a person is noted as a warning,
not 「验收待确认」. A person's explicit confirm (on submit or as a rule) still waits, and a platform can opt back in."""
import unittest

from octopus.config import Settings
from tests.test_evaluator import EvaluatorCase


class DefaultsTests(unittest.TestCase):
    def test_the_platform_asks_no_one_by_default(self):
        self.assertEqual((Settings.default_admission, Settings.default_acceptance), ("auto", "auto"))


class AcceptanceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.service.default_acceptance = "auto"   # the platform default (D68)
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_a_pending_item_is_a_warning_and_the_session_completes(self):
        rid, run = self.run_()
        self.assertEqual(run["status"], "completed", run.get("attention"))
        evaluator = next(t for t in run["tasks"] if (t["spec"].get("role") == "evaluator"))
        gates = {g["name"]: g for g in evaluator["attempts"][-1]["evidence"]["gates"]}
        self.assertEqual(gates["evaluated"]["status"], "warn")
        self.assertIn("错误密码提示", gates["evaluated"]["detail"])
        self.assertIn("自动通过", gates["evaluated"]["detail"])

    def test_a_platform_that_wants_a_person_still_waits(self):
        self.service.default_acceptance = "confirm"
        rid, run = self.run_()
        self.assertEqual(run["status"], "awaiting_acceptance")

    def test_an_explicit_confirm_still_waits_for_the_plan(self):
        rid = self.start(steps=[{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement",
                                 "acceptance": ["可用"]}], admission="confirm", recovery={"enabled": False, "max_cycles": 2})
        self.assertEqual(self.tick(rid)["status"], "awaiting_confirmation")


if __name__ == "__main__":
    unittest.main()
