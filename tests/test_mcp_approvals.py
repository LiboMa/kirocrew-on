"""D38 phase 2: MCP lists a Session's pending approvals and answers one; the tool tells the agent to ask the user."""
import unittest

from octopus import mcp

RID = "run-" + "a" * 32


class Client:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return {"approvals": [], "supported": True} if method == "GET" else {"ok": True}


class McpApprovalsTests(unittest.TestCase):
    def test_both_tools_are_offered_and_the_answer_requires_the_users_consent(self):
        tools = {t["name"]: t for t in mcp.TOOLS}
        self.assertTrue(tools["octopus_approvals"]["annotations"]["readOnlyHint"])
        self.assertIn("用户", tools["octopus_approve"]["description"])
        self.assertIn("同意", tools["octopus_approve"]["description"])
        self.assertTrue(tools["octopus_approve"]["annotations"]["destructiveHint"])   # clients confirm it (A6)
        self.assertIn("supported", tools["octopus_approvals"]["description"])
        schema = tools["octopus_approve"]["inputSchema"]
        self.assertEqual(set(schema["required"]), {"run_id", "origin", "request_id", "decision"})

    def test_the_calls_reach_the_platform(self):
        client = Client()
        mcp.call(client, "octopus_approvals", {"run_id": RID})
        mcp.call(client, "octopus_approve", {"run_id": RID, "origin": "native", "request_id": "7", "request_mid": "m",
                                             "decision": "approve"})
        self.assertEqual(client.calls, [("GET", f"/v1/runs/{RID}/approvals", None),
                                        ("POST", f"/v1/runs/{RID}/approvals/answer",
                                         {"origin": "native", "request_id": "7", "request_mid": "m", "decision": "approve"})])
