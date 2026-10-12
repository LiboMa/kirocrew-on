"""D34 §4: the change journal records who changed which fields of a Session, from what, to what, and why."""
import json
import tempfile
import unittest

from octopus import journal
from octopus.db import Database, dumps
from octopus.security import authenticate, create_user, issue_token


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


if __name__ == "__main__":
    unittest.main()
