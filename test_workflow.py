"""MVP invariants: immutable runs, native parameter contracts and truthful gates."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import socket
import subprocess
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import workflow as w

EXAMPLE = json.loads((w.ROOT / "workflows/development.json").read_text())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [patch.object(w, name, self.root / path) for name, path in (
            ("DEFINITIONS", "definitions"), ("VERSIONS", "versions"), ("RUNS", "runs"))]
        self.patchers.append(patch.object(w, "runtime_features", return_value=["codex_effort_pair_v1"]))
        self.patchers.append(patch.object(w, "MODEL_CACHE", self.root / "provider_models.json"))
        for p in self.patchers:
            p.start()
        w.write_json(w.MODEL_CACHE, {"claude_code": ["global.anthropic.claude-sonnet-4-6",
                                                    "global.anthropic.claude-opus-5"],
                                     "acp": ["kiro-native-model"]})
        self.definition = w.save(copy.deepcopy(EXAMPLE), 0)

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temp.cleanup()

    def prepare(self):
        calls = []
        def request(path, body):
            calls.append((path, body))
            return {"key": body["name"]} if path == "/api/chat/slots" else {"ok": True}
        with patch.object(w.api, "request", side_effect=request):
            manifest = w.prepare("development", 1)
        self.assertEqual([c[0] for c in calls], [
            "/api/chat/slots", f"/api/chat/slots/{manifest['slot']}/context"])
        w.workspace.prepare_project(Path(manifest["run"]), new=True)
        return manifest, Path(manifest["run"])

    def test_versions_and_running_snapshot_are_immutable(self):
        manifest, run = self.prepare()
        before = (run / "workflow-run.json").read_bytes()
        changed = w.change("development", "review", 1, effort="low")
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(w.latest("development")["steps"][2]["effort"], "low")
        self.assertEqual((run / "workflow-run.json").read_bytes(), before)
        self.assertEqual(w.load_run(run)["workflow"]["steps"][2]["effort"], "high")
        with self.assertRaisesRegex(ValueError, "版本冲突"):
            w.change("development", "review", 1, effort="max")
        self.assertEqual(len(list((w.VERSIONS / "development").glob("*.json"))), 2)

    def test_model_and_tool_constraints_do_not_silently_drop_effort(self):
        for fields in [{"tool": "gemini"}, {"tool": "opencode", "effort": "high"},
                       {"model": "auto", "effort": "high"},
                       {"model": "claude-haiku-4.5", "effort": "high"},
                       {"model": "model[high]"}]:
            value = copy.deepcopy(EXAMPLE)
            value["steps"][0].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                w.validate(value)

    def test_dispatch_preserves_every_native_parameter_and_user_input(self):
        _, run = self.prepare()
        w.capture(run, "这是用户原始需求。\n第二行。")
        next_call = w.next_step(run)
        self.assertEqual(next_call["spawn"]["agent"], "poc-kiro")
        self.assertEqual(next_call["spawn"]["model"], "auto")
        self.assertEqual(next_call["spawn"]["reasoning_effort"], "")
        self.assertFalse(next_call["spawn"]["include_memory"])
        self.assertIn("这是用户原始需求。\n第二行。", next_call["spawn"]["task"])
        with self.assertRaisesRegex(ValueError, "需求已冻结"):
            w.capture(run, "不要改成本次的另一个需求")

    def test_native_chat_submission_is_exact_and_ambiguous_send_not_retried(self):
        manifest, run = self.prepare()
        with patch.object(w.api, "request", side_effect=TimeoutError("late")) as request:
            self.assertEqual(w.submit(manifest, "用户请求一个计时器。")["status"], "uncertain")
            with self.assertRaises(ValueError):
                w.submit(manifest, "用户请求一个计时器。")
        request.assert_called_once_with("/api/chat?ws=1", {
            "slot": manifest["slot"], "message": "用户请求一个计时器。"})

    def test_snapshot_tampering_is_detected(self):
        manifest, run = self.prepare()
        manifest["workflow"]["steps"][0]["tool"] = "claude"
        w.write_json(run / "workflow-run.json", manifest)
        with self.assertRaisesRegex(ValueError, "快照已被修改"):
            w.load_run(run)

    def test_old_gateway_cannot_silently_run_explicit_codex_effort(self):
        manifest, run = self.prepare()
        w.capture(run, "这个运行要求明确设置 Codex 的 Effort。")
        with patch.object(w, "runtime_features", return_value=[]):
            with self.assertRaisesRegex(ValueError, "尚未加载"):
                w.next_step(run)
            with patch.object(w.api, "request") as request, self.assertRaises(ValueError):
                w.submit(manifest, "这个运行要求明确设置 Codex 的 Effort。")
            request.assert_not_called()

    def test_accept_requires_native_identity_not_agent_self_report(self):
        manifest, run = self.prepare()
        w.capture(run, "验证这次任务的真实后端。")
        step = manifest["workflow"]["steps"][0]
        task = {"id": "abc123", "parent": manifest["parent"], "done": True,
                "agent": "poc-kiro", "result": "我是 Kiro，已完成。状态：无 BLOCKED。"}
        row = {**step, "task": task, "route": {"actual_backend": "claude"},
               "requested_model": "auto", "resolved_model": "",
               "native_request": {"task_id": "abc123", "reasoning_effort": "", "cwd": str(run / "workspace/project")},
               "effort_observation": {"value": None, "source": "not_reported"}}
        snapshot = {"connected": True, "at": time.time(), "steps": [row]}
        w.write_json(run / "workflow-live.json", snapshot)
        (run / "workspace/plan-handoff.md").write_text("实际交接")
        with self.assertRaisesRegex(ValueError, "后端"):
            w.accept(run, "abc123")
        row["route"]["actual_backend"] = "kiro"
        task["parent"] = "dashboard:other"
        w.write_json(run / "workflow-live.json", snapshot)
        with self.assertRaisesRegex(ValueError, "父会话"):
            w.accept(run, "abc123")
        task["parent"] = manifest["parent"]
        row["native_request"]["cwd"] = str(run / "unrelated")
        w.write_json(run / "workflow-live.json", snapshot)
        with self.assertRaisesRegex(ValueError, "项目工作目录"):
            w.accept(run, "abc123")
        row["native_request"]["cwd"] = str(run / "workspace/project")
        w.write_json(run / "workflow-live.json", snapshot)
        self.assertTrue(w.accept(run, "abc123")["ok"])
        next_call = w.next_step(run)
        self.assertEqual(next_call["step"], "code")
        self.assertEqual(next_call["spawn"]["reasoning_effort"], "medium")
        self.assertEqual(next_call["spawn"]["model"], "global.anthropic.claude-sonnet-4-6")
        self.assertIn("code-instructions.md", next_call["spawn"]["task"])
        self.assertIn("我是 Kiro，已完成。", (run / "workspace/plan-result.txt").read_text())

    def test_empty_native_response_cannot_be_accepted_as_delivery(self):
        manifest, run = self.prepare()
        w.capture(run, "测试空返回不能变成已完成。")
        step = manifest["workflow"]["steps"][0]
        row = {**step, "task": {"id": "abc123de", "parent": manifest["parent"],
                               "done": True, "result": "_No response._"}}
        w.write_json(run / "workflow-live.json",
                     {"connected": True, "at": time.time(), "steps": [row]})
        (run / "workspace/plan-handoff.md").write_text("文件存在也不足以接受空返回。")
        with self.assertRaisesRegex(ValueError, "未返回完成结果"):
            w.accept(run, "abc123de")

    def test_model_matching_uses_native_id_not_fuzzy_family_match(self):
        self.assertTrue(w.model_matches("openai.gpt-6-astra", "openai.gpt-6-astra[high]"))
        self.assertFalse(w.model_matches("openai.gpt-6-astra", "openai.gpt-6-sol[high]"))
        self.assertFalse(w.model_matches("claude-opus-5", "global.anthropic.claude-opus-5"))

    def test_unknown_claude_model_is_refused_before_session_or_receipt_creation(self):
        definition = copy.deepcopy(EXAMPLE)
        definition["steps"][1]["model"] = "global.anthropic.claude-opus-5-5"
        # Portable definitions can be stored, but this host cannot silently run
        # a requested model it has not seen this adapter advertise.
        w.save(definition, 1)
        with patch.object(w.api, "request") as request:
            with self.assertRaisesRegex(ValueError, "Claude Code 当前公布"):
                w.prepare("development", 2)
            request.assert_not_called()
        self.assertFalse(w.RUNS.exists())

    def test_model_recheck_before_dispatch_preserves_existing_run(self):
        _, run = self.prepare()
        before = (run / "workflow-run.json").read_bytes()
        w.capture(run, "继续已有运行，但模型候选已变化。")
        w.write_json(run / "accepted/plan.json", {"task_id": "plan-1", "result": "计划完成"})
        w.write_json(w.MODEL_CACHE, {"claude_code": ["another-model"]})
        with self.assertRaisesRegex(ValueError, "Claude Code 当前公布"):
            w.next_step(run)
        self.assertEqual((run / "workflow-run.json").read_bytes(), before)
        self.assertFalse((run / "dispatch-code.json").exists())
        w.validate_model_selection([{"tool": "claude", "model": "auto", "name": "默认"}])

    def test_model_candidates_are_scoped_to_the_actual_harness(self):
        self.assertEqual(w.advertised_models("kiro"), ["kiro-native-model"])
        self.assertNotIn("kiro-native-model", w.advertised_models("claude"))
        w.write_json(w.MODEL_CACHE, {})
        with self.assertRaisesRegex(ValueError, "还没有 Claude Code"):
            w.validate_model_selection([{"tool": "claude", "model": "opus", "name": "编码"}])

    def test_model_check_failure_does_not_claim_the_coding_task_failed(self):
        definition = copy.deepcopy(EXAMPLE)
        definition["steps"][0].update(tool="claude", model="global.anthropic.claude-opus-5")
        w.save(definition, 1)
        with patch.object(w.api, "request", side_effect=lambda path, body:
                          {"key": body["name"]} if path == "/api/chat/slots" else {"ok": True}):
            manifest = w.prepare("development", 2)
        run = Path(manifest["run"])
        task = {"id": "model-check", "parent": manifest["parent"], "agent": "poc-claude",
                "done": True, "result": "测试实际通过。", "task": w.marker(manifest, "plan")}
        (run / "checks").mkdir()
        w.write_json(run / "checks/model-check.json",
                     {"passed": False, "error": "实际模型与配置不匹配。"})
        original_read = w.read_json
        def read(path, default=None):
            if str(path).endswith("/subagents/model-check/state.json"):
                return {"resolved_model": "global.anthropic.claude-sonnet-4-6",
                        "requested_model": "global.anthropic.claude-opus-5"}
            return original_read(path, default)
        with patch.object(w, "read_json", side_effect=read), patch.object(
                w, "dispatch_receipts", return_value={}), patch.object(
                w, "route_for", return_value={"actual_backend": "claude"}):
            projected = w.project(run, [task], {"running": False}, [])
        self.assertEqual(projected["status"], "check_failed")
        self.assertEqual(projected["steps"][0]["status"], "check_failed")
        self.assertTrue(projected["steps"][0]["model_mismatch"])
        self.assertTrue(projected["steps"][0]["task"]["done"])
        self.assertFalse((run / "accepted/plan.json").exists())

    def test_app_entry_bootstrap_does_not_change_session_or_persist_token(self):
        manifest, run = self.prepare()
        before = (run / "workflow-run.json").read_bytes()
        with patch.object(w.api, "token", return_value="test-token") as token:
            entry = w.app_entry(run)
        token.assert_called_once_with(5476, ttl="8h")
        self.assertIn("sid=" + manifest["slot"], entry["url"])
        self.assertIn("token=test-token", entry["url"])
        self.assertEqual(entry["expires_in"], 300)
        self.assertEqual((run / "workflow-run.json").read_bytes(), before)
        self.assertNotIn("test-token", (run / "app-context.txt").read_text())

    def test_app_entry_http_requires_same_origin_post_and_existing_run(self):
        manifest, _ = self.prepare()
        (self.root / "state").mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        ready = threading.Event()
        holder = {}
        def create(address, handler):
            holder["server"] = ThreadingHTTPServer(address, handler)
            ready.set()
            return holder["server"]
        def call(path, *, method="POST", origin=None, host=None, body=None):
            headers = {"Content-Type": "application/json", "X-Workflow-MVP": "1"}
            if origin:
                headers["Origin"] = origin
            if host:
                headers["Host"] = host
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                data=json.dumps(body or {"run": manifest["id"]}).encode() if method == "POST" else None,
                headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)
        with patch.object(w, "ROOT", self.root), patch.object(w, "observe", return_value=None), \
                patch.object(w, "ThreadingHTTPServer", side_effect=create), \
                patch.object(w.api, "chat_entry_url", return_value="native-auth-link") as mint:
            thread = threading.Thread(target=w.serve, args=(port,), daemon=True)
            thread.start()
            self.assertTrue(ready.wait(3))
            try:
                origin = f"http://127.0.0.1:{port}"
                status, capabilities = call("/api/runtime", method="GET")
                self.assertEqual(status, 200)
                self.assertIn("stage_retry_v1", capabilities["workbench_features"])
                self.assertIn("session_editor_v3", capabilities["workbench_features"])
                self.assertEqual(call("/api/app-entry")[0], 403)
                self.assertEqual(call("/api/app-entry", origin="http://unrelated.test")[0], 403)
                self.assertEqual(call("/api/app-entry", method="GET")[0], 404)
                self.assertEqual(call("/api/app-entry", origin=origin, host=f"localhost:{port}")[0], 403)
                self.assertEqual(call("/api/app-entry", origin=origin, body={"run": "../outside"})[0], 400)
                mint.assert_not_called()
                self.assertEqual(call("/api/app-entry", origin=origin)[0], 200)
                mint.assert_called_once_with(manifest["slot"])
                run = Path(manifest["run"])
                _, retry_receipts, _ = self.seed_retry(manifest, run)
                retry_body = {"run": manifest["id"], "stage": "code", "request_id": "http-retry-1"}
                with patch.object(w, "dispatch_receipts", return_value=retry_receipts), \
                        patch.object(w.api, "request") as native_mutation:
                    self.assertEqual(call("/api/retry", body=retry_body)[0], 403)
                    self.assertEqual(call("/api/retry", origin=origin,
                                          body={**retry_body, "stage": "../outside"})[0], 400)
                    first = call("/api/retry", origin=origin, body=retry_body)
                    self.assertEqual(first[0], 200)
                    self.assertTrue(first[1]["ok"])
                    self.assertEqual(call("/api/retry", origin=origin, body=retry_body), first)
                    self.assertEqual(call("/api/retry", origin=origin,
                                          body={**retry_body, "request_id": "http-retry-2"})[0], 400)
                    state = call("/api/state?run=" + manifest["id"], method="GET")[1]
                    self.assertEqual(state["steps"][1]["status"], "pending")
                    self.assertEqual(state["retry"], first[1])
                    native_mutation.assert_not_called()
                w.change("development", "code", 1, model="global.anthropic.claude-opus-5-5")
                body = {"workflow_id": "development", "revision": 2,
                        "request_id": "qa-invalid-model", "input": "不应创建这个任务。"}
                with patch.object(w.api, "request") as dispatch:
                    self.assertEqual(call("/api/start", origin=origin, body=body)[0], 400)
                    dispatch.assert_not_called()
                self.assertFalse((self.root / "state/workflow-intake/qa-invalid-model.json").exists())
                # A confirmed prior request remains retrievable after a newer
                # config is saved; it must never create a second native run.
                body.update(revision=1, request_id="qa-confirmed-retry")
                w.write_json(self.root / "state/workflow-intake/qa-confirmed-retry.json",
                             {"status": "accepted", "request_hash": w.fingerprint({
                                 "path": "/api/start", "body": body})})
                self.assertEqual(call("/api/start", origin=origin, body=body)[1]["status"], "accepted")
            finally:
                holder["server"].shutdown()
                holder["server"].server_close()
                thread.join(3)

    def test_public_config_keeps_model_and_effort_separate(self):
        definition = w.validate(EXAMPLE)
        self.assertEqual(definition["steps"][2]["model"], "openai.gpt-6-astra")
        self.assertEqual(definition["steps"][2]["effort"], "high")

    def seed_retry(self, manifest, run, statuses=("accepted", "failed", "pending", "pending")):
        w.capture(run, "验证阶段重试与候选隔离。")
        rows, receipts, tasks = [], {}, []
        for i, (step, status) in enumerate(zip(manifest["workflow"]["steps"], statuses)):
            task_id = "abc0000" + str(i)
            receipt = {"task_id": task_id, "agent": w.TOOLS[step["tool"]]["agent"],
                       "model": step["model"], "reasoning_effort": step["effort"],
                       "cwd": str(run / "workspace/project")}
            route = {"actual_backend": step["tool"], "crew_session_key": "subagent:" + task_id}
            task = {"id": task_id, "parent": manifest["parent"], "agent": receipt["agent"],
                    "done": status not in {"running", "submitted"}, "started": time.time() - 10,
                    "task": w.marker(manifest, step["id"]), "result": "真实执行夹具",
                    "error": "failure fixture" if status == "failed" else None}
            row = {**step, "status": status, "task": task if status != "pending" else None,
                   "native_request": receipt if status != "pending" else None,
                   "route": route, "attempts": [{"id": task_id}] if status != "pending" else [],
                   "requested_model": step["model"], "resolved_model": step["model"],
                   "effort_observation": {"value": step["effort"] or None}}
            if status != "pending":
                receipts[step["id"]] = receipt
                tasks.append(task)
                (run / "workspace" / (step["id"] + "-handoff.md")).write_text("旧交接")
            if status == "accepted":
                accepted = {"task_id": task_id, "result": task["result"], "route": route,
                            "requested_model": step["model"], "resolved_model": step["model"],
                            "native_request": receipt, "effort_observation": row["effort_observation"]}
                w.write_json(run / "accepted" / (step["id"] + ".json"), accepted)
            rows.append(row)
        snapshot = {"manifest": manifest, "connected": True, "at": time.time(),
                    "status": "failed", "parent_running": False, "approvals": [], "steps": rows}
        w.write_json(run / "workflow-live.json", snapshot)
        return snapshot, receipts, tasks

    def test_retry_invalidates_downstream_preserves_history_and_is_idempotent(self):
        manifest, run = self.prepare()
        snapshot, receipts, tasks = self.seed_retry(manifest, run, ("accepted",) * 4)
        original_manifest = (run / "workflow-run.json").read_bytes()
        with patch.object(w, "dispatch_receipts", return_value=receipts), \
                patch.object(w, "route_for", return_value=None):
            result = w.retry_stage(run, "code", "retry-history-1")
            self.assertEqual(result["invalidated"], ["review", "deliver"])
            self.assertTrue((run / "accepted/plan.json").is_file())
            for sid in ("code", "review", "deliver"):
                self.assertFalse((run / "accepted" / (sid + ".json")).exists())
                self.assertTrue((run / "accepted/previous" / (sid + "-retry-history-1.json")).is_file())
                self.assertEqual((run / "retry-history/retry-history-1" / sid /
                                  (sid + "-handoff.md")).read_text(), "旧交接")
                self.assertFalse((run / "workspace" / (sid + "-handoff.md")).exists())
            self.assertEqual(w.retry_stage(run, "code", "retry-history-1"), result)
            with self.assertRaisesRegex(ValueError, "请求 ID 冲突"):
                w.retry_stage(run, "review", "retry-history-1")
            with self.assertRaises(ValueError):
                w.retry_stage(run, "code", "retry-history-2")
            dispatch = w.next_step(run)
            self.assertEqual(dispatch["step"], "code")
            self.assertEqual(dispatch["tool"], "spawn_continue")
            self.assertEqual(dispatch["continue"]["conversation"], "abc00001")
            self.assertEqual(dispatch["spawn"]["agent"], "poc-claude")
            projected = w.project(run, tasks, {"running": False}, [])
            self.assertEqual([s["status"] for s in projected["steps"]],
                             ["accepted", "pending", "pending", "pending"])
            self.assertEqual(projected["status"], "ready")
            # A stale observer publication cannot check the old task back in.
            w.write_json(run / "workflow-live.json", snapshot)
            with self.assertRaisesRegex(ValueError, "旧候选"):
                w.accept(run, "abc00001")
        self.assertEqual((run / "workflow-run.json").read_bytes(), original_manifest)

    def test_retry_new_task_must_pass_existing_checkpoint_and_write_new_handoff(self):
        manifest, run = self.prepare()
        _, receipts, tasks = self.seed_retry(manifest, run, ("failed", "pending", "pending", "pending"))
        with patch.object(w, "dispatch_receipts", return_value=receipts), \
                patch.object(w, "route_for", return_value={"actual_backend": "kiro",
                                                         "crew_session_key": "subagent:abc00000"}), \
                patch.object(Path, "home", return_value=self.root):
            w.retry_stage(run, "plan", "retry-new-task")
            receipts["plan"] = {**receipts["plan"], "task_id": "def00000", "conversation": "abc00000"}
            task = {**tasks[0], "id": "def00000", "error": None, "started": time.time(), "result": "本轮完成"}
            projected = w.project(run, [*tasks, task], {"running": False}, [])
            projected["steps"][0]["requested_model"] = "auto"
            w.write_json(run / "workflow-live.json", projected)
            dispatch = w.next_step(run)
            self.assertTrue(dispatch["wait"])
            self.assertEqual(dispatch["task_id"], "def00000")
            self.assertEqual(projected["steps"][0]["status"], "awaiting_check")
            receipts["plan"] = {**receipts["plan"], "task_id": "def00001"}
            with self.assertRaisesRegex(ValueError, "旧候选"):
                w.accept(run, "def00000")
            receipts["plan"]["task_id"] = "def00000"
            with self.assertRaisesRegex(ValueError, "交接文件"):
                w.accept(run, "def00000")
            (run / "workspace/plan-handoff.md").write_text("本次交接")
            self.assertTrue(w.accept(run, "def00000")["ok"])
            self.assertTrue(w.read_json(run / "checks/def00000.json")["passed"])
            self.assertEqual(w.next_step(run)["step"], "code")

    def test_retry_fallback_and_rejected_states_do_not_mutate_evidence(self):
        for status in ("running", "submitted", "awaiting_check", "pending"):
            manifest, run = self.prepare()
            self.seed_retry(manifest, run, ("accepted", status, "pending", "pending"))
            before = (run / "accepted/plan.json").read_bytes()
            with self.subTest(status=status), self.assertRaises(ValueError):
                w.retry_stage(run, "code", "retry-invalid-state")
            self.assertFalse((run / "retry-state.json").exists())
            self.assertEqual((run / "accepted/plan.json").read_bytes(), before)
        manifest, run = self.prepare()
        snapshot, _, _ = self.seed_retry(manifest, run, ("check_failed", "pending", "pending", "pending"))
        snapshot["steps"][0]["native_request"] = None
        with patch.object(w, "dispatch_receipts", return_value={}):
            for fields in ({"parent_running": True}, {"approvals": [{"id": "approval"}]},
                           {"at": time.time() - 60}, {"connected": False}):
                w.write_json(run / "workflow-live.json", {**snapshot, **fields})
                with self.subTest(fields=fields), self.assertRaises(ValueError):
                    w.retry_stage(run, "plan", "retry-fallback-1")
                self.assertFalse((run / "retry-state.json").exists())
            w.write_json(run / "workflow-live.json", snapshot)
            w.retry_stage(run, "plan", "retry-fallback-1")
            self.assertEqual(w.next_step(run)["tool"], "spawn_run")
            self.assertEqual(w.next_step(run)["spawn"]["agent"], "poc-kiro")

    def test_retry_refuses_a_new_dispatch_not_yet_in_the_observer(self):
        manifest, run = self.prepare()
        _, receipts, _ = self.seed_retry(manifest, run)
        receipts["code"]["task_id"] = "def00001"
        with patch.object(w, "dispatch_receipts", return_value=receipts):
            with self.assertRaisesRegex(ValueError, "派发已更新"):
                w.retry_stage(run, "code", "retry-stale-dispatch")
        self.assertFalse((run / "retry-state.json").exists())

    def test_retry_rebuilds_in_project_when_previous_conversation_cwd_differs(self):
        for mismatch in ("request", "route"):
            manifest, run = self.prepare()
            snapshot, receipts, _ = self.seed_retry(manifest, run)
            if mismatch == "request":
                receipts["code"]["cwd"] = str(run / "workspace")
            else:
                snapshot["steps"][1]["route"]["cwd"] = str(run / "workspace")
            w.write_json(run / "workflow-live.json", snapshot)
            (run / "workspace/project/user-change.txt").write_text("keep developer edits")
            baseline = (run / "project.json").read_bytes()
            with self.subTest(mismatch=mismatch), \
                    patch.object(w, "dispatch_receipts", return_value=receipts):
                w.retry_stage(run, "code", "retry-cwd-" + mismatch)
                dispatch = w.next_step(run)
                self.assertEqual(dispatch["tool"], "spawn_run")
                self.assertNotIn("continue", dispatch)
                self.assertEqual(dispatch["spawn"]["cwd"], str(run / "workspace/project"))
                self.assertEqual((run / "project.json").read_bytes(), baseline)
                self.assertEqual((run / "workspace/project/user-change.txt").read_text(), "keep developer edits")

    def test_concurrent_retry_requests_share_one_invalidation(self):
        manifest, run = self.prepare()
        _, receipts, _ = self.seed_retry(manifest, run, ("accepted",) * 4)
        with patch.object(w, "dispatch_receipts", return_value=receipts), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(w.retry_stage, run, "code", "retry-concurrent") for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(w.retry_state(run)["requests"]), 1)
        self.assertEqual(len(list((run / "accepted/previous").glob("*.json"))), 3)

    def test_three_stage_auto_definition_can_prepare_and_dispatch_in_order(self):
        value = json.loads((Path(__file__).parent / "workflows/ui-optimize-three-stage.json").read_text())
        definition = w.save(value, 0)
        self.assertEqual([s["tool"] for s in definition["steps"]], ["kiro", "codex", "claude"])
        self.assertIn("Fabel 5.1", definition["steps"][2]["prompt"])
        w.write_json(w.MODEL_CACHE, {})
        with patch.object(w.api, "request", side_effect=lambda path, body:
                          {"key": body["name"]} if path == "/api/chat/slots" else {"ok": True}):
            manifest = w.prepare(value["id"], 1)
        run = Path(manifest["run"])
        w.capture(run, "验证三阶段可移植配置")
        self.assertEqual(w.next_step(run)["status"], "workspace_required")
        w.workspace.prepare_project(run, new=True)
        for step in definition["steps"]:
            dispatch = w.next_step(run)
            self.assertEqual(dispatch["step"], step["id"])
            self.assertEqual(dispatch["spawn"]["agent"], w.TOOLS[step["tool"]]["agent"])
            self.assertEqual(dispatch["spawn"]["model"], "auto")
            self.assertEqual(dispatch["spawn"]["reasoning_effort"], "")
            w.write_json(run / "accepted" / (step["id"] + ".json"), {"task_id": step["id"], "result": "夹具"})
        self.assertTrue(w.next_step(run)["done"])
        value["steps"][2]["model"] = "Fabel 5.1"
        with self.assertRaises(ValueError):
            w.validate(value)

    def test_session_progress_uses_the_shared_javascript_calculation(self):
        script = """
const assert = require("node:assert/strict");
const {calculate} = require(process.argv[1]);
assert.deepEqual(calculate(null), {done:0,total:0,percent:0});
for (const [statuses, expected] of [
  [["accepted","running"],50], [["accepted","failed"],50],
  [["accepted","accepted","pending"],67], [["accepted","accepted"],100],
  [["running","check_failed"],0], [["pending"],0]]) {
  const data = {manifest:{workflow:{steps:statuses.map((_,i)=>({id:String(i)}))}},
    steps:statuses.map(status=>({status}))};
  assert.equal(calculate(data).percent, expected);
}
console.log("progress: 7 cases passed");
"""
        result = subprocess.run(["node", "-e", script, str(Path(__file__).parent / "ui/workflow-progress.js")],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_long_input_and_history_use_files_within_native_task_limit(self):
        _, run = self.prepare()
        requirement = "完整需求" * 2500
        w.capture(run, requirement)
        w.write_json(run / "accepted/plan.json",
                     {"task_id": "native-1", "result": "真实前序结果" * 3000})
        dispatch = w.next_step(run)
        self.assertLessEqual(len(dispatch["spawn"]["task"]), 5000)
        self.assertIn(requirement, (run / "workspace/code-instructions.md").read_text())
        self.assertEqual((run / "workspace/plan-result.txt").read_text(), "真实前序结果" * 3000 + "\n")

    def test_step_ids_are_unique_and_no_shell_or_path_injection(self):
        value = copy.deepcopy(EXAMPLE)
        value["steps"][0]["id"] = "../outside"
        with self.assertRaises(ValueError):
            w.validate(value)
        value["steps"][0]["id"] = value["steps"][1]["id"]
        with self.assertRaisesRegex(ValueError, "不可重复"):
            w.validate(value)

    def test_preview_cannot_escape_or_read_hidden_runtime_files(self):
        _, run = self.prepare()
        workspace = run / "workspace"
        (workspace / "index.html").write_text("42")
        (workspace / ".private.json").write_text("not public")
        (workspace / "link.json").symlink_to(run / "workflow-run.json")
        self.assertEqual(w.preview_file(run, "index.html"), (workspace / "index.html").resolve())
        for path in ("../workflow-run.json", "%2e%2e/workflow-run.json", ".private.json", "link.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                w.preview_file(run, path)

    def test_queue_receipt_is_shown_before_native_task_enters_running_list(self):
        manifest, run = self.prepare()
        receipts = {"plan": {"task_id": "abc123", "reasoning_effort": ""}}
        with patch.object(w, "dispatch_receipts", return_value=receipts):
            projected = w.project(run, [], {"running": False}, [])
        self.assertEqual(projected["status"], "running")
        self.assertEqual(projected["steps"][0]["status"], "submitted")
        self.assertIsNone(projected["steps"][0]["task"])
        self.assertIsNone(projected["steps"][0]["route"])

    def test_real_tool_event_extracts_effort_and_task_identity(self):
        manifest, _ = self.prepare()
        event = {"role": "tool", "meta": {"tool_name": "@kirocrew-core/spawn_run",
                 "tool_call_id": "native-call-1", "input": json.dumps({
                     "task": w.marker(manifest, "review"), "agent": "poc-codex",
                     "model": "openai.gpt-6-astra", "reasoning_effort": "high"}),
                 "output": "Spawned 1 subagent(s):\n  abc123de (poc-codex): Review"}}
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps(event)):
            receipts = w.dispatch_receipts(manifest)
        self.assertEqual(receipts["review"]["task_id"], "abc123de")
        self.assertEqual(receipts["review"]["reasoning_effort"], "high")

    def test_chat_permission_is_visible_when_global_approvals_are_empty(self):
        _, run = self.prepare()
        meta = {"approval_id": "native-permission", "tool_title": "Running: spawn_run"}
        parent = {"running": True, "messages": [{"role": "permission", "meta": meta}]}
        with patch.object(w, "dispatch_receipts", return_value={}):
            projected = w.project(run, [], parent, [])
            self.assertEqual(projected["status"], "awaiting_approval")
            self.assertEqual(projected["approvals"][0]["source"], "native App permission event")
            meta["resolved"] = "approved"
            self.assertEqual(w.project(run, [], parent, [])["approvals"], [])
            native = {"id": "1", "source": "subagent", "tool": "Read file",
                      "slot": w.load_run(run)["slot"]}
            other = {**native, "id": "2", "slot": "unrelated-user-conversation"}
            projected = w.project(run, [], {"running": False}, [native, other])
            self.assertEqual(projected["approvals"], [native])
            self.assertEqual(projected["status"], "awaiting_approval")

    def test_verified_evidence_survives_gateway_memory_reset(self):
        manifest, run = self.prepare()
        accepted = {"task_id": "abc123de", "result": "Verified result",
                    "route": {"actual_backend": "kiro", "native_session_id": "native-1"},
                    "requested_model": "auto", "resolved_model": "",
                    "effort_observation": {"value": None, "source": "not_reported"},
                    "native_request": {"task_id": "abc123de", "reasoning_effort": "", "cwd": str(run / "workspace/project")}}
        w.write_json(run / "accepted/plan.json", accepted)
        with patch.object(w, "dispatch_receipts", return_value={}):
            row = w.project(run, [], {"running": False}, [])["steps"][0]
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["task"]["parent"], manifest["parent"])
        self.assertEqual(row["route"], accepted["route"])
        self.assertEqual(row["evidence_source"], "persisted verified checkpoint")

    def test_continuation_tracks_new_task_without_losing_original_parameters(self):
        manifest, run = self.prepare()
        w.capture(run, "验证续作继承原生请求并单独核对新任务。")
        events = [
            {"role": "tool", "meta": {"tool_name": "spawn_run", "tool_call_id": "first",
             "input": json.dumps({"task": w.marker(manifest, "plan"), "agent": "poc-kiro",
                                  "model": "auto", "reasoning_effort": "", "cwd": str(run / "workspace/project")}),
             "output": "Spawned 1 subagent(s):\n  abc123de (poc-kiro): plan"}},
            {"role": "tool", "meta": {"tool_name": "spawn_continue", "tool_call_id": "followup",
             "input": json.dumps({"conversation": "abc123de", "task": "补齐交接文件"}),
             "output": "Continued conversation abc123de as run def456ab."}},
        ]
        # Native event persistence may repeat the final tool event.
        events.append(events[-1])
        with patch.object(Path, "exists", return_value=True), patch.object(
                Path, "read_text", return_value="\n".join(map(json.dumps, events))):
            receipts = w.dispatch_receipts(manifest)
        receipt = receipts["plan"]
        self.assertEqual(receipt["task_id"], "def456ab")
        self.assertEqual(receipt["conversation"], "abc123de")
        self.assertEqual(receipt["agent"], "poc-kiro")
        self.assertEqual(receipt["model"], "auto")
        old = {"task_id": "abc123de", "result": "_No response._"}
        w.write_json(run / "accepted/plan.json", old)
        task = {"id": "def456ab", "parent": manifest["parent"], "agent": "poc-kiro",
                "done": True, "result": "实际补齐交接", "task": "补齐交接文件", "started": 2}
        with patch.object(w, "dispatch_receipts", return_value=receipts), patch.object(
                w, "route_for", return_value={"actual_backend": "kiro", "crew_session_key": "subagent:abc123de"}):
            projected = w.project(run, [task], {"running": False}, [])
            self.assertEqual(projected["steps"][0]["task"]["id"], "def456ab")
            self.assertEqual(projected["steps"][0]["status"], "awaiting_check")
            self.assertEqual(w.next_step(run)["step"], "plan")
            projected["steps"][0]["requested_model"] = "auto"
            w.write_json(run / "workflow-live.json", projected)
            (run / "workspace/plan-handoff.md").write_text("新一轮的真实交接。")
            self.assertTrue(w.accept(run, "def456ab")["ok"])
        self.assertEqual(w.read_json(run / "accepted/previous/plan-abc123de.json"), old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
