"""Coordinator repair state machine; native calls are deterministic substitutes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token


SPEC = [
    {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现功能和测试"},
    {"id": "review", "name": "独立审查", "tool": "codex", "prompt": "检查实际源码、测试与安全"},
]


class RecoveryTests(unittest.TestCase):
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
        self.assertIn("空输入", corrected[0]["repair"]["failure"]["evidence"]["outcome"]["summary"])
        self.assertEqual(corrected[1]["repair"]["decision"]["target_task_id"], "code")
        self.assertEqual(corrected[0]["spec"], result["tasks"][0]["attempts"][0]["spec"])
        self.assertNotIn("coordination", corrected[0])
        self.assertEqual(result["version"], 2)

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
        self.decision = {"action": "wait_for_user", "reason": "目标 API 需要认证", "instructions": "请配置项目测试账号后继续"}
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

    def test_invalid_rework_target_cannot_skip_tasks_or_cross_run(self):
        self.decision["target_task_id"] = "other-run-task"
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["recovery"]["latest"]["status"], "blocked")
        self.assertEqual(self.rounds, {"code": 1, "review": 1})

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

    def test_missing_quality_report_cannot_be_accepted(self):
        original = self.request
        def missing(config, path, body, **kwargs):
            result = original(config, path, body, **kwargs)
            result.pop("outcome", None)
            return result
        self.engine = Engine(self.db, missing)
        self.decision = {"action": "wait_for_user", "reason": "执行结果未交付", "instructions": "补充本轮真实 outcome 后重跑"}
        result = self.tick()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["progress"]["accepted"], 0)
        self.assertEqual(result["tasks"][0]["attempts"][0]["evidence"]["failure_type"], "contract")

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
