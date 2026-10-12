"""Failure cases that matter to the handoff: no synthetic completion."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import pipeline as p
from pipeline_spec import STAGES, EMPTY_CORE


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        (self.run / "gates").mkdir()
        self.manifest = {"id": "test-run", "parent": "dashboard:test-run", "slot": "test-run", "title": "Test"}
        p.write_json(self.run / "manifest.json", self.manifest)
        self.task = {"id": "native123", "parent": "dashboard:test-run", "agent": "poc-pipeline-code",
                     "task": "[PIPELINE:test-run STAGE:code]", "started": 1, "done": True, "outcome": "completed"}

    def review(self, **overrides):
        return {"candidate_sha256": "abc", "verdict": "pass", "tests_passed": True,
                "findings": [], "summary": "core review only", **overrides}

    def test_security_and_evidence_must_pass_together(self):
        for bad in [
            {"candidate_sha256": "other"}, {"tests_passed": False}, {"verdict": "fail"},
            {"findings": [{"severity": "high"}]}, {"findings": [{"severity": "unknown"}]},
            {"findings": "none"}, {"summary": ""},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                p.validate_review(self.review(**bad), "abc")
        p.validate_review(self.review(), "abc")

    def test_completed_process_requires_gate(self):
        with patch.object(p, "route_for", return_value=None):
            state = p.project_state(self.run, [self.task], {}, [])
        self.assertEqual(state["stages"][1]["status"], "checking")
        self.assertEqual(state["passed_count"], 0)

    def test_failed_native_task_is_never_success(self):
        with patch.object(p, "route_for", return_value=None):
            state = p.project_state(self.run, [{**self.task, "outcome": "failed"}], {}, [])
        self.assertEqual(state["status"], "blocked")

    def test_other_parent_and_approvals_are_excluded(self):
        other = {**self.task, "parent": "dashboard:private-user-session"}
        state = p.project_state(self.run, [other], {}, [{"session_key": "dashboard:private-user-session"}])
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["approvals"], [])
        self.assertIsNone(state["stages"][1]["task"])

    def test_parent_approval_is_visible_but_resolved_is_not(self):
        pending = {"role": "permission", "cls": json.dumps({"request_id": "approval1", "tool_title": "read task"})}
        done = {"role": "permission", "cls": json.dumps({"request_id": "approval2", "resolved": "approved"})}
        state = p.project_state(self.run, [], {"messages": [pending, done]}, [])
        self.assertEqual(state["status"], "awaiting_approval")
        self.assertEqual([a["id"] for a in state["approvals"]], ["approval1"])

    def test_checkpoint_rejects_wrong_run_and_actual_backend(self):
        with patch.object(p, "native_tasks", return_value=[]):
            value = p.checkpoint(self.run, "code", self.task["id"])
        self.assertFalse(value["passed"])
        self.assertIn("belongs to another run", value["error"])
        with patch.object(p, "native_tasks", return_value=[self.task]), patch.object(
            p, "route_for", return_value={"actual_backend": "kiro", "agent": self.task["agent"]}
        ):
            value = p.checkpoint(self.run, "code", self.task["id"])
        self.assertFalse(value["passed"])
        self.assertIn("backend", value["error"])

    def test_preview_only_serves_explicit_files(self):
        self.assertIsNone(p.public_file(self.run, "/files/../../config.json"))
        self.assertIsNone(p.public_file(self.run, "/files/manifest.json"))
        self.assertIsNone(p.public_file(self.run, "/preview/private.json"))
        self.assertIsNotNone(p.public_file(self.run, "/preview/index.html"))
        (self.run / "workspace/web").mkdir(parents=True)
        (self.run / "workspace/web/index.html").symlink_to("/etc/hosts")
        self.assertIsNone(p.public_file(self.run, "/preview/index.html"))

    def test_checkpoint_only_uses_fresh_scoped_observer_projection(self):
        manifest = {**self.manifest, "run": str(self.run)}
        snapshot = {"manifest": manifest, "connected": True, "last_success_at": time.time(),
                    "stages": [{"task": self.task}]}
        p.write_json(self.run / "live-state.json", snapshot)
        with patch.object(p.api, "request", side_effect=AssertionError("Agent must not call owner API")):
            self.assertEqual(p.native_tasks(manifest), [self.task])
            for changes in [{"connected": False}, {"last_success_at": time.time()-16},
                            {"manifest": {**manifest, "parent": "dashboard:another"}}]:
                p.write_json(self.run / "live-state.json", {**snapshot, **changes})
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    p.native_tasks(manifest)

    def test_continuation_uses_native_template_binding_not_guessed_agent(self):
        task = {**self.task, "agent": ""}
        stored = {"id": task["id"], "parent_session": task["parent"],
                  "agent": "poc-pipeline-code", "conversation_key": "subagent:original",
                  "execution_context": {"template_id": "poc-pipeline-code", "selection_kind": "template"}}
        with patch.object(p, "read_json", return_value=stored):
            result = p.task_identity(task)
        self.assertEqual(result["agent"], "poc-pipeline-code")
        self.assertEqual(result["identity_source"], "native-persistence")
        for bad in [{**stored, "parent_session": "dashboard:other"},
                    {**stored, "execution_context": {"template_id": "poc-kiro", "selection_kind": "template"}}]:
            with patch.object(p, "read_json", return_value=bad):
                self.assertEqual(p.task_identity(task)["agent"], "")

    def test_intake_uses_native_slot_context_and_chat_without_dispatching_workers(self):
        calls = []
        def native(path, body=None):
            calls.append((path, body))
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/chat?ws=1":
                return {"ok": True, "slot": body["slot"], "mid": "native-mid"}
            return {"ok": True}
        (self.run / "state").mkdir()
        with patch.object(p, "ROOT", self.run), patch.object(p, "RUNS", self.run / "state/runs"), \
                patch.object(p.api, "request", side_effect=native):
            manifest = p.new_run("做一个会议成本计算器，输入人数时薪时长，显示成本。", "成本计算器")
            run = Path(manifest["run"])
            self.assertFalse((run / "workspace/plan.md").exists())
            self.assertFalse((run / "workspace/tests/acceptance.test.mjs").exists())
            self.assertEqual((run / "workspace/web/core.mjs").read_text(), EMPTY_CORE)
            result = p.submit_run(manifest)
            self.assertEqual(result["status"], "accepted")
            self.assertEqual([x[0] for x in calls],
                             ["/api/chat/slots", f"/api/chat/slots/{manifest['slot']}/context", "/api/chat?ws=1"])
            self.assertEqual(calls[-1][1]["message"], (run / "workspace/REQUEST.md").read_text().strip())
            with self.assertRaises(ValueError):
                p.submit_run(manifest)

    def test_app_input_prepares_empty_conversation_then_captures_request_without_owner_api(self):
        (self.run / "state").mkdir()
        with patch.object(p, "ROOT", self.run), patch.object(p, "RUNS", self.run / "state/runs"), \
                patch.object(p.api, "request", side_effect=lambda path, body: {"key": body["name"]}
                             if path == "/api/chat/slots" else {"ok": True}):
            manifest = p.new_run()
        run = Path(manifest["run"])
        self.assertIsNone(manifest["request_sha256"])
        self.assertFalse((run / "workspace/REQUEST.md").exists())
        with patch.object(p.api, "request", side_effect=AssertionError("Agent cannot use owner identity")):
            captured = p.capture_request(run, "做一个单位换算器，支持摄氏和华氏温度转换。")
            self.assertTrue(captured["request_sha256"])
            with self.assertRaises(ValueError):
                p.capture_request(run, "另外一条修改后的需求，不可以改动冻结的原始记录。")

    def test_ambiguous_native_send_is_not_retried(self):
        (self.run / "workspace").mkdir()
        (self.run / "workspace/REQUEST.md").write_text("用户的本次完整产品需求。")
        p.write_json(self.run / "intake.json", {"status": "prepared"})
        manifest = {**self.manifest, "run": str(self.run)}
        with patch.object(p.api, "request", side_effect=TimeoutError("ack missing")) as native:
            self.assertEqual(p.submit_run(manifest)["status"], "uncertain")
            with self.assertRaises(ValueError):
                p.submit_run(manifest)
            self.assertEqual(native.call_count, 1)

    def test_requirement_validation(self):
        for text in ["", "short", 123, "x" * 12001, "some text with \x00 inside"]:
            with self.subTest(text=str(text)[:20]), self.assertRaises(ValueError):
                p.validate_request(text)

    def test_code_gate_rejects_modified_generated_spec_before_executing_tests(self):
        (self.run / "baseline").mkdir()
        (self.run / "workspace").mkdir()
        for name, value in {"baseline/core.mjs": EMPTY_CORE, "workspace/REQUEST.md": "original user request",
                            "workspace/TASK.md": "original constraints", "workspace/SPEC.md": "changed spec"}.items():
            (self.run / name).write_text(value)
        manifest = {**self.manifest, "schema": 2, "baseline_sha256": p.digest(self.run / "baseline/core.mjs"),
                    "request_sha256": p.digest(self.run / "workspace/REQUEST.md"),
                    "task_sha256": p.digest(self.run / "workspace/TASK.md")}
        p.write_json(self.run / "manifest.json", manifest)
        p.write_json(self.run / "gates/plan.json", {"passed": True, "frozen": {"SPEC.md": "old hash"}})
        with patch.object(p, "native_tasks", return_value=[self.task]), patch.object(
                p, "route_for", return_value={"actual_backend": "claude", "agent": self.task["agent"], "native_session_id": "native"}), \
                patch.object(p, "tests", side_effect=AssertionError("Must reject before tests")):
            gate = p.checkpoint(self.run, "code", self.task["id"])
        self.assertFalse(gate["passed"])
        self.assertIn("Frozen planning artifact changed", gate["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
