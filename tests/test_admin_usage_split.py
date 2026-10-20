"""D53 (owner 2026-10-20): administration and use are designed apart — an administrator and the person using the platform
may be two people. What a user reads (errors, hints, waiting reasons, MCP tool descriptions, the Skill) says why and
「请平台管理员……」; the operations command itself belongs to the administrator's pages and docs/OPERATIONS.md."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
OPS = re.compile(r"deploy\.sh|deploy/aws/|systemctl|sudo |kiro-login|boundary add|worker-host|重启 Worker")


class UserTextTests(unittest.TestCase):
    def test_no_platform_message_hands_a_user_an_operations_command(self):
        found = []
        for path in sorted((ROOT / "octopus").glob("*.py")):
            tree = ast.parse(path.read_text())
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            found += [f"{path.name}:{n.lineno}: {n.value[:80]}" for n in ast.walk(tree)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings and OPS.search(n.value)]
        self.assertEqual(found, [])

    def test_the_skill_hands_no_operations_command_either(self):
        self.assertIsNone(OPS.search((ROOT / "skills/kirocrew-on/SKILL.md").read_text()))

    def test_a_member_without_a_boundary_is_told_to_ask_the_administrator(self):
        from octopus.domain import DISABLED_HINT, HINT
        for hint in (HINT, DISABLED_HINT):
            self.assertIn("请平台管理员", hint)


if __name__ == "__main__":
    unittest.main()
