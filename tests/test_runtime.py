"""D34 §5: transient native failures retry once without a recovery cycle; an expired login waits instead of failing."""
import json
import unittest

from octopus.db import dumps
from octopus.engine import Engine
from tests.test_teams_workers import TeamsWorkersCase

CANCELLED = {"done": True, "result": "", "error": "cancelled (stop_reason=cancelled): turn cancelled by the runtime — partial result preserved"}


class RuntimeCase(TeamsWorkersCase):
    def setUp(self):
        super().setUp()
        self.calls, self.natives, self.inspected = [], [], 0
        self.outcome, self.after_inspect = dumps({"decision": "pass", "summary": "通过"}), None

    def worker(self, config, path, body, **kwargs):
        self.calls.append(path)
        if path == "/prepare":
            return {"native_parent": "octopus-" + body["run_id"], "workspace": "/w/" + body["run_id"]}
        if path == "/dispatch":
            return {"status": "running", "native_id": "n" + body["attempt_id"][-7:], "context_hash": "h"}
        if path == "/inspect":
            self.inspected += 1
            native = self.natives.pop(0) if self.natives else {"done": True, "result": "完成", "error": ""}
            reply = {"status": "running", "native": native, "evidence": {"actual_backend": "claude"},
                     "handoff": "交接", "outcome": self.outcome}
            if self.after_inspect:
                self.after_inspect()
            return reply
        raise AssertionError(path)

    def session(self, recovery=True):
        return self.service.start(self.c, {"request_id": "start-0000000001", "input": "研发需求与真实验证", "source": {"type": "new"},
                                           "team_id": "team-p", "recovery": {"enabled": recovery, "max_cycles": 2},
                                           "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})["id"]

    def tick(self, times):
        engine = Engine(self.db, request=self.worker)
        for _ in range(times):
            engine.tick()

    def detail(self, rid):
        value = self.service.run(self.c, rid)
        return value, value["tasks"][0]["attempts"]

    def kinds(self, rid):
        with self.db.read() as db:
            return [r["kind"] for r in db.execute("SELECT kind FROM events WHERE run_id=? ORDER BY id", (rid,))]


class TransientTests(RuntimeCase):
    def test_runtime_cancellation_is_retried_once_without_a_recovery_cycle(self):
        rid = self.session()
        self.natives = [CANCELLED]
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "runtime_cancelled")
        self.assertEqual((run["status"], run["recovery"]["used"]), ("completed", 0))
        self.assertIn("attempt.retry", self.kinds(rid))

    def test_a_second_transient_failure_goes_to_recovery(self):
        rid = self.session()
        self.natives = [CANCELLED, CANCELLED]
        self.tick(5)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "blocked"])
        self.assertNotIn("transient", attempts[1]["evidence"])
        self.assertEqual((run["status"], run["recovery"]["used"]), ("diagnosing", 1))

    def test_a_cancellation_after_stop_is_not_retried(self):
        rid = self.session()
        self.natives = [CANCELLED]
        def stop():
            with self.db.transaction() as db:   # the user's stop commits while the result is in flight
                db.execute("UPDATE runs SET desired='stop',status='stopping' WHERE id=?", (rid,))
        self.tick(2)
        self.after_inspect = stop
        self.tick(1)
        run, attempts = self.detail(rid)
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("transient", attempts[0]["evidence"])
        self.assertNotIn("attempt.retry", self.kinds(rid))

    def test_missing_result_with_a_passing_outcome_is_accepted(self):
        rid = self.session()
        self.natives = [{"done": True, "result": "_No response._", "error": ""}]
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], [a["status"] for a in attempts]), ("completed", ["accepted"]))
        self.assertTrue(attempts[0]["evidence"]["result_missing"])

    def test_missing_result_without_outcome_is_retried(self):
        rid = self.session()
        self.natives, self.outcome = [{"done": True, "result": "", "error": ""}], ""
        def restore():
            self.outcome = dumps({"decision": "pass", "summary": "通过"})
        self.after_inspect = restore
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "result_missing")
        self.assertEqual(run["recovery"]["used"], 0)

    def test_an_explicit_blocked_reply_is_not_transient(self):
        rid = self.session(recovery=False)
        self.natives, self.outcome = [{"done": True, "result": "无法继续\nBLOCKED 需要仓库权限", "error": ""}], ""
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], len(attempts)), ("blocked", 1))

    def test_expired_login_is_an_auth_failure_without_automatic_recovery(self):
        rid = self.session()
        self.natives = [{"done": True, "result": "", "error": "kiro_crew.acp.client.AcpError: Your session has expired. Code 401"}]
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], len(attempts)), ("blocked", 1))
        self.assertEqual(attempts[0]["evidence"]["failure_type"], "auth")
        self.assertEqual(run["recovery"]["used"], 0)


if __name__ == "__main__":
    unittest.main()
