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

    def test_a_rejecting_review_whose_edits_could_not_be_put_back_still_blocks(self):
        """D57 whole-branch review: relax turned every fail into a warning, so a diverge review that asked for changes and
        whose own edits could not be undone (_put_back_review's review_bound fail) let those edits into the next candidate."""
        evidence = self.evidence("outcome_pass", "implementation")
        evidence["gates"].append({"name": "review_bound", "status": "fail", "detail": "撤回审查改动失败：工作区仍有审查者的改动"})
        before = repr(evidence)
        self.assertIsNone(phases.relax(evidence, self.PLAN[0], {}, "原因", self.PLAN))
        self.assertEqual(repr(evidence), before)

    def test_a_converge_task_and_a_platform_task_are_never_relaxed(self):
        self.assertIsNone(phases.relax(self.evidence("outcome_pass", "tests"), self.PLAN[1], {}, "原因", self.PLAN))
        plan = [spec("plan", "research"), spec("check", "research", role="evaluator")]
        snapshot = {"planning": {"mode": "agent"}}
        for task in plan:
            with self.subTest(task=task["id"]):
                self.assertIsNone(phases.relax(self.evidence("outcome_pass", "tests"), task, snapshot, "原因", plan))
        design = {"purpose": "workflow_design"}
        self.assertIsNone(phases.relax(self.evidence("outcome_pass", "tests"), self.PLAN[0], design, "原因", self.PLAN))


class RequiredChecksTests(unittest.TestCase):
    """D69 Task 2b (D57 session's review): a must-pass check that no task can run because the tasks are diverge is said, not
    a plan made invalid; one that a non-running task carries is said too."""
    REPLACED = [{"id": "task-2", "name": "实现", "required": ["python3 -m unittest -q"]}]

    def test_a_research_only_replan_keeps_going_and_says_why_the_check_does_not_run(self):
        from octopus.planning import _carry
        notes = []
        _carry(self.REPLACED, [spec("research", "research")], notes)
        self.assertIn("必须通过的检查没有可运行的任务（发散阶段不运行检查命令）", " ".join(notes))

    def test_a_runnable_task_without_room_is_still_invalid(self):
        from octopus.planning import _carry
        full = spec("code", "implement", verify=[f"check-{n}" for n in range(10)])
        with self.assertRaises(Error) as caught:
            _carry(self.REPLACED, [full], [])
        self.assertEqual(caught.exception.code, "plan_invalid")

    def test_a_planned_must_pass_check_that_will_not_run_is_said(self):
        from octopus.planning import _required_checks

        class Db:
            def execute(self, sql, params=()):
                class Row:
                    def fetchone(inner):
                        return {"input": "必须通过 python3 -m unittest"}
                return Row()
        steps = [spec("research", "research", verify=["python3 -m unittest"], verify_required=["python3 -m unittest"],
                      verify_basis={"python3 -m unittest": "必须通过 python3 -m unittest"})]
        notes = []
        _required_checks(Db(), "run-x", {}, steps, notes, False)
        self.assertIn("任务「research」的必过检查不会运行（发散阶段不运行检查命令）", " ".join(notes))


class PromptTests(unittest.TestCase):
    """D69 Task 3: the planner is told the phases in one line, and the kinds of general work (D65); a diverge task's
    Agent is told its conclusion does not hold up the Session."""
    def test_both_planning_prompts_name_the_phases_and_the_general_kinds(self):
        from octopus import planning
        for spec in (planning.plan_step(), planning.replan_step("replan-1", [], "失败", {"reason": "r", "instructions": "i"})):
            with self.subTest(task=spec["id"]):
                self.assertIn("phase：diverge", spec["prompt"])
                self.assertIn("research", spec["prompt"])
                self.assertIn("write", spec["prompt"])

    def test_a_diverge_task_is_told_what_its_phase_means(self):
        from octopus.worker import briefing
        self.assertIn("发散阶段", briefing({}, {"kind": "research"}))
        self.assertIn("发散阶段", briefing({}, {"kind": "implement", "phase": "diverge"}))
        self.assertNotIn("发散阶段", briefing({}, {"kind": "implement"}))


class EntryTests(unittest.TestCase):
    """D69 Task 4: an imported workflow may carry its phases, in either language; the API says what a phase is."""
    def test_an_imported_phase_in_chinese_or_english(self):
        from octopus.workflow_import import import_workflow
        content = ('{"name": "调研", "steps": [{"name": "调研", "tool": "kiro", "prompt": "调研", "阶段": "发散"},'
                   '{"name": "方案", "tool": "claude", "prompt": "方案", "phase": "design"},'
                   '{"name": "撰写", "tool": "codex", "prompt": "写", "phase": "later"}]}')
        result = import_workflow(content, "json")
        self.assertEqual([s.get("phase") for s in result["steps"]], ["diverge", "design", None])
        self.assertTrue(any("later" in note for note in result["notes"]))

    def test_the_openapi_names_the_phases_and_the_general_kinds(self):
        from octopus.schema import openapi
        text = str(openapi())
        self.assertIn("'diverge', 'design', 'converge'", text)
        self.assertIn("'research', 'write'", text)


if __name__ == "__main__":
    unittest.main()
