"""D44: an agent confirms a Session's plan only after showing it to the user; the Skill says how."""
from pathlib import Path
import unittest

from octopus import mcp

SKILL = Path(__file__).resolve().parents[1] / "skills" / "kirocrew-on" / "SKILL.md"


class McpConfirmTests(unittest.TestCase):
    def tool(self, name):
        return next(t for t in mcp.TOOLS if t["name"] == name)

    def test_confirm_routes_to_the_versioned_command(self):
        seen = []
        class Client:
            def request(self, method, path, body):
                seen.append((method, path, body))
                return {"status": "accepted"}
        args = {"run_id": "run-1", "request_id": "mcp-confirm-1", "expected_version": 3}
        mcp.call(Client(), "octopus_confirm", args)
        self.assertEqual(seen, [("POST", "/v1/runs/run-1/confirm", {"request_id": "mcp-confirm-1", "expected_version": 3})])
        self.assertIn("run_id", args)   # the caller's arguments are not changed

    def test_the_agent_is_told_to_ask_the_user_first(self):
        tool = self.tool("octopus_confirm")
        self.assertIn("得到用户明确同意", tool["description"])
        self.assertEqual(tool["inputSchema"]["required"], ["run_id", "request_id", "expected_version"])

    def test_a_session_can_be_started_to_wait_for_confirmation(self):
        schema = self.tool("octopus_start")["inputSchema"]["properties"]
        self.assertEqual(schema["admission"]["enum"], ["confirm", "auto"])

    def test_the_skill_says_how_to_confirm(self):
        text = SKILL.read_text()
        for words in ("awaiting_confirmation", "octopus_confirm", "admission"):
            self.assertIn(words, text)


if __name__ == "__main__":
    unittest.main()
