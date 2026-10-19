"""D49 phase 2: where a Session runs. A Team Session runs on its submitter's own boundary; without one it falls back to the
Team's environment with a hint (owner 2026-10-19, option A); whether an environment still serves a Session follows its
creator; the platform alone answers where a new Session would go."""
import json
import tempfile
import unittest

from octopus import domain
from octopus.db import Database, dumps
from octopus.domain import Service, unserved
from octopus.security import Error, authenticate, create_user, issue_token

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["完成"]}]


class PlacementCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Database(self.temporary.name)
        self.service = Service(self.db)
        self.config = dumps({"url": "fixture", "token": "fixture", "capabilities": {"isolation": "dedicated"}})
        with self.db.transaction() as db:
            self.admin = authenticate(db, issue_token(db, create_user(db, "admin", "admin-password-123", admin=True)["id"])["token"])
            users = {name: create_user(db, name, name + "-password-123", admin=False) for name in ("alice", "bob", "dave")}
            self.ids = {name: row["id"] for name, row in users.items()}
            self.people = {name: authenticate(db, issue_token(db, row["id"])["token"]) for name, row in users.items()}
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-a','研发组',1)")
            for name, role in (("alice", "member"), ("bob", "member"), ("dave", "owner")):
                db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-a',?,?)", (self.ids[name], role))
            db.execute("INSERT INTO workers(id,name,team_id,config,scope) VALUES('teamenv','研发组环境','team-a',?,'dedicated')",
                       (self.config,))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('shared','personal',?,?,'shared')",
                       (self.admin["id"], self.config))
            for name in ("alice", "bob"):
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('shared',?)", (self.ids[name],))
        self.own("alice-b", "alice")
        self.requests = 0

    def own(self, wid, person):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'dedicated')",
                       (wid, wid, self.ids[person], self.config))

    def start(self, person, team="team-a", **body):
        self.requests += 1
        created = self.service.start(self.people[person], {
            "request_id": f"placement-fixture-{self.requests}", "input": "实现登录功能", "source": {"type": "new"}, "steps": STEPS,
            **({"team_id": team} if team else {}), **body})
        return created, self.service.run(self.people[person], created["id"])

    def row(self, rid):
        with self.db.read() as db:
            return dict(db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())


class RuleTests(PlacementCase):
    def test_a_team_session_runs_on_the_submitters_own_boundary(self):
        created, run = self.start("alice")
        self.assertEqual(run["worker_id"], "alice-b")
        self.assertEqual(run["placement"], {"kind": "own"})
        self.assertEqual(created["placement"], {"kind": "own"})

    def test_without_one_it_falls_back_to_the_teams_environment_with_a_hint(self):
        _, run = self.start("bob")
        self.assertEqual(run["worker_id"], "teamenv")
        self.assertEqual(run["placement"]["kind"], "team")
        self.assertIn("boundary add", run["placement"]["hint"])

    def test_a_team_without_an_environment_takes_a_member_with_a_boundary(self):
        with self.db.transaction() as db:
            db.execute("DELETE FROM workers WHERE id='teamenv'")
        self.assertEqual(self.start("alice")[1]["worker_id"], "alice-b")
        with self.assertRaises(Error) as caught:
            self.start("bob")
        self.assertEqual(caught.exception.code, "team_without_worker")
        self.assertIn("自己的执行边界", str(caught.exception))

    def test_another_members_boundary_cannot_be_named(self):
        with self.assertRaises(Error) as caught:
            self.start("bob", worker_id="alice-b")
        self.assertEqual(caught.exception.status, 403)

    def test_two_own_boundaries_wait_in_a_team_too(self):
        self.own("alice-b2", "alice")
        _, run = self.start("alice")
        self.assertIsNone(run["worker_id"])
        self.assertEqual(run["status"], "waiting_worker")

    def test_a_named_environment_is_kept_and_recorded(self):
        _, run = self.start("alice", worker_id="teamenv")
        self.assertEqual((run["worker_id"], run["placement"]), ("teamenv", {"kind": "requested"}))

    def test_personal_sessions_are_unchanged(self):
        self.assertEqual(self.start("alice", team=None)[1]["placement"], {"kind": "own"})
        _, run = self.start("bob", team=None)
        self.assertEqual((run["worker_id"], run["placement"]), ("shared", {"kind": "only"}))

    def test_the_timeline_records_the_placement(self):
        created, _ = self.start("bob")
        with self.db.read() as db:
            event = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='run.created'", (created["id"],)).fetchone()
        self.assertEqual(json.loads(event["payload"])["placement"], "team")


class ServingTests(PlacementCase):
    def test_the_creator_leaving_the_team_makes_it_wait(self):
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("DELETE FROM members WHERE team_id='team-a' AND user_id=?", (self.ids["alice"],))
            self.assertIn("会话创建者已不在该 Team", unserved(db, self.row(created["id"])))

    def test_a_disabled_boundary_waits_and_can_move_to_the_team(self):
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='alice-b'")
            self.assertEqual(unserved(db, self.row(created["id"])), "执行环境已停用")
            db.execute("UPDATE runs SET status='waiting_worker' WHERE id=?", (created["id"],))
        self.service.operate(self.people["dave"], created["id"], "assign-worker",
                             {"request_id": "assign-fixture-1", "worker_id": "teamenv",
                              "expected_version": self.row(created["id"])["version"]})
        self.assertEqual(self.row(created["id"])["worker_id"], "teamenv")

    def test_a_team_environment_change_leaves_member_boundaries_alone(self):
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET scope='spare', team_id=NULL, owner_id=?, last_team='team-a' WHERE id='teamenv'",
                       (self.admin["id"],))   # as Service.set_environment leaves the Team's former host
            self.assertIsNone(unserved(db, self.row(created["id"])))

    def test_the_space_rule_is_one(self):   # the start, the check before dispatch and the assignment ask the same function
        with self.db.read() as db:
            names = {w["id"] for w in domain.space_workers(db, self.ids["alice"], "team-a")}
            others = {w["id"] for w in domain.space_workers(db, self.ids["bob"], "team-a")}
        self.assertEqual((names, others), ({"alice-b", "teamenv"}, {"teamenv"}))


if __name__ == "__main__":
    unittest.main()
