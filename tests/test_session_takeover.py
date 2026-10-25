"""D74 (owner 2026-10-25, 方案一「接手」): a collaborator or Team member takes over a Session that runs on someone else's own
boundary — the same Session moves to their own boundary and runs as them from then on. Its executor (``runs.executor_id``)
is apart from its creator (``owner_id``): where it may run, whose limits and knowledge space it uses, who directs and
publishes it follow the executor; the creator keeps owning (and deleting) it."""
from octopus import knowledge, quotas
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

if __name__ == "__main__":
    import unittest
    unittest.main()
