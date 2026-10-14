"""D37 §3–§9: the Engine asks the Worker for each code task's candidate and runs its checks before accepting it, binds a
review to the latest candidate, sends gate failures through recovery, and leaves old Sessions and old Workers alone."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.db import dumps
from octopus.engine import Engine
from octopus.security import Error
from octopus.worker import Worker
from tests.test_teams_workers import TeamsWorkersCase

C1, C0, BASE = "c" * 40, "0" * 40, "b" * 40
IMPLEMENT = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["可用"],
             "verify": ["npm test"]}
REVIEW = {"id": "review", "name": "审查", "tool": "claude", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]}


class FakeWorker:
    """The Worker as the Engine sees it; each test changes the answers it needs."""

    def __init__(self):
        self.calls, self.dispatched = [], {}
        self.candidate = lambda body: {"sha": C1, "parent": body.get("parent", BASE), "changed": True, "files": 1,
                                       "insertions": 2, "deletions": 0, "rewritten": False, "secrets": []}
        self.check = lambda body: {"head": body["expected"], "clean": True, "changed_files": []}
        self.verify = [{"state": "running", "passed": None, "failed": None, "results": []},
                       {"state": "done", "passed": True, "failed": None,
                        "results": [{"command": "npm test", "exit": 0, "seconds": 1.0, "tail": "ok"}]}]
        self.outcomes = {}

    def __call__(self, config, path, body=None, **kwargs):
        self.calls.append((path, body))
        if path == "/prepare":
            return {"native_parent": "octopus-x", "workspace": "/w/x"}
        if path == "/dispatch":
            self.dispatched[body["attempt_id"]] = body
            return {"status": "running", "native_id": "n" + body["attempt_id"][-7:], "context_hash": "h"}
        if path == "/inspect":
            task = self.dispatched[body["attempt_id"]]["spec"]["id"]
            outcome = self.outcomes.get(task, {"decision": "pass", "summary": "完成"})
            return {"status": "running", "native": {"done": True, "result": "完成", "error": ""},
                    "evidence": {"actual_backend": "claude"}, "handoff": "交接", "outcome": dumps(outcome)}
        if path == "/candidate":
            return self.candidate(body) if body["mode"] == "commit" else self.check(body)
        if path == "/verify/start":
            return {"job": "job-" + body["attempt_id"]}
        if path == "/verify/status":
            return self.verify.pop(0) if len(self.verify) > 1 else self.verify[0]
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        raise AssertionError(path)

    def paths(self):
        return [path for path, _ in self.calls]


class EngineGateTests(TeamsWorkersCase):
    def session(self, steps, recovery=False, request="start-0000000001"):
        return self.service.start(self.c, {"request_id": request, "input": "研发需求与真实验证", "source": {"type": "new"},
                                           "team_id": "team-p", "recovery": {"enabled": recovery, "max_cycles": 2},
                                           "steps": steps})["id"]

    def run_engine(self, worker, ticks=12):
        engine = Engine(self.db, request=worker)
        for _ in range(ticks):
            engine.tick()
        return engine

    def attempt(self, rid, task):
        with self.db.read() as db:
            row = db.execute("SELECT a.* FROM attempts a JOIN tasks t ON t.current_attempt=a.id WHERE t.run_id=? AND t.id=?",
                             (rid, task)).fetchone()
        return dict(row), json.loads(row["evidence"] or "{}")

    def gates(self, evidence):
        return {g["name"]: g["status"] for g in evidence.get("gates", [])}

    def test_new_sessions_use_the_third_contract(self):
        self.assertEqual(self.snapshot(self.session([IMPLEMENT]))["execution_contract"], 3)

    def snapshot(self, rid):
        with self.db.read() as db:
            return json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])

    def test_a_code_task_gets_a_candidate_and_its_checks_run(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT])
        self.run_engine(worker)
        paths = [p for p in worker.paths() if p != "/prepare"]
        self.assertEqual(paths[:6], ["/dispatch", "/inspect", "/candidate", "/verify/start", "/verify/status", "/verify/status"])
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual(attempt["status"], "accepted")
        self.assertEqual(self.gates(evidence), {"native_completed": "pass", "identity_match": "pass", "handoff_present": "pass",
                                                "outcome_valid": "pass", "outcome_pass": "pass", "diff_present": "pass",
                                                "no_secrets": "pass", "checks_pass": "pass"})
        self.assertEqual(evidence["candidate"]["sha"], C1)
        self.assertEqual(evidence["check_results"][0]["command"], "npm test")
        message = [b for p, b in worker.calls if p == "/candidate"][0]["message"]
        self.assertIn("实现", message)

    def test_failing_checks_send_the_task_back_through_recovery(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT], recovery=True)
        worker.verify = [{"state": "done", "passed": False, "failed": 0,
                          "results": [{"command": "npm test", "exit": 1, "seconds": 2.0, "tail": "1 failing"}]}]
        self.run_engine(worker, ticks=5)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual((evidence["failure_type"], evidence["gate"]), ("gate", "checks_pass"))
        self.assertIn("npm test", attempt["error"])
        with self.db.read() as db:
            context = json.loads(db.execute("SELECT context FROM recoveries WHERE run_id=?", (rid,)).fetchone()["context"])
        self.assertEqual(context["evidence"]["check_results"][0]["tail"], "1 failing")

    def test_an_empty_diff_fails_implement_but_not_test_or_untyped_tasks(self):
        cases = ((IMPLEMENT, "fail"), ({**IMPLEMENT, "kind": "test"}, "skip"),
                 ({k: v for k, v in IMPLEMENT.items() if k != "kind"}, "skip"))
        for index, (step, expected) in enumerate(cases):
            with self.subTest(kind=step.get("kind")):
                worker, rid = FakeWorker(), self.session([step], request=f"start-000000001{index}")
                worker.candidate = lambda body: {"sha": BASE, "parent": BASE, "changed": False, "files": 0, "insertions": 0,
                                                 "deletions": 0, "rewritten": False, "secrets": []}
                self.run_engine(worker)
                _, evidence = self.attempt(rid, "code")
                self.assertEqual(self.gates(evidence)["diff_present"], expected)

    def test_a_secret_fails_no_secrets(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT])
        worker.candidate = lambda body: {"sha": C1, "parent": BASE, "changed": True, "files": 1, "insertions": 1,
                                         "deletions": 0, "rewritten": False, "secrets": ["src/x.js"]}
        self.run_engine(worker)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual((self.gates(evidence)["no_secrets"], evidence["gate"]), ("fail", "no_secrets"))
        self.assertIn("src/x.js", attempt["error"])
        self.assertNotIn("/verify/start", worker.paths())

    def test_a_review_is_bound_to_the_latest_candidate(self):
        for index, (reviewed, expected) in enumerate(((C1, "pass"), (C1[:7], "pass"), (C0, "fail"))):
            with self.subTest(reviewed=reviewed):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], request=f"start-000000002{index}")
                worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": reviewed}
                self.run_engine(worker, ticks=16)
                review = [b for b in worker.dispatched.values() if b["spec"]["id"] == "review"][0]
                self.assertEqual(review["candidate"]["sha"], C1)
                _, evidence = self.attempt(rid, "review")
                self.assertEqual(self.gates(evidence)["review_bound"], expected)

    def test_a_review_that_changes_code_is_not_bound(self):
        worker = FakeWorker()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW])
        worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}
        worker.check = lambda body: {"head": C1, "clean": False, "changed_files": ["src/a.js"]}
        self.run_engine(worker, ticks=16)
        attempt, evidence = self.attempt(rid, "review")
        self.assertEqual(self.gates(evidence)["review_bound"], "fail")
        self.assertIn("审查不应修改代码", attempt["error"])

    def test_stopping_during_checks_ends_them_and_ignores_late_results(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT])
        worker.verify = [{"state": "running", "passed": None, "failed": None, "results": []}]
        engine = Engine(self.db, request=worker)
        for _ in range(5):
            engine.tick()
        self.assertIn("/verify/start", worker.paths())
        self.service.operate(self.c, rid, "stop", {"request_id": "stop-0000000001", "expected_version": self.version(rid)})
        mark = len(worker.calls)
        for _ in range(3):
            engine.tick()
        after = [p for p, _ in worker.calls[mark:]]
        self.assertIn("/control", after)
        self.assertNotIn("/verify/status", after)
        self.assertEqual(self.service.run(self.c, rid)["status"], "stopped")

    def version(self, rid):
        with self.db.read() as db:
            return db.execute("SELECT version FROM runs WHERE id=?", (rid,)).fetchone()["version"]

    def test_old_sessions_and_old_workers_keep_todays_behaviour(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT])
        with self.db.transaction() as db:
            snapshot = self.snapshot(rid)
            snapshot["execution_contract"] = 2
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), rid))
        self.run_engine(worker)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual(attempt["status"], "accepted")
        self.assertNotIn("/candidate", worker.paths())
        self.assertEqual(len(evidence["gates"]), 5)

        worker, rid = FakeWorker(), self.session([IMPLEMENT], request="start-0000000099")

        def old(body):
            raise Error(404, "not_found", "接口不存在")
        worker.candidate = old
        self.run_engine(worker)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual(attempt["status"], "accepted")
        self.assertEqual({k: v for k, v in self.gates(evidence).items() if k in ("diff_present", "no_secrets", "checks_pass")},
                         {"diff_present": "skip", "no_secrets": "skip", "checks_pass": "skip"})
        self.assertNotIn("/verify/start", worker.paths())

    def test_continue_and_recover_do_not_lower_the_contract(self):
        rid = self.session([IMPLEMENT])
        with self.db.transaction() as db:
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) "
                       "VALUES('attempt-x',?,'code',1,'{}','accepted',0,0)", (rid,))
            db.execute("UPDATE tasks SET status='accepted',current_attempt='attempt-x' WHERE run_id=?", (rid,))
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (rid,))
        self.service.operate(self.c, rid, "continue", {"request_id": "continue-000001", "expected_version": self.version(rid),
                                                        "task_id": "code", "recovery": {"enabled": True, "max_cycles": 2}})
        self.assertEqual(self.snapshot(rid)["execution_contract"], 3)


class WorkerReviewTests(unittest.TestCase):
    def test_a_review_is_told_which_commit_to_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = Worker(root / "worker", "x" * 40, crew_home=root / "crew")
            rid = "run-" + "1" * 32

            def native(path, body=None, method=None):
                if path == "/api/chat/slots/octopus-" + rid and body is None:
                    raise gateway_client.GatewayError(404, '{"error":"not found"}')
                if path == "/api/chat/slots":
                    return {"key": "octopus-" + rid}
                return {"id": "abcdef12"} if path == "/api/spawn" else {"ok": True}
            node.native = native
            node.prepare({"run_id": rid, "source": {"type": "new"}})
            attempt = "attempt-" + "3" * 32
            body = {"run_id": rid, "attempt_id": attempt, "input": "需求", "upstream": [], "quality": True,
                    "spec": {"id": "review", "name": "审查", "tool": "claude", "model": "auto", "effort": "", "prompt": "审查"},
                    "contract": {"kind": "review", "acceptance": ["无阻断问题"]}, "candidate": {"sha": C1, "parent": BASE}}
            node.dispatch(body)
            folder = node.path(rid) / "attempts" / attempt
            text = (folder / "instructions.md").read_text()
            for part in (C1, f"git diff {BASE} {C1}", '"reviewed_commit"', "不修改代码"):
                self.assertIn(part, text)
            self.assertEqual(json.loads((folder / "context.json").read_text())["candidate"], {"sha": C1, "parent": BASE})
            with self.assertRaises(Error):
                node.dispatch({**body, "attempt_id": "attempt-" + "4" * 32, "candidate": {"sha": "--output=x", "parent": BASE}})
