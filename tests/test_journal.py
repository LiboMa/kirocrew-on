"""D34 §4: the change journal records who changed which fields of a Session, from what, to what, and why."""
import json
import tempfile
import unittest

from octopus import journal
from octopus.db import Database, dumps
from octopus.security import authenticate, create_user, issue_token
from tests.test_access import AccessCase


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        with self.db.transaction() as db:
            self.user = create_user(db, "alice", "Test-password-123!", admin=True)
            db.execute("INSERT INTO runs(id,owner_id,snapshot,input,source,status,created,updated) "
                       "VALUES('run-1',?,'{}','需求','{}','ready',0,0)", (self.user["id"],))
            db.execute("INSERT INTO tasks(run_id,id,ordinal,spec,status) VALUES('run-1','code',0,'{}','pending')")

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        with self.db.read() as db:
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM events ORDER BY id")]

    def test_change_records_only_what_changed(self):
        with self.db.transaction() as db:
            diff = journal.change(db, "runs", "run-1", journal.ENGINE, "run.state", reason="派发前核对",
                                  event={"status": "waiting_worker"}, status="waiting_worker", error=None, updated=5)
        self.assertEqual(diff, {"status": ["ready", "waiting_worker"]})
        (event,) = self.events()
        self.assertEqual((event["run_id"], event["kind"]), ("run-1", "run.state"))
        self.assertEqual(event["payload"], {"status": "waiting_worker", "actor": {"kind": "engine"},
                                            "changes": {"status": ["ready", "waiting_worker"]}, "reason": "派发前核对"})
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT updated FROM runs").fetchone()[0], 5)

    def test_unchanged_write_records_nothing(self):
        with self.db.transaction() as db:
            self.assertEqual(journal.change(db, "runs", "run-1", journal.ENGINE, "run.state", status="ready", updated=9), {})
        self.assertEqual(self.events(), [])

    def test_long_text_is_recorded_by_length_and_hash(self):
        with self.db.transaction() as db:
            journal.change(db, "runs", "run-1", journal.person({"id": self.user["id"], "username": "alice", "client": "mcp"}),
                           "command.continue", input="长" * 600)
        change = self.events()[0]["payload"]["changes"]["input"]
        self.assertEqual(change[0], "需求")
        self.assertEqual(change[1]["chars"], 600)
        self.assertEqual(len(change[1]["sha256"]), 64)

    def test_task_rows_and_many_rows(self):
        with self.db.transaction() as db:
            journal.change(db, "tasks", ("run-1", "code"), journal.WORKER, "attempt.state", status="running")
            journal.change_rows(db, "tasks", "run_id=? AND status=?", ("run-1", "running"), journal.ENGINE, "task.reset",
                                status="pending")
        first, second = self.events()
        self.assertEqual(first["payload"]["changes"], {"status": ["pending", "running"]})
        self.assertEqual(second["payload"]["changes"], [{"id": "code", "changes": {"status": ["running", "pending"]}}])

    def test_clients_from_token_labels(self):
        self.assertEqual([journal.client(x) for x in ("Web login", "claude MCP", "kiro MCP", "API", None)],
                         ["console", "mcp", "mcp", "api", "api"])
        with self.db.transaction() as db:
            token = issue_token(db, self.user["id"], label="claude MCP")["token"]
            self.assertEqual(authenticate(db, token)["client"], "mcp")


class CommandJournalTests(AccessCase):
    def changes(self, rid):
        with self.db.read() as db:
            return [(r["kind"], json.loads(r["payload"])) for r in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (rid,))]

    def test_one_command_records_every_field_it_changes(self):
        env = self.shared_env()
        run = self.start(self.a, worker_id=env)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='stopped' WHERE id=?", (run["id"],))   # fixture only
            db.execute("UPDATE tasks SET status='blocked' WHERE run_id=?", (run["id"],))
        actor = {**self.a, "client": "console"}
        self.service.operate(actor, run["id"], "continue", {"request_id": "continue-000001", "expected_version": 1,
                                                            "task_id": "code", "input": "研发需求与真实验证（修订）"})
        self.assertEqual(self.changes(run["id"])[0][0], "run.created")
        self.assertEqual(self.changes(run["id"])[0][1]["actor"]["kind"], "user")
        command = next(p for k, p in self.changes(run["id"]) if k == "command.continue")
        self.assertEqual(command["actor"], {"kind": "user", "id": self.a["id"], "username": "alice", "client": "console"})
        self.assertEqual(command["task_id"], "code")
        self.assertEqual(command["changes"]["status"], ["stopped", "ready"])
        self.assertEqual(command["changes"]["input"], ["研发需求与真实验证", "研发需求与真实验证（修订）"])
        self.assertEqual(command["changes"]["version"], [1, 2])
        reset = next(p for k, p in self.changes(run["id"]) if k == "task.reset")
        self.assertEqual(reset["changes"][0]["id"], "code")
        latest = self.service.revisions(self.a, run["id"])[-1]["version"]
        self.assertEqual(self.service.revision(self.a, run["id"], latest)["snapshot"]["input"], "研发需求与真实验证（修订）")

    def test_assign_worker_records_the_worker(self):
        run = self.start(self.a)   # no environment: waiting_worker
        env = self.shared_env()
        self.service.operate({**self.a, "client": "mcp"}, run["id"], "assign-worker",
                             {"request_id": "assign-0000001", "expected_version": 1, "worker_id": env})
        command = next(p for k, p in self.changes(run["id"]) if k == "command.assign-worker")
        self.assertEqual(command["worker_id"], env)
        self.assertEqual(command["changes"]["worker_id"], [None, env])
        self.assertEqual(command["actor"]["client"], "mcp")

    def test_admin_oversight_keeps_the_by_field(self):
        run = self.start(self.b)
        self.service.operate(self.a, run["id"], "stop", {"request_id": "stop-00000001", "expected_version": 1})
        command = next(p for k, p in self.changes(run["id"]) if k == "command.stop")
        self.assertEqual((command["oversight"], command["by"]["username"]), (True, "alice"))


if __name__ == "__main__":
    unittest.main()
