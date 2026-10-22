"""D70 (owner 2026-10-22: 「计划完成后，不要找我确认……直接开始」): a Coding Agent has no plan to confirm — there is no
octopus_confirm, octopus_start has no admission, and the Skill does not tell it to wait for or ask about a plan."""
from pathlib import Path
import unittest

from octopus import mcp

SKILL = Path(__file__).resolve().parents[1] / "skills" / "kirocrew-on" / "SKILL.md"


class McpNoConfirmTests(unittest.TestCase):
    def test_there_is_no_plan_to_confirm(self):
        names = {t["name"] for t in mcp.TOOLS}
        self.assertNotIn("octopus_confirm", names)
        start = next(t for t in mcp.TOOLS if t["name"] == "octopus_start")
        self.assertNotIn("admission", start["inputSchema"]["properties"])
        with self.assertRaises(Exception):
            mcp.call(object(), "octopus_control", {"run_id": "run-1", "action": "confirm", "request_id": "r", "expected_version": 1})

    def test_the_compact_status_lists_tasks_as_ever(self):
        spec = {"name": "实现", "tool": "claude", "model": "auto", "acceptance": ["可运行"], "verify": ["npm test"]}
        session = {"id": "run-1", "status": "running", "tasks": [{"id": "code", "status": "pending", "spec": spec, "attempts": []}]}
        self.assertNotIn("verify", mcp.compact_status(session, [])["session"]["tasks"][0])

    def test_the_skill_does_not_ask_for_a_plan_confirmation(self):
        text = SKILL.read_text()
        for words in ("octopus_confirm", "awaiting_confirmation", "admission: \"confirm\""):
            self.assertNotIn(words, text)
        self.assertIn("规划完成后直接开始", text)


if __name__ == "__main__":
    unittest.main()
