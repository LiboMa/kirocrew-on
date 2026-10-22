"""D69 (owner 2026-10-22: 「按阶段把关」): a task's phase — diverge (发散), design (设计) or converge (收敛) — written by the
planner or inferred from its kind; the end of a design phase; which failures a phase lets through as warnings, and how."""
import unittest

from octopus import phases
from octopus.domain import steps
from octopus.security import Error


def spec(sid, kind=None, **extra):
    return {"id": sid, "name": sid, "tool": "claude", "prompt": "做", **({"kind": kind} if kind else {}), **extra}


class InferenceTests(unittest.TestCase):
    def test_a_kind_gives_the_phase_and_a_written_phase_wins(self):
        plan = [spec("r", "research"), spec("d", "design"), spec("i", "implement"), spec("w", "write"), spec("t", "test"),
                spec("x", "deliver"), spec("n"), spec("e", "design", phase="diverge")]
        self.assertEqual([phases.phase(s, plan) for s in plan],
                         ["diverge", "design", "converge", "converge", "converge", "converge", "converge", "diverge"])

    def test_a_review_takes_the_phase_of_what_it_reviews(self):
        plan = [spec("r", "research"), spec("d", "design"), spec("rv", "review", depends_on=["r", "d"]),
                spec("i", "implement"), spec("rv2", "review"), spec("rv3", "review", depends_on=["r"])]
        self.assertEqual(phases.phase(plan[2], plan), "design")      # the last non-review task it depends on
        self.assertEqual(phases.phase(plan[4], plan), "converge")    # no dependency: the nearest earlier non-review task
        self.assertEqual(phases.phase(plan[5], plan), "diverge")
        self.assertEqual(phases.phase(spec("first", "review"), [spec("first", "review")]), "converge")

    def test_the_end_of_a_design_phase(self):
        plan = [spec("d", "design"), spec("rv", "review"), spec("i", "implement")]
        self.assertFalse(phases.phase_end("d", plan))
        self.assertTrue(phases.phase_end("rv", plan))
        self.assertTrue(phases.phase_end("only", [spec("only", "design")]))
        self.assertFalse(phases.phase_end("i", plan))   # not a design task

    def test_what_a_phase_lets_through(self):
        plan = [spec("r", "research"), spec("d", "design"), spec("rv", "review"), spec("i", "implement")]
        self.assertEqual(phases.relaxed(plan[0], plan), "发散阶段不阻断：")
        self.assertEqual(phases.relaxed(plan[1], plan), "设计阶段中途不阻断（阶段末再把关）：")
        self.assertIsNone(phases.relaxed(plan[2], plan))   # the design phase's end
        self.assertIsNone(phases.relaxed(plan[3], plan))

    def test_the_contract_takes_a_phase(self):
        self.assertEqual(steps([spec("r", "research", phase="diverge")])[0]["phase"], "diverge")
        with self.assertRaises(Error) as caught:
            steps([spec("r", "research", phase="later")])
        self.assertEqual(caught.exception.status, 400)


class RelaxTests(unittest.TestCase):
    PLAN = [spec("r", "research"), spec("i", "implement")]

    def evidence(self, gate, failure_type, detail="原因"):
        return {"gate": gate, "failure_type": failure_type,
                "gates": [{"name": "native_completed", "status": "pass"}, {"name": gate, "status": "fail", "detail": detail}]}

    def test_a_relaxable_failure_becomes_a_warning(self):
        for gate, failure in (("native_completed", "native_error"), ("identity_match", "tool"), ("outcome_pass", "implementation"),
                              ("outcome_pass", "tests")):
            with self.subTest(gate=gate, failure=failure):
                evidence = self.evidence(gate, failure)
                self.assertEqual(phases.relax(evidence, self.PLAN[0], {}, "原因", self.PLAN), "发散阶段不阻断：")
                record = evidence["gates"][-1]
                self.assertEqual((record["status"], record["detail"], record["relaxed"]), ("warn", "发散阶段不阻断：原因", "fail"))
                self.assertNotIn("gate", evidence)
                self.assertNotIn("failure_type", evidence)
                self.assertEqual((evidence["phase"], evidence["phase_relaxed"], evidence["phase_relaxed_error"]),
                                 ("diverge", gate, "原因"))

    def test_what_is_never_relaxed(self):   # an allow-list: anything else, and anything added later, still blocks
        cases = [("native_completed", "native_stopped"), ("native_completed", "auth"), ("no_secrets", "gate"),
                 ("outcome_pass", "security"), ("outcome_pass", "input"), ("outcome_pass", "auth"),
                 ("outcome_pass", "environment"), ("outcome_pass", "model"), ("checks_pass", "gate"),
                 ("review_bound", "gate"), ("evaluated", "implementation"), ("something_new", "native_error")]
        for gate, failure in cases:
            with self.subTest(gate=gate, failure=failure):
                evidence = self.evidence(gate, failure)
                before = repr(evidence)
                self.assertIsNone(phases.relax(evidence, self.PLAN[0], {}, "原因", self.PLAN))
                self.assertEqual(repr(evidence), before)   # untouched

    def test_a_converge_task_and_a_platform_task_are_never_relaxed(self):
        self.assertIsNone(phases.relax(self.evidence("outcome_pass", "tests"), self.PLAN[1], {}, "原因", self.PLAN))
        plan = [spec("plan", "research"), spec("check", "research", role="evaluator")]
        snapshot = {"planning": {"mode": "agent"}}
        for task in plan:
            with self.subTest(task=task["id"]):
                self.assertIsNone(phases.relax(self.evidence("outcome_pass", "tests"), task, snapshot, "原因", plan))
        design = {"purpose": "workflow_design"}
        self.assertIsNone(phases.relax(self.evidence("outcome_pass", "tests"), self.PLAN[0], design, "原因", self.PLAN))


if __name__ == "__main__":
    unittest.main()
