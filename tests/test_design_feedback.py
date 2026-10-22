"""D66 part 2 (owner 2026-10-22: 「推荐 Cycle 最多不超过 12 个，应该在提示词里有 INPUT/OUTPUT 的约束和提示，回馈的消息也应该是正确的信息，
而非让用户看不懂」): the recommendation prompt states its input and every rule its output is checked by; a recommendation
that breaks one goes back to KiroCrew once with the reason; what the user reads says what was wrong and what to do."""
import json
import unittest
from unittest.mock import patch

from octopus import designs
from octopus.db import dumps
from octopus.engine import Engine
from tests import test_mvp   # its methods only: importing the class would run its tests here too


def step(i, tool="claude", **extra):
    return {"id": f"task-{i}", "name": f"任务 {i}", "tool": tool, "model": "auto", "effort": "", "prompt": "做这一步", **extra}


def reply(steps, name="调研工作流"):
    return "说明\n```json\n" + json.dumps({"name": name, "steps": steps}, ensure_ascii=False) + "\n```"


class PromptTests(unittest.TestCase):
    def test_the_prompt_states_its_input_and_every_output_rule(self):
        text = designs.instructions("需求", {"input": "需求", "basis": []}, "/w", {"claude": ["auto"]},
                                    {"kiro": {"available": True}, "claude": {"available": True}, "codex": {"available": True}})
        for needle in ("## 输入", "## 输出约束", "1–12 个", "只输出一个", "小写字母开头", "kiro", "claude", "codex",
                       "审查任务的工具与被审查改动的作者不同", "80 个字符", "depends_on 只能指向前面的任务"):
            self.assertIn(needle, text, needle)

    def test_a_repair_says_what_was_wrong_and_is_not_part_of_the_basis(self):
        design = {"input": "需求", "basis": [], "repair": {"error": "KiroCrew 推荐了 13 个任务，超过平台上限 12 个"}}
        text = designs.instructions("需求", design, "/w", {}, {})
        self.assertIn("## 上一版未通过平台校验", text)
        self.assertIn("13 个任务", text)
        self.assertEqual(text.count("13 个任务"), 1)   # not repeated inside the basis JSON


class ExplainTests(unittest.TestCase):
    def reason(self, content):
        try:
            designs.parse_recommendation(content)
        except Exception as exc:
            return designs.explain(content, exc)
        return None

    def test_what_a_person_reads(self):
        self.assertEqual(self.reason(reply([step(i) for i in range(13)])), "KiroCrew 推荐了 13 个任务，超过平台上限 12 个")
        self.assertEqual(self.reason("我觉得可以分三步做。"), "KiroCrew 的回答里没有可解析的工作流 JSON")
        self.assertIn("gemini", self.reason(reply([step(0, tool="gemini")])))
        self.assertIn("Task-1", self.reason(reply([{**step(0), "id": "Task-1"}])))
        self.assertIn("审查不独立", self.reason(reply([step(0, kind="implement"), step(1, role="evaluator")])))


class EngineRepairTests(unittest.TestCase):
    setUp = test_mvp.MVPTests.setUp
    design_worker = test_mvp.MVPTests.design_worker
    fake_worker = test_mvp.MVPTests.fake_worker

    def run_design(self, answers):
        body = self.design_worker()
        with patch("octopus.designs.worker_request", return_value={"boundary": "fixture", "workflow_design": True}):
            created = designs.create(self.service, self.a, body)
        self.dispatched, answers = [], list(answers)

        def request(config, path, body, **kw):
            result = self.fake_worker(path, body)
            if path == "/inspect":
                result["native"]["result"] = answers[0] if len(answers) == 1 else answers.pop(0)
            return result
        engine = Engine(self.db, request)
        for _ in range(10):
            engine.tick()
        return designs.status(self.service, self.a, created["id"])

    def test_an_invalid_recommendation_goes_back_once_with_the_reason(self):
        status = self.run_design([reply([step(i) for i in range(13)]), reply(test_mvp.SPEC, "退款接口工作流")])
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["config"]["name"], "退款接口工作流")
        repairs = [d["design"].get("repair") for d in self.dispatched]
        self.assertEqual(repairs[0], None)
        self.assertEqual(repairs[1]["error"], "KiroCrew 推荐了 13 个任务，超过平台上限 12 个")

    def test_the_status_says_it_is_being_fixed(self):
        body = self.design_worker()
        with patch("octopus.designs.worker_request", return_value={"boundary": "fixture", "workflow_design": True}):
            created = designs.create(self.service, self.a, body)
        self.dispatched = []

        def request(config, path, body, **kw):
            result = self.fake_worker(path, body)
            if path == "/inspect":
                repaired = any(d["design"].get("repair") for d in self.dispatched if d["attempt_id"] == body["attempt_id"])
                result["native"] = ({"done": False} if repaired else
                                    {"done": True, "result": reply([step(i) for i in range(13)]), "error": ""})
            return result
        engine = Engine(self.db, request)
        for _ in range(6):
            engine.tick()
        self.assertTrue(designs.status(self.service, self.a, created["id"]).get("repairing"))

    def test_once_only_and_then_the_person_reads_why_and_what_to_do(self):
        status = self.run_design([reply([step(i) for i in range(13)])])
        self.assertEqual(len(self.dispatched), 2)   # the first and the one repair, no more
        self.assertEqual(status["status"], "invalid")
        self.assertEqual(status["error"], "KiroCrew 的推荐未通过平台校验：KiroCrew 推荐了 13 个任务，超过平台上限 12 个。"
                                          "已自动请 KiroCrew 修正过一次。可以重新推荐，或「导入并修改」后采用。")


if __name__ == "__main__":
    unittest.main()
