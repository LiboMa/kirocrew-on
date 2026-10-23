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
        if path == "/control":   # a stop: the native parent and its tasks have ended
            return {"status": "stopped", "lifecycle": "open"}
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


class RevisionTests(EvaluatorCase):
    def test_the_appended_evaluator_is_its_own_revision(self):
        rid, run = self.run_(steps=TEAM[:1])
        self.assertIn("evaluator", [t["id"] for t in run["tasks"]])
        added = [r for r in self.service.revisions(self.actor, rid) if r["kind"] == "platform"]
        self.assertEqual([r["summary"] for r in added], ["平台追加 Evaluator 验收"])
        self.service.operate(self.actor, rid, "continue", {"request_id": "revision-fixture-1", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补上错误密码提示的文案"})
        diff = self.service.revision_diff(self.actor, rid)   # the user's continue against the revision before it
        self.assertEqual([t["id"] for t in diff["tasks"] if t["status"] == "added"], [])

class AcceptanceTests(EvaluatorCase):
    """D71 (owner 2026-10-22, gates retrospective): what the Evaluator leaves to a person is a warning; no Session waits
    in 「验收待确认」 and there is nothing to accept."""
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_a_pending_item_is_a_warning_and_the_session_completes(self):
        rid, run = self.run_()
        self.assertEqual(run["status"], "completed")
        check = next(t for t in run["tasks"] if t["id"] == "check")
        evidence = check["attempts"][-1]["evidence"]
        self.assertTrue(evidence["evaluation"]["automatic"])
        gate = next(g for g in evidence["gates"] if g["name"] == "evaluated")
        self.assertEqual(gate["status"], "warn")
        self.assertIn("错误密码提示", gate["detail"])

    def test_a_session_left_waiting_for_acceptance_is_accepted_by_the_platform(self):   # an earlier version's wait
        rid, run = self.run_()
        check = next(t for t in run["tasks"] if t["id"] == "check")
        with self.db.transaction() as db:
            evidence = dict(check["attempts"][-1]["evidence"])
            evidence["evaluation"] = {"pending": evidence["evaluation"]["pending"]}
            db.execute("UPDATE attempts SET status='awaiting_acceptance', evidence=? WHERE id=?", (dumps(evidence), check["current_attempt"]))
            db.execute("UPDATE tasks SET status='awaiting_acceptance' WHERE run_id=? AND id='check'", (rid,))
            db.execute("UPDATE runs SET status='awaiting_acceptance' WHERE id=?", (rid,))
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        check = next(t for t in run["tasks"] if t["id"] == "check")
        self.assertTrue(check["attempts"][-1]["evidence"]["evaluation"]["automatic"])

    def test_there_is_nothing_to_accept(self):
        rid, run = self.run_()
        with self.assertRaises(Error):
            self.service.operate(self.actor, rid, "accept", {"request_id": "accept-fixture-1", "expected_version": run["version"]})


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


class SurfaceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_the_tools_show_what_was_left_to_a_person(self):   # D71: as a warning; there is no octopus_accept
        from octopus import mcp
        rid, run = self.run_()
        view = mcp.compact_status(run, [])["session"]
        check = next(t for t in view["tasks"] if t["id"] == "check")
        self.assertEqual(check["latest"]["pending"][0]["item"], "错误密码提示")
        self.assertNotIn("octopus_accept", {t["name"] for t in mcp.TOOLS})

    def test_the_report_judges_by_the_evaluator(self):
        from octopus import report
        rid, view = self.run_()
        md, page = report.markdown(view), report.html(view)
        self.assertIn("## 验收（以 Evaluator 为准）", md)
        self.assertIn("| 错误密码提示 | pending（自动通过，记为提示） |", md)
        self.assertIn("<h2>验收（以 Evaluator 为准）</h2>", page)


class FinalReviewTests(EvaluatorCase):
    def pending(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]


    def test_an_evaluator_without_items_judges_the_sessions_items(self):
        steps = [TEAM[0], {**TEAM[1], "acceptance": []}]
        run = self.run_(steps)[1]
        check = next(t for t in run["tasks"] if t["id"] == "check")
        self.assertEqual(check["spec"]["acceptance"], ["登录成功", "错误密码提示"])
        self.assertEqual(run["status"], "completed")

    def test_the_appended_evaluator_judges_items_a_continue_added(self):
        rid, run = self.run_([TEAM[0]])
        self.assertEqual(run["status"], "completed")
        self.service.operate(self.actor, rid, "continue", {"request_id": "refresh-1", "expected_version": run["version"],
                                                           "task_id": "code", "execution": {"acceptance": ["登录成功", "错误密码提示", "导出可用"]}})
        run = self.tick(rid)
        added = next(t for t in run["tasks"] if t["spec"].get("role") == "evaluator")
        self.assertIn("导出可用", added["spec"]["acceptance"])
        self.assertIn("导出可用", run["error"])   # judged: the fixture's Evaluator did not give a verdict for it



class VerdictUnitTests(unittest.TestCase):
    SPEC = {"acceptance": ["A 可用", "B 可用"]}

    def test_items_matched_by_text_keep_their_own_evidence(self):
        from octopus.evaluation import verdicts
        error, pending = verdicts(self.SPEC, {"acceptance": [   # B paraphrased and listed first: A keeps its own evidence
            {"item": "B 大致可用", "result": "pending", "evidence": "about B"}, {"item": "A 可用", "result": "pending", "evidence": "about A"}]})
        self.assertIsNone(error)
        self.assertEqual([(p["item"], p["evidence"]) for p in pending], [("A 可用", "about A"), ("B 可用", "about B")])

    def test_an_extra_or_repeated_failure_fails(self):
        from octopus.evaluation import verdicts
        base = [{"item": "A 可用", "result": "pass", "evidence": "a"}, {"item": "B 可用", "result": "pass", "evidence": "b"}]
        for extra in ({"item": "C 不可用", "result": "fail", "evidence": "c"}, {"item": "A 可用", "result": "fail", "evidence": "a2"}):
            with self.subTest(extra):
                self.assertIsNotNone(verdicts(self.SPEC, {"acceptance": [*base, extra]})[0])

    def test_the_evaluator_is_told_its_own_results(self):
        from pathlib import Path
        from octopus.recovery import quality_instructions
        text = quality_instructions(Path("/w"), {"acceptance": ["A"], "role": "evaluator", "kind": "review"})
        self.assertIn("pass、fail 或 pending", text)
        self.assertNotIn("n/a", text)
        self.assertIn("n/a", quality_instructions(Path("/w"), {"acceptance": ["A"], "kind": "implement"}))


class ContractTests(unittest.TestCase):
    def test_the_console_preview_waits_for_the_same_statuses_as_the_server(self):
        import re
        from pathlib import Path
        from octopus.domain import SETTLED
        text = (Path(__file__).resolve().parents[1] / "web/src/features/sessions/AppPreviewPanel.tsx").read_text()
        console = set(re.findall(r'"(\w+)"', re.search(r"const SETTLED = new Set\(\[(.*?)\]\)", text, re.S).group(1)))
        self.assertEqual(console, set(SETTLED))

    def test_the_openapi_has_no_accept(self):   # D71
        from octopus.schema import openapi
        self.assertNotIn("/v1/runs/{id}/accept", openapi()["paths"])


class BlockedPendingTests(EvaluatorCase):
    """EC2 run-346afc30…: the Evaluator gave a pending verdict and called the whole outcome blocked (input)."""
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案待产品负责人判断"}]]
        self.decision = "blocked"
        self.blocked_line = False

    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        if path == "/inspect" and (self.bodies[body["attempt_id"]].get("contract") or {}).get("role") == "evaluator":
            outcome = json.loads(result["outcome"])
            outcome["failure_type"] = "input"
            result["outcome"] = dumps(outcome)
            if self.blocked_line:
                result["native"]["result"] = "已核对。\nBLOCKED：第二项需要产品负责人判断"
        return result

    def test_a_blocked_outcome_with_only_pending_items_completes_with_a_warning(self):   # D71
        self.assertEqual(self.run_()[1]["status"], "completed")

    def test_even_with_a_blocked_line(self):
        self.blocked_line = True
        self.assertEqual(self.run_()[1]["status"], "completed")

    def test_a_blocked_outcome_without_pending_stays_blocked(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pass", "evidence": "b"}]]
        self.assertEqual(self.run_()[1]["status"], "blocked")

    def test_the_evaluator_is_told_what_decision_pending_takes(self):
        from pathlib import Path
        from octopus import roles
        from octopus.recovery import quality_instructions
        text = quality_instructions(Path("/w"), {"acceptance": ["A"], "role": "evaluator", "kind": "review"})
        self.assertIn("decision 写 pass", text)
        self.assertIn("decision 仍写 pass", roles.card("evaluator"))


class SecondBatchTests(EvaluatorCase):
    def test_a_missing_verdict_is_a_contract_failure(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"}]]
        run = self.run_()[1]
        check = next(t for t in run["tasks"] if t["id"] == "check")
        self.assertEqual(check["attempts"][-1]["evidence"]["failure_type"], "contract")

    def test_a_new_evaluator_does_not_take_an_id_used_before(self):
        from octopus import evaluation
        rid, run = self.run_([TEAM[0]])
        with self.db.transaction() as db:   # as a replan replacing the appended Evaluator: its Attempts stay as history
            db.execute("DELETE FROM tasks WHERE run_id=? AND id='evaluator'", (rid,))
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            config = json.loads(db.execute("SELECT config FROM workers WHERE id='worker'").fetchone()["config"])
            self.assertEqual(evaluation.append(db, rid, snapshot, config), "evaluator-2")

    def test_the_report_has_the_evaluator_section_only_where_the_gate_applied(self):
        from octopus import report
        rid, run = self.run_()
        self.assertIn("## 验收（以 Evaluator 为准）", report.markdown(run))
        older = ConfirmationCase.start(self, steps=TEAM, recovery={"enabled": False, "max_cycles": 2})
        with self.db.transaction() as db:   # a Session from before the gate (contract 3)
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (older,)).fetchone()["snapshot"])
            snapshot["execution_contract"] = 3
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), older))
        self.assertNotIn("以 Evaluator 为准", report.markdown(self.tick(older)))


if __name__ == "__main__":
    unittest.main()
