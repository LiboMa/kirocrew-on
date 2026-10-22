"""D37 §3–§9: the Engine asks the Worker for each code task's candidate and runs its checks before accepting it, binds a
review to the latest candidate, sends gate failures through recovery, and leaves old Sessions and old Workers alone."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from octopus import gates, gateway_client, recovery
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
        self.rewind = lambda body: {"head": body["expected"], "clean": True, "changed_files": [], "reverted": [], "rewound": 0}
        self.verify = [{"state": "running", "passed": None, "failed": None, "results": []},
                       {"state": "done", "passed": True, "failed": None,
                        "results": [{"command": "npm test", "exit": 0, "seconds": 1.0, "tail": "ok"}]}]
        self.outcomes, self.results = {}, {}   # results: a task's native result (the planner's plan)

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
            return {"status": "running", "native": {"done": True, "result": self.results.get(task, "完成"), "error": ""},
                    "evidence": {"actual_backend": "kiro" if task == "plan" else "claude"}, "handoff": "交接",
                    "outcome": dumps(outcome)}
        if path == "/candidate":
            return {"commit": self.candidate, "rewind": self.rewind}.get(body["mode"], self.check)(body)
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
        """The task's latest judged Attempt: D71 retries a failed task after a diagnosis that decided nothing, so its current
        Attempt may be a new one still running."""
        with self.db.read() as db:
            row = db.execute("SELECT * FROM attempts WHERE run_id=? AND task_id=? AND status NOT IN "
                             "('pending','dispatching','running','dispatch_unknown','waiting_approval') ORDER BY number DESC LIMIT 1",
                             (rid, task)).fetchone()
        return dict(row), json.loads(row["evidence"] or "{}")

    def gates(self, evidence):
        return {g["name"]: g["status"] for g in evidence.get("gates", [])}

    def test_new_sessions_use_the_third_contract(self):
        self.assertEqual(self.snapshot(self.session([IMPLEMENT]))["execution_contract"], 4)   # D46 P3

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
        self.assertEqual(evidence["warnings"], [])   # D57: accepted once its checks pass, with nothing to note
        message = [b for p, b in worker.calls if p == "/candidate"][0]["message"]
        self.assertIn("实现", message)

    def test_a_warn_gate_does_not_block(self):   # D57 §4: recorded and listed, neither blocking nor recovered
        def more(engine, run, config, task, attempt, spec, evidence, *rest):
            evidence["gates"].append(gates.record("diff_present", "warn", "本任务没有改动入库文件"))
            return None, None, False
        worker, rid = FakeWorker(), self.session([IMPLEMENT], recovery=True)
        with patch.object(Engine, "_more_gates", more):
            self.run_engine(worker)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual(attempt["status"], "accepted")
        self.assertEqual(len(evidence["warnings"]), 1)
        self.assertEqual((evidence["warnings"][0]["name"], evidence["warnings"][0]["status"]), ("diff_present", "warn"))
        with self.db.read() as db:
            status = db.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()["status"]
            recoveries = db.execute("SELECT COUNT(*) FROM recoveries WHERE run_id=?", (rid,)).fetchone()[0]
        self.assertNotEqual(status, "diagnosing")
        self.assertEqual((status, recoveries), ("completed", 0))

    def test_a_failing_required_check_still_goes_to_recovery(self):   # D57 §5: the checks the user wrote must pass
        worker, rid = FakeWorker(), self.session([IMPLEMENT], recovery=True)
        worker.verify = [{"state": "done", "passed": False, "failed": 0,
                          "results": [{"command": "npm test", "exit": 1, "seconds": 2.0, "tail": "1 failing", "unrunnable": None}]}]
        self.run_engine(worker, ticks=5)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual(json.loads(attempt["spec"])["verify_required"], ["npm test"])   # steps given by the user
        self.assertEqual((attempt["status"], evidence["failure_type"], evidence["gate"]), ("blocked", "gate", "checks_pass"))
        self.assertEqual(attempt["error"], "检查命令失败：npm test（退出码 1）")
        self.assertEqual(self.gates(evidence)["checks_pass"], "fail")
        with self.db.read() as db:
            context = json.loads(db.execute("SELECT context FROM recoveries WHERE run_id=?", (rid,)).fetchone()["context"])
        self.assertEqual(context["evidence"]["check_results"][0]["tail"], "1 failing")

    def planned(self, step, request="start-0000000077"):
        """An agent-planned Session whose plan has ``step`` after the KiroCrew planner's task (D57: the planner's checks)."""
        worker = FakeWorker()
        worker.results["plan"] = "```json\n" + dumps({"intent": "实现", "summary": "实现", "name": "计划", "steps": [step]}) + "\n```"
        rid = self.service.start(self.c, {"request_id": request, "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": True, "max_cycles": 2}})["id"]
        return worker, rid

    def checks_gate(self, evidence):
        return next(g for g in evidence["gates"] if g["name"] == "checks_pass")

    def no_recovery(self, rid):
        with self.db.read() as db:
            return not db.execute("SELECT 1 FROM recoveries WHERE run_id=?", (rid,)).fetchone()

    def test_a_failing_advisory_check_is_a_warning(self):   # D57 §5: a check the planner wrote on its own only reports
        check = "python3 -m pytest tests/ -q -k 'not slow' --maxfail=1 --disable-warnings -p no:cacheprovider --tb=short -x"
        worker, rid = self.planned({**IMPLEMENT, "verify": [check]})
        worker.verify = [{"state": "done", "passed": False, "failed": 0,
                          "results": [{"command": check, "exit": 1, "seconds": 2.0, "tail": "1 failed", "unrunnable": None}]}]
        self.run_engine(worker, ticks=16)
        attempt, evidence = self.attempt(rid, "code")
        spec = json.loads(attempt["spec"])
        self.assertEqual((spec["origin"], spec["verify_required"]), ("planner", []))
        self.assertEqual(attempt["status"], "accepted")
        gate = self.checks_gate(evidence)
        self.assertEqual((gate["status"], gate["detail"]), ("warn", f"建议检查未通过：{check[:80]}（退出码 1）"))
        self.assertGreater(len(check), 80)
        self.assertIn("checks_pass", [w["name"] for w in evidence["warnings"]])
        self.assertNotIn("gate", evidence)
        self.assertTrue(self.no_recovery(rid))

    def test_an_unrunnable_required_check_is_a_warning(self):   # D57 §5.4: the environment's failure never blocks
        cases = (("recorded", {"tail": "bash: line 1: pytest: command not found", "unrunnable": "命令不存在：pytest"}),
                 ("older worker", {"tail": "bash: line 1: pytest: command not found"}),   # judged from the output
                 ("no output", {"tail": ""}))
        for number, (name, result) in enumerate(cases):
            with self.subTest(name):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": ["pytest -q"]}], recovery=True, request="start-%016d" % (40 + number))
                worker.verify = [{"state": "done", "passed": False, "failed": 0,
                                  "results": [{"command": "pytest -q", "exit": 127, "seconds": 0.1, **result}]}]
                self.run_engine(worker, ticks=8)
                attempt, evidence = self.attempt(rid, "code")
                self.assertEqual(json.loads(attempt["spec"])["verify_required"], ["pytest -q"])
                self.assertEqual(attempt["status"], "accepted")
                gate = self.checks_gate(evidence)
                self.assertEqual(gate["status"], "warn")
                self.assertTrue(gate["detail"].startswith("无法执行（环境）："), gate["detail"])
                self.assertIn("请确认环境，或把这条检查改写成可执行的形式", gate["detail"])
                self.assertIn("命令不存在", gate["detail"])
                self.assertTrue(self.no_recovery(rid))

    def test_a_required_check_is_known_by_its_text_not_its_position(self):
        # the required ones listed in another order than the checks run in (a verify_fixes may reorder them, D57)
        step = {**IMPLEMENT, "verify": ["npm run lint", "npm test", "npm run e2e"], "verify_required": ["npm run e2e", "npm test"]}
        cases = (("advisory fails", (1, 0, 0), "warn", "建议检查未通过：npm run lint（退出码 1）"),
                 ("required fails", (0, 0, 2), "fail", "检查命令失败：npm run e2e（退出码 2）"),
                 ("both fail", (1, 3, 0), "fail", "检查命令失败：npm test（退出码 3）"))
        for number, (name, exits, status, detail) in enumerate(cases):
            with self.subTest(name):
                worker = FakeWorker()
                rid = self.session([step], recovery=True, request="start-%016d" % (50 + number))
                results = [{"command": c, "exit": e, "seconds": 1.0, "tail": "", **({"unrunnable": None} if e else {})}
                           for c, e in zip(step["verify"], exits)]
                worker.verify = [{"state": "done", "passed": False, "failed": exits.index(next(e for e in exits if e)),
                                  "results": results}]
                self.run_engine(worker, ticks=8)
                attempt, evidence = self.attempt(rid, "code")
                gate = self.checks_gate(evidence)
                self.assertEqual((gate["status"], gate["detail"]), (status, detail))
                self.assertEqual(attempt["status"], "blocked" if status == "fail" else "accepted")
                if status == "fail":
                    self.assertEqual((attempt["error"], evidence["gate"]), (detail, "checks_pass"))

    def test_checks_that_did_not_finish_are_judged_by_whether_they_must_pass(self):
        commands = ["npm test", "npm run e2e"]
        timeout = {"command": "npm run e2e", "exit": None, "seconds": 600.0, "tail": "", "timeout": True}
        skipped = {"command": "npm run e2e", "exit": None, "seconds": 0.0, "tail": "", "skipped": "total_timeout"}
        passed = {"command": "npm test", "exit": 0, "seconds": 1.0, "tail": ""}
        cases = (({"results": [passed, timeout]}, ["npm run e2e"], ("fail", "检查命令超时：npm run e2e")),
                 ({"results": [passed, timeout]}, [], ("warn", "建议检查未通过：npm run e2e（超时）")),
                 ({"results": [passed, skipped]}, [], ("warn", "建议检查未通过：npm run e2e（未运行：检查总时限已到）")),
                 ({"results": [], "lost": True}, ["npm test"], ("fail", "检查命令中断（Worker 重启）")),
                 ({"results": [], "lost": True}, [],
                  ("warn", "建议检查未通过：npm test（未运行：检查命令中断（Worker 重启））；"
                           "建议检查未通过：npm run e2e（未运行：检查命令中断（Worker 重启））")),
                 ({"results": [passed]}, ["npm run e2e"], ("fail", "检查命令已停止")),   # stopped before it ran
                 ({"results": [passed, {**passed, "command": "npm run e2e"}], "passed": True}, commands, ("pass", "2 条命令")))
        for status, required, expected in cases:
            with self.subTest(status=status, required=required):
                self.assertEqual(gates.checks_verdict(commands, {"state": "done", "passed": False, **status}, required), expected)

    def test_checks_of_agent_sessions_planned_before_d57_are_advisory(self):
        # what such a task carries: its checks, without verify_required or origin; any other Session's are the user's
        for number, (mode, status) in enumerate((("agent", "warn"), ("manual", "fail"))):
            with self.subTest(mode):
                worker, rid = FakeWorker(), self.session([IMPLEMENT], recovery=True, request="start-%016d" % (60 + number))
                with self.db.transaction() as db:
                    snapshot = self.snapshot(rid)
                    snapshot["match_mode"] = mode
                    spec = {k: v for k, v in IMPLEMENT.items()}
                    spec.update(model="auto", effort="")
                    db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), rid))
                    db.execute("UPDATE tasks SET spec=? WHERE run_id=? AND id='code'", (dumps(spec), rid))
                worker.verify = [{"state": "done", "passed": False, "failed": 0,
                                  "results": [{"command": "npm test", "exit": 1, "seconds": 2.0, "tail": "1 failing"}]}]
                self.run_engine(worker, ticks=8)
                attempt, evidence = self.attempt(rid, "code")
                self.assertNotIn("verify_required", json.loads(attempt["spec"]))
                self.assertEqual(self.checks_gate(evidence)["status"], status)
                self.assertEqual(attempt["status"], "accepted" if status == "warn" else "blocked")

    def test_an_empty_diff_is_a_warning_for_every_kind(self):   # D57 §4: never a failure
        # implement / deliver warn; test and untyped tasks were never asked to change files and skip as before
        cases = ((IMPLEMENT, "warn"), ({**IMPLEMENT, "kind": "deliver"}, "warn"), ({**IMPLEMENT, "kind": "test"}, "skip"),
                 ({k: v for k, v in IMPLEMENT.items() if k != "kind"}, "skip"))
        for index, (step, expected) in enumerate(cases):
            with self.subTest(kind=step.get("kind")):
                worker, rid = FakeWorker(), self.session([step], recovery=True, request=f"start-000000001{index}")
                worker.candidate = lambda body: {"sha": BASE, "parent": BASE, "changed": False, "files": 0, "insertions": 0,
                                                 "deletions": 0, "rewritten": False, "secrets": []}
                self.run_engine(worker)
                attempt, evidence = self.attempt(rid, "code")
                self.assertEqual((attempt["status"], self.gates(evidence)["diff_present"]), ("accepted", expected))
                self.assertEqual(self.gates(evidence)["checks_pass"], "pass")   # judging went on
                if expected == "warn":
                    gate = [g for g in evidence["gates"] if g["name"] == "diff_present"][0]
                    self.assertEqual(gate["detail"], "本任务没有改动入库文件。如果它本来就只产出补丁或报告，可以忽略。")
                    self.assertEqual([w["name"] for w in evidence["warnings"]], ["diff_present"])
                    self.assertNotIn("gate", evidence)

    def test_a_missing_handoff_is_written_by_the_platform(self):   # D57 §4: from the native result, with a warning
        worker, seen = FakeWorker(), []

        def call(config, path, body=None, **kwargs):
            result = worker(config, path, body, **kwargs)
            if path == "/inspect":
                result["handoff"] = ""
                seen.append(result)
            return result
        rid = self.session([IMPLEMENT], recovery=True)
        self.run_engine(call)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual((attempt["status"], self.gates(evidence)["handoff_present"]), ("accepted", "warn"))
        self.assertIs(evidence["handoff_generated"], True)
        first, *rest = seen[0]["handoff"].splitlines()
        self.assertEqual(first, "（平台生成：执行者没有写交接文件）")
        self.assertIn("完成", "\n".join(rest))   # the native result's conclusion
        self.assertEqual([w["name"] for w in evidence["warnings"]], ["handoff_present"])
        self.assertEqual(evidence["handoff_text"], seen[0]["handoff"])   # stored: the platform serves it

    @staticmethod
    def without_handoff(worker, task):
        def call(config, path, body=None, **kwargs):
            result = worker(config, path, body, **kwargs)
            if path == "/inspect" and worker.dispatched[body["attempt_id"]]["spec"]["id"] == task:
                result["handoff"] = ""
            return result
        return call

    def test_the_next_task_is_given_the_generated_handoff(self):   # D57 §4: as it is given a real one
        worker, second = FakeWorker(), {**IMPLEMENT, "id": "code2", "name": "实现二"}
        rid = self.session([IMPLEMENT, second])
        self.run_engine(self.without_handoff(worker, "code"), ticks=20)
        _, evidence = self.attempt(rid, "code")
        self.assertTrue(evidence["handoff_text"].startswith("（平台生成：执行者没有写交接文件）\n"))
        body = next(b for p, b in worker.calls if p == "/dispatch" and b["spec"]["id"] == "code2")
        upstream = next(u for u in body["upstream"] if u["id"] == "code")
        self.assertEqual(upstream["handoff_text"], evidence["handoff_text"])
        self.assertNotIn("handoff", upstream)   # no file on the Worker to point at

    def test_the_recovery_context_carries_the_generated_handoff(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT], recovery=True)
        worker.verify = [{"state": "done", "passed": False, "failed": 0,
                          "results": [{"command": "npm test", "exit": 1, "seconds": 2.0, "tail": "1 failing"}]}]
        self.run_engine(self.without_handoff(worker, "code"), ticks=5)
        with self.db.read() as db:
            context = json.loads(db.execute("SELECT context FROM recoveries WHERE run_id=?", (rid,)).fetchone()["context"])
        self.assertTrue(context["evidence"]["handoff_text"].startswith("（平台生成：执行者没有写交接文件）\n"))

    def test_an_invalid_outcome_is_judged_by_what_it_states(self):   # D57 §4: a malformed file never turns a rejection into a pass
        for index, (outcome, status) in enumerate((({"decision": "changes_requested"}, "blocked"),
                                                   ({"decision": "maybe"}, "accepted"))):
            with self.subTest(outcome=outcome):
                worker, rid = FakeWorker(), self.session([IMPLEMENT], recovery=True, request=f"start-00000005{index}")
                worker.outcomes["code"] = outcome
                self.run_engine(worker, ticks=5 if status == "blocked" else 12)
                attempt, evidence = self.attempt(rid, "code")
                self.assertEqual((attempt["status"], self.gates(evidence)["outcome_valid"]), (status, "warn"))
                with self.db.read() as db:
                    recovered = db.execute("SELECT COUNT(*) FROM recoveries WHERE run_id=?", (rid,)).fetchone()[0]
                if status == "blocked":
                    self.assertEqual((self.gates(evidence)["outcome_pass"], evidence["failure_type"]), ("fail", "implementation"))
                    self.assertIn("changes_requested", attempt["error"])
                    self.assertEqual(recovered, 1)   # an ordinary failure: diagnosed
                    self.assertNotIn("/candidate", worker.paths())
                else:
                    self.assertEqual((self.gates(evidence)["outcome_pass"], recovered), ("skip", 0))
                    self.assertEqual([w["name"] for w in evidence["warnings"]], ["outcome_valid"])

    def test_a_secret_fails_no_secrets(self):
        worker, rid = FakeWorker(), self.session([IMPLEMENT])
        worker.candidate = lambda body: {"sha": C1, "parent": BASE, "changed": True, "files": 1, "insertions": 1,
                                         "deletions": 0, "rewritten": False, "secrets": ["src/x.js"]}
        self.run_engine(worker)
        attempt, evidence = self.attempt(rid, "code")
        self.assertEqual((self.gates(evidence)["no_secrets"], evidence["gate"]), ("fail", "no_secrets"))
        self.assertIn("src/x.js", attempt["error"])
        self.assertNotIn("/verify/start", worker.paths())

    def review_bound(self, evidence):
        return [g for g in evidence["gates"] if g["name"] == "review_bound"][0]

    def test_a_review_is_bound_to_the_latest_candidate(self):
        # D57 §4, §13.4: a report naming another version is a warning; the verdict counts, bound to the candidate
        for index, (reviewed, expected) in enumerate(((C1, "pass"), (C1[:7], "pass"), (C0, "warn"), ("", "warn"))):
            with self.subTest(reviewed=reviewed):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], request=f"start-000000002{index}")
                worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": reviewed}
                self.run_engine(worker, ticks=16)
                review = [b for b in worker.dispatched.values() if b["spec"]["id"] == "review"][0]
                self.assertEqual(review["candidate"]["sha"], C1)
                attempt, evidence = self.attempt(rid, "review")
                self.assertEqual(self.gates(evidence)["review_bound"], expected)
                self.assertEqual((attempt["status"], evidence["reviewed"]), ("accepted", C1))
                self.assertNotIn("rewind", [b["mode"] for p, b in worker.calls if p == "/candidate"])   # nothing to put back
                if expected == "warn":
                    detail = self.review_bound(evidence)["detail"]
                    self.assertIn(f"审查报告写的版本（{reviewed[:12] or '缺失'}）与候选 {C1[:7]} 不符", detail)
                    self.assertIn("审查结论照常采用", detail)
                    self.assertEqual(self.service.run(self.c, rid)["verified"]["sha"], C1)   # counts for a publish

    def test_a_review_whose_changes_cannot_be_put_back_is_not_bound(self):
        # the Worker refuses (HEAD does not descend from the candidate, a branch switched, …): failed as before
        cases = (({"head": "e" * 40, "clean": False, "changed_files": []}, "审查期间会话分支被改动"),
                 ({"head": C1, "clean": False, "changed_files": ["src/a.js"]}, "src/a.js"))
        for index, (check, words) in enumerate(cases):
            with self.subTest(words=words):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], request=f"start-000000005{index}")
                worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}
                worker.check = lambda body, check=check: check

                def refused(body):
                    raise Error(409, "conflict", "会话分支不在候选之上，不能撤回")
                worker.rewind = refused
                self.run_engine(worker, ticks=16)
                attempt, evidence = self.attempt(rid, "review")
                self.assertEqual((self.gates(evidence)["review_bound"], attempt["status"]), ("fail", "blocked"))
                self.assertIn(words, attempt["error"])
                self.assertIn("会话分支不在候选之上", attempt["error"])
                self.assertNotIn("reviewed", evidence)

    def test_a_review_that_changes_code_is_reverted_and_still_counts(self):   # D57 §4, §13.4
        worker = FakeWorker()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], recovery=True)
        worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}
        worker.check = lambda body: {"head": "e" * 40, "clean": False, "changed_files": ["src/a.js"]}
        worker.rewind = lambda body: {"head": body["expected"], "clean": True, "changed_files": [],
                                      "reverted": ["docs/review.md", "src/a.js"], "rewound": 1}
        self.run_engine(worker, ticks=16)
        rewinds = [b for p, b in worker.calls if p == "/candidate" and b["mode"] == "rewind"]
        self.assertEqual([b["expected"] for b in rewinds], [C1])
        attempt, evidence = self.attempt(rid, "review")
        gate = self.review_bound(evidence)
        self.assertEqual((gate["status"], gate["files"]), ("warn", ["docs/review.md", "src/a.js"]))
        self.assertIn("审查改动的已跟踪文件已由平台撤回：docs/review.md、src/a.js", gate["detail"])
        self.assertIn("1 个提交", gate["detail"])
        self.assertIn("审查结论照常采用", gate["detail"])
        self.assertNotIn("代码", gate["detail"])   # many repositories hold documents
        self.assertEqual((attempt["status"], evidence["reviewed"]), ("accepted", C1))
        self.assertEqual([w["name"] for w in evidence["warnings"]], ["review_bound"])
        self.assertEqual(self.service.run(self.c, rid)["verified"]["sha"], C1)   # counts as the review a publish needs
        with self.db.read() as db:
            status = db.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()["status"]
            recoveries = db.execute("SELECT COUNT(*) FROM recoveries WHERE run_id=?", (rid,)).fetchone()[0]
        self.assertEqual((status, recoveries), ("completed", 0))

    def test_a_review_written_into_a_document_is_reverted_and_the_next_task_runs(self):
        """The live case: an Evaluator wrote its review into a tracked document, passed 5/5, and was blocked six times by
        「审查不应修改代码」. Now the document is put back, the verdict counts and the Session goes on."""
        worker, doc = FakeWorker(), "docs/agent-harness-research-review.md"
        deliver = {"id": "deliver", "name": "交付", "tool": "claude", "prompt": "交付", "kind": "deliver", "acceptance": ["已交付"]}
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW, deliver], recovery=True)
        worker.outcomes["review"] = {"decision": "pass", "summary": "5/5 通过", "reviewed_commit": C1}
        state = {"changed": [doc]}
        worker.check = lambda body: {"head": body["expected"], "clean": not state["changed"], "changed_files": state["changed"]}

        def rewind(body):
            reverted, state["changed"] = state["changed"], []
            return {"head": body["expected"], "clean": True, "changed_files": [], "reverted": reverted, "rewound": 0}
        worker.rewind = rewind
        self.run_engine(worker, ticks=30)
        attempt, evidence = self.attempt(rid, "review")
        self.assertEqual((attempt["status"], self.gates(evidence)["review_bound"], evidence["reviewed"]), ("accepted", "warn", C1))
        self.assertIn(doc, self.review_bound(evidence)["detail"])
        self.assertIn("deliver", [b["spec"]["id"] for b in worker.dispatched.values()])
        after = [b for b in worker.dispatched.values() if b["spec"]["id"] == "deliver"][0]
        warned, = {u["id"]: u for u in after["upstream"]}["review"]["warnings"]
        self.assertEqual((warned["name"], warned["files"]), ("review_bound", [doc]))
        self.assertEqual(self.attempt(rid, "deliver")[0]["status"], "accepted")
        self.assertEqual(self.service.run(self.c, rid)["status"], "completed")

    def test_an_old_worker_that_cannot_rewind_fails_the_review_as_before(self):   # a mixed deployment never passes it unchecked
        cases = (({"head": C1, "clean": False, "changed_files": ["src/a.js"]}, "审查不应修改代码，修改应交给实现任务：src/a.js"),
                 ({"head": "e" * 40, "clean": False, "changed_files": []}, f"审查期间会话分支被改动（HEAD 不是候选 {C1[:7]}）"))
        for index, (check, words) in enumerate(cases):
            with self.subTest(words=words):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], request=f"start-000000006{index}")
                worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}
                worker.check = lambda body, check=check: check

                def old(body):
                    raise Error(400, "invalid_request", "mode 只能是 commit、check、restore 或 diff")
                worker.rewind = old
                self.run_engine(worker, ticks=16)
                attempt, evidence = self.attempt(rid, "review")
                self.assertEqual((self.gates(evidence)["review_bound"], attempt["status"]), ("fail", "blocked"))
                self.assertIn(words, attempt["error"])
                self.assertNotIn("reviewed", evidence)

    def test_a_rejecting_review_still_has_its_edits_put_back(self):
        """D57 §4: whatever the verdict. A rejection's edits left in the workspace would be swept into the rework's
        candidate; they are put back, recorded beside the rejection, which stays the Attempt's for recovery."""
        rejection = {"decision": "changes_requested", "summary": "缺少空输入的边界处理", "failure_type": "implementation",
                     "reviewed_commit": C1}
        edited = {"head": C1, "clean": False, "changed_files": ["src/a.js"]}

        def refused(body):
            raise Error(409, "candidate_failed", "Git 操作失败（exit 1）：index.lock")

        def old(body):
            raise Error(400, "invalid_request", "mode 只能是 commit、check、restore 或 diff")
        put_back = lambda body: {"head": C1, "clean": True, "changed_files": [], "reverted": ["src/a.js"], "rewound": 0}
        cases = (("warn", edited, put_back, "审查改动的已跟踪文件已由平台撤回：src/a.js"),
                 ("skip", None, None, "审查没有改动已跟踪的文件"),
                 ("fail", edited, refused, "平台未能撤回审查改动的 src/a.js：Git 操作失败（exit 1）：index.lock"),
                 ("skip", edited, old, "未能撤回审查改动的 src/a.js：Worker 版本不支持"))   # a Worker from before D57
        for index, (status, check, rewind, words) in enumerate(cases):
            with self.subTest(status=status, words=words):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], recovery=True, request=f"start-000000007{index}")
                worker.outcomes["review"] = rejection
                if check:
                    worker.check = lambda body, check=check: check
                if rewind:
                    worker.rewind = rewind
                self.run_engine(worker, ticks=8)
                attempt, evidence = self.attempt(rid, "review")
                self.assertEqual((attempt["status"], attempt["error"], evidence["failure_type"]),
                                 ("blocked", "缺少空输入的边界处理", "implementation"))   # what recovery sees, unchanged
                self.assertNotIn("gate", evidence)
                self.assertEqual(self.gates(evidence)["outcome_pass"], "fail")
                gate = self.review_bound(evidence)
                self.assertEqual(gate["status"], status)
                self.assertIn(words, gate["detail"])
                self.assertNotIn("reviewed", evidence)
                modes = [b["mode"] for p, b in worker.calls if p == "/candidate" and b["attempt_id"] == attempt["id"]]
                self.assertEqual(modes, ["check", "rewind"] if check else ["check"])
                if status == "warn":
                    self.assertEqual(gate["files"], ["src/a.js"])
                with self.db.read() as db:
                    self.assertTrue(db.execute("SELECT 1 FROM recoveries WHERE run_id=?", (rid,)).fetchone())

    def test_a_review_the_watchdog_stopped_has_its_edits_put_back(self):
        worker = FakeWorker()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW])
        worker.check = lambda body: {"head": C1, "clean": False, "changed_files": ["docs/review.md"]}
        worker.rewind = lambda body: {"head": C1, "clean": True, "changed_files": [], "reverted": ["docs/review.md"],
                                      "rewound": 0}

        def stalled(config, path, body=None, **kwargs):
            if path == "/inspect" and worker.dispatched[body["attempt_id"]]["spec"]["id"] == "review":
                worker.calls.append((path, body))
                return {"status": "running", "native": {"done": False}, "evidence": {"actual_backend": "claude"},
                        "progress": {"idle_seconds": 10 ** 6}}
            if path == "/attempt/stop":
                return {"stopped": True}
            return worker(config, path, body, **kwargs)
        engine = Engine(self.db, request=stalled)
        engine.wall = lambda: __import__("time").time() + 4 * 3600   # long past the watchdog's limits
        for _ in range(10):
            engine.tick()
        attempt, evidence = self.attempt(rid, "review")
        self.assertEqual((attempt["status"], evidence["failure_type"]), ("blocked", "native_error"))
        self.assertIn("看门狗已停止", attempt["error"])
        gate = self.review_bound(evidence)
        self.assertEqual((gate["status"], gate["files"]), ("warn", ["docs/review.md"]))

    def test_the_rework_after_a_rejecting_review_does_not_take_its_edits(self):
        """End to end on a real workspace: the review edits the implementation and rejects; recovery reworks the code task;
        the rework's candidate holds the implementer's files only."""
        from octopus import sources
        from tests.test_archive import RID, make_worker
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        node, project = make_worker(root)
        rounds, decision = {"code": 0, "review": 0}, {"action": "rework", "target_task_id": "code", "reason": "审查发现边界错误",
                                                       "instructions": "补上空输入的边界处理"}

        class Real(FakeWorker):
            def __call__(self, config, path, body=None, **kwargs):
                if path == "/candidate":
                    self.calls.append((path, body))
                    return node.candidate({**body, "run_id": RID})
                if path == "/dispatch" and not body.get("coordination") and body["attempt_id"] not in self.dispatched:
                    task = body["spec"]["id"]
                    rounds[task] += 1
                    files = {("code", 1): {"src/a.js": "export const a = 1;\n"},
                             ("code", 2): {"src/b.js": "export const b = (x) => x ?? 0;\n"},
                             ("review", 1): {"src/a.js": "审查者顺手改了实现\n"}}.get((task, rounds[task]), {})
                    for name, text in files.items():
                        (project / name).parent.mkdir(parents=True, exist_ok=True)
                        (project / name).write_text(text)
                    self.outcomes["review"] = ({"decision": "changes_requested", "summary": "缺少空输入的边界处理",
                                                "failure_type": "implementation"} if rounds["review"] == 1
                                               else {"decision": "pass", "summary": "无阻断问题"})
                if path == "/inspect" and self.dispatched[body["attempt_id"]].get("coordination"):
                    self.calls.append((path, body))
                    return {"native": {"done": True, "result": "诊断完成"},
                            "evidence": {"actual_backend": self.dispatched[body["attempt_id"]]["spec"]["tool"]},
                            "handoff": "诊断", "decision": dumps(decision)}
                if path == "/inspect" and self.dispatched[body["attempt_id"]]["spec"]["id"] == "review":
                    sha = (self.dispatched[body["attempt_id"]].get("candidate") or {}).get("sha", "")
                    self.outcomes["review"] = {**self.outcomes["review"], "reviewed_commit": sha}
                return super().__call__(config, path, body, **kwargs)
        worker = Real()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], recovery=True)
        self.run_engine(worker, ticks=40)
        self.assertEqual(rounds, {"code": 2, "review": 2})
        with self.db.read() as db:
            codes = [json.loads(r["evidence"]) for r in db.execute(
                "SELECT evidence FROM attempts WHERE run_id=? AND task_id='code' ORDER BY number", (rid,))]
        rework = codes[-1]["candidate"]
        git = lambda *args: sources._git(project, *args)
        self.assertEqual(git("show", rework["sha"] + ":src/a.js"), "export const a = 1;")   # not the reviewer's edit
        self.assertEqual(rework["names"], ["src/b.js"])
        self.assertEqual(self.service.run(self.c, rid)["status"], "completed")

    def test_a_review_that_only_creates_a_file_has_it_moved_out_and_still_counts(self):
        """End to end on a real workspace (D57 §4): the review writes review.md and passes; the file is moved into the
        Attempt's folder, the verdict counts, and the next implement candidate holds the implementer's files only."""
        from octopus import sources
        from tests.test_archive import RID, make_worker
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        node, project = make_worker(root)
        polish = {**IMPLEMENT, "id": "polish", "name": "完善", "verify": []}

        class Real(FakeWorker):
            def __call__(self, config, path, body=None, **kwargs):
                if path == "/candidate":
                    self.calls.append((path, body))
                    return node.candidate({**body, "run_id": RID})
                if path == "/dispatch" and body["attempt_id"] not in self.dispatched:
                    task = body["spec"]["id"]
                    if task == "review":   # what the Worker records when it dispatches a review
                        folder = node.path(RID) / "attempts" / body["attempt_id"]
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / "untracked.json").write_text(json.dumps({"paths": sources.untracked(project)}))
                    for name, text in {"code": {"src/a.js": "export const a = 1;\n"}, "review": {"review.md": "审查结论\n"},
                                       "polish": {"src/b.js": "export const b = 2;\n"}}[task].items():
                        (project / name).parent.mkdir(parents=True, exist_ok=True)
                        (project / name).write_text(text)
                if path == "/inspect" and self.dispatched[body["attempt_id"]]["spec"]["id"] == "review":
                    sha = (self.dispatched[body["attempt_id"]].get("candidate") or {}).get("sha", "")
                    self.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": sha}
                return super().__call__(config, path, body, **kwargs)
        worker = Real()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW, polish])
        self.run_engine(worker, ticks=30)
        c1 = self.attempt(rid, "code")[1]["candidate"]["sha"]
        attempt, evidence = self.attempt(rid, "review")
        gate = self.review_bound(evidence)
        self.assertEqual((gate["status"], gate["files"]), ("warn", ["review.md"]))
        self.assertIn("审查新建的文件已由平台移出：review.md", gate["detail"])
        self.assertIn("移出的文件保存在该次尝试的 review-removed 目录", gate["detail"])
        self.assertIn("审查结论照常采用", gate["detail"])
        self.assertNotIn("已跟踪", gate["detail"])
        self.assertEqual((attempt["status"], evidence["reviewed"]), ("accepted", c1))
        self.assertEqual((node.path(RID) / "attempts" / attempt["id"] / "review-removed/review.md").read_text(), "审查结论\n")
        candidate = self.attempt(rid, "polish")[1]["candidate"]
        self.assertEqual(candidate["names"], ["src/b.js"])
        self.assertNotIn("review.md", sources._git(project, "ls-tree", "-r", "--name-only", candidate["sha"]).splitlines())
        self.assertFalse((project / "review.md").exists())
        self.assertEqual(self.service.run(self.c, rid)["status"], "completed")

    def test_a_rejecting_review_that_creates_a_file_has_it_moved_out(self):   # D57 §4: _put_back_review
        rejection = {"decision": "changes_requested", "summary": "缺少空输入的边界处理", "failure_type": "implementation",
                     "reviewed_commit": C1}
        put_back = lambda reverted, removed, left=(): lambda body: {
            "head": C1, "clean": True, "changed_files": [], "reverted": reverted, "rewound": 0, "removed": removed,
            "created": list(left), "tracked_only": False}

        def refused(body):
            raise Error(409, "candidate_failed", "Git 操作失败（exit 1）：index.lock")
        cases = (("warn", {"changed_files": [], "created": ["review.md"]}, put_back([], ["review.md"]),
                  "审查新建的文件已由平台移出：review.md；移出的文件保存在该次尝试的 review-removed 目录", ["review.md"]),
                 ("warn", {"changed_files": ["src/a.js"], "created": ["review.md"]}, put_back(["src/a.js"], ["review.md"]),
                  "审查改动的已跟踪文件已由平台撤回：src/a.js；审查新建的文件已由平台移出：review.md；"
                  "移出的文件保存在该次尝试的 review-removed 目录", ["src/a.js", "review.md"]),
                 ("skip", {"changed_files": [], "created": []}, None, "审查没有改动文件", None),
                 # a folder the reviewer made a repository, holding files it started with: left in place, and said
                 ("warn", {"changed_files": [], "created": ["tools/"]}, put_back([], [], ["tools/"]),
                  "审查新建的这些文件未能移出，仍在工作区：tools/", None),
                 ("warn", {"changed_files": [], "created": ["review.md"]}, refused,
                  "平台未能移出审查新建的 review.md：Git 操作失败（exit 1）：index.lock", None),
                 ("fail", {"changed_files": ["src/a.js"], "created": ["review.md"]}, refused,
                  "平台未能撤回审查改动的 src/a.js，移出审查新建的 review.md：Git 操作失败（exit 1）：index.lock", None))
        for index, (status, changes, rewind, words, files) in enumerate(cases):
            with self.subTest(words=words):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], recovery=True, request=f"start-000000008{index}")
                worker.outcomes["review"] = rejection
                worker.check = lambda body, changes=changes: {"head": C1, "clean": not changes["changed_files"],
                                                              "tracked_only": False, **changes}
                if rewind:
                    worker.rewind = rewind
                self.run_engine(worker, ticks=8)
                attempt, evidence = self.attempt(rid, "review")
                self.assertEqual((attempt["status"], attempt["error"], evidence["failure_type"]),
                                 ("blocked", "缺少空输入的边界处理", "implementation"))   # what recovery sees, unchanged
                self.assertNotIn("gate", evidence)
                gate = self.review_bound(evidence)
                self.assertEqual((gate["status"], gate.get("files")), (status, files))
                self.assertIn(words, gate["detail"])
                self.assertEqual(gate["detail"].count("review-removed"), 1 if "已由平台移出" in words else 0)
                modes = [b["mode"] for p, b in worker.calls if p == "/candidate" and b["attempt_id"] == attempt["id"]]
                self.assertEqual(modes, ["check", "rewind"] if rewind else ["check"])

    def test_new_files_that_cannot_be_moved_out_never_fail_a_review(self):   # no new failure: D57 §4 only lowers gates
        worker = FakeWorker()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW])
        worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}
        worker.check = lambda body: {"head": C1, "clean": True, "changed_files": [], "created": ["review.md"],
                                     "tracked_only": False}

        def refused(body):
            raise Error(409, "candidate_failed", "Git 操作失败（exit 128）")
        worker.rewind = refused
        self.run_engine(worker, ticks=16)
        attempt, evidence = self.attempt(rid, "review")
        gate = self.review_bound(evidence)
        self.assertEqual((gate["status"], attempt["status"], evidence["reviewed"]), ("warn", "accepted", C1))
        self.assertIn("平台未能移出审查新建的 review.md：Git 操作失败（exit 128）", gate["detail"])   # as _put_back_review says it
        self.assertIn("审查结论照常采用", gate["detail"])
        self.assertNotIn("gate", evidence)

    def test_new_files_that_could_not_be_listed_on_rewind_are_said_not_failed(self):   # tracked files were put back
        rejection = {"decision": "changes_requested", "summary": "缺少空输入的边界处理", "failure_type": "implementation",
                     "reviewed_commit": C1}
        for index, outcome in enumerate(({"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}, rejection)):
            with self.subTest(decision=outcome["decision"]):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], request=f"start-000000010{index}")
                worker.outcomes["review"] = outcome
                worker.check = lambda body: {"head": C1, "clean": False, "changed_files": ["src/a.js"],
                                             "created": ["review.md"], "tracked_only": False}
                worker.rewind = lambda body: {"head": C1, "clean": True, "changed_files": [], "reverted": ["src/a.js"],
                                              "rewound": 0, "removed": [], "listing_failed": True, "created": [],
                                              "tracked_only": True}
                self.run_engine(worker, ticks=16)
                attempt, evidence = self.attempt(rid, "review")
                gate = self.review_bound(evidence)
                self.assertEqual((gate["status"], gate["files"]), ("warn", ["src/a.js"]))
                self.assertIn("审查改动的已跟踪文件已由平台撤回：src/a.js；未能列出审查新建的文件，未做移出", gate["detail"])
                self.assertNotIn("review-removed", gate["detail"])
                self.assertNotIn("gate", evidence)
                self.assertEqual(attempt["status"], "accepted" if outcome is not rejection else "blocked")

    def test_a_rewind_that_leaves_the_workspace_off_the_candidate_names_what_it_did(self):
        worker = FakeWorker()
        rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW])
        worker.outcomes["review"] = {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}
        worker.check = lambda body: {"head": C1, "clean": False, "changed_files": ["src/a.js"], "created": ["review.md"],
                                     "tracked_only": False}
        worker.rewind = lambda body: {"head": C1, "clean": False, "changed_files": ["src/a.js"], "reverted": ["src/a.js"],
                                      "rewound": 0, "removed": ["review.md"], "created": [], "tracked_only": False}
        self.run_engine(worker, ticks=16)
        attempt, evidence = self.attempt(rid, "review")
        self.assertEqual((self.gates(evidence)["review_bound"], attempt["status"]), ("fail", "blocked"))
        self.assertIn(f"平台撤回审查改动的 src/a.js，移出审查新建的 review.md后，工作区仍不是候选 {C1[:7]}：src/a.js",
                      attempt["error"])   # in the words _put_back_review uses

    def test_an_old_worker_without_created_keeps_todays_behaviour(self):   # no created, no tracked_only, no removed
        rejection = {"decision": "changes_requested", "summary": "缺少空输入的边界处理", "failure_type": "implementation",
                     "reviewed_commit": C1}
        for index, (outcome, check, status, words) in enumerate((
                (rejection, None, "skip", "审查没有改动已跟踪的文件"),
                ({"decision": "pass", "summary": "无阻断问题", "reviewed_commit": C1}, None, "pass", C1[:12]),
                (rejection, {"head": C1, "clean": False, "changed_files": ["src/a.js"]}, "warn",
                 "审查改动的已跟踪文件已由平台撤回：src/a.js"))):
            with self.subTest(status=status):
                worker = FakeWorker()
                rid = self.session([{**IMPLEMENT, "verify": []}, REVIEW], request=f"start-000000009{index}")
                worker.outcomes["review"] = outcome
                if check:
                    worker.check = lambda body, check=check: check
                    worker.rewind = lambda body: {"head": C1, "clean": True, "changed_files": [], "reverted": ["src/a.js"],
                                                  "rewound": 0}
                self.run_engine(worker, ticks=16)
                attempt, evidence = self.attempt(rid, "review")
                gate = self.review_bound(evidence)
                self.assertEqual(gate["status"], status)
                self.assertIn(words, gate["detail"])
                self.assertNotIn("移出", gate["detail"])
                self.assertNotEqual(gate["detail"], "审查没有改动文件")
                self.assertEqual(gate.get("files"), ["src/a.js"] if check else None)
                modes = [b["mode"] for p, b in worker.calls if p == "/candidate" and b["attempt_id"] == attempt["id"]]
                self.assertEqual(modes, ["check", "rewind"] if check else ["check"])

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
        self.assertEqual(self.snapshot(rid)["execution_contract"], 4)   # D46 P3

    def test_changing_an_accepted_tasks_files_is_a_warning_naming_them(self):   # D57 §4: left to the review that follows
        worker = FakeWorker()
        names = {"code": ["src/a.py"], "docs": ["src/a.py", "README.md"]}
        shas = {"code": C1, "docs": "d" * 40}
        def candidate(body):
            task = "code" if worker.dispatched[body["attempt_id"]]["spec"]["id"] == "code" else "docs"
            return {"sha": shas[task], "parent": body.get("parent", BASE), "changed": True, "files": len(names[task]),
                    "insertions": 1, "deletions": 0, "rewritten": False, "secrets": [], "names": names[task]}
        worker.candidate = candidate
        docs = {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver",
                "acceptance": ["有文档"], "paths": ["README.md"], "verify": ["npm test"]}
        rid = self.session([IMPLEMENT, docs])
        self.run_engine(worker, ticks=20)
        attempt, evidence = self.attempt(rid, "docs")
        self.assertEqual((attempt["status"], self.gates(evidence).get("artifacts_locked")), ("accepted", "warn"))
        warned = [g for g in evidence["gates"] if g["name"] == "artifacts_locked"][0]
        self.assertIn("「实现」的交付物：src/a.py", warned["detail"])
        self.assertEqual(warned["files"], ["src/a.py"])
        self.assertEqual(self.gates(evidence)["checks_pass"], "pass")   # the gates after it still judged
        self.assertEqual([w["name"] for w in evidence["warnings"]], ["artifacts_locked"])

    def test_the_review_that_follows_is_told_the_upstream_warnings(self):   # D57 §4: 「交给后续审查」
        worker = FakeWorker()
        names = {"code": ["src/a.py"], "docs": ["src/a.py", "README.md"]}
        worker.candidate = lambda body: {"sha": {"code": C1, "docs": "d" * 40}[worker.dispatched[body["attempt_id"]]["spec"]["id"]],
                                         "parent": body.get("parent", BASE), "changed": True, "files": 2, "insertions": 1,
                                         "deletions": 0, "rewritten": False, "secrets": [],
                                         "names": names[worker.dispatched[body["attempt_id"]]["spec"]["id"]]}
        docs = {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver",
                "acceptance": ["有文档"], "paths": ["README.md"]}
        self.session([{**IMPLEMENT, "verify": []}, docs, {**REVIEW, "tool": "codex"}])
        self.run_engine(worker, ticks=24)
        review = [b for b in worker.dispatched.values() if b["spec"]["id"] == "review"][0]
        upstream = {u["id"]: u for u in review["upstream"]}
        self.assertNotIn("warnings", upstream["code"])   # nothing to note
        warned, = upstream["docs"]["warnings"]
        self.assertEqual((warned["name"], warned["files"]), ("artifacts_locked", ["src/a.py"]))
        self.assertIn("「实现」的交付物：src/a.py", warned["detail"])

    def test_a_locked_file_warning_starts_no_diagnosis(self):
        """docs changes the implementation's src/a.py without listing it: a warning, and no Coordinator is asked (D57 §4;
        D54's paths_add is kept for Attempts blocked before it, and checked below)."""
        worker, seen = FakeWorker(), []
        names = {"code": ["src/a.py"], "docs": ["src/a.py", "README.md"]}
        shas = {"code": C1, "docs": "d" * 40}
        worker.candidate = lambda body: {"sha": shas[worker.dispatched[body["attempt_id"]]["spec"]["id"]],
                                         "parent": body.get("parent", BASE), "changed": True, "files": 2, "insertions": 1,
                                         "deletions": 0, "rewritten": False, "secrets": [],
                                         "names": names[worker.dispatched[body["attempt_id"]]["spec"]["id"]]}

        def call(config, path, body=None, **kwargs):
            if path == "/dispatch" and body.get("coordination"):
                seen.append(body["coordination"])
            return worker(config, path, body, **kwargs)
        docs = {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver",
                "acceptance": ["有文档"], "paths": ["README.md"]}
        rid = self.session([IMPLEMENT, docs], recovery=True)
        self.run_engine(call, ticks=40)
        attempt, evidence = self.attempt(rid, "docs")
        warned = [g for g in evidence["gates"] if g["name"] == "artifacts_locked"][0]
        self.assertEqual((attempt["status"], warned["status"], warned["files"]), ("accepted", "warn", ["src/a.py"]))
        with self.db.read() as db:
            status = db.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()["status"]
            recoveries = db.execute("SELECT COUNT(*) FROM recoveries WHERE run_id=?", (rid,)).fetchone()[0]
        self.assertEqual((status, recoveries, seen), ("completed", 0, []))

    # D54 (owner 2026-10-21), kept for compatibility (D57 §4): what the Coordinator may give a task blocked by the old gate
    def test_paths_add_gives_only_the_locked_files_to_the_failed_task(self):
        failed = {"id": "docs", "spec": dumps({"id": "docs", "paths": ["README.md"]})}
        context = {"locked_files": ["src/a.py"]}
        retry = {"action": "retry", "target_task_id": "docs", "reason": "文档须同步修改 src/a.py 的注释",
                 "instructions": "保留改动并重跑", "paths_add": ["src/a.py"]}
        self.assertEqual(recovery.widen(retry, context, failed, failed), ["README.md", "src/a.py"])
        for extra in ("src/other.py", "src/*", "../etc/passwd"):
            with self.subTest(extra):
                with self.assertRaises(Error) as refused:
                    recovery.widen({**retry, "paths_add": ["src/a.py", extra]}, context, failed, failed)
                self.assertIn("不是门禁锁住的文件", str(refused.exception))
        with self.assertRaises(Error) as refused:
            recovery.widen({**retry, "action": "rework", "target_task_id": "code"}, context, failed, {"id": "code"})
        self.assertIn("只用于重试或返工失败的任务", str(refused.exception))
        with self.assertRaises(Error) as refused:   # an artifacts_locked warning gives the Coordinator no locked_files
            recovery.widen(retry, {}, failed, failed)
        self.assertIn("失败的不是 artifacts_locked 门禁", str(refused.exception))

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

    def test_tests_that_pass_on_the_baseline_are_a_warning(self):   # D57 §4
        for passed, verdict in ((True, "warn"), (False, "pass")):
            with self.subTest(baseline_passed=passed):
                call = self.gate_worker(baseline=self.baseline_answer(passed))
                rid = self.session([IMPLEMENT, self.TEST], recovery=True, request="start-%016d" % (2 + passed))
                self.run_engine(call, ticks=24)
                attempt, evidence = self.attempt(rid, "test")
                self.assertEqual(self.gates(evidence).get("tests_fail_on_baseline"), verdict)
                self.assertEqual(attempt["status"], "accepted")
                if verdict == "warn":
                    gate = [g for g in evidence["gates"] if g["name"] == "tests_fail_on_baseline"][0]
                    self.assertEqual(gate["detail"], "测试在改动前也能通过，可能没测到改动")
                    self.assertEqual([w["name"] for w in evidence["warnings"]], ["tests_fail_on_baseline"])
                    self.assertNotIn("gate", evidence)
                    with self.db.read() as db:
                        self.assertFalse(db.execute("SELECT 1 FROM recoveries WHERE run_id=?", (rid,)).fetchone())
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
