"""D70 (owner 2026-10-22: 「计划完成后，不要找我确认……把这个垃圾的过程删掉！直接开始！」): no Session waits for its plan
to be confirmed any more — not for a submitted admission: confirm, a personal or Team rule, or a platform setting. A
Session left waiting by an earlier version is started by the Engine. (D44's confirmation is gone; the fixture stays.)"""
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

    def snapshot(self, rid):
        with self.db.read() as db:
            return json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])


class NoConfirmationTests(ConfirmationCase):
    def test_a_submitted_confirm_no_longer_waits(self):   # an older client or Skill may still send it
        rid = self.start(admission="confirm")
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.dispatched(), ["code", "review"])
        self.assertEqual(run["admission"], "auto")

    def test_an_agent_planned_session_runs_its_plan_at_once(self):
        rid = self.start(steps=None, admission="confirm", plan="agent")
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.dispatched(), ["plan", "code"])

    def test_a_repository_rule_no_longer_holds_a_session(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO policies(id,kind,owner_id,team_id,rule,created_by,created) VALUES('p1','admission',?,NULL,?,?,1)",
                       (self.actor["id"], dumps({"repos": "*", "mode": "confirm"}), self.actor["id"]))
        rid = self.start(source={"type": "git", "url": "https://github.com/o/r.git", "ref": "main"})
        self.assertEqual(self.snapshot(rid)["admission"], "auto")

    def test_a_session_left_waiting_starts_by_itself(self):   # an earlier version's 「计划待确认」
        rid = self.start()
        with self.db.transaction() as db:
            snapshot = self.snapshot(rid)
            db.execute("UPDATE runs SET status='awaiting_confirmation', snapshot=? WHERE id=?",
                       (dumps({**snapshot, "admission": "confirm"}), rid))
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.snapshot(rid)["confirmed"]["via"], "platform")
        self.assertIn("run.confirmed", [e["kind"] for e in self.service.events(self.actor, rid)])

    def test_confirm_is_gone(self):
        rid = self.start()
        with self.assertRaises(Error):
            self.service.operate(self.actor, rid, "confirm", {"request_id": "confirm-0000001", "expected_version": 1})

    def test_admission_is_still_checked(self):   # a typo is still the caller's to fix
        with self.assertRaises(Error) as caught:
            self.start(admission="later")
        self.assertIn("admission 须为 confirm 或 auto", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
