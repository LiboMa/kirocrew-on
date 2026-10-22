"""Coordinator repair state machine; native calls are deterministic substitutes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from octopus import crew_entry, recovery
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import BLOCKED, Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.old_worker import as_old_worker


SPEC = [
    {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现功能和测试"},
    {"id": "review", "name": "独立审查", "tool": "codex", "prompt": "检查实际源码、测试与安全"},
]


class RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            self.user = create_user(db, "owner", "Fixture-password-123!", True)
            self.other = create_user(db, "other", "Fixture-password-123!")
            self.actor = authenticate(db, issue_token(db, self.user["id"])["token"])
            self.other_actor = authenticate(db, issue_token(db, self.other["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','Fixture',?,?)",
                       (self.user["id"], dumps({"url": "fixture", "token": "fixture"})))
        self.calls, self.bodies, self.rounds = [], {}, {"code": 0, "review": 0}
        self.always_fail = False
        self.stop_native = False
        self.input_missing = False
        self.decision = {"action": "rework", "target_task_id": "code",
                         "reason": "审查发现边界错误", "instructions": "修复空输入边界，复跑测试并重新提交审查"}
        self.inspect_hook = None
        self.lose_ack = False
        self.engine = Engine(self.db, self.request)
        self.run = self.service.start(self.actor, {"request_id": "recovery-fixture-1", "input": "修复边界并独立验证",
            "steps": SPEC, "source": {"type": "new"}, "worker_id": "worker"})

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/prepare":
            return {"native_parent": "same-native-parent", "workspace": "/same/workspace"}
        if path == "/control":
            return {"status": "stopped", "lifecycle": "open"}
        aid = body["attempt_id"]
        if path == "/dispatch":
            if aid not in self.bodies:
                self.bodies[aid] = copy.deepcopy(body)
                if not body.get("coordination"):
                    self.rounds[body["spec"]["id"]] += 1
                    self.bodies[aid]["fixture_round"] = self.rounds[body["spec"]["id"]]
            if body.get("coordination") and self.lose_ack:
                self.lose_ack = False
                raise Error(503, "worker_unavailable", "回执丢失")
            return {"status": "running", "native_id": hashlib.sha256(aid.encode()).hexdigest()[:8],
                    "context_hash": hashlib.sha256(dumps(body).encode()).hexdigest()}
        as_old_worker(path)
        self.assertEqual(path, "/inspect")
        record = self.bodies[aid]
        result = {"native": {"done": True, "result": "已检查真实测试与交接"},
                  "evidence": {"actual_backend": record["spec"]["tool"]}, "handoff": "本次交接"}
        if record.get("coordination"):
            if self.inspect_hook:
                hook, self.inspect_hook = self.inspect_hook, None
                hook()
            result["decision"] = dumps(self.decision)
            return result
        failed = record["spec"]["id"] == "review" and (self.always_fail or record["fixture_round"] == 1)
        report = {"decision": "changes_requested" if failed else "pass",
                  "summary": "空输入返回错误，需修复并复验" if failed else "本轮实现和验证通过",
                  "failure_type": "tests" if failed else "none", "findings": ["空输入用例失败"] if failed else []}
        if self.input_missing:
            report.update(decision="blocked", summary="缺少目标仓库源码", failure_type="input")
        if self.stop_native and record["fixture_round"] == 1 and record["spec"]["id"] == "code":
            result["native"]["stopped"] = True
            result["native"]["result"] = ""
        result["outcome"] = dumps(report)
        return result

    def tick(self, count=20):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, self.run["id"])

    def open_entry(self):
        """A person was given the native KiroCrew page (K03): a native stop from now on may be theirs."""
        with self.db.transaction() as db:
            crew_entry.record(db, self.actor, self.run["id"], "worker")


class RecoveryTests(RecoveryCase):
    def test_review_rejection_returns_to_code_and_rereviews_same_workspace(self):
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["workspace"], "/same/workspace")
        self.assertEqual(result["native_parent"], "same-native-parent")
        self.assertEqual(self.rounds, {"code": 2, "review": 2})
        self.assertEqual(result["recovery"]["used"], 1)
        self.assertEqual(result["recovery"]["latest"]["status"], "applied")
        self.assertEqual([len(t["attempts"]) for t in result["tasks"]], [2, 2])
        self.assertEqual(result["tasks"][1]["attempts"][0]["status"], "blocked")
        corrected = [b for b in self.bodies.values() if b.get("repair")]
        self.assertEqual(len(corrected), 2)
        # D46: the reworked task gets the decision and a summary of the failure; the re-review re-checks it
        self.assertIn("空输入", corrected[0]["repair"]["failure"]["summary"])
        self.assertEqual(corrected[0]["repair"]["decision"]["target_task_id"], "code")
        self.assertTrue(corrected[1]["repair"]["recheck"])
        self.assertEqual(corrected[0]["spec"], result["tasks"][0]["attempts"][0]["spec"])
        self.assertNotIn("coordination", corrected[0])
        self.assertEqual(result["version"], 2)
        # The applied decision is a revision with its snapshot (D32).
        revisions = self.service.revisions(self.actor, result["id"])
        self.assertEqual([r["kind"] for r in revisions], ["created", "recovered"])
        self.assertTrue(revisions[1]["summary"].startswith("自动纠错：退回「"))
        self.assertFalse(revisions[1]["reconstructed"])

    def test_a_diagnosis_result_is_stored_masked(self):
        # D57 §3.3: the diagnosis's result, evidence and decision as they are stored; the decision is applied as given
        from tests.test_secret_rules import ANT
        shown = ANT[:4] + "…" + ANT[-2:]
        self.decision = {**self.decision, "instructions": f"用 {ANT} 复跑测试并重新提交审查"}
        plain = self.request

        def request(config, path, body, **kwargs):
            result = plain(config, path, body, **kwargs)
            if path == "/inspect" and self.bodies[body["attempt_id"]].get("coordination"):
                result = {**result, "native": {**result["native"], "result": f"诊断：日志里有 {ANT}"},
                          "evidence": {**result["evidence"], "note": f"见 {ANT}"}}
            return result
        self.engine.request = request
        result = self.tick()
        self.assertEqual((result["status"], self.rounds), ("completed", {"code": 2, "review": 2}))
        with self.db.read() as db:
            row = dict(db.execute("SELECT * FROM recoveries WHERE run_id=?", (self.run["id"],)).fetchone())
            stored = "\n".join(json.dumps([dict(r) for r in db.execute(f"SELECT * FROM {table}")], ensure_ascii=False)
                               for table in ("runs", "tasks", "attempts", "events", "recoveries", "revisions"))
        self.assertEqual(row["status"], "applied")
        self.assertEqual(row["result"], f"诊断：日志里有 {shown}")
        self.assertEqual(json.loads(row["evidence"])["note"], f"见 {shown}")
        decision = json.loads(row["decision"])
        self.assertEqual((decision["action"], decision["target_task_id"]), ("rework", "code"))
        self.assertEqual(decision["instructions"], f"用 {shown} 复跑测试并重新提交审查")
        self.assertNotIn(ANT[4:-2], stored)

    def test_a_diagnosis_that_waits_for_the_user_is_stored_masked(self):
        from tests.test_secret_rules import ANT
        shown = ANT[:4] + "…" + ANT[-2:]
        self.decision = {"action": "wait_for_user", "target_task_id": "code", "missing": "credentials",
                         "reason": "需要新的 Key", "instructions": f"旧 Key {ANT} 已失效，请提供新的"}
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        with self.db.read() as db:
            row = dict(db.execute("SELECT * FROM recoveries WHERE run_id=?", (self.run["id"],)).fetchone())
            error = db.execute("SELECT error FROM runs WHERE id=?", (self.run["id"],)).fetchone()[0]
            events = "".join(r[0] for r in db.execute("SELECT payload FROM events WHERE run_id=?", (self.run["id"],)))
        self.assertEqual((row["status"], json.loads(row["decision"])["action"]), ("blocked", "wait_for_user"))
        self.assertEqual((row["error"], error), (f"需要新的 Key；旧 Key {shown} 已失效，请提供新的",) * 2)
        self.assertNotIn(ANT[4:-2], row["decision"] + events)

    def use_token(self):
        """A Worker token as long as a real one (40 characters): no rule knows it, the platform masks it as it is."""
        token = "wt-" + hashlib.sha256(b"task-7-recovery").hexdigest()[:37]
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='worker'", (dumps({"url": "fixture", "token": token}),))
        return token

    def stored(self):
        with self.db.read() as db:
            return "\n".join(json.dumps([dict(r) for r in db.execute(f"SELECT * FROM {table}")], ensure_ascii=False)
                             for table in ("runs", "tasks", "attempts", "events", "recoveries", "revisions"))

    def test_the_workers_token_in_a_decision_is_kept_out_of_every_record(self):
        # the Coordinator's own words (the reason every event of the rework quotes, the revision's title) included
        token = self.use_token()
        self.decision = {**self.decision, "reason": f"日志里有 {token}", "instructions": f"不要再打印 {token}，修复后复跑"}
        result = self.tick()
        self.assertEqual((result["status"], self.rounds), ("completed", {"code": 2, "review": 2}))
        self.assertNotIn(token, self.stored())
        self.assertIn(f"日志里有 {token[:4]}…{token[-2:]}", self.service.revisions(self.actor, result["id"])[-1]["summary"])

    def test_a_coordinator_that_cannot_start_is_stored_masked(self):
        token = self.use_token()
        plain = self.request

        def request(config, path, body, **kwargs):
            if path == "/dispatch" and body.get("coordination"):
                return {"status": "blocked", "error": f"原生环境拒绝：{token}"}
            return plain(config, path, body, **kwargs)
        self.engine.request = request
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        with self.db.read() as db:
            error = db.execute("SELECT error FROM recoveries WHERE run_id=?", (self.run["id"],)).fetchone()[0]
        self.assertEqual(error, f"协调者未能启动：原生环境拒绝：{token[:4]}…{token[-2:]}")
        self.assertNotIn(token, self.stored())

    def test_cycle_limit_stops_without_infinite_dispatch(self):
        self.always_fail = True
        result = self.tick(40)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["used"], 2)
        self.assertEqual(self.rounds, {"code": 3, "review": 3})
        count = len(self.bodies)
        self.tick()
        self.assertEqual(len(self.bodies), count)
        self.assertIn("2 轮上限", result["attention"]["action"])

    def test_missing_input_and_native_stop_are_not_blindly_retried(self):
        self.input_missing = True
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["used"], 0)
        self.assertEqual(result["attention"]["title"], "等待补充项目或任务信息")
        self.assertEqual(self.rounds["code"], 1)

    def test_explicit_recovery_of_stopped_task_is_idempotent_and_authorized(self):
        self.stop_native = True
        self.open_entry()   # a person's stop in the native page (D57 §6.3: KiroCrew's own teardown is retried instead)
        self.decision["action"] = "retry"
        result = self.tick()
        self.assertEqual(result["recovery"]["used"], 0)
        body = {"request_id": "manual-repair-1", "expected_version": result["version"]}
        with self.assertRaises(Error):
            self.service.operate(self.other_actor, result["id"], "recover", body)
        receipt = self.service.operate(self.actor, result["id"], "recover", body)
        self.assertEqual(receipt, self.service.operate(self.actor, result["id"], "recover", body))
        result = self.tick()
        self.assertEqual(result["recovery"]["history"][0]["context"]["manual"], True)
        self.assertEqual(result["tasks"][0]["attempts"][0]["status"], "blocked")
        self.assertEqual(result["native_parent"], "same-native-parent")

    def test_coordinator_waits_for_user_instead_of_claiming_fixed(self):
        self.decision = {"action": "wait_for_user", "missing": "credentials", "reason": "目标 API 需要认证",
                         "instructions": "请配置项目测试账号后继续"}
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["latest"]["status"], "blocked")
        self.assertIn("测试账号", result["error"])
        self.assertEqual(self.rounds, {"code": 1, "review": 1})

    def test_retry_naming_an_upstream_task_is_applied_as_rework_and_recorded(self):
        # Observed in a real local run: the Coordinator diagnosed correctly but labelled an upstream rerun "retry".
        # Both labels rerun the target and everything downstream, so the Engine normalizes instead of blocking.
        self.decision["action"] = "retry"
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.rounds, {"code": 2, "review": 2})
        applied = result["recovery"]["latest"]
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["decision"]["action"], "rework")
        self.assertEqual(applied["decision"]["normalized_from"], "retry")

    def test_invalid_rework_target_cannot_skip_tasks_or_cross_run(self):   # D71: refused, then the failed task is retried
        self.decision["target_task_id"] = "other-run-task"
        result = self.tick()
        latest = result["recovery"]["latest"]
        self.assertEqual((latest["status"], latest["decision"]["action"], latest["decision"]["target_task_id"]),
                         ("applied", "retry", "review"))
        self.assertIn("other-run-task", latest["decision"]["refused"])
        self.assertEqual(self.rounds, {"code": 1, "review": 2})

    def test_lost_coordinator_receipt_reuses_native_attempt_after_engine_restart(self):
        self.lose_ack = True
        self.tick(6)
        self.engine = Engine(self.db, self.request)
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        calls = [body for path, body in self.calls if path == "/dispatch" and body.get("coordination")]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len({b["attempt_id"] for b in calls}), 1)

    def test_stop_during_diagnosis_does_not_apply_late_decision(self):
        def stop():
            current = self.service.run(self.actor, self.run["id"])
            self.service.operate(self.actor, current["id"], "stop",
                                 {"request_id": "stop-diagnosis-1", "expected_version": current["version"]})
        self.inspect_hook = stop
        result = self.tick()
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["recovery"]["latest"]["status"], "cancelled")
        self.assertEqual(self.rounds, {"code": 1, "review": 1})
        self.assertEqual(len(result["tasks"][0]["attempts"]), 1)

    def test_legacy_run_stays_paused_until_explicit_request(self):
        with self.db.transaction() as db:
            row = db.execute("SELECT snapshot FROM runs WHERE id=?", (self.run["id"],)).fetchone()
            snapshot = json.loads(row["snapshot"])
            snapshot.pop("recovery")
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), self.run["id"]))
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["recovery"]["policy"]["enabled"])
        self.assertEqual(result["recovery"]["used"], 0)

    def test_crash_after_failure_before_diagnosis_is_reconciled(self):
        with patch("octopus.recovery.automatic", side_effect=RuntimeError("simulated crash")):
            result = self.tick(5)
        self.assertEqual(result["recovery"]["used"], 0)
        self.engine = Engine(self.db, self.request)
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recovery"]["used"], 1)
        self.assertEqual(self.rounds, {"code": 2, "review": 2})

    def test_disabled_worker_keeps_diagnosis_resumable_without_new_cycle(self):
        self.tick(5)
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0")
        result = self.tick(3)
        self.assertEqual(result["status"], "diagnosing")
        self.assertEqual(result["recovery"]["used"], 1)
        self.assertIn("停用", result["error"])
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=1")
        self.assertEqual(self.tick()["status"], "completed")
        self.assertEqual(self.rounds, {"code": 2, "review": 2})

    def test_reconcile_does_not_duplicate_pending_diagnosis(self):
        self.tick(5)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked' WHERE id=?", (self.run["id"],))
        from octopus.recovery import reconcile
        reconcile(self.db)
        self.assertEqual(self.service.run(self.actor, self.run["id"])["recovery"]["used"], 1)

    def test_a_missing_quality_report_is_judged_by_the_result_text(self):   # D57 §4: a warning, not a failure
        original = self.request
        def missing(config, path, body, **kwargs):
            result = original(config, path, body, **kwargs)
            result.pop("outcome", None)
            return result
        self.engine = Engine(self.db, missing)
        result = self.tick()
        self.assertEqual((result["status"], result["progress"]["accepted"], result["recovery"]["used"]), ("completed", 2, 0))
        gates = {g["name"]: g["status"] for g in result["tasks"][0]["attempts"][0]["evidence"]["gates"]}
        self.assertEqual((gates["outcome_valid"], gates["outcome_pass"]), ("warn", "skip"))

    def test_a_blocked_reply_without_a_quality_report_cannot_be_accepted(self):
        original = self.request
        def blocked(config, path, body, **kwargs):
            result = original(config, path, body, **kwargs)
            if result.pop("outcome", None) is not None:   # a task's reply, not the Coordinator's
                result["native"]["result"] = "BLOCKED：缺少目标仓库源码"
            return result
        self.engine = Engine(self.db, blocked)
        self.decision = {"action": "wait_for_user", "reason": "执行结果未交付", "instructions": "补充本轮真实 outcome 后重跑"}
        result = self.tick()
        self.assertEqual((result["status"], result["progress"]["accepted"]), ("blocked", 0))
        attempt = result["tasks"][0]["attempts"][0]
        self.assertEqual(attempt["error"], BLOCKED)
        self.assertEqual([(g["name"], g["status"]) for g in attempt["evidence"]["gates"]], [("native_completed", "fail")])

    def test_mcp_recovery_routes_to_same_versioned_api(self):
        from octopus import mcp
        class Client:
            def request(inner, method, path, body):
                self.assertEqual(method, "POST")
                self.assertEqual(path, "/v1/runs/" + self.run["id"] + "/recover")
                return body
        args = {"run_id": self.run["id"], "request_id": "mcp-recover-1", "expected_version": 1}
        self.assertEqual(mcp.call(Client(), "octopus_recover", args)["expected_version"], 1)
        self.assertIn("run_id", args)  # Client adapter must not mutate caller input.

    def test_structured_review_findings_keep_file_line_and_issue(self):
        from octopus.recovery import outcome
        finding = {"file": "src/add.mjs", "line": 2, "issue": "偏移了一位", "severity": "high"}
        value = outcome(dumps({"decision": "changes_requested", "summary": "实际运行测试失败",
                               "failure_type": "tests", "findings": [finding]}))
        self.assertEqual(value["findings"], [finding])
        self.assertEqual(value["decision"], "changes_requested")
        with self.assertRaises(Error):
            outcome(dumps({"decision": "pass", "summary": "无效报告", "findings": [{"line": 2}]}))

    def test_coordinator_dispatch_refusal_stops_with_actionable_error(self):
        original, refusals = self.request, []
        def refusal(config, path, body, **kwargs):
            if path == "/dispatch" and body.get("coordination"):
                refusals.append(body["attempt_id"])
                raise Error(401, "unauthorized", "Worker 认证失效")
            return original(config, path, body, **kwargs)
        self.engine = Engine(self.db, refusal)
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["latest"]["status"], "blocked")
        self.assertEqual(len(refusals), 1)
        self.assertIn("Worker 认证失效", result["attention"]["detail"])


class FailingGateTests(unittest.TestCase):
    def test_the_failing_gate_is_the_first_failure_not_a_review_put_back_after_it(self):
        record = lambda name, status: {"name": name, "status": status}
        rejected = {"failure_type": "tests", "gates": [record("native_completed", "pass"), record("outcome_pass", "fail")]}
        put_back = {**rejected, "gates": rejected["gates"] + [record("review_bound", "fail")]}   # Task 3: rewind failed
        self.assertEqual(recovery.failing_gate(rejected), "outcome_pass")
        self.assertEqual(recovery.failing_gate(put_back), "outcome_pass")
        self.assertEqual(recovery.failing_gate({**put_back, "gate": "checks_pass"}), "checks_pass")
        self.assertEqual(recovery.failing_gate({"failure_type": "contract", "gates": [record("native_completed", "pass")]}),
                         "contract")   # no failed gate: its failure_type
        self.assertEqual(recovery.failing_gate({}, "input"), "input")


SANDBOX = "kiro_crew.acp.client.AcpSandboxInitFailed: sandbox failed to initialize"
FORCED = "ACP process exited (code=-9, SIGKILL, killed by Crew (forced stop))"


class DiagnosisRetryTests(RecoveryCase):
    """D57 §6.3, §13.9: a diagnosis the platform failed (its sandbox, its runtime, KiroCrew's teardown) is dispatched
    again once on the same round — an automatic retry of the budget, not another round."""

    def setUp(self):
        super().setUp()
        self.faults = []   # what the next diagnoses end with, in turn
        original = self.request
        def request(config, path, body, **kwargs):
            result = original(config, path, body, **kwargs)
            if path == "/inspect" and body["attempt_id"] in self.diagnoses() and self.faults:
                result = {**result, "native": {**result["native"], **self.faults.pop(0)}}
                result.pop("decision")
            return result
        self.engine = Engine(self.db, request)

    def diagnoses(self):
        return list(dict.fromkeys(b["attempt_id"] for path, b in self.calls if path == "/dispatch" and b.get("coordination")))

    def test_a_diagnosis_that_dies_in_the_sandbox_is_retried_once(self):
        self.faults = [{"error": SANDBOX}]
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        (round_,) = result["recovery"]["history"]                  # the same round, dispatched again
        self.assertEqual(round_["status"], "applied")
        sent = self.diagnoses()
        self.assertEqual(len(sent), 2)
        self.assertEqual(round_["attempt_id"], sent[1])
        self.assertEqual(round_["context"]["retry_of"], sent[0])
        budget = result["recovery"]["budget"]
        self.assertEqual((budget["cycles"]["used"], budget["retries"]["used"]), (1, 1))
        with self.db.read() as db:
            retried = [json.loads(r["payload"]) for r in db.execute(
                "SELECT payload FROM events WHERE run_id=? AND kind='recovery.retry'", (result["id"],))]
        self.assertEqual([(e["attempt"], e["retry"], e["transient"]) for e in retried], [(sent[0], sent[1], "runtime_sandbox")])

    def test_a_diagnosis_that_dies_twice_retries_the_failed_task(self):   # D71: no stop, the task is retried as it failed
        self.faults = [{"error": SANDBOX}, {"error": SANDBOX}]
        result = self.tick()
        self.assertEqual((result["status"], result["recovery"]["used"]), ("completed", 1))
        self.assertEqual(len(self.diagnoses()), 2)
        latest = result["recovery"]["latest"]
        self.assertEqual((latest["status"], latest["decision"]["action"], latest["decision"]["fallback_from"]),
                         ("applied", "retry", "none"))
        self.assertIn("协调者执行失败或已停止", latest["decision"]["instructions"])

    def test_a_diagnosis_the_runtime_ended_is_retried_too(self):
        self.faults = [{"error": "kiro_crew.acp.client.AcpError: ACP process exited (code=-15, SIGTERM, external)"}]
        self.assertEqual(self.tick()["status"], "completed")
        self.assertEqual(len(self.diagnoses()), 2)

    def test_a_diagnosis_torn_down_while_nobody_could_stop_it_is_retried(self):
        self.faults = [{"stopped": True, "error": FORCED}]
        result = self.tick()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.diagnoses()), 2)
        self.assertEqual(result["recovery"]["history"][0]["context"]["retry"]["transient"], "runtime_teardown")

    def test_a_diagnosis_stopped_after_the_native_page_was_opened_is_not_retried(self):
        self.open_entry()
        self.faults = [{"stopped": True, "error": FORCED}]
        result = self.tick()
        self.assertEqual((result["status"], len(self.diagnoses())), ("blocked", 1))
        self.assertIn("协调者执行失败或已停止", result["error"])

    def test_a_diagnosis_is_not_retried_past_the_retry_budget(self):
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (self.run["id"],)).fetchone()[0])
            snapshot["recovery"] = {**(snapshot.get("recovery") or {"enabled": True, "max_cycles": 2}), "max_retries": 0}
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), self.run["id"]))
        self.faults = [{"error": SANDBOX}]
        result = self.tick()
        self.assertEqual(len(self.diagnoses()), 1)   # the diagnosis is not retried; D71: the failed task is
        self.assertEqual(result["recovery"]["latest"]["decision"]["fallback_from"], "none")
