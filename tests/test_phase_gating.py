"""D69 (owner 2026-10-22: 「按阶段把关」) in the Engine: a diverge task's own failure, and a design task's before its phase
ends, is a warning — the task is accepted, its conclusion goes on, nothing is diagnosed; the design phase's last task and
converge tasks are judged as before; the security floor and a platform fault's automatic retry come first."""
from collections import defaultdict
import unittest

from octopus.domain import runs_checks
from tests.test_recovery import RecoveryCase


def task(sid, kind, **extra):
    return {"id": sid, "name": sid, "tool": "claude", "prompt": "做这一步", "kind": kind, **extra}


class PhaseGatingTests(RecoveryCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:   # the fixture's own Session would take each task's first round
            db.execute("UPDATE runs SET lifecycle='deleted' WHERE id=?", (self.run["id"],))
        self.rounds = defaultdict(int)
        self.native_errors = {}   # task id -> the error its first Attempt ends with
        original = self.request

        def request(config, path, body, **kwargs):
            result = original(config, path, body, **kwargs)
            record = self.bodies.get(body.get("attempt_id"), {})
            error = self.native_errors.get((record.get("spec") or {}).get("id"))
            if path == "/inspect" and error and not record.get("coordination") and record.get("fixture_round") == 1:
                result["native"] = {**result["native"], "error": error, "result": ""}
            return result
        self.request = request
        self.engine.request = request

    def session(self, steps):
        run = self.service.start(self.actor, {"request_id": f"phase-{len(self.calls)}-{id(steps)}", "input": "调研并写报告",
                                              "steps": steps, "source": {"type": "new"}, "worker_id": "worker"})
        return self.tick_run(run["id"])

    def tick_run(self, rid, count=30):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def attempt(self, run, sid, number=1):
        found = next(t for t in run["tasks"] if t["id"] == sid)
        return next(a for a in found["attempts"] if a["number"] == number)

    def test_a_research_task_that_says_changes_requested_is_a_warning(self):
        # the fixture's "review" task answers changes_requested on its first round; here it is a research task
        run = self.session([task("review", "research"), task("code", "write")])
        self.assertEqual(run["status"], "completed")
        first = self.attempt(run, "review")
        self.assertEqual(first["status"], "accepted")
        gate = next(g for g in first["evidence"]["gates"] if g["name"] == "outcome_pass")
        self.assertEqual((gate["status"], gate["relaxed"]), ("warn", "fail"))
        self.assertTrue(gate["detail"].startswith("发散阶段不阻断："))
        self.assertEqual((first["evidence"]["phase"], first["evidence"]["phase_relaxed"]), ("diverge", "outcome_pass"))
        self.assertNotIn("gate", first["evidence"])
        self.assertEqual(run["recovery"]["used"], 0)   # nothing was diagnosed
        self.assertEqual((self.rounds["review"], self.rounds["code"]), (1, 1))

    def test_a_diverge_task_whose_run_failed_says_so_and_the_next_goes_on(self):
        self.native_errors = {"research": "kiro_crew.acp.client.AcpError: model refused the request"}
        run = self.session([task("research", "research"), task("code", "write")])
        self.assertEqual(run["status"], "completed")
        first = self.attempt(run, "research")
        self.assertEqual((first["status"], first["evidence"]["phase_relaxed"]), ("accepted", "native_completed"))
        self.assertIn("任务未完成", first["evidence"]["handoff_text"])
        self.assertIn("model refused", first["evidence"]["phase_relaxed_error"])

    def test_a_platform_fault_is_retried_before_anything_is_let_through(self):
        self.native_errors = {"research": "never started: the session could not be created"}
        run = self.session([task("research", "research"), task("code", "write")])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.rounds["research"], 2)   # retried, then passed: not relaxed
        self.assertEqual(self.attempt(run, "research", 2)["status"], "accepted")

    def test_a_review_in_the_middle_of_the_design_phase_is_a_warning(self):
        run = self.session([task("design", "design"), task("review", "review"), task("design-2", "design")])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.attempt(run, "review")["evidence"]["phase"], "design")
        self.assertEqual(run["recovery"]["used"], 0)

    def test_the_review_that_ends_the_design_phase_is_judged_as_before(self):
        run = self.session([task("design", "design"), task("review", "review"), task("code", "implement")])
        first = self.attempt(run, "review")
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["evidence"]["gate"] if "gate" in first["evidence"] else
                         next(g["name"] for g in first["evidence"]["gates"] if g["status"] == "fail"), "outcome_pass")
        self.assertNotIn("phase_relaxed", first["evidence"])
        self.assertGreaterEqual(run["recovery"]["used"], 1)   # it went to the Coordinator

    def test_a_code_session_is_judged_as_before(self):   # implement → review: everything converges
        run = self.session([task("code", "implement"), task("review", "review")])
        first = self.attempt(run, "review")
        self.assertEqual(first["status"], "blocked")
        self.assertNotIn("phase_relaxed", first["evidence"])


class ChecksTests(unittest.TestCase):
    def test_a_diverge_task_runs_no_checks(self):
        self.assertFalse(runs_checks({"kind": "research", "verify": ["python3 -m unittest"]}))
        self.assertFalse(runs_checks({"kind": "implement", "phase": "diverge", "verify": ["true"]}))
        self.assertTrue(runs_checks({"kind": "implement", "verify": ["true"]}))
        self.assertTrue(runs_checks({"kind": "write", "verify": ["true"]}))


if __name__ == "__main__":
    unittest.main()
