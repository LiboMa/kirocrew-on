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



class EntryTests(ShareCase):
    """Every way in, for a viewer, a collaborator and the company (D64 Task 2)."""
    def outcome(self, call):
        try:
            call()
            return "ok"
        except Error as exc:
            if exc.status == 403 or (exc.status == 404 and "Session 不存在" in str(exc)):
                return "denied"
            return "ok"   # past the access check (a missing candidate, a Worker not reached, …)

    def reads(self, actor):
        service, rid = self.service, self.rid
        return {name: self.outcome(call) for name, call in {
            "run": lambda: service.run(actor, rid), "events": lambda: service.events(actor, rid),
            "revisions": lambda: service.revisions(actor, rid), "revision": lambda: service.revision(actor, rid, 1),
            "changes": lambda: service.changes(actor, rid), "approvals": lambda: service.approvals(actor, rid),
            "attempt_diff": lambda: service.attempt_diff(actor, rid, "attempt-" + "0" * 32)}.items()}

    def stop(self, actor):
        version = self.service.run(self.b, self.rid)["version"]
        return self.outcome(lambda: self.service.operate(actor, self.rid, "stop", {"request_id": "share-stop-" + actor["id"][-8:],
                                                                                  "expected_version": version}))

    def test_a_viewer_reads_everything_and_operates_nothing(self):
        self.share(kind="user", username="eve")
        self.assertEqual(set(self.reads(self.e).values()), {"ok"}, self.reads(self.e))
        self.assertEqual(self.stop(self.e), "denied")
        view = self.service.run(self.e, self.rid)
        self.assertEqual({k: view["permissions"][k] for k in ("operate", "delete", "native", "share", "publish")},
                         {"operate": False, "delete": False, "native": False, "share": False, "publish": False})
        self.assertEqual(view["access"], {"via": "share", "role": "viewer", "by": "bob"})

    def test_a_collaborator_operates_like_a_member_and_never_moves_or_deletes(self):
        self.share(kind="user", username="eve", role="collaborator")
        self.assertEqual(set(self.reads(self.e).values()), {"ok"})
        view = self.service.run(self.e, self.rid)
        self.assertEqual({k: view["permissions"][k] for k in ("operate", "delete", "native", "assign", "share")},
                         {"operate": True, "delete": False, "native": False, "assign": False, "share": False})
        version = view["version"]
        for action in ("assign-worker", "delete"):
            with self.subTest(action):
                self.assertEqual(self.outcome(lambda: self.service.operate(
                    self.e, self.rid, action, {"request_id": "share-" + action, "expected_version": version,
                                               "confirmation": self.rid, "worker_id": "env"})), "denied")
        self.assertEqual(self.stop(self.e), "ok")

    def more_reads(self, actor):
        from octopus import crew_entry, designs
        service, rid = self.service, self.rid
        return {name: self.outcome(call) for name, call in {
            "revision_diff": lambda: service.revision_diff(actor, rid), "archive_entry": lambda: service.archive_entry(actor, rid),
            "preview_entry": lambda: service.preview_entry(actor, rid), "bundle_entry": lambda: service.bundle_entry(actor, rid),
            "observe": lambda: crew_entry.observe(service, actor, rid), "design": lambda: designs.status(service, actor, rid)}.items()}

    def writes(self, actor):
        service, rid = self.service, self.rid
        version = service.run(self.b, rid)["version"]
        return {name: self.outcome(call) for name, call in {
            "publish": lambda: service.publish(actor, rid, {}),   # bodies the check before access accepts
            "steer": lambda: service.steer(actor, rid, {"message": "补充"}),
            "answer_approval": lambda: service.answer_approval(actor, rid, {"origin": "native", "request_id": "r-1",
                                                                             "request_mid": "m-1", "decision": "approve"}),
            "rebind": lambda: service.rebind(actor, rid, {"source": None}),
            "forget_knowledge": lambda: service.forget_knowledge(actor, rid, {"id": "k-1"}),
            "set_publish_policy": lambda: service.set_publish_policy(actor, rid, {"auto": True}),
            "placement": lambda: service.placement(actor, rid=rid)}.items()}

    def test_every_other_reader_and_writer(self):   # final review I2: the whole matrix of D64 Task 2
        self.share(kind="user", username="eve")
        self.assertEqual(set(self.more_reads(self.e).values()), {"ok"}, self.more_reads(self.e))
        self.assertEqual(set(self.writes(self.e).values()), {"denied"}, self.writes(self.e))
        app = {name: self.outcome(call) for name, call in {"app_start": lambda: self.service.app_start(self.e, self.rid),
                                                             "app_stop": lambda: self.service.app_stop(self.e, self.rid)}.items()}
        self.assertEqual(set(app.values()), {"denied"}, app)
        self.share(kind="user", username="eve", role="collaborator")   # an ordinary member's: none of these either
        self.assertEqual(set(self.writes(self.e).values()), {"denied"}, self.writes(self.e))

    def test_the_environments_of_its_space_are_not_shared(self):   # final review I3: placement is for moving it
        self.share(kind="everyone")
        self.assertEqual(self.outcome(lambda: self.service.placement(self.c, rid=self.rid)), "denied")

    def test_a_collaborator_is_not_an_administrators_oversight(self):   # final review I1: a sync was audited as one
        self.share(kind="user", username="eve", role="collaborator")
        with self.db.read() as db:
            self.assertFalse(self.service._oversight(db, self.e, self.rid))
            self.assertFalse(self.service._oversight(db, self.b, self.rid))
            self.assertTrue(self.service._oversight(db, self.a, self.rid))   # alice: an admin, not in it
        self.share(kind="user", username="alice")   # a viewer share: an admin who writes still oversees
        with self.db.read() as db:
            self.assertTrue(self.service._oversight(db, self.a, self.rid))
            self.assertFalse(self.service._oversight(db, self.a, self.rid, read=True))

    def test_the_company_reads(self):
        self.share(kind="everyone")
        self.assertEqual(set(self.reads(self.c).values()), {"ok"})
        self.assertEqual(self.stop(self.c), "denied")

    def test_the_native_entry_is_never_shared(self):
        from octopus import crew_entry
        self.share(kind="user", username="eve", role="collaborator")
        self.assertEqual(self.outcome(lambda: crew_entry.create(self.service, self.e, self.rid)), "denied")

    def test_lists_show_shared_sessions_only_where_asked(self):
        self.share(kind="user", username="eve")
        self.assertEqual([r["id"] for r in self.service.runs(self.e)], [])
        self.assertEqual([r["id"] for r in self.service.runs(self.e, shared=True)], [self.rid])
        self.assertEqual([r["id"] for r in self.service.runs(self.b, shared=True)], [])   # one's own is not "shared with me"

    def test_a_request_is_not_shared_with_its_session(self):   # D55
        self.share(kind="everyone")
        self.assertEqual(self.service.requests(self.c), [])

    def test_removing_closes_an_open_event_stream(self):
        import threading
        import urllib.request
        shared = self.share(kind="user", username="eve")
        with self.http() as call:
            status, _ = call("GET", f"/v1/runs/{self.rid}", self.tokens["eve"])
            self.assertEqual(status, 200)
            url = call.base + f"/v1/runs/{self.rid}/events?stream=1"
            request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.tokens["eve"]})
            seen = []
            with urllib.request.urlopen(request, timeout=10) as stream:
                threading.Timer(1.5, lambda: self.service.unshare_run(self.b, self.rid, shared["id"])).start()
                for line in stream:
                    seen.append(line.decode().strip())
                    if "access_revoked" in seen[-1]:
                        break
        self.assertIn("event: access_revoked", seen)



class FollowUpTests(ShareCase):
    """D64's deferred minors (final review), done after the deploy."""
    def test_a_creator_who_left_the_team_and_was_shared_back_is_a_viewer_everywhere(self):
        tid = self.team()   # alice owns it, bob a member
        rid = self.start(self.b, request="start-0000000004", team_id=tid)["id"]
        with self.db.transaction() as db:
            db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, self.b["id"]))
        self.service.share_run(self.a, rid, {"kind": "user", "username": "bob", "role": "viewer"})
        self.assertEqual(self.service.run(self.b, rid)["access"], {"via": "share", "role": "viewer", "by": "alice"})
        self.assertIn(rid, [r["id"] for r in self.service.runs(self.b, shared=True)])

    def test_a_shared_session_does_not_name_its_request(self):   # D55: the submission is not shared
        self.share(kind="user", username="eve")
        self.assertIsNone(self.service.run(self.e, self.rid)["request"])
        self.assertIsNotNone(self.service.run(self.b, self.rid)["request"])

    def test_pointless_shares_are_refused(self):
        tid = self.team()
        rid = self.start(self.b, request="start-0000000005", team_id=tid)["id"]
        for target, body in (("a member", {"kind": "user", "username": "bob"}), ("its Team", {"kind": "team", "id": tid})):
            with self.subTest(target), self.assertRaises(Error) as caught:
                self.service.share_run(self.a, rid, {**body, "role": "viewer"})
            self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(Error) as caught:   # a personal Session's creator
            self.share(kind="user", username="bob")
        self.assertEqual(caught.exception.status, 400)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET lifecycle='deleted' WHERE id=?", (self.rid,))
        with self.assertRaises(Error) as caught:
            self.share(actor=self.a, kind="everyone")
        self.assertEqual(caught.exception.code, "invalid_state")

    def test_a_member_who_did_not_create_it_keeps_a_members_rights(self):
        tid = self.team()
        rid = self.start(self.a, request="start-0000000006", team_id=tid)["id"]   # alice's; bob an ordinary member
        self.service.share_run(self.a, rid, {"kind": "everyone", "role": "viewer"})
        with self.db.read() as db:
            run_access(db, self.b, rid)   # a write: as a member, not as the company's viewer
        self.assertEqual(self.service.run(self.b, rid)["access"], {"via": "team", "role": "member"})


class McpShareTests(unittest.TestCase):
    def test_a_coding_agent_lists_what_is_shared_with_the_user_and_sees_how_it_reaches_a_session(self):
        from octopus import mcp
        from tests.test_mcp_requests import Client
        client = Client([])
        mcp.call(client, "octopus_runs", {"shared": True})
        self.assertIn("shared=1", client.paths[-1][1])
        mcp.call(client, "octopus_runs", {})
        self.assertNotIn("shared", client.paths[-1][1])
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_runs")
        self.assertEqual(tool["inputSchema"]["properties"]["shared"], {"type": "boolean"})
        self.assertIn("access", mcp.SESSION_FIELDS)
        self.assertFalse([t["name"] for t in mcp.TOOLS if "share" in t["name"] and "workflow" not in t["name"]])   # no tool shares a Session


if __name__ == "__main__":
    unittest.main()
