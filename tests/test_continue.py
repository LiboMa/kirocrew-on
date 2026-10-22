"""D46 P5a, the platform's side: a rework the platform arranges (the Coordinator's rework of a task, the re-check and
re-run after it) continues the task's previous native conversation; first runs, the user's continue, a switched tool, a
new effort and an automatic retry start afresh."""
import json
import unittest

from octopus import mcp
from octopus.db import dumps
from octopus.engine import continuation
from octopus.security import Error
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

    lost = False
    refused = None   # what a Worker answers when it cannot continue the conversation

    def request(self, config, path, body, **kwargs):
        if path == "/dispatch" and body.get("continue") and self.lost:
            self.bodies.setdefault(body["attempt_id"], dict(body))
            self.reconciling = body
            self.lost = False
            return {"status": "dispatch_unknown", "error": "回执未确认"}   # the receipt was lost
        if path == "/inspect" and getattr(self, "reconciling", None) and body["attempt_id"] == self.reconciling["attempt_id"]:
            resumed, self.reconciling = self.reconciling, None   # what the Worker's reconcile answers
            return {"status": "running", "native_id": body["attempt_id"][-8:], "reconciled": True,
                    "continued": resumed["continue"]}
        result = super().request(config, path, body, **kwargs)
        if path == "/dispatch" and body.get("continue"):   # what a Worker of D46 P5a answers
            result = {**result, **({"continue_refused": self.refused} if self.refused else {"continued": body["continue"]})}
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
        self.assertIsNotNone(self.bodies[latest["id"]].get("repair"))   # the platform still sees a rework: not vacuous
        self.assertIsNone(self.sent(latest))

    def test_a_reconciled_continuation_keeps_its_mark(self):
        self.lost = True
        rid, _ = self.rework()
        first, second = self.attempts(rid, "code")
        self.assertEqual(json.loads(second["evidence"])["continued"], {"conversation": first["native_id"], "from": first["id"]})

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


    def test_a_refused_continue_is_explained_on_the_timeline(self):
        self.refused = "not_this_session"
        rid, _ = self.rework()
        with self.db.read() as db:
            payloads = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind='attempt.dispatched'",
                                                             (rid,))]
        refused = [p for p in payloads if p.get("continue_refused")]
        self.assertEqual([p["task_id"] for p in refused], ["code", "review"])   # both reworked tasks asked to continue
        for event in refused:
            self.assertEqual(event.get("reason"), "未能续接上一轮对话（not_this_session），本轮从新对话开始")

class ContinuedChecksTests(ContinueCase):
    def continued(self, rid, request, execution):
        run = self.service.run(self.actor, rid)
        self.service.operate(self.actor, rid, "continue", {"request_id": request, "expected_version": run["version"],
                                                           "task_id": "code", "execution": execution})
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed", run.get("error"))
        return run["tasks"][0]["spec"]

    def test_checks_given_in_a_continue_are_required(self):   # D57 §5.3: what the user writes in a continue must pass
        rid = self.start()
        self.assertEqual(self.tick(rid)["status"], "completed")
        with self.db.transaction() as db:   # the task as a planner gave it: an advisory check
            spec = json.loads(db.execute("SELECT spec FROM tasks WHERE run_id=? AND id='code'", (rid,)).fetchone()[0])
            spec.update(verify=["npm test"], verify_required=[], origin="planner")
            db.execute("UPDATE tasks SET spec=? WHERE run_id=? AND id='code'", (dumps(spec), rid))
        spec = self.continued(rid, "continue-checks-1", {"prompt": "实现（补充说明）"})
        self.assertEqual((spec["verify"], spec["verify_required"], spec["origin"]), (["npm test"], [], "planner"))
        spec = self.continued(rid, "continue-checks-2", {"verify": ["npm test", "npm run lint"]})
        self.assertEqual(spec["verify_required"], ["npm test", "npm run lint"])   # all of them, unmarked
        spec = self.continued(rid, "continue-checks-3", {"verify": ["npm test", "npm run lint"], "verify_required": ["npm run lint"]})
        self.assertEqual(spec["verify_required"], ["npm run lint"])
        spec = self.continued(rid, "continue-checks-4", {"verify_required": []})   # the user may make them all advisory
        self.assertEqual((spec["verify"], spec["verify_required"]), (["npm test", "npm run lint"], []))
        for number, execution in enumerate(({"verify_required": ["make check"]},
                                            {"verify": ["npm test"], "verify_required": ["npm run lint"]},
                                            {"verify": ["npm test"], "verify_required": "npm test"})):
            with self.subTest(execution=execution), self.assertRaises(Error) as caught:
                self.service.operate(self.actor, rid, "continue", {
                    "request_id": f"continue-checks-bad-{number}", "expected_version": self.service.run(self.actor, rid)["version"],
                    "task_id": "code", "execution": execution})
            self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.service.run(self.actor, rid)["tasks"][0]["spec"]["verify_required"], [])


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

    def test_work_that_fell_short_is_continued(self):
        for kind in ("implementation", "contract", "gate", "input"):
            with self.subTest(kind):
                previous = {**self.PREVIOUS, "evidence": json.dumps({"failure_type": kind})}
                self.assertEqual(self.rule(previous=previous)["conversation"], "aaaa1111")

    def test_a_chain_keeps_its_first_conversation(self):
        previous = {**self.PREVIOUS, "native_id": "bbbb2222", "evidence": json.dumps({"continued": {"conversation": "aaaa1111"}})}
        self.assertEqual(self.rule(previous=previous)["conversation"], "aaaa1111")

    def test_what_starts_afresh(self):
        cases = {"no rework": dict(repair=None), "no previous run": dict(previous={}),
                 "no native id": dict(previous={**self.PREVIOUS, "native_id": None}),
                 "another tool": dict(spec={**self.SPEC, "tool": "codex"}), "another model": dict(spec={**self.SPEC, "model": "m"}),
                 "another effort": dict(spec={**self.SPEC, "effort": "high"}),
                 "an explicit effort": dict(previous={**self.PREVIOUS, "spec": json.dumps({**self.SPEC, "effort": "high"})},
                                            spec={**self.SPEC, "effort": "high"}),   # /continue cannot set it
                 "switched": dict(attempt={**self.ATTEMPT, "evidence": json.dumps({"switched_from": {"tool": "codex"}})}),
                 "automatic retry": dict(attempt={**self.ATTEMPT, "evidence": json.dumps({"transient_retry_of": "x"})}),
                 "user continue since": dict(changed=True),
                 # a conversation that broke is not resumed: only one whose work fell short (BACKLOG D46 P5a)
                 "a native error": dict(previous={**self.PREVIOUS, "evidence": json.dumps({"failure_type": "native_error"})}),
                 "a watchdog stop": dict(previous={**self.PREVIOUS, "evidence": json.dumps(
                     {"failure_type": "native_error", "watchdog": {"reason": "31 分钟没有进展"}})}),
                 "a stopped run": dict(previous={**self.PREVIOUS, "evidence": json.dumps({"failure_type": "native_stopped"})}),
                 "an expired login": dict(previous={**self.PREVIOUS, "evidence": json.dumps({"failure_type": "auth"})}),
                 "the wrong backend": dict(previous={**self.PREVIOUS, "evidence": json.dumps({"failure_type": "tool"})})}
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.rule(**kwargs))


if __name__ == "__main__":
    unittest.main()
