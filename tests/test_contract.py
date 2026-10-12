"""D34 §6–§7: structured tasks (kind, deliverables, acceptance, verify, depends_on, paths), the requirement and task
contract passed to every task, and continuing with only the affected tasks."""
import unittest

from octopus.domain import steps
from octopus.security import Error
from octopus.workflow_import import import_workflow


class ContractTests(unittest.TestCase):
    base = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}

    def test_structured_fields_are_kept_only_when_given(self):
        self.assertNotIn("acceptance", steps([self.base])[0])
        step = steps([{**self.base, "kind": "implement", "acceptance": [" 登录成功 ", "登录成功"], "verify": ["npm test"],
                       "deliverables": ["src/login.ts"], "paths": ["src/**"]}])[0]
        self.assertEqual((step["acceptance"], step["verify"], step["kind"]), (["登录成功"], ["npm test"], "implement"))
        self.assertEqual((step["deliverables"], step["paths"]), (["src/login.ts"], ["src/**"]))

    def test_depends_on_must_point_to_earlier_tasks(self):
        review = {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查"}
        self.assertEqual(steps([self.base, {**review, "depends_on": ["code"]}])[1]["depends_on"], ["code"])
        for bad in (["review"], ["nope"]):
            with self.subTest(bad=bad), self.assertRaises(Error):
                steps([self.base, {**review, "depends_on": bad}])

    def test_bad_kinds_and_oversized_lists_are_refused(self):
        for extra in ({"kind": "deploy"}, {"acceptance": ["x"] * 21}, {"verify": "npm test"}, {"acceptance": ["x" * 501]},
                      {"verify": ["v"] * 11}, {"paths": [3]}):
            with self.subTest(extra=extra), self.assertRaises(Error):
                steps([{**self.base, **extra}])

    def test_imported_workflows_keep_the_fields_and_follow_renamed_ids(self):
        result = import_workflow('{"steps": [{"id": "Build", "name": "实现", "tool": "claude", "prompt": "实现", '
                                 '"kind": "implement", "acceptance": ["可运行"], "verify": ["npm test"]}, '
                                 '{"id": "Check", "name": "核对", "tool": "codex", "prompt": "核对", "kind": "test", '
                                 '"depends_on": ["Build"], "acceptance": ["测试通过"], "verify": ["npm test"]}]}')
        self.assertEqual([s["id"] for s in result["steps"]], ["build", "check"])
        self.assertEqual(result["steps"][1]["depends_on"], ["build"])
        self.assertFalse(any("忽略" in note for note in result["notes"]))
        later = import_workflow('[{"id": "a", "name": "甲", "tool": "claude", "prompt": "x", "depends_on": ["b"]}, '
                                '{"id": "b", "name": "乙", "tool": "codex", "prompt": "y", "depends_on": ["a", "甲", "nope"]}]')
        self.assertEqual([s.get("depends_on") for s in later["steps"]], [[], ["a"]])
        self.assertEqual(sum("忽略 depends_on" in note for note in later["notes"]), 2)


if __name__ == "__main__":
    unittest.main()
