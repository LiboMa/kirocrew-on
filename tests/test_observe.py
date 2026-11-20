"""Watching a run's native KiroCrew Session from Octopus (console panel, MCP) without a native login (D30)."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from octopus import crew_entry, mcp
from octopus.db import dumps
from octopus.security import Error, issue_token
from octopus.server import server
from octopus.worker import Worker, write
from tests import test_crew_entry
from tests.test_secret_rules import chars

LONG = "长" * 25_000


class WorkerObserveTests(unittest.TestCase):
    setUp, tearDown = test_crew_entry.CrewEntryTests.setUp, test_crew_entry.CrewEntryTests.tearDown
    update = test_crew_entry.CrewEntryTests.update

    def node(self, slot_reply):
        node = Worker(Path(self.tmp.name) / "worker", "x" * 40, 5678, Path(self.tmp.name) / "crew")
        write(node.path(self.rid) / "parent.json", {"slot": self.parent, "parent": "dashboard:" + self.parent})
        self.native_calls = []

        def native(path, body=None, method=None):
            self.native_calls.append((path, body, method))
            if isinstance(slot_reply, Exception):
                raise slot_reply
            return slot_reply
        node.native = native
        return node

    def test_returns_only_the_conversation_of_the_bound_session(self):
        reply = {"key": self.parent, "title": "番茄钟", "running": True, "stopping": False, "total": 42, "has_more": True,
                 "next_before": 40, "context_pct": 12.5, "queue": [{"secret": "x"}],
                 "messages": [{"role": "user", "content": "做一个番茄钟", "ts": "2026-10-12T03:00:00Z",
                               "source_thread": "t", "source_user": "local-app", "meta": {"tool": "shell"}},
                              {"role": "assistant", "content": LONG, "ts": "2026-10-12T03:01:00Z"},
                              "not a message"]}
        node = self.node(reply)
        result = node.observe({"run_id": self.rid, "native_parent": self.parent, "limit": 20, "before": 60})
        self.assertEqual(self.native_calls, [(f"/api/chat/slots/{self.parent}?limit=20&before=60", None, None)])
        self.assertEqual({k: result[k] for k in ("native_parent", "title", "running", "total", "has_more", "next_before",
                                                 "context_pct")},
                         {"native_parent": self.parent, "title": "番茄钟", "running": True, "total": 42, "has_more": True,
                          "next_before": 40, "context_pct": 12.5})
        self.assertEqual(result["messages"][0], {"role": "user", "content": "做一个番茄钟", "truncated": False,
                                                 "ts": "2026-10-12T03:00:00Z"})
        self.assertEqual(len(result["messages"]), 2)
        self.assertTrue(result["messages"][1]["truncated"])
        self.assertEqual(len(result["messages"][1]["content"]), Worker.OBSERVE_TEXT)
        self.assertNotIn("queue", result)
        self.assertNotIn("local-app", dumps(result))
        self.assertTrue(node.health()["observe"])
        # The Worker reports the platform's release number (MVP 0.1.1), not a constant of its own.
        import octopus
        self.assertEqual((octopus.__version__, node.health()["version"]), ("0.2.0rc1", "0.2.0rc1"))

    def test_the_worker_masks_secrets_and_its_own_token(self):
        # D57 §3.3: the Worker's own token too (no rule knows it); masked before the 20 000-character cut
        secret = "ghp" + "_" + chars("worker-observe", 36, "alnum")
        token = "x" * 40   # the node's own (see node())
        edge = "长" * (Worker.OBSERVE_TEXT - 10) + secret
        node = self.node({"key": self.parent, "messages": [
            {"role": "assistant", "content": f"GH_TOKEN={secret}\nAuthorization: Bearer {token}"},
            {"role": "assistant", "content": edge}]})
        first, second = node.observe({"run_id": self.rid, "native_parent": self.parent})["messages"]
        self.assertEqual(first["content"], f"GH_TOKEN={secret[:4]}…{secret[-2:]}\nAuthorization: Bearer xxxx…xx")
        self.assertTrue(all(secret[i:i + 8] not in second["content"] for i in range(len(secret) - 8)))
        self.assertLessEqual(len(second["content"]), Worker.OBSERVE_TEXT)

    def test_refuses_another_session_bad_paging_and_a_missing_slot(self):
        from octopus import gateway_client
        node = self.node({"key": "another-session", "messages": []})
        body = {"run_id": self.rid, "native_parent": self.parent}
        with self.assertRaises(Error) as caught:
            node.observe({**body, "native_parent": "another-session"})
        self.assertEqual(caught.exception.code, "entry_mismatch")
        with self.assertRaises(Error) as caught:
            node.observe(body)               # the Gateway answered for a different slot
        self.assertEqual(caught.exception.code, "entry_mismatch")
        for paging in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "5"}, {"before": -1}, {"before": "1"}):
            with self.subTest(paging=paging), self.assertRaises(Error):
                node.observe({**body, **paging})
        node = self.node(gateway_client.GatewayError(404, "not found"))
        with self.assertRaises(Error) as caught:
            node.observe(body)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "native_unavailable"))
        write(node.path(self.rid) / "lifecycle.json", {"status": "deleted"})
        with self.assertRaises(Error) as caught:
            node.observe(body)
        self.assertEqual(caught.exception.code, "session_deleted")


class PlatformObserveTests(unittest.TestCase):
    setUp, tearDown, update = test_crew_entry.CrewEntryTests.setUp, test_crew_entry.CrewEntryTests.tearDown, test_crew_entry.CrewEntryTests.update
    OBSERVATION = {"native_parent": "native-existing-session", "title": "t", "running": False, "total": 1,
                   "has_more": False, "next_before": None, "context_pct": None,
                   "messages": [{"role": "assistant", "content": "完成", "truncated": False, "ts": None}]}

    def fake(self, config, path, body=None, timeout=35):
        self.calls.append((path, body))
        if path == "/observe":
            return self.OBSERVATION
        raise AssertionError("observation needs no other Worker call: " + path)

    def observe(self, actor=None, **paging):
        with patch("octopus.crew_entry.worker_request", side_effect=self.fake):
            return crew_entry.observe(self.service, actor or self.actor, self.rid, **paging)

    def test_reads_the_run_through_its_worker(self):
        self.assertEqual(self.observe(before=10, limit=30), self.OBSERVATION)
        self.assertEqual(self.calls, [("/observe", {"run_id": self.rid, "native_parent": self.parent,
                                                     "before": 10, "limit": 30})])
        # Closed Sessions stay readable: watching is not opening.
        self.update(lifecycle="closed")
        self.assertEqual(self.observe()["messages"][0]["content"], "完成")

    def test_an_old_workers_answer_is_masked_by_the_platform(self):
        # (final review 6) a Worker from before D57 §3.3 answers as the conversation was written: the console's observe is
        # masked by the platform too — the rules and the Session's Worker token — and is otherwise what the Worker said
        secret = "ghp" + "_" + chars("old-worker-observe", 36, "alnum")
        token = self.config["token"]
        answer = {**self.OBSERVATION, "title": f"配置 {secret}", "messages": [
            {"role": "assistant", "content": f"GH_TOKEN={secret}\nAuthorization: Bearer {token}", "truncated": False,
             "ts": None}, {"role": "user", "content": "完成", "truncated": False, "ts": None}]}
        with patch("octopus.crew_entry.worker_request", return_value=answer):
            result = crew_entry.observe(self.service, self.actor, self.rid)
        self.assertNotIn(secret[4:-2], dumps(result))
        self.assertNotIn(token, dumps(result))
        self.assertEqual(result["messages"][0]["content"], f"GH_TOKEN={secret[:4]}…{secret[-2:]}\nAuthorization: Bearer "
                                                           f"{token[:4]}…{token[-2:]}")
        self.assertEqual({**result, "messages": result["messages"][1:], "title": None},
                         {**answer, "messages": answer["messages"][1:], "title": None})
        with patch("octopus.crew_entry.worker_request", side_effect=self.fake):
            self.assertEqual(crew_entry.observe(self.service, self.actor, self.rid), self.OBSERVATION)   # clean: as is

    def test_a_team_member_on_a_shared_environment_can_watch_what_they_cannot_open(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-shared','Shared Team',0)")
            db.execute("INSERT INTO members VALUES('team-shared',?,'owner')", (self.actor["id"],))
            db.execute("INSERT INTO members VALUES('team-shared',?,'member')", (self.actors["bob"]["id"],))
            db.execute("UPDATE workers SET scope='shared' WHERE id='worker'")
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('worker','team-shared')")
        self.update(team_id="team-shared")
        self.assertEqual(self.observe(self.actors["bob"]), self.OBSERVATION)

    def test_access_preparation_deletion_and_old_workers(self):
        with self.assertRaises(Error) as caught:
            self.observe(self.actors["bob"])          # bob cannot read alice's personal Session
        self.assertEqual(caught.exception.status, 404)
        self.update(native_parent=None)
        with self.assertRaises(Error) as caught:
            self.observe()
        self.assertEqual(caught.exception.code, "not_prepared")
        self.update(native_parent=self.parent, lifecycle="deleted")
        with self.assertRaises(Error):
            self.observe()
        self.update(lifecycle="open")
        self.assertEqual(self.calls, [])
        with patch("octopus.crew_entry.worker_request", side_effect=Error(404, "not_found", "接口不存在")), \
                self.assertRaises(Error) as caught:
            crew_entry.observe(self.service, self.actor, self.rid)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "worker_upgrade_required"))
        for paging in ({"limit": 0}, {"limit": 101}, {"before": -1}):
            with self.subTest(paging=paging), self.assertRaises(Error):
                self.observe(**paging)

    def test_http_route_needs_read_scope_only_and_is_not_cached(self):
        with self.db.transaction() as db:
            read_token = issue_token(db, self.actor["id"], ["read"])["token"]
        app = server(self.db, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()

        def call(token, query=""):
            request = urllib.request.Request(f"http://127.0.0.1:{app.server_port}/v1/runs/{self.rid}/observe{query}",
                                             headers={"Authorization": "Bearer " + token})
            try:
                response = urllib.request.urlopen(request, timeout=3)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, response.headers, json.load(response)
        try:
            with patch("octopus.crew_entry.worker_request", side_effect=self.fake):
                status, headers, result = call(read_token, "?before=5&limit=10")
                self.assertEqual((status, result), (200, self.OBSERVATION))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(self.calls[-1][1]["before"], 5)
                self.assertEqual(call(read_token, "?limit=abc")[0], 400)
                self.assertEqual(call(self.tokens["bob"])[0], 404)
        finally:
            app.shutdown()
            app.server_close()


class McpObserveTests(unittest.TestCase):
    def test_tool_reads_the_observation_and_keeps_it_small_for_agents(self):
        rid = "run-" + "b" * 32
        calls = []

        class Client:
            url = "https://console.example.test"

            def request(self, method, path, body=None):
                calls.append((method, path))
                return {"native_parent": "octopus-" + rid, "running": True, "total": 3, "has_more": True,
                        "next_before": 1, "title": "t", "context_pct": 3.0,
                        "messages": [{"role": "assistant", "content": LONG, "truncated": True, "ts": None}]}
        result = mcp.call(Client(), "octopus_observe", {"run_id": rid, "limit": 5, "before": 9})
        self.assertEqual(calls, [("GET", f"/v1/runs/{rid}/observe?limit=5&before=9")])
        self.assertLessEqual(len(result["messages"][0]["content"]), mcp.OBSERVE_TEXT)
        self.assertTrue(result["messages"][0]["truncated"])
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_observe")
        self.assertEqual(tool["inputSchema"]["required"], ["run_id"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_mcp_observe_masks_secrets(self):
        # D57 §3.3: masked, never refused (the native conversation itself is a known limit); masked before it is cut, so a
        # credential across the cut leaves none of itself
        secret = "sk-" + "ant-" + "api03-" + chars("observe", 93) + "AA"
        edge = "长" * (mcp.OBSERVE_TEXT - 10) + secret

        class Client:
            def request(self, method, path, body=None):
                return {"native_parent": "octopus-x", "messages": [
                    {"role": "assistant", "content": f"已配置 ANTHROPIC_API_KEY={secret}", "truncated": False, "ts": None},
                    {"role": "assistant", "content": edge, "truncated": False, "ts": None}]}
        result = mcp.call(Client(), "octopus_observe", {"run_id": "run-" + "b" * 32})
        first, second = result["messages"]
        self.assertEqual(first["content"], "已配置 ANTHROPIC_API_KEY=sk-a…AA")
        self.assertFalse(first["truncated"])
        self.assertTrue(all(secret[i:i + 8] not in second["content"] for i in range(len(secret) - 8)))
        self.assertLessEqual(len(second["content"]), mcp.OBSERVE_TEXT)
        self.assertEqual(result["native_parent"], "octopus-x")


class DedicateTests(unittest.TestCase):
    setUp, tearDown, update = test_crew_entry.CrewEntryTests.setUp, test_crew_entry.CrewEntryTests.tearDown, test_crew_entry.CrewEntryTests.update

    def share_with_team(self, isolation="dedicated"):
        self.health["isolation"] = isolation
        self.config["capabilities"] = dict(self.health)
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-w','Web 组',0)")
            db.execute("INSERT INTO members VALUES('team-w',?,'owner')", (self.actor["id"],))
            db.execute("INSERT INTO members VALUES('team-w',?,'member')", (self.actors["bob"]["id"],))
            db.execute("UPDATE workers SET scope='shared', config=? WHERE id='worker'", (dumps(self.config),))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('worker','team-w')")
        self.update(team_id="team-w")

    def test_a_worker_host_shared_with_one_team_becomes_its_own_again(self):
        self.share_with_team()
        with self.assertRaises(Error) as caught:
            self.service.dedicate_worker(self.actors["bob"], "worker")
        self.assertEqual(caught.exception.code, "forbidden")
        self.assertEqual(self.service.dedicate_worker(self.actor, "worker"),
                         {"id": "worker", "scope": "dedicated", "team_id": "team-w"})
        with self.db.read() as db:
            row = db.execute("SELECT scope,team_id,owner_id FROM workers WHERE id='worker'").fetchone()
            self.assertEqual(tuple(row), ("dedicated", "team-w", None))
            self.assertEqual(db.execute("SELECT count(*) FROM worker_spaces WHERE worker_id='worker'").fetchone()[0], 0)
        # Every member of the Team now opens the native entry, as in a personal space.
        with patch("octopus.crew_entry.worker_request",
                   side_effect=lambda config, path, body=None: self.health if path == "/health" else
                   {"native_parent": self.parent, "url": self.health["crew_origin"] + "/chat?sid=" + self.parent +
                    "&token=t"}):
            self.health["crew_origin"] = "https://dnative.example.test"
            self.config["capabilities"] = dict(self.health)
            with self.db.transaction() as db:
                db.execute("UPDATE workers SET config=? WHERE id='worker'", (dumps(self.config),))
            self.assertEqual(crew_entry.create(self.service, self.actors["bob"], self.rid)["native_parent"], self.parent)

    def test_only_a_worker_host_serving_exactly_one_team(self):
        self.share_with_team(isolation="trusted-local")
        with self.assertRaises(Error) as caught:
            self.service.dedicate_worker(self.actor, "worker")
        self.assertEqual(caught.exception.code, "isolation_required")
        with self.db.transaction() as db:
            self.health["isolation"] = "dedicated"
            self.config["capabilities"] = dict(self.health)
            db.execute("UPDATE workers SET config=? WHERE id='worker'", (dumps(self.config),))
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('worker',?)", (self.actors["bob"]["id"],))
        with self.assertRaises(Error) as caught:
            self.service.dedicate_worker(self.actor, "worker")
        self.assertEqual(caught.exception.code, "invalid_state")
        with self.db.transaction() as db:
            db.execute("DELETE FROM worker_spaces")
            db.execute("UPDATE workers SET scope='dedicated', owner_id=NULL, team_id='team-w'")
        with self.assertRaises(Error) as caught:
            self.service.dedicate_worker(self.actor, "worker")   # already dedicated
        self.assertEqual(caught.exception.code, "invalid_state")
        with self.assertRaises(Error) as caught:
            self.service.dedicate_worker(self.actor, "missing")
        self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
