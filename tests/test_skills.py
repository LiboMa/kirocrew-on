"""D50: the platform's coding skill pack — methods a task's Agent follows on every backend, adapted so they never tell an
unattended task to commit, open worktrees, write plan files or wait for a person."""
from pathlib import Path
import re
import unittest

from octopus import skills

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"test-driven-development", "systematic-debugging", "verification-before-completion", "receiving-code-review",
         "frontend-design"}


class PackTests(unittest.TestCase):
    def test_every_skill_has_a_head_and_its_license(self):
        self.assertEqual(set(skills.CATALOG), NAMES)
        for name in NAMES:
            with self.subTest(name):
                text = skills.path(name).read_text()
                head = dict(line.split(": ", 1) for line in re.match(r"---\n(.*?)\n---\n", text, re.S).group(1).splitlines())
                self.assertEqual(set(head), {"name", "description", "source", "license"})
                self.assertEqual(head["name"], name)
                self.assertIn(head["license"], {"MIT", "Apache-2.0"})
                licence = ROOT / "octopus/skills" / ("LICENSE-superpowers" if head["license"] == "MIT" else "LICENSE-frontend-design")
                self.assertTrue(licence.is_file())
                body = text.split("---\n", 2)[2]
                self.assertTrue(body.lstrip().startswith("> 改编说明"), name)   # what was changed, first
                self.assertGreater(len(body), 1500)

    def test_no_skill_tells_a_task_to_commit_open_worktrees_or_ask(self):
        forbidden = re.compile(r"git commit|git push|git worktree|using-git-worktrees|human partner|your partner|docs/superpowers|问用户|询问用户|ask the user",
                               re.I)
        for name in NAMES:
            with self.subTest(name):
                self.assertIsNone(forbidden.search(skills.path(name).read_text()), name)

    def test_the_catalogue_names_every_skill(self):
        text = skills.catalogue()
        for name in NAMES:
            self.assertIn(f"{name}：{skills.description(name)}", text)

    def test_hashes_follow_the_file(self):
        self.assertRegex(skills.sha256("frontend-design"), r"^[0-9a-f]{64}$")


class MethodSectionTests(unittest.TestCase):
    def test_the_method_section_puts_the_contract_first(self):
        folder = Path("/w/attempts/attempt-x")
        text = skills.method_section(["test-driven-development", "frontend-design"], folder)
        self.assertTrue(text.startswith("\n\n## 方法"))
        self.assertIn(str(folder / "skills/test-driven-development.md"), text)
        self.assertIn(str(folder / "skills/frontend-design.md"), text)
        self.assertIn("以本任务契约为准", text)
        self.assertEqual(skills.method_section([], folder), "")


if __name__ == "__main__":
    unittest.main()
