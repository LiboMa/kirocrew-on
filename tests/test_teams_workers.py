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


class EnvironmentChangeTests(TeamsWorkersCase):
    def change(self, worker, actor=None):
        return self.service.change_environment(actor or self.a, "team-p", {"worker_id": worker})

    def test_moving_a_team_from_a_shared_environment_onto_a_spare_host(self):
        self.spare()
        self.assertEqual(self.change("env-spare")["environment"]["worker_id"], "env-spare")
        with self.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM worker_spaces WHERE team_id='team-p'").fetchone())
            row = db.execute("SELECT scope,team_id FROM workers WHERE id='env-spare'").fetchone()
        self.assertEqual(tuple(row), ("dedicated", "team-p"))
        self.assertEqual(json.loads(self.audits("team.environment")[0]["detail"]), {"from": "env-s", "to": "env-spare"})

    def test_the_teams_own_host_becomes_spare_and_serves_only_that_team_again(self):
        self.spare()
        self.change("env-spare")
        self.change("env-s")
        with self.db.read() as db:
            row = db.execute("SELECT scope,team_id,last_team FROM workers WHERE id='env-spare'").fetchone()
        self.assertEqual(tuple(row), ("spare", None, "team-p"))
        other = self.other_team()
        status, code = self.code(self.service.worker_space, self.a, "env-spare", {"team_id": other})
        self.assertEqual((status, code), (409, "environment_reuse"))
        self.change("env-spare")   # back to the Team whose code is on it

    def test_a_redeployed_host_may_serve_another_team(self):
        self.spare()
        self.change("env-spare")
        self.change("env-s")
        health = {"isolation": "dedicated", "boundary": "b-new", "host_principal": "hp-new"}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as call:
            status, _ = call("POST", "/v1/workers/env-spare/connection", self.tokens["alice"],
                             {"connection": {"url": "https://w.example", "token": "y" * 40}})
        self.assertEqual(status, 200)
        self.service.worker_space(self.a, "env-spare", {"team_id": self.other_team()})

    def test_failed_change_keeps_the_old_environment(self):
        self.spare()
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='env-spare'")
        self.assertEqual(self.code(self.change, "env-spare"), (409, "worker_unavailable"))
        self.assertEqual(self.service.teams(self.a)[0]["environment"]["worker_id"], "env-s")

    def test_same_environment_unknown_worker_and_non_admins(self):
        self.assertEqual(self.code(self.change, "env-s"), (409, "conflict"))
        self.assertEqual(self.code(self.change, "nope"), (404, "not_found"))
        self.assertEqual(self.code(self.change, "env-s", self.c), (403, "forbidden"))

    def test_a_team_without_an_environment_is_given_one(self):
        other = self.other_team()
        self.spare()
        self.assertEqual(self.service.change_environment(self.a, other, {"worker_id": "env-spare"})
                         ["environment"]["scope"], "dedicated")

    def test_change_over_http_needs_the_admin_scope(self):
        self.spare()
        with self.http() as call:
            self.assertEqual(call("POST", "/v1/teams/team-p/environment", self.tokens["carol"], {"worker_id": "env-spare"})[0], 403)
            self.assertEqual(call("POST", "/v1/teams/team-p/environment", self.tokens["alice"], {"worker_id": "env-spare"})[0], 200)


class WorkerManagementTests(TeamsWorkersCase):
    def test_rename_is_admin_only_and_audited(self):
        self.assertEqual(self.service.rename_worker(self.a, "env-s", {"name": "共享-1"})["name"], "共享-1")
        self.assertEqual(json.loads(self.audits("worker.rename")[0]["detail"]), {"from": "共享", "to": "共享-1"})
        self.assertEqual(self.code(self.service.rename_worker, self.c, "env-s", {"name": "x"}), (403, "forbidden"))

    def test_retire_refuses_a_team_environment(self):
        self.assertEqual(self.code(self.service.retire_worker, self.a, "env-s"), (409, "in_use"))

    def test_retire_refuses_sessions_in_progress(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('env-b','bob',?,?)", (self.b["id"], self.config("b-b")))
        self.start(self.b, worker_id="env-b")
        self.assertEqual(self.code(self.service.retire_worker, self.a, "env-b"), (409, "busy"))

    def test_retired_worker_disappears_and_is_locked(self):
        self.spare()
        with self.db.transaction() as db:
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('env-s',?)", (self.e["id"],))
        self.service.change_environment(self.a, "team-p", {"worker_id": "env-spare"})
        run = self.start(self.e)   # eve's personal space runs on env-s
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (run["id"],))
        self.service.retire_worker(self.a, "env-s")
        self.assertNotIn("env-s", [w["id"] for w in self.service.workers(self.a)])
        with patch("octopus.domain._probe", return_value={"reachable": False, "error": "x"}):
            self.assertNotIn("env-s", [w["id"] for w in self.service.environments(self.a)])
        with self.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM worker_spaces WHERE worker_id='env-s'").fetchone())
        self.assertEqual(self.service.run(self.e, run["id"])["worker_id"], "env-s")   # history keeps its reference
        self.assertEqual(self.code(self.service.rename_worker, self.a, "env-s", {"name": "x"}), (409, "retired"))
        with self.http() as call:
            self.assertEqual(call("POST", "/v1/workers/env-s/enabled", self.tokens["alice"], {"enabled": True})[0], 409)
            self.assertEqual(call("POST", "/v1/workers/env-s/retire", self.tokens["alice"], {})[0], 409)
        self.assertEqual(self.audits("worker.retire")[0]["target"], "env-s")

    def test_models_refresh_is_open_to_every_space_the_environment_serves(self):
        health = {"isolation": "dedicated", "boundary": "b-s", "host_principal": "hp-b-s", "models": {"claude": ["x"]}}
        with patch("octopus.server.worker_request", return_value=health), self.http() as call:
            self.assertEqual(call("GET", "/v1/workers/env-s/capabilities", self.tokens["bob"])[0], 200)
            self.assertEqual(call("GET", "/v1/workers/env-s/capabilities", self.tokens["eve"])[0], 404)

    def test_environment_operations_are_audited(self):
        self.service.worker_space(self.a, "env-s", {"user_id": self.e["id"]})
        self.service.worker_space(self.a, "env-s", {"user_id": self.e["id"]}, remove=True)
        health = {"isolation": "dedicated", "boundary": "b-r", "host_principal": "hp-r"}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as call:
            self.assertEqual(call("POST", "/v1/workers/env-s/enabled", self.tokens["alice"], {"enabled": False})[0], 200)
            status, created = call("POST", "/v1/workers", self.tokens["alice"],
                                   {"name": "w9", "spare": True, "connection": {"url": "https://w9", "token": "z" * 40}})
            self.assertEqual(call("PATCH", "/v1/workers/env-s", self.tokens["alice"], {"name": "共享-2"})[0], 200)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(self.audits("worker.space.add")[0]["detail"]), {"user_id": self.e["id"]})
        self.assertEqual(len(self.audits("worker.space.remove")), 1)
        self.assertEqual(json.loads(self.audits("worker.enabled")[0]["detail"]), {"enabled": False})
        entry = self.audits("worker.register")[0]
        self.assertEqual(entry["target"], created["id"])
        self.assertNotIn("z" * 40, entry["detail"])
        self.assertEqual(len(self.audits("worker.rename")), 1)

    def test_connection_share_and_dedicate_are_audited(self):
        health = {"isolation": "dedicated", "boundary": "b-s", "host_principal": "hp-b-s"}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as call:
            self.assertEqual(call("POST", "/v1/workers/env-s/connection", self.tokens["alice"],
                                  {"connection": {"url": "https://w", "token": "q" * 40}})[0], 200)
        self.assertEqual(json.loads(self.audits("worker.connection")[0]["detail"]), {"boundary_changed": False})
        self.service.dedicate_worker(self.a, "env-s")
        self.assertEqual(json.loads(self.audits("worker.dedicate")[0]["detail"]), {"team_id": "team-p"})
        self.service.share_worker(self.a, "env-s", {"confirm": True})
        self.assertEqual(len(self.audits("worker.share")), 1)


class DispatchRecheckTests(TeamsWorkersCase):
    def setUp(self):
        super().setUp()
        self.calls, self.done, self.broken = [], False, False

    def worker(self, config, path, body, **kwargs):
        self.calls.append(path)
        if path == "/prepare":
            return {"native_parent": "octopus-" + body["run_id"], "workspace": "/w/" + body["run_id"]}
        if path == "/dispatch":
            return {"status": "running", "native_id": "n-" + body["attempt_id"][-6:], "context_hash": "h"}
        if path == "/inspect":
            native = {"done": True, "result": "完成", "error": "崩溃" if self.broken else ""} if self.done else {"done": False}
            return {"status": "running", "native": native, "evidence": {"actual_backend": "claude"},
                    "handoff": "交接", "outcome": dumps({"decision": "pass", "summary": "通过"})}
        raise AssertionError(path)

    def tick(self, times=1):
        engine = Engine(self.db, request=self.worker)
        for _ in range(times):
            engine.tick()

    def two_steps(self, actor, request="start-0000000001"):
        return self.service.start(actor, {"request_id": request, "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": False, "max_cycles": 2},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"},
                                                    {"id": "check", "name": "核对", "tool": "claude", "prompt": "核对"}]})

    def assign(self, rid, worker):
        version = self.service.run(self.c, rid)["version"]
        self.service.operate(self.c, rid, "assign-worker",
                             {"request_id": "assign-" + worker, "expected_version": version, "worker_id": worker})
        return self.service.run(self.c, rid)

    def test_a_session_whose_team_moved_waits_instead_of_dispatching(self):
        run = self.start(self.c, team_id="team-p")
        self.service.change_environment(self.a, "team-p", {"worker_id": self.spare()})
        self.tick()
        self.assertEqual(self.calls, [])
        detail = self.service.run(self.c, run["id"])
        self.assertEqual((detail["status"], detail["error"]), ("waiting_worker", "执行环境已更换或已移出该空间"))
        self.assertIn("已更换", detail["attention"]["title"])
        self.assertEqual(self.assign(run["id"], "env-spare")["status"], "preparing")

    def test_running_attempt_finishes_then_the_next_task_waits(self):
        run = self.two_steps(self.c)
        self.tick(3)                                    # prepare, dispatch, inspect (still running)
        with self.db.transaction() as db:
            db.execute("DELETE FROM worker_spaces WHERE team_id='team-p'")
        self.done = True
        self.tick(3)
        detail = self.service.run(self.c, run["id"])
        self.assertEqual([t["status"] for t in detail["tasks"]], ["accepted", "pending"])
        self.assertEqual(detail["status"], "waiting_worker")
        self.assertEqual(self.calls.count("/dispatch"), 1)

    def test_reassigning_the_same_environment_resumes_without_preparing_again(self):
        run = self.two_steps(self.c)
        self.tick()                                     # prepared: ready with a native parent
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='env-s'")
        self.tick()
        self.assertEqual(self.service.run(self.c, run["id"])["error"], "执行环境已停用")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=1 WHERE id='env-s'")
        detail = self.assign(run["id"], "env-s")
        self.assertEqual((detail["status"], detail["native_parent"]), ("ready", "octopus-" + run["id"]))

    def test_moving_to_another_environment_prepares_afresh(self):
        run = self.two_steps(self.c)
        self.tick()
        self.service.change_environment(self.a, "team-p", {"worker_id": self.spare()})
        self.tick()
        detail = self.assign(run["id"], "env-spare")
        self.assertEqual((detail["status"], detail["native_parent"], detail["workspace"]), ("preparing", None, None))

    def test_continue_checks_the_environment(self):
        run = self.start(self.c, team_id="team-p")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='stopped' WHERE id=?", (run["id"],))
            db.execute("DELETE FROM worker_spaces WHERE team_id='team-p'")
        self.service.operate(self.c, run["id"], "continue",
                             {"request_id": "continue-000001", "expected_version": 1, "task_id": "code"})
        detail = self.service.run(self.c, run["id"])
        self.assertEqual((detail["status"], detail["error"]), ("waiting_worker", "执行环境已更换或已移出该空间"))

    def test_automatic_diagnosis_is_not_sent_to_an_environment_that_no_longer_serves(self):
        run = self.service.start(self.c, {"request_id": "start-0000000009", "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": True, "max_cycles": 2},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})
        self.tick(3)                                    # prepare, dispatch, inspect (still running)
        with self.db.transaction() as db:
            db.execute("DELETE FROM worker_spaces WHERE team_id='team-p'")
        self.done = self.broken = True                  # the running Attempt now fails
        self.tick(3)
        detail = self.service.run(self.c, run["id"])
        self.assertEqual(self.calls.count("/dispatch"), 1)
        self.assertEqual((detail["status"], detail["recovery"]["used"]), ("blocked", 0))
        self.service.operate(self.c, run["id"], "continue",
                             {"request_id": "continue-000009", "expected_version": 1, "task_id": "code"})
        self.assertEqual(self.service.run(self.c, run["id"])["status"], "waiting_worker")

    def test_recover_refuses_an_environment_that_no_longer_serves(self):
        run = self.start(self.c, team_id="team-p")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked', native_parent='octopus-x' WHERE id=?", (run["id"],))
            db.execute("UPDATE workers SET enabled=0 WHERE id='env-s'")
        self.assertEqual(self.code(self.service.operate, self.c, run["id"], "recover",
                                   {"request_id": "recover-000001", "expected_version": 1}), (409, "environment_changed"))


class ReviewFixTests(TeamsWorkersCase):
    def personal(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('env-b','bob',?,?)", (self.b["id"], self.config("b-b")))
        run = self.start(self.b, worker_id="env-b")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed',native_parent='octopus-b' WHERE id=?", (run["id"],))
        return run["id"]

    def test_sessions_on_a_retired_worker_close_and_delete_without_its_host(self):
        rid = self.personal()
        self.service.retire_worker(self.a, "env-b")
        self.service.operate(self.b, rid, "close", {"request_id": "close-0000001", "expected_version": 1})
        detail = self.service.run(self.b, rid)
        self.assertEqual((detail["lifecycle"], detail["status"]), ("closed", "stopped"))
        Engine(self.db, request=lambda *a, **k: self.fail("a retired Worker was called")).tick()
        self.assertEqual(self.code(self.service.operate, self.b, rid, "reopen",
                                   {"request_id": "reopen-000001", "expected_version": 2}), (409, "retired"))
        self.service.operate(self.b, rid, "delete", {"request_id": "delete-000001", "expected_version": 2, "confirmation": rid})
        self.assertEqual(self.service.run(self.b, rid)["lifecycle"], "deleted")

    def test_retire_counts_a_session_being_reopened(self):
        rid = self.personal()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET lifecycle='closed',status='reopening',desired='reopen' WHERE id=?", (rid,))
        self.assertEqual(self.code(self.service.retire_worker, self.a, "env-b"), (409, "busy"))

    def test_roles_change_in_an_archived_team_but_no_one_new_joins(self):
        self.service.archive_team(self.a, "team-p", {"archived": True})
        self.service.membership(self.c, "team-p", {"username": "bob", "role": "owner"})
        self.service.membership(self.c, "team-p", {"username": "carol"}, remove=True)   # carol may now leave
        self.assertEqual(self.code(self.service.membership, self.b, "team-p", {"username": "eve"}), (409, "team_archived"))

    def test_a_deactivated_member_can_be_removed(self):
        with self.db.transaction() as db:
            db.execute("UPDATE users SET active=0 WHERE id=?", (self.b["id"],))
        self.service.membership(self.c, "team-p", {"username": "bob"}, remove=True)
        members = {m["username"] for m in self.service.teams(self.c)[0]["members"]}
        self.assertEqual(members, {"carol"})
        self.assertEqual(self.code(self.service.membership, self.c, "team-p", {"username": "bob"}), (404, "not_found"))

    def test_sessions_of_an_archived_team_say_they_cannot_be_resumed(self):
        run = self.start(self.c, team_id="team-p")
        self.assertTrue(self.service.run(self.c, run["id"])["permissions"]["resume"])
        self.service.archive_team(self.a, "team-p", {"archived": True})
        self.assertFalse(self.service.run(self.c, run["id"])["permissions"]["resume"])
        self.assertFalse(self.service.runs(self.c)[0]["permissions"]["resume"])


class SchemaTests(unittest.TestCase):
    def test_phase_two_routes_are_documented(self):
        from octopus.schema import openapi
        paths = openapi()["paths"]
        self.assertIn("patch", paths["/v1/teams/{id}"])
        self.assertIn("patch", paths["/v1/workers/{id}"])
        for path in ("/v1/teams/{id}/archive", "/v1/teams/{id}/environment", "/v1/workers/{id}/retire"):
            self.assertEqual(paths[path]["post"].get("tags"), ["执行环境（管理员）"], path)
        self.assertEqual(paths["/v1/workers/{id}"]["patch"].get("tags"), ["执行环境（管理员）"])
        self.assertNotIn("tags", paths["/v1/teams/{id}"]["patch"])
        self.assertIn("admin", openapi()["components"]["schemas"]["Token"]["properties"]["scopes"]["items"]["enum"])


if __name__ == "__main__":
    unittest.main()
