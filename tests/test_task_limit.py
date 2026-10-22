"""D66 (owner 2026-10-22): a workflow or a Session takes 1–12 sequential tasks, not 1–8. EC2 run-40f0355982dd… and
run-443e1fd9…: KiroCrew recommended a 9-task research workflow, the platform refused it as invalid, and the console showed
no workflow to adopt."""
import json
import unittest

from octopus import planning, schema
from octopus.designs import instructions as design_instructions, parse_recommendation
from octopus.domain import steps
from octopus.security import Error


def step(i):
    return {"id": f"task-{i}", "name": f"任务 {i}", "tool": "claude", "model": "auto", "effort": "", "prompt": "做这一步"}


class LimitTests(unittest.TestCase):
    def test_twelve_tasks_and_no_more(self):
        self.assertEqual(len(steps([step(i) for i in range(12)])), 12)
        with self.assertRaises(Error) as caught:
            steps([step(i) for i in range(13)])
        self.assertIn("1–12", str(caught.exception))

    def test_the_nine_task_recommendation_from_ec2_is_a_workflow_now(self):
        ids = ["intake-checkout", "brainstorm", "deep-research", "competitive-analysis", "cloud-deployment-cost",
               "theme-design", "build-html", "codex-review", "delivery"]
        body = {"name": "深度 GitHub Repo 调研工作流", "steps": [{**step(0), "id": sid} for sid in ids]}
        text = "I'll start by reading the instructions file.\n```json\n" + json.dumps(body, ensure_ascii=False) + \
               "\n```\n\n[OPTIONS: 采用这个工作流 | 把云部署成本合并进调研任务改回 8 个]"
        self.assertEqual(len(parse_recommendation(text)["steps"]), 9)

    def test_the_prompts_and_the_api_say_twelve(self):
        self.assertIn("1–12 个顺序任务", design_instructions("需求", {"steps": []}, "/w", {}))
        self.assertIn("不超过 12 个", design_instructions("需求", {"steps": []}, "/w", {}))
        document = schema.openapi()
        self.assertEqual(document["components"]["schemas"]["Workflow"]["properties"]["steps"]["maxItems"], 12)

    def test_an_imported_workflow_takes_twelve(self):
        from octopus.workflow_import import import_workflow
        body = json.dumps({"steps": [{"name": f"t{i}", "tool": "claude", "prompt": "p"} for i in range(12)]})
        self.assertEqual(len(import_workflow(body)["steps"]), 12)

    def test_a_replan_of_twelve_tasks_still_fits(self):
        long = "很长的内容" * 3000
        plan = [{"id": "t%d" % i, "name": "任务" * 40, "kind": "implement"} for i in range(12)]
        kept = [{"id": f"task-{i}", "name": "很长的任务名" * 10, "verify": ["c" * 400] * 10} for i in range(11)]
        spec = planning.replan_step("replan-1", plan, long, {"reason": long, "instructions": long}, {"claude": ["m" * 60] * 60},
                                    {"kiro": {"available": True, "reason": long}}, [long] * 5,
                                    {"available": ["c" * 40] * 60, "missing": ["d" * 40] * 60}, kept)
        self.assertLessEqual(len(spec["prompt"]), 7800)


if __name__ == "__main__":
    unittest.main()
