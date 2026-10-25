"""D74 (owner 2026-10-25, 方案一「接手」): a collaborator or Team member takes over a Session that runs on someone else's own
boundary — the same Session moves to their own boundary and runs as them from then on. Its executor (``runs.executor_id``)
is apart from its creator (``owner_id``): where it may run, whose limits and knowledge space it uses, who directs and
publishes it follow the executor; the creator keeps owning (and deleting) it."""
import json

from octopus import knowledge, quotas
from octopus.security import Error
from octopus.domain import executor_of, run_permissions, unserved
from tests.test_session_placement import PlacementCase


class ExecutorCase(PlacementCase):
    def setUp(self):
        super().setUp()
        self.own("bob-b", "bob")
        # A personal Session of alice, shared with bob as a collaborator
        self.created, _ = self.start("alice", team=None)
        self.rid = self.created["id"]
        with self.db.transaction() as db:
            db.execute("INSERT INTO shares(id,run_id,kind,subject,role,created_by,created) VALUES('share-1',?,'user',?,"
                       "'collaborator',?,1)", (self.rid, self.ids["bob"], self.ids["alice"]))

    def take(self, person="bob", worker="bob-b"):   # what a finished move leaves (the move itself is tested apart)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET executor_id=?, worker_id=? WHERE id=?", (self.ids[person], worker, self.rid))


class ExecutorTests(ExecutorCase):
    def test_the_creator_runs_it_until_someone_takes_it_over(self):
        self.assertEqual(executor_of(self.row(self.rid)), self.ids["alice"])
        self.take()
        self.assertEqual(executor_of(self.row(self.rid)), self.ids["bob"])
        self.assertEqual(self.row(self.rid)["owner_id"], self.ids["alice"])   # still alice's Session

    def test_the_executors_own_boundary_serves_it(self):
        self.take()
        with self.db.read() as db:
            self.assertIsNone(unserved(db, self.row(self.rid)))
        with self.db.transaction() as db:   # alice's own boundary does not serve bob's turn
            db.execute("UPDATE runs SET worker_id='alice-b' WHERE id=?", (self.rid,))
        with self.db.read() as db:
            self.assertIsNotNone(unserved(db, self.row(self.rid)))

    def test_limits_and_knowledge_follow_the_executor(self):
        self.take()
        with self.db.transaction() as db:
            db.execute("INSERT INTO quotas(scope,subject,running,updated,updated_by) VALUES('user',?,1,1,?)",
                       (self.ids["bob"], self.ids["bob"]))
            db.execute("UPDATE runs SET status='running', desired='active' WHERE id=?", (self.rid,))
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated)"
                       " VALUES('attempt-q',?,'code',1,'{}','running','{}',1,1)", (self.rid,))
            db.execute("UPDATE tasks SET current_attempt='attempt-q' WHERE run_id=?", (self.rid,))
        with self.db.read() as db:
            run = self.row(self.rid)
            self.assertEqual(quotas.usage(db, owner_id=self.ids["bob"]), 1)   # bob runs it now
            self.assertEqual(quotas.usage(db, owner_id=self.ids["alice"]), 0)
            self.assertEqual(quotas.full(db, run)["scope"], "user")   # bob's limit of 1 is used
            self.assertEqual(knowledge.space(run), self.ids["bob"])

    def test_the_executor_directs_publishes_and_answers_approvals_but_does_not_share(self):
        self.take()
        with self.db.read() as db:
            bob = run_permissions(db, self.people["bob"], self.row(self.rid))
            alice = run_permissions(db, self.people["alice"], self.row(self.rid))
        self.assertTrue(bob["direct"])
        self.assertTrue(bob["publish"])
        self.assertFalse(bob["share"])   # sharing stays with the creator
        self.assertFalse(alice["direct"])   # R10: now bob's boundary — alice takes it back to direct it
        self.assertTrue(alice["share"])


    def test_the_creators_personal_credential_does_not_follow_the_session(self):
        # Pinned to alice's personal token, a Session bob runs must not use it: his turn runs as him (D74)
        from octopus.credentials import bound_usable
        with self.db.transaction() as db:
            db.execute("INSERT INTO credentials(id,name,kind,host,scope,owner_id,access,hint,secret,created_by,created,updated)"
                       " VALUES('cred-a','alice 的 GitHub','github_pat','github.com','user',?,'write','…1a2b','x',?,1,1)",
                       (self.ids["alice"], self.ids["alice"]))
            credential = db.execute("SELECT * FROM credentials WHERE id='cred-a'").fetchone()
            self.assertTrue(bound_usable(db, credential, self.row(self.rid)))
        self.take()
        with self.db.read() as db:
            self.assertFalse(bound_usable(db, credential, self.row(self.rid)))

    def test_the_executor_opens_the_native_entry_on_their_own_boundary(self):
        with self.db.read() as db:
            self.assertFalse(run_permissions(db, self.people["bob"], self.row(self.rid))["native"])   # alice's boundary
        self.take()
        with self.db.read() as db:
            self.assertTrue(run_permissions(db, self.people["bob"], self.row(self.rid))["native"])
            self.assertFalse(run_permissions(db, self.people["alice"], self.row(self.rid))["native"])   # R7: bob's now


class TakeoverCommandTests(ExecutorCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:   # a Session that has stopped: what a takeover moves
            db.execute("UPDATE runs SET status='blocked', native_parent='octopus-x', workspace='/w/alice' WHERE id=?", (self.rid,))
        self.keys = 0

    def takeover(self, person="bob", **extra):
        self.keys += 1
        version = self.row(self.rid)["version"]
        return self.service.takeover(self.people[person], self.rid,
                                     {"request_id": f"takeover-fixture-{self.keys}", "expected_version": version, **extra})

    def code(self, call):
        try:
            call()
        except Error as error:
            return error.status, error.code
        return None

    def test_a_collaborator_takes_it_over_to_their_own_boundary(self):
        before = self.row(self.rid)["version"]
        result = self.takeover()
        self.assertEqual((result["takeover"]["state"], result["takeover"]["to"], result["takeover"]["from"]),
                         ("moving", "bob-b", "alice-b"))
        row = self.row(self.rid)
        self.assertEqual(json.loads(row["snapshot"])["takeover"]["by"], self.ids["bob"])
        self.assertEqual(row["version"], before + 1)
        self.assertEqual(row["worker_id"], "alice-b")   # the Engine moves it; until then it stays where it was
        with self.db.read() as db:
            kinds = [r["kind"] for r in db.execute("SELECT kind FROM events WHERE run_id=?", (self.rid,))]
        self.assertIn("run.takeover_requested", kinds)

    def test_the_same_request_takes_it_over_once(self):
        version = self.row(self.rid)["version"]
        body = {"request_id": "takeover-fixture-same", "expected_version": version}
        first = self.service.takeover(self.people["bob"], self.rid, body)
        self.assertEqual(self.service.takeover(self.people["bob"], self.rid, body), first)
        self.assertEqual(self.row(self.rid)["version"], version + 1)

    def test_who_may_not_take_it_over(self):
        with self.db.transaction() as db:
            db.execute("UPDATE shares SET role='viewer' WHERE id='share-1'")
        self.assertEqual(self.code(self.takeover)[0], 403)   # a viewer only reads
        with self.db.transaction() as db:
            db.execute("UPDATE shares SET role='collaborator' WHERE id='share-1'")
        self.assertEqual(self.code(lambda: self.takeover("alice")), (409, "already_yours"))   # it runs on her own boundary
        with self.db.transaction() as db:   # dave can continue it but has no boundary of his own to take it to
            db.execute("INSERT INTO shares(id,run_id,kind,subject,role,created_by,created) VALUES('share-2',?,'user',?,"
                       "'collaborator',?,1)", (self.rid, self.ids["dave"], self.ids["alice"]))
        self.assertEqual(self.code(lambda: self.takeover("dave")), (409, "no_own_environment"))
        self.assertEqual(self.code(lambda: self.service.takeover(self.admin, self.rid, {
            "request_id": "takeover-fixture-admin", "expected_version": self.row(self.rid)["version"]}))[0], 403)

    def test_only_a_session_that_has_stopped_moves(self):
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='running' WHERE id=?", (self.rid,))
        self.assertEqual(self.code(self.takeover), (409, "busy"))
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked' WHERE id=?", (self.rid,))
        self.assertEqual(self.code(lambda: self.takeover(expected_version=0)), (409, "version_conflict"))

    def test_nothing_else_runs_while_it_moves(self):
        self.takeover()
        version = self.row(self.rid)["version"]
        self.assertEqual(self.code(lambda: self.service.operate(self.people["alice"], self.rid, "continue", {
            "request_id": "continue-while-moving", "expected_version": version})), (409, "takeover_in_progress"))
        self.assertEqual(self.code(self.takeover), (409, "takeover_in_progress"))

    def test_no_preview_publish_or_sync_while_it_moves(self):
        self.takeover()
        alice = self.people["alice"]
        for call in (lambda: self.service.app_start(alice, self.rid), lambda: self.service.publish(alice, self.rid, {}),
                     lambda: self.service.sync(alice, self.rid, b"bundle", "a" * 40, "sync-while-moving", 0, old="b" * 40)):
            self.assertEqual(self.code(call), (409, "takeover_in_progress"))

    def test_the_console_knows_who_may_take_it_over(self):
        with self.db.read() as db:
            bob = run_permissions(db, self.people["bob"], self.row(self.rid))
            alice = run_permissions(db, self.people["alice"], self.row(self.rid))
        self.assertTrue(bob["takeover"])
        self.assertFalse(alice["takeover"])
        self.assertIn("已经在你的执行环境上", alice["takeover_reason"])

if __name__ == "__main__":
    import unittest
    unittest.main()
