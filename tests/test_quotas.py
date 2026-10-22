"""D61 M4-4 part 2: how many tasks a person or a Team runs at once. An administrator sets the default per person and a
person's own; a Team's owner (or an administrator) the Team's. Nothing set limits nothing."""
import json
import time
import unittest

from octopus import quotas
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase


class QuotaCase(AccessCase):
    def run_row(self, owner, team=None, status="running", rid=None, desired="active", lifecycle="open"):
        rid = rid or "run-" + str(time.time_ns())[-12:] + owner["id"][-4:]
        now = time.time()
        with self.db.transaction() as db:
            db.execute("INSERT INTO runs(id,owner_id,team_id,snapshot,input,source,status,desired,lifecycle,created,updated) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid, owner["id"], team, "{}", "需求", "{}", status, desired, lifecycle,
                                                        now, now))
        return rid

    def attempt(self, rid, status="running", number=1, task="code"):
        aid = f"attempt-{rid}-{task}-{number}"
        with self.db.transaction() as db:
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                       "VALUES(?,?,?,?,'{}',?,'{}',?,?)", (aid, rid, task, number, status, time.time(), time.time()))
        return aid

    def diagnosis(self, rid, failed, status="running"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO recoveries(id,run_id,number,failed_task_id,failed_attempt_id,attempt_id,status,context,"
                       "created,updated) VALUES(?,?,1,'code',?,?,?,'{}',?,?)",
                       ("recovery-" + rid, rid, failed, "attempt-coordinator-" + rid, status, time.time(), time.time()))

    def set(self, actor, body):
        return self.service.set_quota(actor, body)


class StoreTests(QuotaCase):
    def test_nothing_set_limits_nothing(self):
        with self.db.read() as db:
            self.assertEqual(quotas.limits(db, self.b["id"], None), {"user": None, "team": None})

    def test_a_persons_own_limit_comes_before_the_default(self):
        self.set(self.a, {"scope": "default", "running": 3})
        self.set(self.a, {"scope": "user", "id": self.e["id"], "running": 1})
        tid = self.team()
        self.set(self.a, {"scope": "team", "id": tid, "running": 4})
        with self.db.read() as db:
            self.assertEqual(quotas.limits(db, self.b["id"], tid), {"user": 3, "team": 4})
            self.assertEqual(quotas.limits(db, self.e["id"], None), {"user": 1, "team": None})

    def test_usage_counts_a_persons_tasks_and_a_teams(self):
        tid = self.team()
        mine, teams = self.run_row(self.b), self.run_row(self.b, tid)
        self.attempt(mine)
        self.attempt(teams, "waiting_approval")
        self.attempt(self.run_row(self.a, tid), "dispatching")
        self.attempt(mine, "accepted", number=2, task="review")   # finished: not running
        with self.db.read() as db:
            self.assertEqual(quotas.usage(db, owner_id=self.b["id"]), 2)   # a Team Session counts for its submitter too
            self.assertEqual(quotas.usage(db, team_id=tid), 2)

    def test_what_a_stopped_or_blocked_session_left_does_not_count(self):   # a disabled Worker leaves a running row
        for status, desired, lifecycle in (("blocked", "active", "open"), ("running", "stopped", "open"),
                                           ("completed", "active", "closed")):
            self.attempt(self.run_row(self.b, status=status, desired=desired, lifecycle=lifecycle))
        with self.db.read() as db:
            self.assertEqual(quotas.usage(db, owner_id=self.b["id"]), 0)

    def test_a_coordinators_diagnosis_counts(self):
        rid = self.run_row(self.b, status="diagnosing")
        failed = self.attempt(rid, "blocked")
        self.diagnosis(rid, failed)
        with self.db.read() as db:
            self.assertEqual(quotas.usage(db, owner_id=self.b["id"]), 1)

    def test_full_names_the_scope_that_is_full(self):
        tid = self.team()
        self.set(self.a, {"scope": "team", "id": tid, "running": 1})
        self.attempt(self.run_row(self.a, tid))
        waiting = self.run_row(self.b, tid, status="ready")
        with self.db.read() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (waiting,)).fetchone()
            self.assertEqual(quotas.full(db, run), {"scope": "team", "limit": 1, "used": 1, "team_name": "研发 Team"})
            self.set(self.a, {"scope": "user", "id": self.b["id"], "running": 1})
        self.attempt(self.run_row(self.b))
        with self.db.read() as db:
            self.assertEqual(quotas.full(db, run)["scope"], "user")   # the person's own comes first


class PermissionTests(QuotaCase):
    def owned_by_bob(self):
        tid = "team-bobs"
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,0)", (tid, "Bob 的 Team"))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.b["id"]))
            db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.e["id"]))
        return tid

    def refused(self, actor, body, code):
        with self.assertRaises(Error) as caught:
            self.set(actor, body)
        self.assertEqual(caught.exception.code, code)

    def test_a_teams_owner_sets_the_teams_and_only_that(self):
        tid = self.owned_by_bob()
        self.assertEqual(self.set(self.b, {"scope": "team", "id": tid, "running": 2})["teams"][0]["limit"], 2)
        self.refused(self.e, {"scope": "team", "id": tid, "running": 5}, "forbidden")   # a member
        self.refused(self.b, {"scope": "default", "running": 5}, "forbidden")
        self.refused(self.b, {"scope": "user", "id": self.e["id"], "running": 5}, "forbidden")
        self.refused(self.b, {"scope": "user", "id": self.b["id"], "running": 50}, "forbidden")   # not even one's own

    def test_the_range_and_removing(self):
        for running in (0, 51, "2", True, 1.5):
            self.refused(self.a, {"scope": "default", "running": running}, "invalid_request")
        self.refused(self.a, {"scope": "everyone", "running": 2}, "invalid_request")
        self.refused(self.a, {"scope": "user", "id": "user-nobody", "running": 2}, "not_found")
        self.set(self.a, {"scope": "default", "running": 2})
        self.assertIsNone(self.set(self.a, {"scope": "default", "running": None})["default"])
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM quotas").fetchone()[0], 0)

    def test_every_change_is_audited(self):
        self.set(self.a, {"scope": "user", "id": self.b["id"], "running": 2})
        self.set(self.a, {"scope": "user", "id": self.b["id"], "running": None})
        with self.db.read() as db:
            rows = db.execute("SELECT action, target, detail FROM audit WHERE action='quota.set' ORDER BY id").fetchall()
        self.assertEqual([(r["target"], json.loads(r["detail"])["running"]) for r in rows],
                         [("user:" + self.b["id"], 2), ("user:" + self.b["id"], None)])

    def test_what_a_person_sees(self):
        tid = self.team()
        self.set(self.a, {"scope": "default", "running": 3})
        self.set(self.a, {"scope": "team", "id": tid, "running": 4})
        self.attempt(self.run_row(self.b, tid))
        seen = self.service.quotas(self.b)
        self.assertEqual(seen["mine"], {"limit": 3, "used": 1})
        self.assertEqual(seen["teams"], [{"id": tid, "name": "研发 Team", "limit": 4, "used": 1, "manage": False}])
        self.assertNotIn("default", seen)
        self.assertNotIn("users", seen)
        admin = self.service.quotas(self.a)
        self.assertEqual((admin["default"], admin["teams"][0]["manage"]), (3, True))

    def test_over_http(self):
        tid = self.owned_by_bob()
        with self.http() as call:
            self.assertEqual(call("POST", "/v1/quotas", self.tokens["bob"], {"scope": "team", "id": tid, "running": 2})[0], 200)
            self.assertEqual(call("POST", "/v1/quotas", self.tokens["eve"], {"scope": "team", "id": tid, "running": 9})[0], 403)
            status, seen = call("GET", "/v1/quotas", self.tokens["eve"])
        self.assertEqual((status, seen["teams"][0]["limit"]), (200, 2))

    def test_the_api_description_lists_it(self):
        from octopus.schema import openapi
        document = openapi()
        self.assertEqual(set(document["paths"]["/v1/quotas"]), {"get", "post"})
        self.assertEqual(set(document["components"]["schemas"]["Quota"]["properties"]), {"scope", "id", "running"})


if __name__ == "__main__":
    unittest.main()
