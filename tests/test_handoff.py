"""D40: what a task is told of the tasks before it is a short, structured handoff (its conclusion, findings, candidate and
checks, and where the full handoff is), not their raw results and evidence; and long-running Attempts are polled less
often while a user's stop still acts at once."""
import copy
import json
import tempfile
import time
import unittest

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["可运行"]},
         {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]}]


class HandoffCase(unittest.TestCase):
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
        self.calls, self.result, self.outcome, self.done = [], "完成", {"decision": "pass", "summary": "实现了登录并补了测试",
                                                                       "failure_type": "none", "findings": []}, True
        self.evidence = {"actual_backend": "claude", "handoff_path": "/w/attempts/a/handoff.md",
                         "check_results": [{"command": "npm test", "exit": 0, "seconds": 3.0, "tail": "x" * 4000}]}

    def engine(self, **kwargs):
        return Engine(self.db, self.request, **kwargs)

    def start(self, steps=STEPS):
        return self.service.start(self.actor, {"request_id": "handoff-fixture-1", "input": "实现登录", "steps": steps,
                                               "source": {"type": "new"}, "worker_id": "worker"})["id"]

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/w"}
        if path == "/dispatch":
            return {"status": "running", "native_id": body["attempt_id"][-8:], "context_hash": "h"}
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        as_old_worker(path)
        if not self.done:
            return {"native": {"done": False}, "evidence": {"actual_backend": "claude"}}
        return {"native": {"done": True, "result": self.result}, "evidence": copy.deepcopy(self.evidence), "handoff": "交接",
                "outcome": dumps(self.outcome)}

    def dispatched(self, task):
        return [b for p, b in self.calls if p == "/dispatch" and b["spec"]["id"] == task]


class HandoffTests(HandoffCase):
    def run_both(self):
        engine, rid = self.engine(), self.start()
        for _ in range(12):
            engine.tick()
        return self.dispatched("review")[0]["upstream"]

    def test_a_task_gets_the_conclusion_not_the_raw_record(self):
        item = self.run_both()[0]
        self.assertEqual((item["id"], item["name"], item["kind"], item["backend"]), ("code", "实现", "implement", "claude"))
        self.assertEqual((item["decision"], item["summary"]), ("pass", "实现了登录并补了测试"))
        self.assertEqual(item["handoff"], "/w/attempts/a/handoff.md")   # the full handoff, for the details
        self.assertEqual(item["checks"], [{"command": "npm test", "exit": 0}])
        self.assertNotIn("evidence", item)
        self.assertNotIn("result", item)

    def test_a_large_record_stays_a_short_handoff(self):
        self.result = "输出" * 30000
        self.outcome = {**self.outcome, "summary": "很长的结论" * 1400, "findings": ["问题" * 300] * 40,
                        "suggestions": ["建议"] * 40}
        self.evidence["check_results"] = [{"command": "c%d" % i, "exit": 0, "tail": "t" * 4000} for i in range(10)]
        item = self.run_both()[0]
        self.assertLess(len(json.dumps(item, ensure_ascii=False)), 6000)
        self.assertTrue(item["summary"].endswith("…"))

    def test_without_an_outcome_the_result_opens_the_summary(self):
        self.outcome = None
        def request(config, path, body, **kwargs):
            answer = HandoffCase.request(self, config, path, body, **kwargs)
            if path == "/inspect" and isinstance(answer, dict):
                answer.pop("outcome", None)
            return answer
        self.request = request
        engine, rid = Engine(self.db, request), self.start()
        with self.db.transaction() as db:   # a Session from before structured outcomes
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()[0])
            snapshot["execution_contract"] = 1
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), rid))
        self.result = "第一行结论\n" + "细节" * 2000
        for _ in range(12):
            engine.tick()
        item = self.dispatched("review")[0]["upstream"][0]
        self.assertTrue(item["summary"].startswith("第一行结论"))
        self.assertLessEqual(len(item["summary"]), 1501)


class PollingTests(HandoffCase):
    def test_a_long_running_attempt_is_polled_less_often_and_a_stop_acts_at_once(self):
        self.done, now = False, [1000.0]
        engine, rid = self.engine(poll_backoff=True, clock=lambda: now[0]), self.start(STEPS[:1])
        inspects = lambda: sum(1 for p, _ in self.calls if p == "/inspect")
        for _ in range(3):
            engine.tick()                 # prepared, dispatched, seen running
        seen = inspects()
        engine.tick()
        self.assertEqual(inspects(), seen)            # polled again only after the interval
        now[0] += 2.5
        engine.tick()
        self.assertEqual(inspects(), seen + 1)
        with self.db.transaction() as db:   # running for 40 minutes: polled every 20 s
            db.execute("UPDATE attempts SET created=created-2400 WHERE run_id=?", (rid,))
        now[0] += 2.5
        engine.tick()
        now[0] += 10
        engine.tick()
        self.assertEqual(inspects(), seen + 2)
        run = self.service.run(self.actor, rid)
        self.service.operate(self.actor, rid, "stop", {"request_id": "stop-request-0001", "expected_version": run["version"]})
        engine.tick()                     # the stop is not held back by the polling interval
        self.assertIn("/control", [p for p, _ in self.calls])


if __name__ == "__main__":
    unittest.main()
