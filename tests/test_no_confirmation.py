"""D68 (owner 2026-10-22: 「从任何工作流决定开始后，不需要人工再确认」): once a workflow is decided and started, the platform
asks no one again — the plan starts without 「计划待确认」 (D70) and what the Evaluator leaves to a person is noted as a
warning, never 「验收待确认」 (D71: the platform setting that brought it back is gone)."""
import unittest

from octopus.config import Settings
from tests.test_evaluator import EvaluatorCase


class DefaultsTests(unittest.TestCase):
    def test_there_is_no_setting_to_wait_for_a_person(self):
        self.assertFalse(hasattr(Settings, "default_acceptance") or hasattr(Settings, "default_admission"))

    def test_the_api_server_starts(self):   # octopus serve: b3d5353 passed a setting server() did not take
        import tempfile
        from octopus.db import Database
        from octopus.server import server
        with tempfile.TemporaryDirectory() as root:
            httpd = server(Database(root), 0)
            httpd.server_close()


class AcceptanceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
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


    def test_an_explicit_confirm_no_longer_waits_for_the_plan(self):   # D70: plans are never confirmed
        rid = self.start(steps=[{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement",
                                 "acceptance": ["可用"]}], admission="confirm", recovery={"enabled": False, "max_cycles": 2})
        self.assertNotEqual(self.tick(rid)["status"], "awaiting_confirmation")


if __name__ == "__main__":
    unittest.main()
