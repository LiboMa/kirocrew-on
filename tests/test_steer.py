"""D46 P5b, the platform's side: those who may publish a Session give a running task a note; it is audited and on the
timeline; finished tasks, others, archived Teams and older Workers are refused with their reason."""
import json
import unittest
from unittest.mock import patch

from octopus import mcp
from octopus.db import dumps
from octopus.security import Error
from tests.test_confirmation import ConfirmationCase


class SteerCase(ConfirmationCase):
    def setUp(self):
        super().setUp()
        self.caps({"steer": True})
        self.running, self.sent = True, []

    def caps(self, capabilities):
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": capabilities}),))

    def request(self, config, path, body, **kwargs):
        if path == "/inspect" and self.running:
            return {"native_id": body["attempt_id"][-8:], "native": {}}   # still running
        return super().request(config, path, body, **kwargs)

    def running_session(self, tool="kiro"):
        rid = self.start(steps=[{"id": "code", "name": "实现", "tool": tool, "prompt": "实现", "kind": "deliver",
                                 "acceptance": ["完成"]}])
        run = self.tick(rid, 5)
        self.assertEqual(next(t for t in run["tasks"] if t["id"] == "code")["status"], "running")
        return rid

    def steer(self, rid, actor=None, **body):
        def worker(config, path, payload, **kwargs):
            self.sent.append((path, payload))
            return {"ok": True, "status": "steered"}
        with patch("octopus.transport.worker_request", side_effect=worker):
            return self.service.steer(actor or self.actor, rid, {"task_id": "code", "message": "先只改 README", **body})


class SteerTests(SteerCase):
    def test_the_creator_steers_a_running_task(self):
        rid = self.running_session()
        answer = self.steer(rid)
        self.assertEqual(answer["status"], "steered")
        path, payload = self.sent[0]
        attempt = next(t for t in self.service.run(self.actor, rid)["tasks"] if t["id"] == "code")["current_attempt"]
        self.assertEqual((path, payload), ("/steer", {"run_id": rid, "attempt_id": attempt, "message": "先只改 README",
                                                     "mode": "interrupt"}))

    def test_only_a_kiro_task_takes_a_note(self):
        # KiroCrew 0.10.0 steers only the kiro backend: codex refuses, Claude Code would drop it (review 2026-10-19)
        rid = self.running_session(tool="claude")
        with self.assertRaises(Error) as caught:
            self.steer(rid)
        self.assertEqual(caught.exception.code, "steer_unsupported")
        self.assertEqual(self.sent, [])

    def test_the_timeline_and_audit_record_it(self):
        rid = self.running_session()
        self.steer(rid)
        with self.db.read() as db:
            event = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='task.steered'", (rid,)).fetchone()
            audit = db.execute("SELECT detail FROM audit WHERE action='task.steer' AND target=?", (rid,)).fetchone()
        self.assertIn("先只改 README", json.loads(event["payload"])["reason"])
        self.assertEqual(json.loads(audit["detail"])["task"], "code")

    def test_others_and_archived_teams_cannot(self):
        rid = self.running_session()
        with self.assertRaises(Error) as other:
            self.steer(rid, actor=self.other)
        self.assertIn(other.exception.status, {403, 404})
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-a','组',1)")
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-a',?,'owner')", (self.actor["id"],))
            db.execute("UPDATE runs SET team_id='team-a' WHERE id=?", (rid,))
            db.execute("UPDATE teams SET archived=1 WHERE id='team-a'")
        with self.assertRaises(Error) as archived:
            self.steer(rid)
        self.assertEqual(archived.exception.code, "team_archived")
        self.assertEqual(self.sent, [])

    def test_only_a_running_task_is_steered(self):
        self.running = False
        rid = self.start(steps=[{"id": "code", "name": "实现", "tool": "kiro", "prompt": "实现", "kind": "deliver",
                                 "acceptance": ["完成"]}])
        self.assertEqual(self.tick(rid)["status"], "completed")
        with self.assertRaises(Error) as caught:
            self.steer(rid)
        self.assertEqual(caught.exception.code, "not_running")
        self.assertEqual(self.sent, [])

    def test_what_is_said_is_checked(self):
        rid = self.running_session()
        for body in ({"message": ""}, {"message": "x" * 4001}, {"mode": "now"}, {"mode": "follow_up"}, {"task_id": "nope"}):
            with self.subTest(body=str(body)[:30]), self.assertRaises(Error):
                self.steer(rid, **body)
        self.assertEqual(self.sent, [])

    def test_an_older_worker_says_so(self):
        rid = self.running_session()
        self.caps({})
        with self.assertRaises(Error) as caught:
            self.steer(rid)
        self.assertEqual(caught.exception.code, "steer_unsupported")


class InterfaceTests(unittest.TestCase):
    def test_the_mcp_tool(self):
        tools = {t["name"]: t for t in mcp.TOOLS}
        self.assertIn("octopus_steer", tools)
        self.assertEqual(set(tools["octopus_steer"]["inputSchema"]["required"]), {"run_id", "task_id", "message"})
        self.assertNotIn("mode", tools["octopus_steer"]["inputSchema"]["properties"])   # interrupt only
        seen = []

        class Client:
            def request(self, method, path, body=None):
                seen.append((method, path, body))
                return {"ok": True}
        mcp.call(Client(), "octopus_steer", {"run_id": "run-1", "task_id": "code", "message": "加一句"})
        self.assertEqual(seen, [("POST", "/v1/runs/run-1/steer", {"task_id": "code", "message": "加一句"})])

    def test_the_route_and_schema(self):
        from octopus.schema import openapi
        from octopus.server import required_scope
        self.assertIn("/v1/runs/{id}/steer", openapi()["paths"])
        self.assertEqual(required_scope("POST", "/v1/runs/run-1/steer"), "execute")


if __name__ == "__main__":
    unittest.main()
