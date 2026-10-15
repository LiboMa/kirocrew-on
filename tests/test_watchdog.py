"""D39 §3.2: a native task with no progress (or running too long) is stopped by the platform, retried once without a
recovery cycle, and sent to diagnosis if it stalls again; never doubled when the Worker cannot stop it."""
import copy
import json
import tempfile
import time
import unittest

from octopus import recovery
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

STEP = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现并验证", "kind": "implement", "acceptance": ["可运行"]}
MINUTE = 60.0


class WatchdogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','W',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture", "capabilities": {}})))
        self.calls = []
        self.idle = 31 * MINUTE        # how long ago the native task last made progress
        self.stop_error, self.awaiting = None, False
        self.engine = Engine(self.db, self.request)

    def start(self, recovery_policy=None, request="watchdog-fixture-1"):
        return self.service.start(self.actor, {"request_id": request, "input": "实现并验证", "steps": [STEP],
                                               "source": {"type": "new"}, "worker_id": "worker",
                                               **({"recovery": recovery_policy} if recovery_policy else {})})["id"]

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/w"}
        if path == "/dispatch":
            return {"status": "running", "native_id": body["attempt_id"][-8:], "context_hash": "h"}
        if path == "/attempt/stop":
            if self.stop_error:
                raise self.stop_error
            return {"stopped": True}
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        as_old_worker(path)
        if path == "/inspect" and any(b.get("coordination") for p, b in self.calls if p == "/dispatch"
                                      and b["attempt_id"] == body["attempt_id"]):
            return {"native": {"done": True, "result": "诊断"}, "evidence": {"actual_backend": "kiro"}, "handoff": "交接",
                    "decision": dumps({"action": "wait_for_user", "reason": "仍然卡住", "instructions": "检查环境后续作"})}
        return {"native": {"done": False, "awaiting_approval": self.awaiting}, "evidence": {"actual_backend": "claude"},
                "progress": {"updated_at": time.time() - self.idle, "turns": 4, "last_tool": "npm run build", "status": "running"}}

    def tick(self, rid, count=1):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def age(self, rid, seconds):
        """The running Attempt as if it was dispatched ``seconds`` ago (a new Attempt cannot have been idle for long)."""
        with self.db.transaction() as db:
            db.execute("UPDATE attempts SET created=created-? WHERE run_id=? AND status IN ('running','dispatching')",
                       (seconds, rid))

    def later(self, rid, count):
        """Dispatched and seen running, then ``self.idle`` later."""
        self.tick(rid, 2)
        self.age(rid, self.idle)
        return self.tick(rid, count)

    def stops(self):
        return [b for p, b in self.calls if p == "/attempt/stop"]


class PolicyTests(unittest.TestCase):
    def test_the_policy_keeps_its_shape_and_checks_the_limits(self):
        self.assertEqual(recovery.policy({"enabled": True, "max_cycles": 2}), {"enabled": True, "max_cycles": 2})
        self.assertEqual(recovery.policy({"idle_minutes": 10})["idle_minutes"], 10)
        self.assertNotIn("idle_minutes", recovery.policy({"idle_minutes": 30}))   # the default is not stored
        for bad in ({"idle_minutes": 3}, {"attempt_minutes": 1000}, {"idle_minutes": "10"}):
            with self.subTest(bad), self.assertRaises(Error):
                recovery.policy(bad)


class WatchdogTests(WatchdogCase):
    def test_a_stalled_task_is_stopped_and_retried_once(self):
        rid = self.start()
        run = self.later(rid, 3)
        attempts = sorted(run["tasks"][0]["attempts"], key=lambda a: a["number"])
        first = attempts[0]
        self.assertEqual(len(self.stops()), 1)
        self.assertEqual(first["status"], "blocked")
        self.assertIn("31 分钟没有进展", first["error"])
        self.assertIn("npm run build", first["error"])
        self.assertEqual(first["evidence"]["watchdog"]["reason"], first["error"])
        self.assertEqual(first["evidence"].get("transient"), "stalled")   # as D34 marks the Attempt it retried
        self.assertEqual(attempts[1]["evidence"].get("transient_retry_of"), first["id"])
        self.assertEqual(run["recovery"]["used"], 0)

    def test_a_retry_that_stalls_again_goes_to_diagnosis(self):
        rid = self.start()
        self.later(rid, 3)                # stopped, the retry dispatched and seen running
        self.age(rid, self.idle)          # the retry stalls too
        run = self.tick(rid, 6)
        attempts = sorted(run["tasks"][0]["attempts"], key=lambda a: a["number"])
        self.assertEqual([a["status"] for a in attempts[:2]], ["blocked", "blocked"])
        self.assertEqual(len(attempts), 2)                     # no third run: the Coordinator decides
        self.assertEqual(run["recovery"]["used"], 1)

    def test_a_quiet_task_inside_the_limit_runs_on(self):
        self.idle = 29 * MINUTE
        rid = self.start()
        run = self.later(rid, 3)
        self.assertEqual(self.stops(), [])
        self.assertEqual(run["tasks"][0]["attempts"][0]["status"], "running")

    def test_a_task_running_too_long_is_stopped(self):
        self.idle = 1 * MINUTE
        rid = self.start()
        self.tick(rid, 2)                 # dispatched and seen running
        self.age(rid, 241 * MINUTE)       # four hours later, still making progress
        run = self.tick(rid, 1)
        first = sorted(run["tasks"][0]["attempts"], key=lambda a: a["number"])[0]
        self.assertIn("运行超过 240 分钟", first["error"])

    def test_an_old_worker_is_never_doubled(self):
        self.stop_error = Error(404, "not_found", "接口不存在")
        rid = self.start()
        run = self.later(rid, 4)
        attempts = run["tasks"][0]["attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "running")
        self.assertTrue(attempts[0]["evidence"]["watchdog"]["unsupported"])
        self.assertEqual(len(self.stops()), 1)       # asked once, never again

    def test_waiting_for_approval_or_checks_is_not_stalled(self):
        self.awaiting = True
        rid = self.start()
        run = self.later(rid, 3)
        self.assertEqual(self.stops(), [])
        self.assertEqual(run["tasks"][0]["attempts"][0]["status"], "waiting_approval")

    def test_without_recovery_it_only_stops(self):
        rid = self.start({"enabled": False, "max_cycles": 1})
        run = self.later(rid, 4)
        self.assertEqual(len(run["tasks"][0]["attempts"]), 1)
        self.assertEqual(run["tasks"][0]["attempts"][0]["status"], "blocked")
        self.assertEqual(run["status"], "blocked")

    def test_the_session_settings_set_the_limits(self):
        self.idle = 6 * MINUTE
        rid = self.start({"enabled": True, "max_cycles": 2, "idle_minutes": 5})
        self.later(rid, 3)
        self.assertEqual(len(self.stops()), 1)


if __name__ == "__main__":
    unittest.main()
