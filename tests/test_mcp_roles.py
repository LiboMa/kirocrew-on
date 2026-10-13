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

    def request(self, method, path, body=None, **options):
        self.calls.append((method, path, body))
        self.options = options
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

    def test_a_wrong_argument_type_is_an_error_not_a_crash(self):
        out = io.StringIO()
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "octopus_admin_users", "arguments": {"action": "deactivate", "username": 123}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
        users = [{"id": "u1", "username": "alice"}]
        mcp.serve(Fake({("GET", "/v1/admin/users"): users}), io.StringIO("".join(json.dumps(x) + "\n" for x in lines)), out)
        first, second = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual((first["result"]["isError"], first["result"]["structuredContent"]["code"]), (True, "client_error"))
        self.assertEqual((second["id"], second["result"]), (2, {}))

    def test_an_unknown_scope_is_not_remembered_and_the_probe_is_short(self):
        class Flaky(Fake):
            def request(self, method, path, body=None, **options):
                self.options = options
                if not self.calls:
                    self.calls.append(path)
                    raise ApiError(504, "http_error", "Gateway Timeout")
                return {**ME, "scopes": ["read", "execute"]}
        client = Flaky()
        out = io.StringIO()
        listing = "".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/list"}) + "\n" for i in (1, 2))
        mcp.serve(client, io.StringIO(listing), out)
        first, second = [{t["name"] for t in json.loads(line)["result"]["tools"]} for line in out.getvalue().splitlines()]
        self.assertIn("octopus_admin_users", first)          # unknown: everything
        self.assertNotIn("octopus_admin_users", second)      # asked again, now known
        self.assertEqual(client.options.get("timeout"), 5)

    def test_tools_list_over_stdio_follows_the_token(self):
        out = io.StringIO()
        mcp.serve(Fake({("GET", "/v1/me"): ME}), io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"), out)
        names = {t["name"] for t in json.loads(out.getvalue())["result"]["tools"]}
        self.assertIn("octopus_changes", names)
        self.assertNotIn("octopus_admin_teams", names)


class AppToolTests(unittest.TestCase):
    """octopus_app (D36 §5.3): start, look at, open and stop a Session's remote preview."""

    def test_each_action_calls_its_endpoint(self):
        client = Fake({("GET", "/v1/runs/r1/app"): {"status": "running", "log": [f"line {i}" for i in range(200)]},
                       ("POST", "/v1/runs/r1/app/open"): {"url": "https://preview.example/_kirocrew/open/t", "expires_in": 600}})
        mcp.call(client, "octopus_app", {"run_id": "r1", "action": "start"})
        mcp.call(client, "octopus_app", {"run_id": "r1", "action": "stop"})
        status = mcp.call(client, "octopus_app", {"run_id": "r1", "action": "status"})
        opened = mcp.call(client, "octopus_app", {"run_id": "r1", "action": "open"})
        self.assertEqual(client.calls, [("POST", "/v1/runs/r1/app", {"action": "start"}),
                                        ("POST", "/v1/runs/r1/app", {"action": "stop"}),
                                        ("GET", "/v1/runs/r1/app", None), ("POST", "/v1/runs/r1/app/open", {})])
        self.assertEqual(status["log"], [f"line {i}" for i in range(160, 200)])   # the last 40 lines
        self.assertEqual(opened["url"], "https://preview.example/_kirocrew/open/t")
        self.assertIn("一次", opened["note"])

    def test_an_unknown_action_is_refused_before_any_call(self):
        client = Fake()
        with self.assertRaises(ValueError):
            mcp.call(client, "octopus_app", {"run_id": "r1", "action": "restart"})
        self.assertEqual(client.calls, [])

    def test_ordinary_tokens_list_it(self):
        self.assertIn("octopus_app", {t["name"] for t in mcp.tools_for(Fake({("GET", "/v1/me"): ME}))})


USERS = [{"id": "u1", "username": "alice"}, {"id": "u2", "username": "bob"}]


class AdminToolTests(unittest.TestCase):
    def calls(self, name, args):
        client = Fake({("GET", "/v1/admin/users"): USERS})
        mcp.call(client, name, args)
        return [c for c in client.calls if c[:2] != ("GET", "/v1/admin/users") or args.get("action") == "list"]

    def test_user_actions(self):
        cases = {"list": [("GET", "/v1/admin/users", None)],
                 "deactivate": [("POST", "/v1/users/u2/active", {"active": False})],
                 "activate": [("POST", "/v1/users/u2/active", {"active": True})],
                 "promote": [("POST", "/v1/users/u2/admin", {"admin": True})],
                 "demote": [("POST", "/v1/users/u2/admin", {"admin": False})]}
        for action, expected in cases.items():
            with self.subTest(action=action):
                self.assertEqual(self.calls("octopus_admin_users", {"action": action, "username": "bob"}), expected)

    def test_an_unknown_username_is_not_found_and_nothing_is_written(self):
        client = Fake({("GET", "/v1/admin/users"): USERS})
        with self.assertRaises(ApiError) as caught:
            mcp.call(client, "octopus_admin_users", {"action": "deactivate", "username": "nobody"})
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "not_found"))
        self.assertEqual([c[0] for c in client.calls], ["GET"])

    def test_team_actions(self):
        team = "/v1/teams/t1"
        cases = {"list": ({}, [("GET", "/v1/teams", None)]),
                 "create": ({"name": "新组", "owner": "bob", "worker_id": "w1"},
                            [("POST", "/v1/teams", {"name": "新组", "owner": "bob", "worker_id": "w1"})]),
                 "rename": ({"team_id": "t1", "name": "二组"}, [("PATCH", team, {"name": "二组"})]),
                 "archive": ({"team_id": "t1"}, [("POST", team + "/archive", {"archived": True})]),
                 "restore": ({"team_id": "t1"}, [("POST", team + "/archive", {"archived": False})]),
                 "set_member": ({"team_id": "t1", "username": "bob", "role": "owner"},
                                [("POST", team + "/members", {"username": "bob", "role": "owner"})]),
                 "remove_member": ({"team_id": "t1", "username": "bob"}, [("DELETE", team + "/members", {"username": "bob"})]),
                 "environment": ({"team_id": "t1", "worker_id": "w2"}, [("POST", team + "/environment", {"worker_id": "w2"})])}
        for action, (args, expected) in cases.items():
            with self.subTest(action=action):
                self.assertEqual(self.calls("octopus_admin_teams", {"action": action, **args}), expected)

    def test_environment_actions(self):
        worker = "/v1/workers/w1"
        cases = {"list": ({}, [("GET", "/v1/admin/environments", None)]),
                 "share": ({"worker_id": "w1", "confirm": True}, [("POST", worker + "/share", {"confirm": True})]),
                 "add_space": ({"worker_id": "w1", "team_id": "t1"}, [("POST", worker + "/spaces", {"team_id": "t1"})]),
                 "remove_space": ({"worker_id": "w1", "user_id": "u2"}, [("DELETE", worker + "/spaces", {"user_id": "u2"})]),
                 "dedicate": ({"worker_id": "w1"}, [("POST", worker + "/dedicate", {})]),
                 "enable": ({"worker_id": "w1"}, [("POST", worker + "/enabled", {"enabled": True})]),
                 "disable": ({"worker_id": "w1"}, [("POST", worker + "/enabled", {"enabled": False})]),
                 "rename": ({"worker_id": "w1", "name": "共享-1"}, [("PATCH", worker, {"name": "共享-1"})]),
                 "retire": ({"worker_id": "w1"}, [("POST", worker + "/retire", {})])}
        for action, (args, expected) in cases.items():
            with self.subTest(action=action):
                self.assertEqual(self.calls("octopus_admin_environments", {"action": action, **args}), expected)

    def test_unknown_actions_and_missing_arguments_are_refused(self):
        for name, args in (("octopus_admin_users", {"action": "create", "username": "x"}),
                           ("octopus_admin_teams", {"action": "delete", "team_id": "t1"}),
                           ("octopus_admin_teams", {"action": "rename"}),
                           ("octopus_admin_environments", {"action": "share", "worker_id": "w1"})):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mcp.call(Fake({("GET", "/v1/admin/users"): USERS}), name, args)

    def test_setting_a_member_needs_the_role(self):
        with self.assertRaisesRegex(ValueError, "role"):
            mcp.call(Fake(), "octopus_admin_teams", {"action": "set_member", "team_id": "t1", "username": "bob"})

    def test_no_tool_creates_users_or_sets_passwords(self):
        text = json.dumps([t for t in mcp.TOOLS if t["name"].startswith("octopus_admin_")], ensure_ascii=False)
        self.assertNotIn("password", text)
        users = next(t for t in mcp.TOOLS if t["name"] == "octopus_admin_users")
        self.assertNotIn("create", users["inputSchema"]["properties"]["action"]["enum"])


if __name__ == "__main__":
    unittest.main()
