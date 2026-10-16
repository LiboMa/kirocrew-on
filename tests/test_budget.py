"""D42: a Session's automatic recovery budget — the Coordinator's rounds, automatic retries and time — counted from the
Session's start or the user's latest continue / requested diagnosis; what ran out is said."""
import json
import time
import unittest

from octopus import recovery
from octopus.security import Error
from tests.test_replan import ReplanCase

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["可运行"]},
         {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]}]
CANCELLED = {"done": True, "result": "", "error": "cancelled (stop_reason=cancelled): turn cancelled by the runtime"}
RETRY = {"action": "retry", "target_task_id": "review", "reason": "审查未通过", "instructions": "修复后复审"}
WAIT = {"action": "wait_for_user", "reason": "等待", "instructions": "检查"}


class BudgetCase(ReplanCase):
    def setUp(self):
        super().setUp()
        self.cancelled, self.failing = set(), set()
        self.decisions = [RETRY] * 2 + [WAIT] * 4

    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        record = self.bodies.get(body.get("attempt_id"), {})
        if path == "/inspect" and not record.get("coordination"):
            spec = record["spec"]
            number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == spec["id"])
            if (spec["id"], number) in self.cancelled:
                result["native"] = dict(CANCELLED)
        return result

    def begin(self, **policy):
        return self.start(STEPS, recovery={"enabled": True, "max_cycles": 2, **policy})

    def run_row(self, rid):
        with self.db.read() as db:
            return db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()

    def budget(self, rid):
        with self.db.read() as db:
            return recovery.budget(db, db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())

    def attempts(self, run, task):
        return sorted({t["id"]: t for t in run["tasks"]}[task]["attempts"], key=lambda a: a["number"])


class BudgetTests(BudgetCase):
    def test_the_budget_settings_are_checked_and_defaults_not_stored(self):
        self.assertEqual(recovery.policy({"max_retries": 2})["max_retries"], 2)
        self.assertEqual(recovery.policy({"budget_minutes": 60})["budget_minutes"], 60)
        self.assertEqual(recovery.policy({"max_retries": 4, "budget_minutes": 720}), {"enabled": True, "max_cycles": 2})
        self.assertEqual(recovery.policy({"max_retries": 0})["max_retries"], 0)
        for bad in ({"max_retries": -1}, {"max_retries": 21}, {"max_retries": "2"}, {"max_retries": True},
                    {"budget_minutes": 10}, {"budget_minutes": 5000}):
            with self.subTest(bad), self.assertRaises(Error):
                recovery.policy(bad)

    def test_an_existing_session_counts_from_its_creation(self):
        rid = self.begin()
        budget = self.budget(rid)
        self.assertEqual(budget["since"], self.run_row(rid)["created"])
        self.assertEqual((budget["cycles"], budget["retries"], budget["minutes"]),
                         ({"used": 0, "limit": 2}, {"used": 0, "limit": 4}, {"used": 0, "limit": 720}))
        self.assertIsNone(budget["exhausted"])

    def test_a_continue_gives_the_coordinator_new_rounds(self):
        self.failing = {("review", 1), ("review", 2), ("review", 3)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(run["recovery"]["history"]), 1)
        self.assertEqual(self.budget(rid)["exhausted"], "cycles")
        self.service.operate(self.actor, rid, "continue", {"request_id": "budget-continue-1", "expected_version": run["version"],
                                                           "task_id": "review"})
        run = self.tick(rid, 40)
        self.assertEqual(len(run["recovery"]["history"]), 2)   # diagnosed again after the user's continue
        self.assertEqual(self.budget(rid)["cycles"], {"used": 1, "limit": 1})

    def test_a_requested_diagnosis_is_not_counted_and_resets_the_window(self):
        self.failing = {("review", 1), ("review", 2), ("review", 3)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(self.budget(rid)["exhausted"], "cycles")
        self.service.operate(self.actor, rid, "recover", {"request_id": "budget-recover-1", "expected_version": run["version"]})
        run = self.tick(rid, 40)
        history = run["recovery"]["history"]
        self.assertEqual([bool(r["context"].get("manual")) for r in history], [False, True, False])
        self.assertEqual(self.budget(rid)["cycles"], {"used": 1, "limit": 1})

    def test_the_retry_budget_spans_the_session(self):
        self.cancelled = {("code", 1), ("review", 1)}
        self.decisions = [RETRY, WAIT]
        rid = self.begin(max_retries=1)
        run = self.tick(rid, 40)
        code, review = self.attempts(run, "code"), self.attempts(run, "review")
        self.assertEqual(code[1]["evidence"].get("transient_retry_of"), code[0]["id"])   # the first one is retried
        self.assertEqual(len(run["recovery"]["history"]), 1)                              # the second goes to diagnosis
        self.assertFalse(review[1]["evidence"].get("transient_retry_of"))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.budget(rid)["retries"], {"used": 1, "limit": 1})

    def test_past_the_time_budget_nothing_automatic_starts(self):
        self.cancelled = {("code", 1)}
        rid = self.begin(budget_minutes=30)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(self.attempts(run, "code")), 1)
        self.assertEqual(run["recovery"]["history"], [])
        self.assertEqual(self.budget(rid)["exhausted"], "minutes")

    def test_a_continue_restarts_the_clock(self):
        rid = self.begin(budget_minutes=30)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "completed")
        before = time.time()
        self.service.operate(self.actor, rid, "continue", {"request_id": "budget-continue-2", "expected_version": run["version"],
                                                           "task_id": "review"})
        self.assertGreaterEqual(json.loads(self.run_row(rid)["snapshot"])["budget_since"], before)
        self.assertEqual(self.budget(rid)["minutes"]["used"], 0)


class BudgetVisibleTests(BudgetCase):
    def test_the_summary_and_the_attention_say_the_rounds_ran_out(self):
        self.failing = {("review", 1), ("review", 2)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(run["recovery"]["budget"]["exhausted"], "cycles")
        self.assertEqual(run["recovery"]["budget"]["cycles"], {"used": 1, "limit": 1})
        self.assertTrue(run["attention"]["action"].endswith(
            "自动纠错已达到 1 轮上限（自会话开始）；续作会重新计算预算，也可在续作时调大。"), run["attention"]["action"])

    def test_the_attention_says_the_retries_ran_out(self):
        self.cancelled, self.decisions = {("code", 1)}, [WAIT]
        rid = self.begin(max_retries=0)
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertIn("自动重试已达到 0 次上限（自会话开始）", run["attention"]["action"])

    def test_the_attention_says_the_time_ran_out(self):
        self.cancelled = {("code", 1)}
        rid = self.begin(budget_minutes=30)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertIn("自动纠错时长已超过 30 分钟（自会话开始）", run["attention"]["action"])

    def test_the_line_says_where_the_budget_counts_from(self):
        value = {"reset": False, "cycles": {"used": 1, "limit": 2}, "retries": {"used": 0, "limit": 4},
                 "minutes": {"used": 35, "limit": 720}, "exhausted": None}
        self.assertEqual(recovery.budget_line(value), "预算（自会话开始）：纠错 1/2 轮 · 自动重试 0/4 次 · 35/720 分钟")
        value.update(reset=True, exhausted="cycles", cycles={"used": 2, "limit": 2})
        self.assertEqual(recovery.budget_line(value),
                         "预算（自上次续作 / 请求诊断起）：纠错 2/2 轮（已用完） · 自动重试 0/4 次 · 35/720 分钟")

    def test_mcp_and_the_report_show_the_budget(self):
        from octopus import mcp, report
        self.failing = {("review", 1), ("review", 2)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(mcp.compact_status(run, [])["session"]["recovery"]["budget"], run["recovery"]["budget"])
        line = recovery.budget_line(run["recovery"]["budget"])
        self.assertIn(line, report.markdown(run))
        self.assertIn(line, report.html(run))


if __name__ == "__main__":
    unittest.main()
