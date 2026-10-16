"""D44: a Session may run only once its plan is confirmed — planning finishes as before, then it waits before its first
working task; a confirm or a continue starts it. Without `admission` the platform default (auto) applies."""
import copy
import json
import tempfile
import unittest

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from octopus.server import required_scope
from tests.old_worker import as_old_worker

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "deliver", "acceptance": ["完成"]},
         {"id": "review", "name": "审查", "tool": "kiro", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]}]
PLAN = """```json
{"intent": "补全", "summary": "需求", "name": "计划", "steps": [
 {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "deliver", "acceptance": ["完成"]}]}
```"""


class ConfirmationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            other = create_user(db, "other", "Fixture-password-123!", False)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            self.other = authenticate(db, issue_token(db, other["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','W',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture", "capabilities": {}})))
        self.bodies, self.failing, self.decisions = {}, set(), []
        self.engine = Engine(self.db, self.request)
        self.requests = 0

    def start(self, steps=STEPS, **body):
        self.requests += 1
        return self.service.start(self.actor, {"request_id": f"confirm-fixture-{self.requests}", "input": "实现并验证",
                                               "source": {"type": "new"}, "worker_id": "worker",
                                               **({"steps": steps} if steps else {}), **body})["id"]

    def request(self, config, path, body, **kwargs):
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/w"}
        if path == "/dispatch":
            self.bodies.setdefault(body["attempt_id"], copy.deepcopy(body))
            return {"status": "running", "native_id": body["attempt_id"][-8:], "context_hash": "h"}
        as_old_worker(path)
        record = self.bodies[body["attempt_id"]]
        spec = record["spec"]
        result = {"native": {"done": True, "result": PLAN if spec["id"] == "plan" else "完成"},
                  "evidence": {"actual_backend": spec["tool"], "handoff_path": "/w/handoff.md"}, "handoff": "交接"}
        if record.get("coordination"):
            result["decision"] = dumps(self.decisions.pop(0))
            return result
        number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == spec["id"])
        failed = (spec["id"], number) in self.failing
        result["outcome"] = dumps({"decision": "changes_requested" if failed else "pass", "summary": spec["id"] + " 的结论",
                                   "failure_type": "implementation" if failed else "none", "findings": []})
        return result

    def tick(self, rid, count=30):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def dispatched(self, task=None):
        return [b["spec"]["id"] for b in self.bodies.values() if not b.get("coordination") and (task is None or b["spec"]["id"] == task)]

    def confirm(self, rid, actor=None, request="confirm-0000001"):
        run = self.service.run(self.actor, rid)
        return self.service.operate(actor or self.actor, rid, "confirm", {"request_id": request, "expected_version": run["version"]})

    def snapshot(self, rid):
        with self.db.read() as db:
            return json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])


class ConfirmationTests(ConfirmationCase):
    def test_a_confirmed_plan_runs_to_completion(self):
        rid = self.start(admission="confirm")
        run = self.tick(rid)
        self.assertEqual(run["status"], "awaiting_confirmation")
        self.assertEqual(self.dispatched(), [])
        self.assertEqual(run["attention"]["title"], "计划待确认")
        self.confirm(rid)
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.snapshot(rid)["confirmed"]["via"], "confirm")
        self.assertEqual(self.snapshot(rid)["confirmed"]["by"], "owner")

    def test_an_agent_planned_session_plans_then_waits(self):
        rid = self.start(steps=None, admission="confirm", plan="agent")
        run = self.tick(rid)
        self.assertEqual(run["status"], "awaiting_confirmation")
        self.assertEqual(self.dispatched(), ["plan"])   # planning ran; its tasks wait
        self.assertEqual([t["id"] for t in run["tasks"]], ["plan", "code"])
        self.confirm(rid)
        self.assertEqual(self.tick(rid)["status"], "completed")

    def test_a_rework_after_confirmation_does_not_wait_again(self):
        self.failing = {("review", 1)}
        self.decisions = [{"action": "rework", "target_task_id": "code", "reason": "实现有缺陷", "instructions": "修复"}]
        rid = self.start(admission="confirm", recovery={"enabled": True, "max_cycles": 2})
        self.tick(rid)
        self.confirm(rid)
        run = self.tick(rid, 60)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.dispatched("code"), ["code", "code"])

    def test_without_admission_the_platform_default_applies(self):
        rid = self.start()
        self.assertEqual(self.tick(rid)["status"], "completed")
        self.service.default_admission = "confirm"
        rid = self.start()
        self.assertEqual(self.tick(rid)["status"], "awaiting_confirmation")

    def test_continuing_a_waiting_plan_confirms_it(self):
        rid = self.start(admission="confirm")
        run = self.tick(rid)
        self.service.operate(self.actor, rid, "continue", {"request_id": "confirm-continue-1", "expected_version": run["version"],
                                                           "task_id": "code", "override": {"prompt": "实现（补充说明）"}})
        self.assertEqual(self.tick(rid)["status"], "completed")
        self.assertEqual(self.snapshot(rid)["confirmed"]["via"], "continue")

    def test_confirm_outside_the_wait_is_refused(self):
        rid = self.start(admission="confirm")
        with self.assertRaises(Error) as caught:
            self.confirm(rid)   # not yet waiting: still preparing
        self.assertEqual(caught.exception.status, 409)

    def test_only_those_who_may_execute_confirm(self):
        self.assertEqual(required_scope("POST", "/v1/runs/run-x/confirm"), "execute")
        rid = self.start(admission="confirm")
        self.tick(rid)
        with self.assertRaises(Error):
            self.confirm(rid, actor=self.other)
        self.assertEqual(self.service.run(self.actor, rid)["status"], "awaiting_confirmation")

    def test_admission_is_checked(self):
        with self.assertRaises(Error) as caught:
            self.start(admission="later")
        self.assertIn("admission 须为 confirm 或 auto", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
