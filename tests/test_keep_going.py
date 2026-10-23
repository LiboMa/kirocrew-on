"""D71 (owner 2026-10-22, gates retrospective: 「协调者只在缺凭证 / 输入时停」): a Session stops for a person only when the
Coordinator says what only the person can supply — a repository credential or authorisation (missing: credentials) or
information about the requirement (missing: input). Any other wait, a decision the platform refuses, and a diagnosis
without a usable decision become a retry of the failed task with what the Coordinator said, within the recovery budget.
The D57 §6.2 hold (no diagnosis after two waits) and rediagnose are gone."""
import unittest

from octopus import recovery
from tests.test_recovery import RecoveryCase


class KeepGoingTests(RecoveryCase):
    def retried(self, result):
        latest = result["recovery"]["latest"]
        return latest["status"], latest["decision"]["action"], latest["decision"].get("target_task_id"), \
            latest["decision"].get("fallback_from")

    def test_a_wait_without_a_missing_credential_or_input_is_a_retry(self):
        self.decision = {"action": "wait_for_user", "reason": "不确定空输入应返回什么", "instructions": "请用户确认后再修"}
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.retried(result), ("applied", "retry", "review", "wait_for_user"))
        self.assertEqual(self.rounds, {"code": 1, "review": 2})
        repair = [b for b in self.bodies.values() if b.get("repair")][0]["repair"]
        self.assertIn("请用户确认后再修", repair["decision"]["instructions"])   # what it said reaches the retry

    def test_a_missing_credential_or_input_still_waits(self):
        for missing in ("credentials", "input"):
            with self.subTest(missing=missing):
                self.setUp()
                self.decision = {"action": "wait_for_user", "missing": missing, "reason": "目标 API 需要认证",
                                 "instructions": "请配置项目测试账号后继续"}
                result = self.tick()
                self.assertEqual(result["status"], "blocked")
                self.assertIn("测试账号", result["error"])
                self.assertEqual(self.rounds, {"code": 1, "review": 1})

    def test_a_refused_decision_is_a_retry_of_the_failed_task(self):
        self.decision = {"action": "rework", "target_task_id": "nowhere", "reason": "审查发现边界错误", "instructions": "修复边界"}
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        status, action, target, fallback = self.retried(result)
        self.assertEqual((status, action, target, fallback), ("applied", "retry", "review", "rework"))
        self.assertIn("nowhere", result["recovery"]["latest"]["decision"]["refused"])

    def test_a_diagnosis_without_a_usable_decision_is_a_retry(self):
        self.decision = {"action": "explode", "reason": "x", "instructions": "y"}
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.retried(result)[:3], ("applied", "retry", "review"))

    def test_the_budget_still_ends_it(self):   # kept by the owner: 2 cycles
        self.always_fail = True
        self.decision = {"action": "wait_for_user", "reason": "不确定", "instructions": "请确认"}
        result = self.tick(40)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["used"], 2)

    def test_there_is_no_hold_after_two_waits_and_no_rediagnose(self):
        self.assertFalse(hasattr(recovery, "held"))
        self.decision = {"action": "wait_for_user", "missing": "input", "reason": "缺少需求", "instructions": "请补充"}
        for number in range(3):   # each asked diagnosis runs, however many waits came before
            result = self.tick()
            self.assertEqual(result["status"], "blocked")
            self.service.operate(self.actor, result["id"], "recover", {"request_id": f"keep-going-{number:04d}",
                                                                      "expected_version": result["version"]})
        from octopus import mcp
        recover = next(t for t in mcp.TOOLS if t["name"] == "octopus_recover")
        self.assertNotIn("rediagnose", recover["inputSchema"]["properties"])

    def test_the_coordinator_is_told_when_it_may_wait(self):
        from pathlib import Path
        text = recovery.coordinator_instructions({}, Path("/w/diagnosis"))
        self.assertIn('"missing":"credentials"', text.replace(" ", ""))
        self.assertIn("只有缺少仓库凭证或授权、或缺少只有用户能提供的需求信息时才用 wait_for_user", text)


if __name__ == "__main__":
    unittest.main()
