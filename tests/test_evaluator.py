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


class SurfaceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_the_tools_accept_and_show_what_waits(self):
        from octopus import mcp
        rid, run = self.run_()
        view = mcp.compact_status(run, [])["session"]
        check = next(t for t in view["tasks"] if t["id"] == "check")
        self.assertEqual(check["latest"]["pending"][0]["item"], "错误密码提示")
        seen = []
        class Client:
            def request(self, method, path, body):
                seen.append((method, path))
                return {}
        mcp.call(Client(), "octopus_accept", {"run_id": rid, "request_id": "accept-mcp-1", "expected_version": run["version"]})
        self.assertEqual(seen, [("POST", f"/v1/runs/{rid}/accept")])

    def test_the_report_judges_by_the_evaluator(self):
        from octopus import report
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "accept", {"request_id": "accept-report-1", "expected_version": run["version"]})
        view = self.tick(rid)
        md, page = report.markdown(view), report.html(view)
        self.assertIn("## 验收（以 Evaluator 为准）", md)
        self.assertIn("| 错误密码提示 | pending（owner 确认通过） |", md)
        self.assertIn("<h2>验收（以 Evaluator 为准）</h2>", page)


class FinalReviewTests(EvaluatorCase):
    def pending(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_a_team_member_who_is_not_the_creator_cannot_accept(self):
        from octopus.security import authenticate, create_user, issue_token
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-a','组',1)")
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-a',?,'owner')", (self.actor["id"],))
            db.execute("UPDATE workers SET owner_id=NULL, team_id='team-a'")
            member = create_user(db, "member", "Fixture-password-123!", False)
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-a',?,'member')", (member["id"],))
            other = authenticate(db, issue_token(db, member["id"])["token"])
        self.pending()
        rid, run = self.run_(team_id="team-a")
        with self.assertRaises(Error) as caught:
            self.service.operate(other, rid, "accept", {"request_id": "accept-member-1", "expected_version": run["version"]})
        self.assertEqual(caught.exception.code, "forbidden")

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

    def test_the_compact_status_drops_pending_once_accepted(self):
        from octopus import mcp
        self.pending()
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "accept", {"request_id": "accept-mcp-2", "expected_version": run["version"]})
        view = mcp.compact_status(self.tick(rid), [])["session"]
        latest = next(t for t in view["tasks"] if t["id"] == "check")["latest"]
        self.assertNotIn("pending", latest)
        self.assertEqual(latest["accepted_by"], "owner")


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

    def test_the_openapi_lists_accept(self):
        from octopus.schema import openapi
        self.assertIn("/v1/runs/{id}/accept", openapi()["paths"])


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

    def test_a_blocked_outcome_with_only_pending_items_waits_for_a_person(self):
        self.assertEqual(self.run_()[1]["status"], "awaiting_acceptance")

    def test_even_with_a_blocked_line(self):
        self.blocked_line = True
        self.assertEqual(self.run_()[1]["status"], "awaiting_acceptance")

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


class StoppedAcceptanceTests(EvaluatorCase):
    def test_a_session_waiting_for_acceptance_is_not_sent_to_diagnosis(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pending", "evidence": "b"}]]
        rid, run = self.run_()
        self.assertEqual(run["status"], "awaiting_acceptance")
        self.service.operate(self.actor, rid, "stop", {"request_id": "stop-accept-1", "expected_version": run["version"]})
        run = self.tick(rid)
        self.assertEqual(run["status"], "stopped")
        with self.assertRaises(Error) as caught:   # stopped, its Evaluator still waits for a person: nothing failed
            self.service.operate(self.actor, rid, "recover", {"request_id": "recover-accept-1", "expected_version": run["version"]})
        self.assertIn("验收", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
