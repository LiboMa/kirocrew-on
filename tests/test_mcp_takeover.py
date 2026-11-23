"""D74 for Coding Agents: octopus_status says whether the user may take a Session over (and why not), who runs it and how
a takeover stands; octopus_takeover asks for one, as the console's button does."""
import unittest

from octopus import mcp

RID = "run-" + "c" * 32
SESSION = {"id": RID, "name": "登录", "status": "completed", "desired": "active", "lifecycle": "open", "version": 7, "error": None,
           "attention": None, "progress": {"accepted": 1, "total": 1}, "input": "实现登录", "team_id": None, "worker_id": "alice-b",
           "tasks": [], "executed_by": {"id": "u-bob", "username": "bob"},
           "takeover": {"state": "failed", "to": "bob-b", "from": "alice-b", "by": "u-bob", "by_name": "bob", "requested_at": 1,
                        "reason": "会话分支超过 100 MB，不能接手", "code": "takeover_too_large"},
           "permissions": {"operate": True, "delete": False, "native": False, "direct": False,
                           "direct_reason": "此会话在 alice 的执行边界上运行", "takeover": True, "takeover_reason": None}}


class Client:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == f"/v1/runs/{RID}/takeover":
            return {"id": RID, "version": 8, "takeover": {"state": "moving", "to": "bob-b", "from": "alice-b"}}
        return {f"/v1/runs/{RID}": SESSION, f"/v1/runs/{RID}/events": []}[path]


class TakeoverTests(unittest.TestCase):
    def test_the_status_says_who_may_take_it_over_and_how_it_stands(self):
        session = mcp.call(Client(), "octopus_status", {"run_id": RID})["session"]
        self.assertEqual(session["permissions"], {"direct": False, "direct_reason": "此会话在 alice 的执行边界上运行",
                                                  "takeover": True, "takeover_reason": None})
        self.assertEqual(session["executed_by"], {"id": "u-bob", "username": "bob"})
        self.assertEqual((session["takeover"]["state"], session["takeover"]["reason"]), ("failed", "会话分支超过 100 MB，不能接手"))

    def test_a_takeover_is_asked_for_with_the_version_the_user_saw(self):
        client = Client()
        answer = mcp.call(client, "octopus_takeover", {"run_id": RID, "request_id": "takeover-mcp-1", "expected_version": 7})
        self.assertEqual(answer["takeover"]["state"], "moving")
        self.assertEqual(client.calls[-1], ("POST", f"/v1/runs/{RID}/takeover", {"request_id": "takeover-mcp-1", "expected_version": 7}))

    def test_the_tool_says_what_it_does_to_whom(self):
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_takeover")
        self.assertEqual(tool["inputSchema"]["required"], ["run_id", "request_id", "expected_version"])
        self.assertIn("以用户自己的身份", tool["description"])
        self.assertIn("先征得用户同意", tool["description"])


if __name__ == "__main__":
    unittest.main()
