"""D60 M4-4 part 1: a sub-agent KiroCrew deferred (low memory, critical posture, cap at zero) is queued, not lost — the
Worker recognises it through GET /api/spawn/{id}, and the platform shows it as a queue the watchdog does not count."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client, recovery
from octopus.security import Error
from octopus.worker import Worker
from tests.test_watchdog import MINUTE, WatchdogCase

RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}


class WorkerQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew")
        self.attempt = "attempt-" + "2" * 32
        self.listed, self.status = [], None

        def native(path, body=None, method=None, timeout=None):
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn" and body is not None:
                return {"id": "aabb1234", "status": "queued", "reason": "low_memory",
                        "reason_detail": "only 2.47 GB available, need 2.50 GB"}
            if path == "/api/spawn":
                return {"agents": self.listed}   # the ordinary list leaves queued runs out
            if path == "/api/spawn/aabb1234":
                if self.status is None:
                    raise gateway_client.GatewayError(404, '{"error":"not found"}')
                return self.status
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})
        self.node.dispatch({"run_id": RID, "attempt_id": self.attempt, "input": "需求", "spec": SPEC, "upstream": []})

    def inspect(self):
        return self.node.inspect({"run_id": RID, "attempt_id": self.attempt})

    def queued_row(self, **extra):
        return {"id": "aabb1234", "task": f"[OCTOPUS-ATTEMPT:{self.attempt}] 编码", "done": False, "status": "queued", "queued": True,
                "reason": "low_memory", "reason_detail": "only 2.47 GB available, need 2.50 GB", "elapsed": 1200, **extra}

    def test_a_queued_run_is_queued_not_unknown(self):
        self.status = self.queued_row()
        result = self.inspect()
        self.assertEqual(result["native"], {"done": False, "queued": True})
        self.assertEqual(result["queued"], {"reason": "low_memory", "detail": "only 2.47 GB available, need 2.50 GB",
                                            "elapsed": 1200, "resuming": False})

    def test_a_continuation_said_spawned_but_waits(self):   # /continue answers spawned; the run's own status says queued
        self.status = self.queued_row(resuming=True)
        self.assertTrue(self.inspect()["queued"]["resuming"])

    def test_someone_elses_queued_run_is_not_this_attempt(self):
        self.status = self.queued_row(task="[OCTOPUS-ATTEMPT:attempt-" + "9" * 32 + "] 别的")
        with self.assertRaises(Error) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "native_unavailable")

    def test_a_run_that_began_and_waits_again_is_queued(self):   # final review I1: its state file exists, the list omits it
        _, parent = self.node.binding(RID)
        folder = self.node.home / "subagents" / "aabb1234"
        folder.mkdir(parents=True)
        (folder / "state.json").write_text(json.dumps({"id": "aabb1234", "parent_session": parent["parent"]}))
        self.status = self.queued_row(resuming=True, task="编码（重启后）")   # the state file proves it is ours
        result = self.inspect()
        self.assertEqual(result["native"], {"done": False, "queued": True})
        self.assertTrue(result["queued"]["resuming"])

    def test_neither_listed_nor_queued_is_still_unknown(self):
        with self.assertRaises(Error) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "native_unavailable")



class EngineQueueTests(WatchdogCase):
    QUEUED = {"reason": "low_memory", "detail": "only 2.47 GB available, need 2.50 GB", "elapsed": 12000, "resuming": False}

    def setUp(self):
        super().setUp()
        self.queued, self.never, self.then = None, 0, None

    def request(self, config, path, body, **kwargs):
        coordination = any(b.get("coordination") for p, b in self.calls if p == "/dispatch"
                           and b["attempt_id"] == (body or {}).get("attempt_id"))
        if path == "/inspect" and self.queued and not coordination:
            self.calls.append((path, body))
            return {"native": {"done": False, "queued": True}, "queued": self.queued, "evidence": {}, "handoff": ""}
        if path == "/inspect" and self.never and not coordination:
            self.never -= 1
            self.calls.append((path, body))
            return {"native": {"done": True, "error": "never started: waiting for memory", "result": ""}, "evidence": {},
                    "handoff": ""}
        if path == "/inspect" and self.then and not coordination:
            answer, self.then = self.then, None
            self.calls.append((path, body))
            return answer
        return super().request(config, path, body, **kwargs)

    def attempts(self, rid):
        with self.db.read() as db:
            return [json.loads(r["evidence"] or "{}") for r in
                    db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id='code' ORDER BY number", (rid,))]

    def budget(self, rid):
        with self.db.read() as db:
            return recovery.budget(db, db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())

    def events(self, rid):
        with self.db.read() as db:
            return [r[0] for r in db.execute("SELECT kind FROM events WHERE run_id=? ORDER BY id", (rid,))]

    def evidence(self, rid):
        with self.db.read() as db:
            row = db.execute("SELECT evidence FROM attempts WHERE run_id=? ORDER BY created DESC LIMIT 1", (rid,)).fetchone()
        return json.loads(row[0])

    def test_a_queue_is_shown_and_never_stopped_by_the_watchdog(self):
        rid = self.start()
        self.queued = self.QUEUED
        self.tick(rid, 2)
        self.age(rid, 200 * MINUTE)
        run = self.tick(rid, 3)
        self.assertEqual(self.stops(), [])
        self.assertEqual(run["error"], "排队：等待执行环境资源（执行环境内存不足）")
        self.assertNotIn("2.47", run["error"])
        self.assertEqual(self.evidence(rid)["queued"]["reason"], "low_memory")
        self.assertIn("attempt.queued", self.events(rid))

    def test_the_queue_is_what_the_session_needs_not_an_error(self):   # spec §3.2: 「需要处理」, not a red error line
        rid = self.start()
        self.queued = self.QUEUED
        run = self.tick(rid, 3)
        self.assertEqual(run["attention"]["title"], "排队：等待执行环境资源（执行环境内存不足）")
        self.assertIn("无需操作", run["attention"]["action"])
        self.assertNotIn("2.47", json.dumps(run["attention"], ensure_ascii=False))
        self.queued = None
        self.assertIsNone(self.tick(rid)["attention"])

    def test_the_queue_comes_back_after_another_error(self):   # final review I2: a failed look meanwhile
        rid = self.start()
        self.queued = self.QUEUED
        self.tick(rid, 3)
        self.engine.update(rid, "running", "Worker 暂时不可用")
        self.assertEqual(self.tick(rid)["error"], "排队：等待执行环境资源（执行环境内存不足）")
        self.assertEqual(self.events(rid).count("attempt.queued"), 1)

    def test_a_queue_is_looked_at_less_often(self):   # final review I3: not every second on a Gateway short of memory
        self.engine.poll_backoff = True
        rid = self.start()
        self.queued = self.QUEUED
        self.tick(rid, 3)
        looks = [p for p, _ in self.calls].count("/inspect")
        self.tick(rid, 5)
        self.assertEqual([p for p, _ in self.calls].count("/inspect"), looks)

    def test_the_clock_starts_when_the_queue_ends(self):
        rid = self.start()
        self.queued = self.QUEUED
        self.tick(rid, 2)
        self.age(rid, 200 * MINUTE)
        self.tick(rid)
        self.queued = None   # it started: the native task now reports 31 minutes without progress
        run = self.tick(rid, 2)
        self.assertEqual(self.stops(), [])   # measured from the end of the queue, not from the dispatch
        self.assertIsNone(run["error"])
        self.assertTrue(self.evidence(rid)["queued"]["until"])
        self.assertIn("attempt.dequeued", self.events(rid))

    def test_never_started_is_dispatched_again_once(self):
        rid = self.start()
        self.tick(rid, 2)
        self.never = 1
        self.tick(rid, 2)
        with self.db.read() as db:
            rows = db.execute("SELECT number, status, evidence FROM attempts WHERE run_id=? ORDER BY number", (rid,)).fetchall()
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(json.loads(rows[0]["evidence"]).get("transient"), "never_started")
        self.assertIn("attempt.retry", self.events(rid))

    def test_never_started_does_not_use_the_retry_budget(self):   # D60 §3.4: retries off, still dispatched again
        rid = self.start({"enabled": True, "max_cycles": 1, "max_retries": 0})
        self.tick(rid, 2)
        self.never = 1
        self.tick(rid, 2)
        self.assertEqual([a.get("transient") for a in self.attempts(rid)], ["never_started", None])
        self.assertEqual(self.budget(rid)["retries"]["used"], 0)

    def test_the_third_never_started_in_a_row_goes_to_the_coordinator(self):
        rid = self.start({"enabled": True, "max_cycles": 1})
        self.tick(rid, 2)
        self.never = 3
        self.tick(rid, 8)
        attempts = self.attempts(rid)
        self.assertEqual([a.get("transient") for a in attempts], ["never_started", "never_started", None])
        self.assertEqual(attempts[2].get("never_started"), 2)
        with self.db.read() as db:
            self.assertTrue(db.execute("SELECT 1 FROM recoveries WHERE run_id=?", (rid,)).fetchone())

    def test_after_a_never_started_the_one_automatic_retry_is_still_there(self):
        rid = self.start()
        self.tick(rid, 2)
        self.never = 1
        self.tick(rid, 2)   # dispatched again; now it starts and its ACP process dies
        self.then = {"native": {"done": True, "error": "ACP process exited", "result": ""}, "evidence": {}, "handoff": ""}
        self.tick(rid, 2)
        self.assertEqual([a.get("transient") for a in self.attempts(rid)][:2], ["never_started", "runtime_exited"])

    def test_a_resumed_run_that_never_started_goes_to_the_coordinator(self):   # it had begun: not a lack of resources
        rid = self.start({"enabled": True, "max_cycles": 1})
        self.queued = {**self.QUEUED, "resuming": True}
        self.tick(rid, 3)
        self.queued, self.never = None, 1
        self.tick(rid, 2)
        first = self.attempts(rid)[0]
        self.assertIsNone(first.get("transient"))
        self.assertTrue(first["queued"]["until"])
        self.assertNotIn("attempt.retry", self.events(rid))

    def test_the_queue_is_not_counted_in_the_time_budget(self):
        rid = self.start()
        self.queued = self.QUEUED
        self.tick(rid, 3)
        with self.db.transaction() as db:   # it waited 100 minutes
            row = db.execute("SELECT id, evidence FROM attempts WHERE run_id=? AND status='running'", (rid,)).fetchone()
            evidence = json.loads(row["evidence"])
            evidence["queued"]["since"] -= 100 * MINUTE
            db.execute("UPDATE attempts SET evidence=? WHERE id=?", (json.dumps(evidence), row["id"]))
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (100 * MINUTE, rid))
        self.queued = None
        self.tick(rid)
        self.assertLess(self.budget(rid)["minutes"]["used"], 5)

    def test_the_watchdog_still_acts_after_a_queue(self):   # final review: the reverse of the clock test
        rid = self.start()
        self.queued = self.QUEUED
        self.tick(rid, 3)
        self.queued = None
        self.tick(rid)   # the queue ends
        with self.db.transaction() as db:   # …40 minutes ago, and the task has made no progress since
            row = db.execute("SELECT id, evidence FROM attempts WHERE run_id=? AND status='running'", (rid,)).fetchone()
            evidence = json.loads(row["evidence"])
            evidence["queued"]["until"] -= 40 * MINUTE
            db.execute("UPDATE attempts SET evidence=?, created=created-? WHERE id=?",
                       (json.dumps(evidence), 60 * MINUTE, row["id"]))
        self.tick(rid, 2)
        self.assertTrue(self.stops())


class McpQueueTests(unittest.TestCase):
    def test_the_hosts_numbers_stay_out_of_a_coding_agents_view(self):   # final review minor: detail only for administrators
        from octopus import mcp
        events = [{"id": 1, "kind": "attempt.queued", "created": 1.0,
                   "payload": {"cause": "low_memory", "detail": "only 2.47 GB available, need 2.50 GB", "reason": "排队：…"}}]
        recent = mcp.compact_status({"id": "run-1", "status": "running", "tasks": []}, events)["events"]
        self.assertEqual(recent[0]["payload"], {"cause": "low_memory", "reason": "排队：…"})


if __name__ == "__main__":
    unittest.main()
