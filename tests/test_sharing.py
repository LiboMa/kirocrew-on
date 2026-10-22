"""D64 M4-1: a Session shared with a person, a Team or the whole company (every user of the platform), as a viewer or a
collaborator — a collaborator is what an ordinary Team member is; the company only views. Deleting and the native entry
stay with the creator, the Team owner and administrators."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from octopus import migrations
from octopus.domain import access_of, run_access
from octopus.security import Error, authenticate, create_user, issue_token
from tests.test_access import PASSWORD, AccessCase


class MigratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "octopus.sqlite3"
        self.backups = Path(self.temporary.name) / "backups"

    def test_a_missing_number_runs_after_a_later_one(self):   # two branches each add a migration; either may land first
        latest = max(number for number, _, _ in migrations.MIGRATIONS)
        migrations.apply(self.path, backup_dir=self.backups)
        later = migrations.MIGRATIONS + [(latest + 2, "later", "CREATE TABLE later(x INTEGER);")]
        migrations.apply(self.path, later, backup_dir=self.backups)
        both = later + [(latest + 1, "earlier", "CREATE TABLE earlier(x INTEGER);")]
        result = migrations.apply(self.path, both, backup_dir=self.backups)
        with sqlite3.connect(self.path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            versions = [r[0] for r in db.execute("SELECT version FROM schema_version ORDER BY version")]
        self.assertIn("earlier", tables)
        self.assertEqual(versions[-2:], [latest + 1, latest + 2])
        self.assertEqual(result["to"], latest + 2)

    def test_a_newer_database_is_still_refused(self):
        migrations.apply(self.path, backup_dir=self.backups)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schema_version VALUES(999)")
        with self.assertRaisesRegex(RuntimeError, "高于程序支持"):
            migrations.apply(self.path, backup_dir=self.backups)


class ShareCase(AccessCase):
    """alice: admin and owner of 研发 Team (bob a member). bob creates the Sessions; eve and carol are outsiders."""
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            carol = create_user(db, "carol", PASSWORD)
            self.tokens["carol"] = issue_token(db, carol["id"])["token"]
            self.c = self.users["carol"] = authenticate(db, self.tokens["carol"])
        self.shared_env()
        self.rid = self.start(self.b)["id"]

    def other_team(self, name="评审组", members=("eve",)):
        tid = "team-" + name.encode().hex()[:12]
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,0)", (tid, name))
            for user in members:
                db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.users[user]["id"]))
        return tid

    def can(self, actor, **kwargs):
        with self.db.read() as db:
            try:
                run_access(db, actor, self.rid, **kwargs)
                return 200
            except Error as exc:
                return exc.status

    def share(self, actor=None, **body):
        return self.service.share_run(actor or self.b, self.rid, {"role": "viewer", **body})


class AccessTests(ShareCase):
    def test_without_a_share_nothing_changes(self):
        self.assertEqual([self.can(self.e), self.can(self.e, read=True)], [404, 404])
        self.assertEqual(self.can(self.b, owner=True), 200)

    def test_a_viewer_reads_and_cannot_operate(self):
        self.share(kind="user", username="eve")
        self.assertEqual([self.can(self.e, read=True), self.can(self.e), self.can(self.e, owner=True)], [200, 403, 403])
        self.assertEqual(self.can(self.e, read=True, shares=False), 403)

    def test_a_collaborator_operates_and_still_cannot_delete(self):
        self.share(kind="user", username="eve", role="collaborator")
        self.assertEqual([self.can(self.e, read=True), self.can(self.e), self.can(self.e, owner=True)], [200, 200, 403])
        self.assertEqual(self.can(self.e, shares=False), 403)   # the native entry and the like

    def test_a_team_share_follows_its_members(self):
        tid = self.other_team()
        self.share(actor=self.a, kind="team", id=tid)   # alice is an admin: any Team
        self.assertEqual(self.can(self.e, read=True), 200)
        with self.db.transaction() as db:
            db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.c["id"]))
        self.assertEqual(self.can(self.c, read=True), 200)   # joined later: sees it at once
        with self.db.transaction() as db:
            db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, self.e["id"]))
        self.assertEqual(self.can(self.e, read=True), 404)   # left: no longer

    def test_the_company_only_views(self):
        with self.assertRaises(Error) as caught:
            self.share(kind="everyone", role="collaborator")
        self.assertEqual(caught.exception.status, 400)
        self.share(kind="everyone")
        self.assertEqual([self.can(self.c, read=True), self.can(self.c)], [200, 403])

    def test_the_highest_role_wins_and_a_member_is_never_lowered(self):
        tid = self.team()   # bob's Team Session: alice the owner, bob a member
        rid = self.start(self.b, request="start-0000000002", team_id=tid)["id"]
        self.service.share_run(self.a, rid, {"kind": "everyone", "role": "viewer"})
        with self.db.read() as db:
            run_access(db, self.b, rid)   # a member operates as before
        self.share(kind="everyone")
        self.share(kind="user", username="eve", role="collaborator")
        self.assertEqual(self.can(self.e), 200)

    def test_what_the_session_says_about_access(self):
        self.share(kind="user", username="eve")
        with self.db.read() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (self.rid,)).fetchone()
            self.assertEqual(access_of(db, self.b, run), {"via": "owner"})
            self.assertEqual(access_of(db, self.e, run), {"via": "share", "role": "viewer", "by": "bob"})
            self.assertEqual(access_of(db, self.a, run), {"via": "admin"})


class ManageTests(ShareCase):
    def test_who_may_share(self):
        with self.assertRaises(Error) as caught:
            self.share(actor=self.e, kind="everyone")   # cannot see it
        self.assertEqual(caught.exception.status, 404)
        self.share(kind="user", username="eve", role="collaborator")
        with self.assertRaises(Error) as caught:
            self.share(actor=self.e, kind="user", username="carol")   # a collaborator does not widen access
        self.assertEqual(caught.exception.status, 403)

    def test_whom_one_may_share_with(self):
        for body, status in (({"kind": "user", "username": "nobody"}, 404), ({"kind": "team", "id": self.other_team()}, 404),
                             ({"kind": "planet"}, 400), ({"kind": "user", "username": "eve", "role": "owner"}, 400)):
            with self.subTest(body), self.assertRaises(Error) as caught:
                self.service.share_run(self.b, self.rid, {"role": "viewer", **body})
            self.assertEqual(caught.exception.status, status)
        with self.db.transaction() as db:
            db.execute("UPDATE users SET active=0 WHERE id=?", (self.c["id"],))
        with self.assertRaises(Error) as caught:
            self.share(kind="user", username="carol")
        self.assertEqual(caught.exception.status, 404)

    def test_sharing_again_changes_the_role_and_removing_is_immediate(self):
        first = self.share(kind="user", username="eve")
        second = self.share(kind="user", username="eve", role="collaborator")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual([s["role"] for s in self.service.run_shares(self.b, self.rid)], ["collaborator"])
        self.service.unshare_run(self.b, self.rid, second["id"])
        self.assertEqual(self.can(self.e, read=True), 404)
        with self.db.read() as db:
            actions = [r["action"] for r in db.execute("SELECT action FROM audit WHERE action LIKE 'share.%' ORDER BY id")]
        self.assertEqual(actions, ["share.create", "share.create", "share.delete"])

    def test_an_archived_teams_session_takes_no_new_share(self):
        tid = self.team()
        rid = self.start(self.b, request="start-0000000003", team_id=tid)["id"]
        shared = self.service.share_run(self.a, rid, {"kind": "everyone", "role": "viewer"})
        with self.db.transaction() as db:
            db.execute("UPDATE teams SET archived=1 WHERE id=?", (tid,))
        with self.assertRaises(Error) as caught:
            self.service.share_run(self.a, rid, {"kind": "user", "username": "eve", "role": "viewer"})
        self.assertEqual(caught.exception.code, "team_archived")
        self.service.unshare_run(self.a, rid, shared["id"])   # removing still works

    def test_over_http(self):
        with self.http() as call:
            status, shared = call("POST", f"/v1/runs/{self.rid}/shares", self.tokens["bob"],
                                  {"kind": "user", "username": "eve", "role": "viewer"})
            self.assertEqual((status, shared["name"], shared["role"]), (201, "eve", "viewer"))
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/shares", self.tokens["bob"])[1][0]["id"], shared["id"])
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/shares", self.tokens["eve"])[0], 403)
            self.assertEqual(call("DELETE", f"/v1/runs/{self.rid}/shares/{shared['id']}", self.tokens["bob"])[0], 200)
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/shares", self.tokens["bob"])[1], [])


if __name__ == "__main__":
    unittest.main()
