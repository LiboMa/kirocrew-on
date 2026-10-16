"""D46 P1: what the Agents are told is consistent — one name and one set of rules for the planner, no default tool chain,
no stale instructions, a diagnosis and an observer prompt of their own, environment facts clipped."""
import unittest

from composition.templates import agent_prompt
from octopus import planning, prompts
from octopus.designs import instructions as design_instructions
from octopus.domain import steps

DECISION = {"reason": "计划缺少测试", "instructions": "补测试"}


def first():
    return planning.plan_step()["prompt"]


def again():
    return planning.replan_step("replan-1", [], "审查未通过", DECISION)["prompt"]


class PlannerPromptTests(unittest.TestCase):
    def test_each_prompt_states_its_own_task_count(self):
        self.assertNotIn("1–8 个顺序任务", prompts.intent_prompt("{}", "需求", "/w"))
        self.assertIn("1–7 个顺序任务", first())
        self.assertIn("1–6 个顺序任务", again())
        self.assertIn("1–8 个顺序任务", design_instructions("需求", {"steps": []}, "/w", {}))

    def test_the_planner_has_one_name(self):
        for prompt in (first(), again()):
            self.assertIn("KiroCrew 规划者", prompt)
            self.assertNotIn("工作流设计者", prompt)
        self.assertIn("KiroCrew 规划者（工作流设计）", design_instructions("需求", {"steps": []}, "/w", {}))

    def test_no_default_tool_chain_and_reviews_stay_independent(self):
        for prompt in (first(), again()):
            self.assertNotIn("OpenCode 交付", prompt)
            self.assertIn("与被审查代码不同的工具", prompt)

    def test_the_model_rule_makes_the_requested_exception(self):
        self.assertIn("需求明确指定的除外", prompts.intent_prompt("{}", "需求", "/w"))

    def test_the_source_is_said_to_be_prepared(self):
        self.assertNotIn("源码准备由主协调者负责", first())
        self.assertIn("源码已由平台准备", first())

    def test_both_plans_carry_the_same_rules(self):
        for words in ("deliverables", ".gitignore", ".octopus/preview.json", "验收项取自 summary", "选择依据", "verify 不安装依赖"):
            for prompt in (first(), again()):
                self.assertIn(words, prompt, words)

    def test_the_replan_names_the_sections_as_they_are(self):
        self.assertIn("「需求与验收标准（KiroCrew 规划补全）」", again())
        self.assertNotIn("「需求补全」", again())

    def test_the_first_plan_fits_with_many_models(self):
        models = {"claude": ["model-%03d" % i for i in range(200)], "codex": ["auto"] + ["c%03d" % i for i in range(200)]}
        tools = {t: {"available": True} for t in ("kiro", "claude", "codex")}
        steps([planning.plan_step(models, tools, ["很长的限制说明" * 50] * 10)])   # the Worker refuses a task prompt over 8000


class SharedPromptTests(unittest.TestCase):
    def test_agents_do_not_push_or_open_pull_requests_themselves(self):
        self.assertNotIn("授权已具备时直接完成", prompts.COMMON)
        self.assertIn("不自行推送分支或创建 PR / MR", prompts.COMMON)

    def test_the_executor_raises_more_work_through_its_outcome(self):
        self.assertNotIn("主协调者", prompts.WORKER)
        self.assertIn("outcome.json", prompts.WORKER)

    def test_the_diagnosis_agent_is_not_told_to_implement(self):
        text = agent_prompt("poc-diagnose")
        self.assertNotIn("研发执行者", text)
        self.assertIn("decision.json", text)
        self.assertIn("不修改源码", text)

    def test_the_session_observer_does_not_dispatch(self):
        text = agent_prompt("poc-coordinator")
        self.assertNotIn("spawn_run", text)
        self.assertIn("不派发", text)

    def test_the_tool_agents_keep_the_executor_prompt(self):
        for name in ("poc-kiro", "poc-claude", "poc-codex"):
            self.assertIn("研发执行者", agent_prompt(name))


class EnvelopeTests(unittest.TestCase):
    def envelope(self, kind=None, stage=None, requirement=None, **contract):
        from octopus.worker import envelope
        return envelope("实现登录并验证", requirement, {**({"kind": kind} if kind else {}), **contract}, "完成本任务", [],
                        "/w/project", "/w/attempt/handoff.md", stage=stage)

    def test_planning_and_diagnosis_are_not_told_to_develop(self):
        for stage in ("planning", "design", "coordination"):
            text = self.envelope(stage=stage)
            self.assertNotIn("依据需求修改源码", text, stage)
            self.assertNotIn("依据需求进行开发", text, stage)
        self.assertIn("只做分析与规划，不修改业务代码", self.envelope(stage="planning"))
        self.assertIn("只诊断，不修改源码", self.envelope(stage="coordination"))

    def test_each_kind_is_told_what_it_delivers(self):
        self.assertIn("本任务须在源码目录中产生改动", self.envelope("implement"))
        self.assertIn("本任务须在源码目录中产生改动", self.envelope("deliver"))
        self.assertIn("只读审查，不修改代码", self.envelope("review"))
        self.assertIn("只写测试与测试报告，不修改实现", self.envelope("test"))

    def test_the_handoff_contents_are_in_the_instructions(self):
        for kind in ("implement", "review", None):
            self.assertIn("交接文件写：源码基线、改动文件、执行的命令与退出码", self.envelope(kind))

    def test_checks_say_whether_the_platform_runs_them(self):
        self.assertIn("检查命令（任务结束后平台运行，须通过）", self.envelope("implement", verify=["npm test"]))
        self.assertIn("建议的检查命令（平台不运行）", self.envelope("review", verify=["npm test"]))

    def test_a_delivery_task_gets_the_preview_format(self):
        text = self.envelope("deliver")
        for words in (".octopus/preview.json", '"$PORT"', "1–4 个进程", "ready"):
            self.assertIn(words, text)
        self.assertNotIn(".octopus/preview.json", self.envelope("implement"))

    def test_an_outdated_requirement_says_so(self):
        text = self.envelope("implement", requirement={"intent": "登录", "summary": "需求", "outdated": True})
        self.assertIn("用户已在续作中修改需求，以「本次研发任务」为准", text)

    def test_an_old_platform_dispatch_still_reads_well(self):
        text = self.envelope()   # no kind, no stage: as before
        self.assertIn("依据需求进行开发和真实验证", text)
        self.assertIn("完成本任务", text)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        from tests.test_confirmation import ConfirmationCase
        class Case(ConfirmationCase):
            def runTest(self):
                pass
        self.case = Case()
        self.case.setUp()
        self.addCleanup(self.case.tmp.cleanup)

    def bodies(self, task):
        return [b for b in self.case.bodies.values() if not b.get("coordination") and b["spec"]["id"] == task]

    def test_the_planning_task_is_dispatched_as_planning(self):
        case = self.case
        rid = case.start(steps=None, plan="agent")
        case.tick(rid)
        self.assertEqual(self.bodies("plan")[0].get("stage"), "planning")
        self.assertNotIn("stage", self.bodies("code")[0])

    def test_a_requirement_changed_by_a_continue_is_marked_outdated(self):
        case = self.case
        rid = case.start(steps=None, plan="agent")
        run = case.tick(rid)
        self.assertFalse(self.bodies("code")[0]["requirement"].get("outdated"))
        case.service.operate(case.actor, rid, "continue", {"request_id": "p1-continue-1", "expected_version": run["version"],
                                                           "task_id": "code", "input": "实现并验证，另外支持英文界面"})
        case.tick(rid)
        self.assertTrue(self.bodies("code")[-1]["requirement"].get("outdated"))


class RepairTests(unittest.TestCase):
    def setUp(self):
        from tests.test_replan import ReplanCase
        class Case(ReplanCase):
            def runTest(self):
                pass
        self.case = Case()
        self.case.setUp()
        self.addCleanup(self.case.tmp.cleanup)
        self.case.failing = {("review", 1)}
        self.case.decisions = [{"action": "rework", "target_task_id": "code", "reason": "空输入未处理", "instructions": "补上空输入校验"}]

    def test_only_the_reworked_task_is_told_to_repair(self):
        rid = self.case.start(recovery={"enabled": True, "max_cycles": 2})
        run = self.case.tick(rid, 60)
        self.assertEqual(run["status"], "completed")
        code, review, deliver = (self.case.dispatched(t) for t in ("code", "review", "deliver"))
        self.assertIn("实施修复", code[1]["repair"]["instruction"])
        self.assertTrue(review[1]["repair"]["recheck"])                     # the re-review checks the findings were fixed
        self.assertNotIn("实施修复", review[1]["repair"]["instruction"])
        self.assertNotIn("repair", deliver[0])                              # nothing to repair there

    def test_the_repair_carries_a_summary_not_the_whole_context(self):
        rid = self.case.start(recovery={"enabled": True, "max_cycles": 2})
        self.case.tick(rid, 60)
        failure = self.case.dispatched("code")[1]["repair"]["failure"]
        self.assertEqual(set(failure) - {"task", "error", "failure_type", "findings", "summary"}, set())
        self.assertEqual(failure["task"], "review")
        self.assertIn("缺少测试", failure["findings"])

    def test_the_coordinator_reads_its_context_from_the_file(self):
        from pathlib import Path
        from octopus.recovery import coordinator_instructions
        text = coordinator_instructions({"input": "UNIQUE-REQUIREMENT-TEXT", "environment": {}}, Path("/w/attempt"))
        self.assertNotIn("UNIQUE-REQUIREMENT-TEXT", text)
        self.assertIn("/w/attempt/context.json", text)


class TemplateTests(unittest.TestCase):
    def templates(self):
        import json
        from octopus import intent
        return [*intent.CORE, *json.loads(intent.LEGACY.read_text())]

    def test_built_in_templates_carry_a_contract(self):
        from octopus.domain import KINDS
        for template in self.templates():
            for step in template["steps"]:
                where = f"{template['id']}/{step['id']}"
                self.assertIn(step.get("kind"), KINDS, where)
                self.assertTrue(step.get("acceptance"), where)
                self.assertNotEqual(step["tool"], "opencode", where)   # disabled (K01)
                self.assertNotIn("candidate.diff", step["prompt"], where)   # the platform commits the candidate
                if step["kind"] == "review":
                    self.assertNotIn("报告 BLOCKED", step["prompt"], where)
                    self.assertIn("changes_requested", step["prompt"], where)
            steps(template["steps"])   # what a Session accepts

    def test_template_steps_do_not_repeat_the_goal(self):
        from octopus import intent
        prompt = intent.preset_steps(intent.CORE[0], "很长的目标" * 100)[0]["prompt"]
        self.assertNotIn("本次目标：", prompt)
        self.assertIn("当前步骤：1/", prompt)


if __name__ == "__main__":
    unittest.main()
