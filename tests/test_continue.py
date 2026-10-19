"""D46 P5a, the platform's side: a rework the platform arranges (the Coordinator's rework of a task, the re-check and
re-run after it) continues the task's previous native conversation; first runs, the user's continue, a switched tool, a
new effort and an automatic retry start afresh."""
import json
import unittest

from octopus import mcp
from octopus.db import dumps
from octopus.engine import continuation
from octopus import report
from tests.test_confirmation import ConfirmationCase

REWORK = {"action": "rework", "target_task_id": "code", "reason": "实现有缺陷", "instructions": "修复"}


class ContinueCase(ConfirmationCase):
    def setUp(self):
        super().setUp()
        self.caps({"continue": True})

    def caps(self, capabilities):
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": capabilities}),))

    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        if path == "/dispatch" and body.get("continue"):
            result = {**result, "continued": body["continue"]}   # what a Worker of D46 P5a answers
        return result

    def attempts(self, rid, task):
        with self.db.read() as db:
            return [dict(r) for r in db.execute("SELECT * FROM attempts WHERE run_id=? AND task_id=? ORDER BY number", (rid, task))]

    def sent(self, attempt):
        return self.bodies[attempt["id"]].get("continue")

    def rework(self, failing=(("review", 1),), decisions=(REWORK,)):
        self.failing, self.decisions = set(failing), list(decisions)
        rid = self.start(recovery={"enabled": True, "max_cycles": 3})
        run = self.tick(rid, 80)
        self.assertEqual(run["status"], "completed", run.get("error"))
        return rid, run


class FlowTests(ContinueCase):
    def test_a_coordinators_rework_continues(self):
        rid, _ = self.rework()
        first, second = self.attempts(rid, "code")
        self.assertIsNone(self.sent(first))
        self.assertEqual(self.sent(second), {"conversation": first["native_id"], "from": first["id"]})

    def test_a_recheck_after_rework_continues(self):
        rid, _ = self.rework()
        first, second = self.attempts(rid, "review")
        self.assertEqual(self.sent(second), {"conversation": first["native_id"], "from": first["id"]})

    def test_the_conversation_is_the_first_of_the_chain(self):
        rid, _ = self.rework(failing=(("review", 1), ("review", 2)), decisions=(REWORK, REWORK))
        attempts = self.attempts(rid, "code")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.sent(attempts[2]), {"conversation": attempts[0]["native_id"], "from": attempts[1]["id"]})

    def test_after_a_user_continue_it_starts_afresh(self):
        rid, run = self.rework()
        self.service.operate(self.actor, rid, "continue", {"request_id": "continue-p5-1", "expected_version": run["version"],
                                                           "task_id": "code", "input": "改一下需求：再加一个减法"})
        self.assertEqual(self.tick(rid, 40)["status"], "completed")
        latest = self.attempts(rid, "code")[-1]
        self.assertIsNone(self.sent(latest))

    def test_an_older_worker_is_never_asked(self):
        self.caps({})
        rid, _ = self.rework()
        self.assertTrue(all(self.sent(a) is None for t in ("code", "review") for a in self.attempts(rid, t)))

    def test_the_attempt_says_it_continued(self):
        rid, run = self.rework()
        second = self.attempts(rid, "code")[1]
        evidence = json.loads(second["evidence"])
        self.assertEqual(evidence["continued"]["conversation"], self.attempts(rid, "code")[0]["native_id"])
        code = next(t for t in mcp.compact_status(run, [])["session"]["tasks"] if t["id"] == "code")
        self.assertEqual(code["latest"]["continued"], True)
        text = report.markdown(run)
        self.assertIn("续接上一轮", text)


class RuleTests(unittest.TestCase):
    PREVIOUS = {"id": "attempt-" + "a" * 32, "native_id": "aaaa1111", "spec": json.dumps({"tool": "claude", "model": "auto", "effort": ""}),
                "evidence": "{}"}
    ATTEMPT = {"id": "attempt-" + "b" * 32, "evidence": "{}"}
    SPEC = {"tool": "claude", "model": "auto", "effort": ""}
    REPAIR = {"recovery_id": "r", "instruction": "修复"}

    def rule(self, previous=None, attempt=None, spec=None, repair=REPAIR, changed=False):
        return continuation(self.PREVIOUS if previous is None else previous, attempt or self.ATTEMPT, spec or self.SPEC,
                            repair, changed)

    def test_a_rework_continues_the_previous_conversation(self):
        self.assertEqual(self.rule(), {"conversation": "aaaa1111", "from": self.PREVIOUS["id"]})

    def test_a_chain_keeps_its_first_conversation(self):
        previous = {**self.PREVIOUS, "native_id": "bbbb2222", "evidence": json.dumps({"continued": {"conversation": "aaaa1111"}})}
        self.assertEqual(self.rule(previous=previous)["conversation"], "aaaa1111")

    def test_what_starts_afresh(self):
        cases = {"no rework": dict(repair=None), "no previous run": dict(previous={}),
                 "no native id": dict(previous={**self.PREVIOUS, "native_id": None}),
                 "another tool": dict(spec={**self.SPEC, "tool": "codex"}), "another model": dict(spec={**self.SPEC, "model": "m"}),
                 "another effort": dict(spec={**self.SPEC, "effort": "high"}),
                 "switched": dict(attempt={**self.ATTEMPT, "evidence": json.dumps({"switched_from": {"tool": "codex"}})}),
                 "automatic retry": dict(attempt={**self.ATTEMPT, "evidence": json.dumps({"transient_retry_of": "x"})}),
                 "user continue since": dict(changed=True)}
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.rule(**kwargs))


if __name__ == "__main__":
    unittest.main()
