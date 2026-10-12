"""Access management phase 2: Team and Worker management, the dispatch re-check, and their audit (spec §5, §6)."""
import json
import unittest
from unittest.mock import patch

from octopus.db import dumps
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.test_access import PASSWORD, AccessCase


class TeamsWorkersCase(AccessCase):
    """carol owns「平台组」(team-p) with bob as a member, on the shared environment env-s; alice (admin) is not a member."""

    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            carol = create_user(db, "carol", PASSWORD)
            self.tokens["carol"] = issue_token(db, carol["id"])["token"]
            self.users["carol"] = self.c = authenticate(db, self.tokens["carol"])
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-p','平台组',0)")
            db.execute("INSERT INTO members VALUES('team-p',?,'owner')", (self.c["id"],))
            db.execute("INSERT INTO members VALUES('team-p',?,'member')", (self.b["id"],))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('env-s','共享',?,?,'shared')",
                       (self.a["id"], self.config("b-s")))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('env-s','team-p')")

    @staticmethod
    def config(boundary):
        return dumps({"url": "http://127.0.0.1:1", "token": "x" * 40,
                      "capabilities": {"isolation": "dedicated", "boundary": boundary, "host_principal": "hp-" + boundary}})

    def spare(self, wid="env-spare"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'spare')",
                       (wid, wid, self.a["id"], self.config("b-" + wid)))
        return wid

    def other_team(self, tid="team-r", name="研发组"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,0)", (tid, name))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.c["id"]))
        return tid

    def audits(self, action):
        with self.db.read() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit WHERE action=? ORDER BY id", (action,))]

    def code(self, call, *args, **kwargs):
        with self.assertRaises(Error) as caught:
            call(*args, **kwargs)
        return caught.exception.status, caught.exception.code


class TeamRenameArchiveTests(TeamsWorkersCase):
    def test_owner_renames_and_it_is_audited(self):
        self.assertEqual(self.service.rename_team(self.c, "team-p", {"name": "平台二组"})["name"], "平台二组")
        self.assertEqual({t["id"]: t["name"] for t in self.service.teams(self.c)}["team-p"], "平台二组")
        entry = self.audits("team.rename")[0]
        self.assertEqual((entry["actor_id"], json.loads(entry["detail"])), (self.c["id"], {"from": "平台组", "to": "平台二组"}))

    def test_admin_renames_a_team_they_are_not_in(self):
        self.service.rename_team(self.a, "team-p", {"name": "平台三组"})
        self.assertEqual(self.audits("team.rename")[0]["actor_id"], self.a["id"])

    def test_member_is_told_only_the_owner_renames_and_outsiders_learn_nothing(self):
        self.assertEqual(self.code(self.service.rename_team, self.b, "team-p", {"name": "x 组"}), (403, "forbidden"))
        self.assertEqual(self.code(self.service.rename_team, self.e, "team-p", {"name": "x 组"}), (404, "not_found"))
        self.assertEqual(self.code(self.service.rename_team, self.c, "team-p", {"name": ""}), (400, "invalid_request"))

    def test_duplicate_name_conflicts(self):
        self.other_team()
        self.assertEqual(self.code(self.service.rename_team, self.c, "team-p", {"name": "研发组"}), (409, "conflict"))

    def test_archive_requires_an_elevated_admin(self):
        self.assertEqual(self.code(self.service.archive_team, self.c, "team-p", {"archived": True}), (403, "forbidden"))
        self.assertEqual(self.code(self.service.archive_team, self.a, "team-p", {"archived": "yes"}), (400, "invalid_request"))

    def test_archived_team_refuses_new_sessions_and_members_but_old_sessions_stay_readable_and_stoppable(self):
        run = self.start(self.c, team_id="team-p")
        self.service.archive_team(self.a, "team-p", {"archived": True})
        self.assertEqual(self.code(self.start, self.c, "start-0000000002", team_id="team-p"), (409, "team_archived"))
        self.assertEqual(self.code(self.service.membership, self.c, "team-p", {"username": "eve"}), (409, "team_archived"))
        self.assertEqual(self.service.run(self.b, run["id"])["id"], run["id"])
        self.assertEqual(self.code(self.service.operate, self.c, run["id"], "continue",
                                   {"request_id": "continue-000001", "expected_version": 1, "task_id": "code"}),
                         (409, "team_archived"))
        self.service.operate(self.c, run["id"], "stop", {"request_id": "stop-00000001", "expected_version": 1})
        self.assertTrue({t["id"]: t for t in self.service.teams(self.c)}["team-p"]["archived"])
        self.service.archive_team(self.a, "team-p", {"archived": False})
        self.start(self.c, "start-0000000003", team_id="team-p")
        self.assertEqual([e["action"] for e in self.audits("team.archive") + self.audits("team.restore")],
                         ["team.archive", "team.restore"])

    def test_team_creation_is_audited(self):
        self.spare()
        created = self.service.create_team(self.a, {"name": "新组", "owner": "carol", "worker_id": "env-spare"})
        self.assertEqual(self.audits("team.create")[0]["target"], created["id"])

    def test_rename_and_archive_over_http(self):
        with self.http() as call:
            self.assertEqual(call("PATCH", "/v1/teams/team-p", self.tokens["carol"], {"name": "平台四组"})[0], 200)
            self.assertEqual(call("POST", "/v1/teams/team-p/archive", self.tokens["carol"], {"archived": True})[0], 403)
            self.assertEqual(call("POST", "/v1/teams/team-p/archive", self.tokens["alice"], {"archived": True})[0], 200)


class MembershipTests(TeamsWorkersCase):
    def test_admin_hosts_members_without_being_one(self):
        self.service.membership(self.a, "team-p", {"username": "eve", "role": "owner"})
        roles = {m["username"]: m["role"] for m in {t["id"]: t for t in self.service.teams(self.a)}["team-p"]["members"]}
        self.assertEqual(roles["eve"], "owner")
        self.assertEqual(json.loads(self.audits("team.member.set")[0]["detail"]), {"user": self.e["id"], "role": "owner"})

    def test_admin_without_admin_scope_is_not_a_host(self):
        plain = {**self.a, "scopes": ["read", "execute", "manage"]}
        self.assertEqual(self.code(self.service.membership, plain, "team-p", {"username": "eve"}), (404, "not_found"))

    def test_member_leaves_and_loses_access_to_team_sessions(self):
        run = self.start(self.b, team_id="team-p")
        self.service.membership(self.b, "team-p", {"username": "bob"}, remove=True)
        self.assertEqual(self.audits("team.member.leave")[0]["actor_id"], self.b["id"])
        self.assertEqual(self.code(self.service.run, self.b, run["id"]), (404, "not_found"))
        self.assertEqual(self.service.run(self.c, run["id"])["status"], "preparing")   # the Team's Session runs on

    def test_member_cannot_remove_others_or_promote_themselves(self):
        self.assertEqual(self.code(self.service.membership, self.b, "team-p", {"username": "carol"}, remove=True),
                         (403, "forbidden"))
        self.assertEqual(self.code(self.service.membership, self.b, "team-p", {"username": "bob", "role": "owner"}),
                         (403, "forbidden"))

    def test_the_last_active_owner_cannot_leave(self):
        self.assertEqual(self.code(self.service.membership, self.c, "team-p", {"username": "carol"}, remove=True),
                         (409, "conflict"))

    def test_owner_removal_is_audited(self):
        self.service.membership(self.c, "team-p", {"username": "bob"}, remove=True)
        self.assertEqual(json.loads(self.audits("team.member.remove")[0]["detail"]), {"user": self.b["id"]})

    def test_member_leaves_over_http(self):
        with self.http() as call:
            self.assertEqual(call("DELETE", "/v1/teams/team-p/members", self.tokens["bob"], {"username": "bob"})[0], 200)


if __name__ == "__main__":
    unittest.main()
