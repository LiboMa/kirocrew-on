"""D38 phase 2: the platform lists a Session's pending approvals and passes an answer on, for the people who may
publish it (A3), with an audit record."""
import json
import tempfile
import unittest
from unittest.mock import patch

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import Error, authenticate, create_user, issue_token

ITEM = {"origin": "native", "request_id": "7", "request_mid": "mid-7", "tool": "shell", "tool_input": "npm test",
        "tool_purpose": "运行测试"}


class ApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            self.owner = create_user(db, "owner", "Fixture-password-123!")
            self.member = create_user(db, "member", "Fixture-password-123!")
            self.admin = create_user(db, "admin2", "Fixture-password-123!", True)
            self.actors = {name: authenticate(db, issue_token(db, user["id"])["token"])
                           for name, user in (("owner", self.owner), ("member", self.member), ("admin", self.admin))}
            db.execute("INSERT INTO teams(id,name,created) VALUES('team','T',1)")
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team',?,'owner'),('team',?,'member')",
                       (self.owner["id"], self.member["id"]))
            db.execute("INSERT INTO workers(id,name,team_id,config) VALUES('worker','W','team',?)",   # the Team's environment
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": {}}),))
        self.calls = []
        self.answers = {"/approvals": {"approvals": [ITEM]}, "/approvals/answer": {"ok": True}}

    def worker(self, config, path, body=None, **kwargs):
        self.calls.append((path, body))
        value = self.answers[path]
        if isinstance(value, Exception):
            raise value
        return value

    def session(self):
        with patch("octopus.transport.worker_request", side_effect=self.worker):
            rid = self.service.start(self.actors["owner"], {"request_id": "approvals-fixture-1", "input": "做一个功能",
                                     "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                     "source": {"type": "new"}, "worker_id": "worker", "team_id": "team"})["id"]
        with self.db.transaction() as db:   # prepared on the Worker: it has its native parent Session
            db.execute("UPDATE runs SET native_parent=? WHERE id=?", ("octopus-" + rid, rid))
        return rid

    def call(self, name, *args):
        with patch("octopus.transport.worker_request", side_effect=self.worker):
            return getattr(self.service, name)(*args)

    def test_the_pending_approvals_are_listed(self):
        rid = self.session()
        self.assertEqual(self.call("approvals", self.actors["member"], rid), {"approvals": [ITEM], "supported": True})
        self.assertEqual(self.calls[-1], ("/approvals", {"run_id": rid}))

    def test_an_old_worker_lists_nothing_and_says_so(self):
        rid = self.session()
        self.answers["/approvals"] = Error(404, "not_found", "接口不存在")
        self.assertEqual(self.call("approvals", self.actors["owner"], rid), {"approvals": [], "supported": False})
        self.answers["/approvals/answer"] = Error(404, "not_found", "接口不存在")
        with self.assertRaises(Error) as caught:
            self.call("answer_approval", self.actors["owner"], rid, {**{k: ITEM[k] for k in ("origin", "request_id", "request_mid")},
                                                                    "decision": "approve"})
        self.assertEqual(caught.exception.code, "approvals_unsupported")

    def test_who_may_answer(self):
        rid = self.session()
        body = {"origin": "native", "request_id": "7", "request_mid": "mid-7", "decision": "approve"}
        with self.assertRaises(Error) as caught:
            self.call("answer_approval", self.actors["member"], rid, body)
        self.assertEqual(caught.exception.code, "forbidden")
        for name in ("owner", "admin"):
            with self.subTest(name):
                self.assertTrue(self.call("answer_approval", self.actors[name], rid, body)["ok"])
        self.assertEqual(self.calls[-1], ("/approvals/answer", {"run_id": rid, **body}))

    def test_an_answer_is_audited_and_journaled(self):
        rid = self.session()
        self.call("answer_approval", self.actors["owner"], rid,
                  {"origin": "native", "request_id": "7", "request_mid": "mid-7", "decision": "reject"})
        with self.db.read() as db:
            audit = db.execute("SELECT * FROM audit WHERE action='approval.answer'").fetchone()
        detail = json.loads(audit["detail"])
        self.assertEqual((detail["decision"], detail["tool"], detail["input"]), ("reject", "shell", "npm test"))
        self.assertIn("approval.answered", [e["kind"] for e in self.service.events(self.actors["owner"], rid)])

    def test_a_request_no_longer_pending_is_a_clear_conflict(self):
        rid = self.session()
        self.answers["/approvals"] = {"approvals": []}
        with self.assertRaises(Error) as caught:
            self.call("answer_approval", self.actors["owner"], rid,
                      {"origin": "native", "request_id": "7", "request_mid": "mid-7", "decision": "approve"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "approval_not_pending"))

    def test_the_session_says_who_may_answer(self):
        rid = self.session()
        self.assertTrue(self.service.run(self.actors["owner"], rid)["permissions"]["publish"])
        self.assertFalse(self.service.run(self.actors["member"], rid)["permissions"]["publish"])

    def test_waiting_for_approval_says_where_to_answer(self):
        rid = self.session()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='waiting_approval' WHERE id=?", (rid,))
        attention = self.service.run(self.actors["owner"], rid)["attention"]
        self.assertEqual(attention["title"], "等待批准")
        self.assertIn("待批准", attention["action"])
