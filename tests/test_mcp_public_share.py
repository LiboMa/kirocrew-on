"""D75 for Coding Agents: octopus_public_share makes, lists, revokes and re-codes a Session's public links."""
import unittest

from octopus import mcp

RID = "run-" + "d" * 32


class Client:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return {"ok": True}


class PublicShareToolTests(unittest.TestCase):
    def test_each_action_reaches_its_route(self):
        client = Client()
        base = f"/v1/runs/{RID}/public-shares"
        mcp.call(client, "octopus_public_share", {"run_id": RID, "action": "create", "kind": "app", "mode": "open", "expires": "1d"})
        mcp.call(client, "octopus_public_share", {"run_id": RID, "action": "list"})
        mcp.call(client, "octopus_public_share", {"run_id": RID, "action": "new_code", "share_id": "pshare-1"})
        mcp.call(client, "octopus_public_share", {"run_id": RID, "action": "revoke", "share_id": "pshare-1"})
        self.assertEqual(client.calls, [("POST", base, {"kind": "app", "mode": "open", "expires": "1d"}), ("GET", base, None),
                                        ("POST", base + "/pshare-1/code", {}), ("DELETE", base + "/pshare-1", None)])

    def test_an_unknown_action_says_so(self):   # M9
        with self.assertRaises(ValueError) as caught:
            mcp.call(Client(), "octopus_public_share", {"run_id": RID, "action": "publish"})
        self.assertIn("action", str(caught.exception))

    def test_the_tool_says_it_is_for_an_explicit_request(self):
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_public_share")
        self.assertIn("只在用户明确要求公开分享时使用", tool["description"])


if __name__ == "__main__":
    unittest.main()
