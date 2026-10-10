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


if __name__ == "__main__":
    unittest.main()
