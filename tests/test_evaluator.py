"""D46 P3: a Session that changes code passes on its Evaluator's verdict on each acceptance item — a fail goes to rework,
a pending one waits for a person (「验收待确认」); the implementer's own pass is not the Session's."""
import json
import unittest

from octopus.db import dumps
from octopus.security import Error
from tests.test_confirmation import ConfirmationCase

TEAM = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "role": "sde",
         "acceptance": ["登录成功", "错误密码提示"], "verify": ["true"]},
        {"id": "check", "name": "评估", "tool": "codex", "prompt": "评估", "kind": "review", "role": "evaluator",
         "acceptance": ["登录成功", "错误密码提示"]}]
CAPABILITIES = {"role_cards": True, "tools": {"kiro": {"available": True}, "claude": {"available": True}, "codex": {"available": True}}}


class EvaluatorCase(ConfirmationCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:   # a Worker with role cards: the Evaluator gate applies
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": CAPABILITIES}),))
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pass", "evidence": "截图"}]]
        self.decision = "pass"

    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        if path == "/inspect" and (self.bodies[body["attempt_id"]].get("contract") or {}).get("role") == "evaluator":
            outcome = json.loads(result["outcome"])
            outcome.update(decision=self.decision, acceptance=self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0])
            result["outcome"] = dumps(outcome)
        return result

    def run_(self, steps=TEAM, **body):
        rid = self.start(steps=steps, recovery={"enabled": False, "max_cycles": 2}, **body)
        return rid, self.tick(rid)


class VerdictTests(EvaluatorCase):
    def test_every_item_passing_completes_the_session(self):
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")

    def test_a_failed_item_goes_to_rework(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "fail", "evidence": "没有提示"}]]
        _, run = self.run_()
        self.assertEqual(run["status"], "blocked")
        self.assertIn("错误密码提示", run["error"])

    def test_a_missing_item_or_evidence_is_refused(self):
        for verdicts in ([[{"item": "登录成功", "result": "pass", "evidence": "curl 200"}]],
                         [[{"item": "登录成功", "result": "pass", "evidence": ""}, {"item": "错误密码提示", "result": "pass", "evidence": "x"}]],
                         [[{"item": "登录成功", "result": "maybe", "evidence": "x"}, {"item": "错误密码提示", "result": "pass", "evidence": "x"}]]):
            with self.subTest(verdicts):
                self.verdicts = verdicts
                _, run = self.run_()
                self.assertEqual(run["status"], "blocked")

    def test_paraphrased_items_in_order_are_matched(self):
        self.verdicts = [[{"item": "能登录", "result": "pass", "evidence": "curl 200"},
                          {"item": "密码错误时有提示", "result": "pass", "evidence": "截图"}]]
        self.assertEqual(self.run_()[1]["status"], "completed")

    def test_a_changes_requested_evaluator_goes_to_recovery(self):
        self.decision = "changes_requested"
        _, run = self.run_()
        self.assertEqual(run["status"], "blocked")


class AcceptanceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def accept(self, rid, version, request="accept-fixture-1"):
        return self.service.operate(self.actor, rid, "accept", {"request_id": request, "expected_version": version})

    def test_a_pending_item_waits_for_a_person_then_completes(self):
        rid, run = self.run_()
        self.assertEqual(run["status"], "awaiting_acceptance")
        self.assertEqual(run["attention"]["title"], "验收待确认")
        self.assertIn("错误密码提示", run["attention"]["detail"])
        self.accept(rid, run["version"])
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        check = next(t for t in run["tasks"] if t["id"] == "check")
        self.assertEqual(check["attempts"][-1]["evidence"]["evaluation"]["accepted_by"]["username"], "owner")

    def test_only_a_waiting_session_can_be_accepted(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pass", "evidence": "b"}]]
        rid, run = self.run_()
        with self.assertRaises(Error):
            self.accept(rid, run["version"], "accept-fixture-2")

    def test_awaiting_acceptance_can_be_stopped_or_continued(self):
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "continue", {"request_id": "accept-fixture-3", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补上错误密码提示的文案"})
        self.assertNotEqual(self.service.run(self.actor, rid)["status"], "awaiting_acceptance")

    def test_a_rework_after_pending_reevaluates(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pending", "evidence": "b"}],
                         [{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pass", "evidence": "c"}]]
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "continue", {"request_id": "accept-fixture-4", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补文案"})
        self.assertEqual(self.tick(rid)["status"], "completed")

    def test_an_archived_teams_session_cannot_accept(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-a','组',1)")
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-a',?,'owner')", (self.actor["id"],))
            db.execute("UPDATE workers SET owner_id=NULL, team_id='team-a'")
        rid, run = self.run_(team_id="team-a")
        self.assertEqual(run["status"], "awaiting_acceptance")
        with self.db.transaction() as db:
            db.execute("UPDATE teams SET archived=1 WHERE id='team-a'")
        with self.assertRaises(Error) as caught:
            self.accept(rid, run["version"], "accept-fixture-5")
        self.assertEqual(caught.exception.code, "team_archived")


class ScopeTests(ConfirmationCase):
    def test_without_role_cards_nothing_changes(self):
        rid = self.start(steps=TEAM, recovery={"enabled": False, "max_cycles": 2})   # the fixture's Worker has no role_cards
        self.assertEqual(self.tick(rid)["status"], "completed")

    def test_new_sessions_use_contract_4(self):
        rid = self.start(steps=TEAM)
        with self.db.read() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
        self.assertEqual(snapshot["execution_contract"], 4)


CODE_ONLY = [TEAM[0]]


class AppendTests(EvaluatorCase):
    def test_a_session_without_an_evaluator_gets_one_on_another_tool(self):
        rid, run = self.run_(CODE_ONLY)
        added = next(t for t in run["tasks"] if t["spec"].get("role") == "evaluator")
        self.assertNotEqual(added["spec"]["tool"], "claude")
        self.assertEqual(added["spec"]["acceptance"], ["登录成功", "错误密码提示"])
        self.assertEqual(added["mapping"]["label"], "平台追加（改代码的会话须经 Evaluator）")
        self.assertEqual(run["status"], "completed")

    def test_a_planned_reviewer_tool_is_preferred(self):
        steps = [TEAM[0], {"id": "look", "name": "审查", "tool": "kiro", "prompt": "审查", "kind": "review", "acceptance": ["无阻断"]}]
        run = self.run_(steps)[1]
        self.assertEqual(next(t for t in run["tasks"] if t["spec"].get("role") == "evaluator")["spec"]["tool"], "kiro")

    def test_with_an_evaluator_none_is_added(self):
        run = self.run_()[1]
        self.assertEqual([t["id"] for t in run["tasks"]], ["code", "check"])

    def test_an_implementation_after_the_evaluator_gets_another(self):
        steps = [*TEAM, {"id": "more", "name": "再实现", "tool": "claude", "prompt": "再实现", "kind": "implement",
                         "acceptance": ["导出可用"], "verify": ["true"]}]
        run = self.run_(steps)[1]
        self.assertEqual(sum(t["spec"].get("role") == "evaluator" for t in run["tasks"]), 2)

    def test_a_session_without_code_changes_gets_none(self):
        steps = [{"id": "plan-doc", "name": "设计", "tool": "kiro", "prompt": "设计", "kind": "design", "acceptance": ["有规格"]}]
        self.assertEqual([t["id"] for t in self.run_(steps)[1]["tasks"]], ["plan-doc"])

    def test_without_another_tool_the_evaluator_is_added_and_noted(self):
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture",
                               "capabilities": {"role_cards": True, "tools": {"claude": {"available": True}}}}),))
        rid, run = self.run_(CODE_ONLY)
        added = next(t for t in run["tasks"] if t["spec"].get("role") == "evaluator")
        self.assertEqual(added["spec"]["tool"], "claude")
        with self.db.read() as db:
            titles = [str(json.loads(r["payload"]).get("changes") or "") for r in db.execute(
                "SELECT payload FROM events WHERE run_id=? AND kind='run.evaluator'", (rid,))]
        self.assertTrue(any("审查不独立" in t for t in titles), titles)


class PromptTests(unittest.TestCase):
    def test_the_planner_is_told_to_plan_an_evaluator(self):
        from octopus import planning
        self.assertIn("改代码的计划最后安排一个 evaluator 任务", planning.plan_step()["prompt"])

    def test_the_evaluator_card_answers_the_challenges(self):
        from octopus import roles
        card = roles.card("evaluator")
        self.assertIn("pending", card)
        self.assertIn("Challenge", card)
        self.assertLessEqual(len(card), 600)


if __name__ == "__main__":
    unittest.main()
