"""Slow Sessions do not stall others; a Session is never advanced twice at once; unavailability backs off."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from octopus.db import Database, dumps
from octopus.engine import Engine
from octopus.security import Error


def seed(db, *run_ids):
    now = time.time()
    with db.transaction() as c:
        c.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u','u','x',1,1,?)", (now,))
        c.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w','w','u',?)",
                  (dumps({"url": "http://127.0.0.1:1", "token": "t" * 40, "capabilities": {}}),))
        for rid in run_ids:
            c.execute("INSERT INTO runs(id,owner_id,worker_id,snapshot,input,source,status,created,updated) "
                      "VALUES(?,?,?,?,?,?,'preparing',?,?)", (rid, "u", "w", "{}", "goal", dumps({"type": "new"}), now, now))


def status(db, rid):
    with db.read() as c:
        return c.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()[0]


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name))
        self.release = threading.Event()
        self.active, self.peak, self.lock = {}, {}, threading.Lock()

    def tearDown(self):
        self.release.set()
        self.temporary.cleanup()

    def request(self, config, path, body=None, timeout=35):
        rid = body["run_id"]
        with self.lock:
            self.active[rid] = self.active.get(rid, 0) + 1
            self.peak[rid] = max(self.peak.get(rid, 0), self.active[rid])
        try:
            if rid == "run-slow":
                self.release.wait(10)
            return {"native_parent": "dashboard:" + rid, "workspace": "/tmp/" + rid}
        finally:
            with self.lock:
                self.active[rid] -= 1

    def test_slow_session_does_not_block_others(self):
        seed(self.db, "run-slow", "run-fast")
        engine = Engine(self.db, request=self.request, workers=2)
        engine.tick(wait=False)
        deadline = time.time() + 5
        while status(self.db, "run-fast") != "ready" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(status(self.db, "run-fast"), "ready")
        self.assertEqual(status(self.db, "run-slow"), "preparing")
        self.release.set()
        self.assertTrue(engine.drain(5))
        self.assertEqual(status(self.db, "run-slow"), "ready")

    def test_same_session_is_never_advanced_concurrently(self):
        seed(self.db, "run-slow")
        engine = Engine(self.db, request=self.request, workers=4)
        engine.tick(wait=False)
        time.sleep(0.2)
        engine.tick(wait=False)
        engine.tick(wait=False)
        self.release.set()
        self.assertTrue(engine.drain(5))
        self.assertEqual(self.peak["run-slow"], 1)

    def test_drain_waits_for_inflight_advance(self):
        seed(self.db, "run-slow")
        engine = Engine(self.db, request=self.request, workers=1)
        engine.tick(wait=False)
        self.assertFalse(engine.drain(0.2))
        self.release.set()
        self.assertTrue(engine.drain(5))

    def test_worker_unavailable_keeps_status_and_backs_off(self):
        seed(self.db, "run-a")
        now, calls = [100.0], []

        def unavailable(config, path, body=None, timeout=35):
            calls.append(now[0])
            raise Error(503, "worker_unavailable", "Worker 未返回可核实回执")

        engine = Engine(self.db, request=unavailable, workers=1, retry_unavailable=2.0, clock=lambda: now[0])
        engine.tick()
        self.assertEqual(status(self.db, "run-a"), "preparing")
        engine.tick()
        self.assertEqual(len(calls), 1)
        now[0] += 2.1
        engine.tick()
        self.assertEqual(len(calls), 2)
        now[0] += 2.1
        engine.tick()
        self.assertEqual(len(calls), 2)  # second failure doubles the delay to 4s
        now[0] += 2.0
        engine.tick()
        self.assertEqual(len(calls), 3)

    def test_legacy_mode_still_blocks_on_prepare_failure(self):
        seed(self.db, "run-a")

        def unavailable(config, path, body=None, timeout=35):
            raise Error(503, "worker_unavailable", "Worker 未返回可核实回执")

        Engine(self.db, request=unavailable).tick()
        self.assertEqual(status(self.db, "run-a"), "blocked")


class FinalReviewEngineTests(unittest.TestCase):
    """I1: reconcile must not act on in-flight Sessions. I2: bounded submission, fresh rows, cancellable drain."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name))
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.temporary.cleanup()

    def test_reconcile_skips_sessions_that_are_being_advanced(self):
        from octopus import recovery
        seed(self.db, "run-x")
        now = time.time()
        with self.db.transaction() as c:
            c.execute("UPDATE runs SET status='running', native_parent='p', snapshot=? WHERE id='run-x'",
                      (dumps({"recovery": {"enabled": True, "max_cycles": 2}}),))
            c.execute("INSERT INTO tasks(run_id,id,ordinal,spec,status,current_attempt) VALUES('run-x','code',0,'{}','blocked','a1')")
            c.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,error,created,updated) "
                      "VALUES('a1','run-x','code',1,'{}','blocked',?,'boom',?,?)", (dumps({"failure_type": "tests"}), now, now))
        recovery.reconcile(self.db, skip={"run-x"})
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM recoveries").fetchone()[0], 0)
        recovery.reconcile(self.db)
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM recoveries").fetchone()[0], 1)

    def test_tick_passes_in_flight_sessions_to_reconcile(self):
        from unittest.mock import patch
        seed(self.db, "run-slow")
        engine = Engine(self.db, request=lambda c, p, b=None, timeout=35: (self.release.wait(10), {"native_parent": "x", "workspace": "/w"})[1], workers=2)
        engine.tick(wait=False)
        time.sleep(0.2)
        seen = []
        with patch("octopus.recovery.reconcile", side_effect=lambda db, skip=frozenset(): seen.append(set(skip))):
            engine.tick(wait=False)
        self.release.set()
        self.assertTrue(engine.drain(5))
        self.assertEqual(seen, [{"run-slow"}])

    def test_non_blocking_tick_submits_only_free_workers(self):
        seed(self.db, "run-a", "run-b", "run-c")
        engine = Engine(self.db, request=lambda c, p, b=None, timeout=35: (self.release.wait(10), {"native_parent": "x", "workspace": "/w"})[1], workers=1)
        self.assertEqual(len(engine.tick(wait=False)), 1)
        self.assertEqual(engine.tick(wait=False), [])
        self.release.set()
        self.assertTrue(engine.drain(5))

    def test_queued_session_sees_a_stop_requested_while_waiting(self):
        seed(self.db, "run-a", "run-b")
        calls = []

        def request(config, path, body=None, timeout=35):
            calls.append((body["run_id"], path))
            if body["run_id"] == "run-a" and path == "/prepare":
                with self.db.transaction() as c:  # the user stops run-b while run-a is being prepared
                    c.execute("UPDATE runs SET desired='stop' WHERE id='run-b'")
                return {"native_parent": "x", "workspace": "/w"}
            if path == "/control":
                return {"status": "stopped", "lifecycle": "open"}
            return {"native_parent": "x", "workspace": "/w"}

        Engine(self.db, request=request, workers=1).tick()
        self.assertIn(("run-b", "/control"), calls)
        self.assertNotIn(("run-b", "/prepare"), calls)

    def test_drain_cancels_and_never_starts_waiting_work(self):
        seed(self.db, "run-a", "run-b")
        calls = []

        def request(config, path, body=None, timeout=35):
            calls.append(body["run_id"])
            self.release.wait(10)
            return {"native_parent": "x", "workspace": "/w"}

        engine = Engine(self.db, request=request, workers=1)
        engine.tick(wait=False)
        time.sleep(0.1)
        self.assertFalse(engine.drain(0.2))
        self.release.set()
        time.sleep(0.3)
        self.assertEqual(calls, ["run-a"])

if __name__ == "__main__":
    unittest.main()
