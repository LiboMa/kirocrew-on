"""Access management phase 3 (spec §7): MCP tools by role — identity, environments, assigning a Worker, the change
journal and the administrator tools, listed by the token's scope."""
import io
import json
import unittest

from octopus import mcp
from octopus.client import ApiError


class Fake:
    url = "https://on.example"

    def __init__(self, responses=None):
        self.responses, self.calls = responses or {}, []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        value = self.responses.get((method, path.split("?")[0]), {"ok": True})
        if isinstance(value, Exception):
            raise value
        return value


ME = {"id": "u1", "username": "alice", "admin": 0, "scopes": ["read", "execute", "manage"], "client": "mcp"}
WORKERS = [
    {"id": "shared-team", "name": "共享", "enabled": True, "scope": "shared", "spaces": [{"team_id": "t1"}], "capabilities": {}},
    {"id": "shared-me", "name": "个人共享", "enabled": True, "scope": "shared", "spaces": [{"user_id": "u1"}],
     "capabilities": {"isolation": "trusted-local", "models": {"claude": ["auto"]}}},
    {"id": "team-own", "name": "w1", "enabled": True, "scope": "dedicated", "team_id": "t1", "owner_id": None, "capabilities": {}},
    {"id": "my-own", "name": "mine", "enabled": True, "scope": "dedicated", "team_id": None, "owner_id": "u1", "capabilities": {}},
    {"id": "spare", "name": "spare", "enabled": True, "scope": "spare", "team_id": None, "owner_id": "u1", "capabilities": {}},
    {"id": "off", "name": "off", "enabled": False, "scope": "dedicated", "team_id": None, "owner_id": "u1", "capabilities": {}},
]


class RoleToolTests(unittest.TestCase):
    def test_me_reports_identity_scopes_and_the_teams_one_belongs_to(self):
        client = Fake({("GET", "/v1/me"): {**ME, "admin": 1, "scopes": ME["scopes"] + ["admin"]},
                       ("GET", "/v1/teams"): [{"id": "t1", "name": "平台组", "role": "owner", "archived": 0, "members": [],
                                               "environment": {"worker_id": "w1", "name": "w1", "scope": "dedicated"}},
                                              {"id": "t2", "name": "别人的组", "role": None, "members": []}]})
        me = mcp.call(client, "octopus_me", {})
        self.assertEqual((me["username"], me["elevated"], me["client"]), ("alice", True, "mcp"))
        self.assertEqual(me["teams"], [{"id": "t1", "name": "平台组", "role": "owner", "archived": False,
                                        "environment": {"worker_id": "w1", "name": "w1", "scope": "dedicated"}}])

    def test_environments_are_the_ones_a_space_can_use(self):
        client = Fake({("GET", "/v1/me"): ME, ("GET", "/v1/workers"): WORKERS})
        personal = mcp.call(client, "octopus_environments", {})
        self.assertEqual([w["id"] for w in personal["environments"]], ["shared-me", "my-own"])
        self.assertEqual(personal["environments"][0]["models"], {"claude": ["auto"]})
        team = mcp.call(client, "octopus_environments", {"team_id": "t1"})
        self.assertEqual([w["id"] for w in team["environments"]], ["shared-team", "team-own"])

    def test_assign_worker_and_changes_call_the_api(self):
        client = Fake()
        mcp.call(client, "octopus_assign_worker", {"run_id": "r1", "request_id": "assign-0001", "expected_version": 3,
                                                  "worker_id": "w1"})
        mcp.call(client, "octopus_changes", {"run_id": "r1", "who": "user", "limit": 20})
        mcp.call(client, "octopus_changes", {"run_id": "r1"})
        self.assertEqual(client.calls[0], ("POST", "/v1/runs/r1/assign-worker",
                                           {"request_id": "assign-0001", "expected_version": 3, "worker_id": "w1"}))
        self.assertEqual([c[1] for c in client.calls[1:]], ["/v1/runs/r1/changes?who=user&limit=20", "/v1/runs/r1/changes?limit=50"])

    def test_start_leaves_choosing_the_worker_to_the_platform(self):
        client = Fake({("GET", "/v1/me"): ME, ("GET", "/v1/workers"): WORKERS})
        mcp.call(client, "octopus_start", {"request_id": "start-0001", "input": "需求", "source": {"type": "new"}, "team_id": "t1"})
        self.assertEqual([c[:2] for c in client.calls], [("POST", "/v1/runs")])
        self.assertNotIn("worker_id", client.calls[0][2])

    def test_admin_tools_are_listed_only_for_an_admin_token(self):
        names = lambda client: {t["name"] for t in mcp.tools_for(client)}
        plain = names(Fake({("GET", "/v1/me"): {**ME, "scopes": ["read", "execute"]}}))
        self.assertIn("octopus_me", plain)
        self.assertFalse(any(n.startswith("octopus_admin_") for n in plain))
        admin = names(Fake({("GET", "/v1/me"): {**ME, "admin": 1, "scopes": ME["scopes"] + ["admin"]}}))
        self.assertTrue({"octopus_admin_users", "octopus_admin_teams", "octopus_admin_environments"} <= admin)
        unknown = names(Fake({("GET", "/v1/me"): ApiError(503, "worker_unavailable", "x")}))
        self.assertIn("octopus_admin_users", unknown)

    def test_tools_list_over_stdio_follows_the_token(self):
        out = io.StringIO()
        mcp.serve(Fake({("GET", "/v1/me"): ME}), io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"), out)
        names = {t["name"] for t in json.loads(out.getvalue())["result"]["tools"]}
        self.assertIn("octopus_changes", names)
        self.assertNotIn("octopus_admin_teams", names)


if __name__ == "__main__":
    unittest.main()
