"""EC2 run-40a1…: a review failed and the Coordinator sent the fix to the task planned after it, three times; the platform
refused each time without telling it why. It is now told which targets it may choose, and why a decision was refused."""
import unittest

from octopus import planning
from octopus.recovery import coordinator_instructions
from tests.test_replan import ReplanCase

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["可运行"]},
         {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]},
         {"id": "fix", "name": "按审查修复", "tool": "claude", "prompt": "修复", "kind": "implement", "acceptance": ["已修复"]}]
FORWARD = {"action": "rework", "target_task_id": "fix", "reason": "审查发现缺陷", "instructions": "在 fix 中修复"}
WAIT = {"action": "wait_for_user", "reason": "等待", "instructions": "检查"}


class TargetCase(ReplanCase):
    def setUp(self):
        super().setUp()
        self.failing = {("review", 1)}
        self.decisions = [FORWARD, WAIT]

    def coordinations(self):
        return [b["coordination"] for b in self.bodies.values() if b.get("coordination")]


class TargetTests(TargetCase):
    def test_the_coordinator_is_given_the_targets_it_may_choose(self):
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 2})
        self.tick(rid, 40)
        self.assertEqual([t["id"] for t in self.coordinations()[0]["allowed_targets"]], ["code", "review"])

    def test_a_target_after_the_failed_task_is_refused_saying_which_are_allowed(self):
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 2})
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertIn("fix 排在失败任务 review 之后", run["error"])
        self.assertIn("code、review", run["error"])
        self.assertIn("rework", run["error"])

    def test_the_next_diagnosis_knows_why_the_last_decision_was_refused(self):
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 2})
        run = self.tick(rid, 40)
        self.service.operate(self.actor, rid, "recover", {"request_id": "targets-recover-1", "expected_version": run["version"]})
        self.tick(rid, 40)
        previous = self.coordinations()[1]["previous_decisions"]
        self.assertEqual(previous[0]["target_task_id"], "fix")
        self.assertEqual(previous[0]["status"], "blocked")
        self.assertIn("排在失败任务 review 之后", previous[0]["refused"])

    def test_a_wait_for_user_is_not_reported_as_refused(self):
        self.decisions = [WAIT, WAIT]
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 2})
        run = self.tick(rid, 40)
        self.service.operate(self.actor, rid, "recover", {"request_id": "targets-recover-2", "expected_version": run["version"]})
        self.tick(rid, 40)
        self.assertNotIn("refused", self.coordinations()[1]["previous_decisions"][0])


class PromptTests(unittest.TestCase):
    def test_the_coordinator_is_told_to_send_a_fix_upstream_and_not_to_repeat_a_refusal(self):
        text = coordinator_instructions({"environment": {}}, __import__("pathlib").Path("/w"))
        for words in ("allowed_targets", "refused", "排在失败任务之后的任务"):
            self.assertIn(words, text)

    def test_the_coordinator_is_told_which_gates_only_warn(self):   # D57 §4
        text = coordinator_instructions({"environment": {}}, __import__("pathlib").Path("/w"))
        for name in ("diff_present", "artifacts_locked", "tests_fail_on_baseline", "handoff_present", "outcome_valid"):
            self.assertIn(name, text[text.index("只记提示"):])
        self.assertIn("evidence.handoff_text", text)
        self.assertIn("升级前", text[text.index("locked_files"):text.index("locked_files") + 120])   # paths_add: old Sessions only

    def test_the_planner_does_not_plan_a_fix_task_after_a_review(self):
        for prompt in (planning.plan_step()["prompt"],
                       planning.replan_step("replan-1", [], "失败", {"reason": "r", "instructions": "i"})["prompt"]):
            self.assertIn("不要在审查之后另设", prompt)


if __name__ == "__main__":
    unittest.main()
