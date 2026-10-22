"""D61 M4-4 part 2: how many tasks a person or a Team runs at once. An administrator sets the default per person and a
person's own; a Team's owner (or an administrator) the Team's. Nothing set limits nothing."""
import json
import time
import unittest

from octopus import quotas
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase
from tests.test_watchdog import WatchdogCase


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



class EngineQuotaTests(WatchdogCase):
    """The owner's Sessions on one Worker; a Session's tasks run until ``finish`` says they are done."""
    def setUp(self):
        super().setUp()
        self.signal, self.done, self.exit_once = False, set(), set()
        self.engine.workers = 4
        from concurrent.futures import ThreadPoolExecutor
        self.engine.pool = ThreadPoolExecutor(max_workers=4)

    def request(self, config, path, body, **kwargs):
        if path == "/inspect" and body["run_id"] in self.exit_once:
            self.exit_once.discard(body["run_id"])
            self.calls.append((path, body))
            return {"native": {"done": True, "error": "ACP process exited", "result": ""}, "evidence": {}, "handoff": ""}
        if path == "/inspect" and body["run_id"] in self.done:
            self.calls.append((path, body))
            return {"native": {"done": True, "result": "完成"}, "evidence": {"actual_backend": "claude"}, "handoff": "交接",
                    "outcome": dumps({"decision": "pass", "summary": "完成并验证", "failure_type": "none", "findings": []})}
        return super().request(config, path, body, **kwargs)

    def limit(self, scope, subject, running):
        with self.db.transaction() as db:
            quotas.store(db, self.actor, scope, subject, running)

    def started(self, n):
        return [self.start(request=f"quota-fixture-{i}") for i in range(n)]

    def attempts(self, rid):
        with self.db.read() as db:
            return [dict(r) for r in db.execute("SELECT status, task_id FROM attempts WHERE run_id=? ORDER BY created", (rid,))]

    def kinds(self, rid):
        with self.db.read() as db:
            return [r[0] for r in db.execute("SELECT kind FROM events WHERE run_id=? ORDER BY id", (rid,))]

    def ticks(self, n=3):
        for _ in range(n):
            self.engine.tick()

    def test_over_the_limit_the_next_task_waits(self):
        self.limit("user", self.actor["id"], 1)
        first = self.start()
        self.ticks()
        second = self.start(request="quota-fixture-2")
        self.ticks(4)
        self.assertEqual([a["status"] for a in self.attempts(first)], ["running"])
        self.assertEqual(self.attempts(second), [])
        run = self.service.run(self.actor, second)
        text = "排队：已达到并发上限（你同时运行的任务已达 1 个，上限 1）"
        self.assertEqual((run["status"], run["error"]), ("ready", text))
        self.assertEqual(run["attention"]["title"], text)
        self.assertIn("无需操作", run["attention"]["action"])
        self.assertEqual(self.kinds(second).count("run.quota_wait"), 1)

    def test_it_starts_when_a_task_ends(self):
        self.limit("user", self.actor["id"], 1)
        first = self.start()
        self.ticks()
        second = self.start(request="quota-fixture-2")
        self.ticks()
        self.done.add(first)
        self.ticks(4)
        self.assertTrue(self.attempts(second))
        self.assertIn("run.quota_go", self.kinds(second))
        run = self.service.run(self.actor, second)
        self.assertIsNone(run["error"])
        with self.db.read() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (second,)).fetchone()[0])
        self.assertNotIn("quota_wait", snapshot)
        self.assertGreater(snapshot["budget_waited"], 0)

    def test_two_ready_sessions_and_one_place(self):   # both advance in the same tick, on two threads
        self.limit("default", "", 1)
        runs = self.started(2)
        self.ticks()
        self.assertEqual(sum(bool(self.attempts(rid)) for rid in runs), 1)

    def test_the_one_that_waited_longer_goes_first(self):
        self.limit("user", self.actor["id"], 1)
        first = self.start()
        self.ticks()
        second = self.start(request="quota-fixture-2")
        self.ticks()
        third = self.start(request="quota-fixture-3")
        self.ticks()
        self.done.add(first)
        self.ticks(4)
        self.assertTrue(self.attempts(second))
        self.assertEqual(self.attempts(third), [])

    def test_a_stopped_waiter_does_not_hold_the_others(self):
        self.limit("user", self.actor["id"], 1)
        first = self.start()
        self.ticks()
        second = self.start(request="quota-fixture-2")
        self.ticks()
        third = self.start(request="quota-fixture-3")
        self.ticks()
        version = self.service.run(self.actor, second)["version"]
        self.service.operate(self.actor, second, "stop", {"request_id": "quota-stop-0001", "expected_version": version})
        self.done.add(first)
        self.ticks(4)
        self.assertTrue(self.attempts(third))

    def test_a_waiter_not_seen_for_long_does_not_hold_the_order(self):   # e.g. its Worker backs off: it is not looked at
        self.limit("user", self.actor["id"], 1)
        first = self.start()
        self.ticks()
        second = self.start(request="quota-fixture-2")
        self.ticks()
        third = self.start(request="quota-fixture-3")
        self.ticks()
        self.engine.backoff[second] = (1, self.engine.clock() + 10_000)
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (second,)).fetchone()[0])
            snapshot["quota_wait"]["seen"] -= 1000
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), second))
        self.done.add(first)
        self.ticks(4)
        self.assertTrue(self.attempts(third))

    def test_a_teams_limit_names_the_team(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-q','编排组',0)")
            db.execute("INSERT INTO members VALUES('team-q',?,'owner')", (self.actor["id"],))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('worker','team-q')")
        self.limit("team", "team-q", 1)
        runs = self.started(2)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET team_id='team-q'")
        self.ticks(4)
        waiting = next(rid for rid in runs if not self.attempts(rid))
        self.assertEqual(self.service.run(self.actor, waiting)["error"],
                         "排队：已达到并发上限（Team「编排组」同时运行的任务已达 1 个，上限 1）")

    def test_lowering_the_limit_keeps_what_runs(self):
        runs = self.started(2)
        self.ticks()
        self.limit("user", self.actor["id"], 1)
        self.ticks(3)
        self.assertEqual([[a["status"] for a in self.attempts(rid)] for rid in runs], [["running"], ["running"]])
        self.assertEqual(self.stops(), [])
        third = self.start(request="quota-fixture-3")
        self.ticks(3)
        self.assertEqual(self.attempts(third), [])

    def test_an_automatic_retry_does_not_wait(self):   # it takes the place of the Attempt that just ended
        self.limit("user", self.actor["id"], 1)
        first = self.start()
        self.ticks()
        second = self.start(request="quota-fixture-2")
        self.ticks()
        self.exit_once.add(first)
        self.ticks(3)
        self.assertEqual([a["status"] for a in self.attempts(first)], ["blocked", "running"])
        self.assertEqual(self.attempts(second), [])

    def test_without_limits_nothing_changes(self):
        runs = self.started(2)
        self.ticks()
        self.assertTrue(all(self.attempts(rid) for rid in runs))
        self.assertFalse(any(k.startswith("run.quota") for rid in runs for k in self.kinds(rid)))


if __name__ == "__main__":
    unittest.main()
