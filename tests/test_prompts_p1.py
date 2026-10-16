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


if __name__ == "__main__":
    unittest.main()
