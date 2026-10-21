"""D55 for Coding Agents: the submissions they made (octopus_requests, compact) and the rules that admit them and the
preferences they are planned with (octopus_policies) — read only: a Coding Agent does not change its own admission."""
import json
import unittest

from octopus import mcp
from tests.test_confirmation import ConfirmationCase

ONE = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "deliver", "acceptance": ["完成"]}]
REQUEST = {"client": "claude", "input": "需" * 3000, "status": "awaiting_confirmation", "created": 1.0, "source": {"type": "new"},
           "explicit": {}, "policy": {"admission": {"mode": "confirm", "basis": "default"}}, "owner": {"id": "u"},
           "team_id": None, "sessions": [{"id": "run-1", "name": "会话", "status": "awaiting_confirmation", "updated": 2.0}]}


class Client:
    def __init__(self, answer):
        self.answer, self.paths = answer, []

    def request(self, method, path, body=None):
        self.paths.append((method, path))
        return self.answer


class McpRequestTests(unittest.TestCase):
    def test_the_requests_are_compact_and_say_when_there_is_more(self):
        client = Client([{**REQUEST, "id": f"request-{n}"} for n in range(3)])
        result = mcp.call(client, "octopus_requests", {"q": "登录", "client": "claude", "limit": 2})
        self.assertEqual(client.paths, [("GET", "/v1/requests?q=%E7%99%BB%E5%BD%95&client=claude&limit=3")])
        self.assertEqual([i["id"] for i in result["items"]], ["request-0", "request-1"])
        self.assertTrue(result["more"])
        self.assertEqual(set(result["items"][0]), {"id", "client", "input", "status", "created", "admission", "sessions"})
        self.assertEqual(len(result["items"][0]["input"]), 201)
        self.assertEqual(result["items"][0]["admission"], {"mode": "confirm", "basis": "default"})
        self.assertEqual(result["items"][0]["sessions"], [{"id": "run-1", "name": "会话", "status": "awaiting_confirmation"}])
        client = Client([])
        self.assertEqual(mcp.call(client, "octopus_requests", {}), {"items": [], "more": False})
        self.assertEqual(client.paths, [("GET", "/v1/requests?limit=21")])
        with self.assertRaises(ValueError):
            mcp.call(client, "octopus_requests", {"limit": 51})

    def test_the_policies_are_read_only(self):
        client = Client([{"id": "policy-1", "kind": "admission"}])
        self.assertEqual(mcp.call(client, "octopus_policies", {"team_id": "team-1"}), [{"id": "policy-1", "kind": "admission"}])
        self.assertEqual(mcp.call(client, "octopus_policies", {}), [{"id": "policy-1", "kind": "admission"}])
        self.assertEqual(client.paths, [("GET", "/v1/policies?team_id=team-1"), ("GET", "/v1/policies")])
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_policies")
        self.assertEqual(set(tool["inputSchema"]["properties"]), {"team_id"})
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(next(t for t in mcp.TOOLS if t["name"] == "octopus_requests")["annotations"]["readOnlyHint"])
        self.assertEqual([t["name"] for t in mcp.TOOLS if t["name"].startswith("octopus_polic")], ["octopus_policies"])


class StatusTests(unittest.TestCase):
    def test_the_compact_status_says_why_it_waits_and_where_it_came_from(self):   # final review
        session = {"id": "run-1", "status": "awaiting_confirmation", "tasks": [], "admission": "confirm",
                   "admission_basis": {"mode": "confirm", "basis": "default"}, "request": {"id": "request-1", "client": "claude"}}
        view = mcp.compact_status(session, [])["session"]
        self.assertEqual(view["admission_basis"], {"mode": "confirm", "basis": "default"})
        self.assertEqual(view["admission"], "confirm")
        self.assertEqual(view["request"], {"id": "request-1", "client": "claude"})


class StartReceiptTests(ConfirmationCase):
    def test_the_receipt_names_its_request(self):
        body = {"request_id": "receipt-00000001", "input": "实现登录页", "source": {"type": "new"}, "worker_id": "worker", "steps": ONE}
        first = self.service.start(self.actor, dict(body))
        self.assertTrue(first["request_id"].startswith("request-"))
        self.assertEqual(self.service.start(self.actor, dict(body))["request_id"], first["request_id"])
        self.assertEqual(self.service.run(self.actor, first["id"])["request"]["id"], first["request_id"])
        self.assertLess(len(json.dumps(first)), 400)


if __name__ == "__main__":
    unittest.main()
