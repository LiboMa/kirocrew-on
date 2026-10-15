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
                                                "no_secrets": "pass", "artifacts_locked": "skip",   # this fake names no files
                                                "checks_pass": "pass"})
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

    def test_a_worker_refusal_is_a_gate_failure_not_a_hang(self):
        for failing, gate in (("candidate", "diff_present"), ("verify", "checks_pass")):
            with self.subTest(failing=failing):
                worker = FakeWorker()
                rid = self.session([IMPLEMENT], recovery=True, request=f"start-00000003{len(failing)}")
                refusal = Error(409, "candidate_failed", "工作区不在分支 octopus/x（当前 main），未发布")
                if failing == "candidate":
                    worker.candidate = lambda body: (_ for _ in ()).throw(refusal)
                else:
                    original = worker.__call__

                    def refusing(config, path, body=None, **kwargs):
                        if path == "/verify/start":
                            raise refusal
                        return original(config, path, body, **kwargs)
                    worker.__call__ = refusing
                engine = Engine(self.db, request=worker if failing == "candidate" else refusing)
                for _ in range(6):
                    engine.tick()
                attempt, evidence = self.attempt(rid, "code")
                self.assertEqual((attempt["status"], evidence.get("failure_type"), evidence.get("gate")), ("blocked", "gate", gate))
                self.assertIn("工作区不在分支", attempt["error"])
                with self.db.read() as db:
                    self.assertTrue(db.execute("SELECT 1 FROM recoveries WHERE run_id=?", (rid,)).fetchone())

    def accepted(self, rid, rows):
        """Fixture: accepted Attempts with candidates / review bindings, in the order (and at the times) given."""
        with self.db.transaction() as db:
            for number, (task, evidence, updated) in enumerate(rows, 1):
                aid = f"attempt-{rid[-8:]}-{number}"
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,'{}','accepted',?,?,?)", (aid, rid, task, number, dumps(evidence), updated, updated))
                db.execute("UPDATE tasks SET status='accepted',current_attempt=? WHERE run_id=? AND id=?", (aid, rid, task))

    @staticmethod
    def made(sha, ok=True):
        return {"candidate": {"sha": sha, "parent": BASE, "changed": True, "moved": True},
                "gates": [{"name": "checks_pass", "status": "pass" if ok else "fail"}]}

    def test_the_latest_candidate_is_what_the_branch_holds_not_the_task_order(self):
        second = {**IMPLEMENT, "id": "code2", "name": "实现二"}
        rid = self.session([IMPLEMENT, second, REVIEW])
        # code was rerun after code2 was kept: its candidate is the newer one on the branch
        self.accepted(rid, [("code2", self.made("2" * 40), 10.0), ("code", self.made("3" * 40), 20.0)])
        with self.db.read() as db:
            self.assertEqual(Engine._head_candidate(db, rid)["sha"], "3" * 40)

    def test_which_candidate_is_verified(self):
        deliver = {**IMPLEMENT, "id": "deliver", "name": "交付", "kind": "deliver"}
        second = {**IMPLEMENT, "id": "code2", "name": "实现二"}
        reviewed = lambda sha: {"reviewed": sha, "gates": [{"name": "review_bound", "status": "pass"}]}
        cases = [
            ("no review: the latest passing candidate", [IMPLEMENT], [("code", self.made(C1), 1.0)], C1),
            ("its gates failed: nothing", [IMPLEMENT], [("code", self.made(C1, ok=False), 1.0)], None),
            ("delivery after the review counts", [IMPLEMENT, REVIEW, deliver],
             [("code", self.made(C1), 1.0), ("review", reviewed(C1), 2.0), ("deliver", self.made("2" * 40), 3.0)], "2" * 40),
            ("code after the review is unreviewed", [IMPLEMENT, REVIEW, second],
             [("code", self.made(C1), 1.0), ("review", reviewed(C1), 2.0), ("code2", self.made("2" * 40), 3.0)], None),
            ("a review not yet passed: nothing", [IMPLEMENT, REVIEW], [("code", self.made(C1), 1.0)], None),
        ]
        for index, (name, steps, rows, expected) in enumerate(cases):
            with self.subTest(name):
                rid = self.session(steps, request=f"start-00000004{index}")
                self.accepted(rid, rows)
                verified = self.service.run(self.c, rid)["verified"]
                self.assertEqual(verified and verified["sha"], expected)

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

    def test_a_later_task_may_not_change_an_accepted_tasks_files_unless_its_paths_say_so(self):
        worker = FakeWorker()
        names = {"code": ["src/a.py"], "docs": ["src/a.py", "README.md"]}
        shas = {"code": C1, "docs": "d" * 40}
        def candidate(body):
            task = "code" if worker.dispatched[body["attempt_id"]]["spec"]["id"] == "code" else "docs"
            return {"sha": shas[task], "parent": body.get("parent", BASE), "changed": True, "files": len(names[task]),
                    "insertions": 1, "deletions": 0, "rewritten": False, "secrets": [], "names": names[task]}
        worker.candidate = candidate
        docs = {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver",
                "acceptance": ["有文档"], "paths": ["README.md"]}
        rid = self.session([IMPLEMENT, docs])
        self.run_engine(worker, ticks=20)
        _, evidence = self.attempt(rid, "docs")
        self.assertEqual(self.gates(evidence).get("artifacts_locked"), "fail")
        failed = [g for g in evidence["gates"] if g["name"] == "artifacts_locked"][0]
        self.assertIn("「实现」的交付物：src/a.py", failed["detail"])

    def test_a_task_without_paths_is_not_locked(self):
        worker = FakeWorker()
        worker.candidate = lambda body: {"sha": C1, "parent": body.get("parent", BASE), "changed": True, "files": 1,
                                         "insertions": 1, "deletions": 0, "rewritten": False, "secrets": [], "names": ["src/a.py"]}
        deliver = {"id": "deliver", "name": "交付", "tool": "claude", "prompt": "交付", "kind": "deliver", "acceptance": ["可交付"]}
        rid = self.session([IMPLEMENT, deliver])
        self.run_engine(worker, ticks=20)
        _, evidence = self.attempt(rid, "deliver")
        self.assertEqual(self.gates(evidence).get("artifacts_locked"), "skip")

    TEST = {"id": "test", "name": "测试", "tool": "claude", "prompt": "补测试", "kind": "test", "acceptance": ["测试覆盖新行为"],
            "verify": ["npm test"]}

    def gate_worker(self, names=None, changed=None, artifacts=None, baseline=None):
        """A FakeWorker for task chains: per-task candidates (sha, file names, changed), checks that pass leaving
        ``artifacts[task]``, and the baseline run answering ``baseline``; it records what each request carried."""
        worker, jobs = FakeWorker(), {}
        names, changed, artifacts = names or {}, changed or {}, artifacts or {}
        shas = {}
        def task_of(body):
            return worker.dispatched[body["attempt_id"]]["spec"]["id"]
        def call(config, path, body=None, **kwargs):
            if path == "/candidate" and body["mode"] == "commit":
                worker.calls.append((path, body))
                task = task_of(body)
                sha = shas.setdefault(task, (str(len(shas) + 1) * 40)[:40])
                return {"sha": sha, "parent": body.get("parent", BASE), "changed": changed.get(task, True), "files": 1,
                        "insertions": 1, "deletions": 0, "rewritten": False, "secrets": [], "names": names.get(task, [task + ".py"])}
            if path == "/verify/start":
                worker.calls.append((path, body))
                job = ("base-" if body.get("baseline") else "job-") + body["attempt_id"]
                jobs[job] = baseline if body.get("baseline") else {
                    "state": "done", "passed": True, "failed": None, "error": None, "artifacts": artifacts.get(task_of(body), []),
                    "results": [{"command": c, "exit": 0, "seconds": 1.0, "tail": ""} for c in body["commands"]]}
                return {"job": job, **({"baseline": True, "base": BASE} if body.get("baseline") else {})}
            if path == "/verify/status":
                worker.calls.append((path, body))
                return jobs[body["job"]]
            return worker(config, path, body, **kwargs)
        call.worker, call.shas = worker, shas
        return call

    def baseline_answer(self, passed, **last):
        return {"state": "done", "passed": passed, "failed": None if passed else 0, "error": None, "artifacts": [],
                "results": [{"command": "npm test", "exit": 0 if passed else 1, "seconds": 1.0, "tail": "", **last}]}

    def test_a_test_tasks_tests_must_fail_on_the_baseline(self):
        for passed, verdict in ((True, "fail"), (False, "pass")):
            with self.subTest(baseline_passed=passed):
                call = self.gate_worker(baseline=self.baseline_answer(passed))
                rid = self.session([IMPLEMENT, self.TEST], request="start-%016d" % (2 + passed))
                self.run_engine(call, ticks=24)
                attempt, evidence = self.attempt(rid, "test")
                self.assertEqual(self.gates(evidence).get("tests_fail_on_baseline"), verdict)
                self.assertEqual(attempt["status"], "blocked" if verdict == "fail" else "accepted")
                started = [b for p, b in call.worker.calls if p == "/verify/start" and b.get("baseline")]
                # measured from where the test task started: the implementation's candidate
                self.assertEqual(started[0]["baseline"], {"candidate": call.shas["test"], "start": call.shas["code"]})

    def test_tests_for_code_the_session_found_are_not_judged(self):
        call = self.gate_worker(baseline=self.baseline_answer(True))
        rid = self.session([self.TEST])
        self.run_engine(call, ticks=16)
        attempt, evidence = self.attempt(rid, "test")
        skipped = [g for g in evidence["gates"] if g["name"] == "tests_fail_on_baseline"][0]
        self.assertEqual((skipped["status"], attempt["status"]), ("skip", "accepted"))
        self.assertIn("此前没有源码改动", skipped["detail"])
        self.assertFalse([b for p, b in call.worker.calls if p == "/verify/start" and b.get("baseline")])

    def test_a_baseline_that_says_nothing_about_the_tests_is_skipped(self):
        cases = (("install", {**self.TEST, "verify": ["npm ci && npm test"]}, self.baseline_answer(True), "安装依赖"),
                 ("timeout", self.TEST, {**self.baseline_answer(False, exit=None, timeout=True)}, "超时"),
                 ("stopped", self.TEST, {**self.baseline_answer(False, exit=None, stopped=True)}, "已停止"),
                 ("unchanged", self.TEST, self.baseline_answer(True), "没有改动"))
        for number, (name, test, answer, words) in enumerate(cases):
            with self.subTest(name):
                call = self.gate_worker(baseline=answer, changed={"test": name != "unchanged"})
                rid = self.session([IMPLEMENT, test], request="start-%016d" % (10 + number))
                self.run_engine(call, ticks=24)
                attempt, evidence = self.attempt(rid, "test")
                skipped = [g for g in evidence["gates"] if g["name"] == "tests_fail_on_baseline"][0]
                self.assertEqual(skipped["status"], "skip")
                self.assertIn(words, skipped["detail"])
                self.assertEqual(attempt["status"], "accepted")

    def test_a_tasks_files_are_measured_from_where_it_started(self):
        docs = {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver",
                "acceptance": ["有文档"], "paths": ["README.md"]}
        call = self.gate_worker(names={"code": ["src/a.py"], "docs": ["README.md"]})
        rid = self.session([IMPLEMENT, docs])
        self.run_engine(call, ticks=24)
        sent = [b for p, b in call.worker.calls if p == "/candidate" and b["mode"] == "commit"
                and call.worker.dispatched[b["attempt_id"]]["spec"]["id"] == "docs"]
        self.assertEqual(sent[0]["since"], call.shas["code"])
        self.assertNotIn("since", [b for p, b in call.worker.calls if p == "/candidate" and b["mode"] == "commit"][0])

    def test_what_the_checks_left_is_nobodys_deliverable(self):
        first = {"id": "notes", "name": "说明", "tool": "claude", "prompt": "写说明", "kind": "deliver",
                 "acceptance": ["有说明"], "paths": ["docs/"]}
        second = {"id": "readme", "name": "自述", "tool": "claude", "prompt": "写自述", "kind": "deliver",
                  "acceptance": ["有自述"], "paths": ["README.md"]}
        call = self.gate_worker(names={"code": ["src/a.py"], "notes": [".coverage", "docs/a.md"], "readme": [".coverage", "README.md"]},
                                artifacts={"code": [".coverage"]})
        rid = self.session([IMPLEMENT, first, second])
        self.run_engine(call, ticks=30)
        _, evidence = self.attempt(rid, "readme")
        self.assertEqual(self.gates(evidence).get("artifacts_locked"), "pass")

    def test_an_old_worker_skips_the_baseline_gate(self):
        worker = FakeWorker()   # its /verify/start does not echo "baseline": it ran the commands on the candidate
        worker.verify = [{"state": "done", "passed": True, "failed": None,
                          "results": [{"command": "npm test", "exit": 0, "seconds": 1.0, "tail": "ok"}]}]
        test = {"id": "test", "name": "测试", "tool": "claude", "prompt": "补测试", "kind": "test",
                "acceptance": ["测试覆盖新行为"], "verify": ["npm test"]}
        rid = self.session([test])
        self.run_engine(worker, ticks=16)
        attempt, evidence = self.attempt(rid, "test")
        self.assertEqual(self.gates(evidence).get("tests_fail_on_baseline"), "skip")
        self.assertEqual(attempt["status"], "accepted")

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
