"""Lifecycle and portability boundaries, isolated from all user state."""
import copy
from concurrent.futures import ThreadPoolExecutor
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import workflow as w
import workflow_library as lib
from workflow_files import emit_document, parse_document


EXAMPLE = {
    "schema": 1, "id": "example", "name": "中文工作流",
    "steps": [
        {"id": "build", "name": "编码", "tool": "claude", "model": "auto", "effort": "",
         "prompt": "支持中文：\n保留空行。\n\n代码包含 ``` 标记。\n验证真实结果。"},
        {"id": "review", "name": "审查", "tool": "codex", "model": "auto", "effort": "",
         "prompt": "读取真实 diff 并审查。"},
    ],
}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(w, key, self.root / value) for key, value in (
            ("ROOT", ""), ("DEFINITIONS", "workflows"), ("VERSIONS", "versions"),
            ("RUNS", "runs"), ("MODEL_CACHE", "models.json"))]
        for p in self.patches:
            p.start()
        self.definition = w.save(copy.deepcopy(EXAMPLE), 0)
        self.run = w.RUNS / "workflow-test-123"
        self.run.mkdir(parents=True)
        self.manifest = {"id": self.run.name, "run": str(self.run), "slot": self.run.name,
                         "parent": "dashboard:" + self.run.name, "created_at": 1,
                         "workflow": self.definition, "workflow_sha256": w.fingerprint(self.definition)}
        w.write_json(self.run / "workflow-run.json", self.manifest)
        self.before = (self.run / "workflow-run.json").read_bytes()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_formats_roundtrip_preserve_multiline_unicode_and_fences(self):
        for format in ["yaml", "markdown", "json"]:
            with self.subTest(format=format):
                content = emit_document(self.definition, format)
                self.assertEqual(parse_document(content, format), self.definition)
                imported = lib.import_workflow(content, format)
                self.assertNotEqual(imported["config"]["id"], self.definition["id"])
                self.assertEqual(imported["config"]["steps"], self.definition["steps"])
                self.assertEqual(imported["config"]["revision"], 0)
                self.assertEqual(w.latest("example")["revision"], 1)

    def test_names_are_unique_after_unicode_case_and_space_normalization(self):
        original = copy.deepcopy(EXAMPLE)
        original.update(id="names", name="Ｒｅｖｉｅｗ　 WorkFLOW")
        saved = w.save(original, 0)
        self.assertEqual(saved["name"], "Review WorkFLOW")
        for name in ["review workflow", "Review   Workflow", "ｒｅｖｉｅｗ WORKFLOW"]:
            candidate = {**original, "id": "duplicate", "name": name}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "已被"):
                w.save(candidate, 0)
        self.assertFalse((w.VERSIONS / "duplicate").exists())

    def test_duplicate_rename_does_not_mutate_either_definition_or_run(self):
        saved = w.save({**copy.deepcopy(EXAMPLE), "id": "other", "name": "独立工作流"}, 0)
        with self.assertRaisesRegex(ValueError, "已被"):
            w.save({**saved, "name": self.definition["name"]}, 1)
        self.assertEqual(w.latest("other"), saved)
        self.assertEqual(w.latest("example"), self.definition)
        self.assertEqual((self.run / "workflow-run.json").read_bytes(), self.before)
        self.assertEqual(w.save(saved, 1), saved)

    def test_deleted_names_stay_reserved_and_restore_keeps_identity(self):
        lib.delete_workflow("example", 1, True)
        with self.assertRaisesRegex(ValueError, "已被"):
            w.save({**copy.deepcopy(EXAMPLE), "id": "replacement"}, 0)
        lib.delete_workflow("example", 1, False)
        self.assertEqual(w.latest("example"), self.definition)

    def test_import_suggests_unique_name_but_save_rechecks(self):
        first = lib.import_workflow(json.dumps(EXAMPLE), "json")["config"]
        second = lib.import_workflow(json.dumps(EXAMPLE), "json")["config"]
        self.assertEqual(first["name"], "中文工作流 (2)")
        w.save(first, 0)
        with self.assertRaisesRegex(ValueError, "已被"):
            w.save(second, 0)
        third = lib.import_workflow(json.dumps(EXAMPLE), "json")["config"]
        self.assertEqual(third["name"], "中文工作流 (3)")

    def test_concurrent_saves_cannot_claim_the_same_name(self):
        def attempt(number):
            try:
                return w.save({**copy.deepcopy(EXAMPLE), "id": f"parallel-{number}",
                               "name": "同一名称"}, 0)
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(sum(isinstance(r, str) and "已被" in r for r in results), 3)
        self.assertEqual(len(lib.library()["definitions"]), 2)

    def test_invisible_name_characters_rejected_and_long_suffix_bounded(self):
        for name in ["中文\u200b工作流", "中文\u202e工作流", "\u200b"]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "不可见"):
                w.save({**copy.deepcopy(EXAMPLE), "id": "invisible", "name": name}, 0)
        name = "名" * 80
        w.save({**copy.deepcopy(EXAMPLE), "id": "long-name", "name": name}, 0)
        suggestion = lib.available_name(name)
        self.assertEqual(len(suggestion), 80)
        self.assertTrue(suggestion.endswith(" (2)"))

    def test_import_rejects_unsafe_ambiguous_or_duplicate_structure(self):
        for content, format in [
            ("x: !!python/object/apply:os.system ['touch /tmp/no']", "yaml"),
            ("x: &x [*x]", "yaml"), ("schema: 1\nschema: 2\n", "yaml"),
            ('{"schema":1,"schema":2}', "json"),
            ("# 普通文档，没有可导入配置。", "markdown"),
            ("```yaml\nschema: 1\n```\n```yaml\nschema: 1\n```\n", "markdown"),
        ]:
            with self.subTest(content=content), self.assertRaises(Exception):
                lib.import_workflow(content, format)
        duplicate = copy.deepcopy(EXAMPLE)
        duplicate["steps"][1]["id"] = duplicate["steps"][0]["id"]
        with self.assertRaisesRegex(ValueError, "ID"):
            lib.import_workflow(json.dumps(duplicate), "json")

    def test_archive_is_reversible_and_never_calls_gateway(self):
        with patch.object(w.api, "request") as request:
            lib.archive_sessions([self.run.name], True)
            self.assertTrue(lib.session_meta(self.run)["archived"])
            lib.archive_sessions([self.run.name], False)
            self.assertFalse(lib.session_meta(self.run)["archived"])
        request.assert_not_called()
        self.assertEqual((self.run / "workflow-run.json").read_bytes(), self.before)

    def test_batch_archive_validates_every_target_first(self):
        with self.assertRaises(ValueError):
            lib.archive_sessions([self.run.name, "workflow-missing"], True)
        self.assertFalse((self.run / "session-meta.json").exists())
        for values in [[self.run.name, self.run.name], ["../outside"], []]:
            with self.assertRaises(ValueError):
                lib.archive_sessions(values, True)

    def test_delete_retains_versions_and_sessions_and_blocks_new_runs(self):
        lib.delete_workflow("example", 1, True)
        self.assertIn("example", lib.library()["deleted"])
        self.assertEqual(w.latest("example"), self.definition)
        with patch.object(w.api, "request") as request:
            with self.assertRaisesRegex(ValueError, "删除"):
                w.prepare("example", 1)
            request.assert_not_called()
        with self.assertRaisesRegex(ValueError, "删除"):
            w.save(self.definition, 1)
        with self.assertRaisesRegex(ValueError, "版本冲突"):
            lib.delete_workflow("example", 99, False)
        lib.delete_workflow("example", 1, False)
        self.assertEqual(w.save(self.definition, 1), self.definition)
        self.assertEqual((self.run / "workflow-run.json").read_bytes(), self.before)

    def test_export_uses_native_bundle_and_does_not_claim_unfinished_release(self):
        bundle = {"messages": [{"role": "user", "content": "请实现分类导出。"},
                               {"role": "assistant", "content": "还未完成。"}],
                  "bundle_version": 2, "layer_b_skipped": True}
        raw = gzip.compress(json.dumps(bundle).encode())
        with patch.object(w.api, "export_session", return_value=raw) as native:
            exported = lib.export_sessions([self.run.name])
        native.assert_called_once_with(self.run.name)
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            prefix = self.run.name + "/"
            self.assertEqual(archive.read(prefix + "session.kcsession.json.gz"), raw)
            self.assertIn("尚未全部交接", archive.read(prefix + "release-notes.md").decode())
            self.assertIn(self.run.name + "/build", archive.read(prefix + "development-manual.md").decode())
            self.assertIn("请实现分类导出", archive.read(prefix + "features.json").decode())
            self.assertEqual(parse_document(archive.read(prefix + "workflow.yaml").decode(), "yaml"),
                             self.definition)
        self.assertEqual((self.run / "workflow-run.json").read_bytes(), self.before)

    def test_native_export_failure_does_not_produce_fake_success(self):
        with patch.object(w.api, "export_session", side_effect=RuntimeError("Gateway offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                lib.export_sessions([self.run.name])

    def test_design_admission_is_idempotent_and_does_not_save_workflow(self):
        request_id = str(uuid.uuid4())
        with patch.object(lib.threading, "Thread") as thread:
            first = lib.start_design("Claude 编码，Codex 审查。", request_id)
            second = lib.start_design("Claude 编码，Codex 审查。", request_id)
            self.assertEqual(first, second)
            self.assertEqual(thread.call_count, 1)
            with self.assertRaisesRegex(ValueError, "冲突"):
                lib.start_design("不同意图", request_id)
        self.assertEqual(len(lib.library()["definitions"]), 1)
        for invalid in ["../x", "-" * 32, None]:
            with self.assertRaises(ValueError):
                lib.start_design("测试工作流", invalid)

    def test_design_uses_existing_chat_and_validates_generated_file(self):
        with patch.object(lib.threading, "Thread"):
            job = lib.start_design("只编排工作流，不开发。", str(uuid.uuid4()))
        path = lib.design_path(job["id"])
        calls = []
        def request(endpoint, body):
            calls.append((endpoint, body))
            if endpoint == "/api/chat/slots":
                return {"key": body["name"]}
            (path / "workflow.json").write_text(json.dumps(EXAMPLE))
            return {"ok": True}
        with patch.object(w.api, "request", side_effect=request):
            lib._generate(path, job)
        result = lib.design_status(job["id"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual([p for p, _ in calls], ["/api/chat/slots", "/api/chat?ws=1"])
        self.assertEqual(calls[0][1]["agent"], "poc-kiro")
        self.assertEqual(result["config"]["id"], job["workflow_id"])
        self.assertEqual(result["config"]["revision"], 0)
        (path / "workflow.json").write_text('{"broken":true}')
        self.assertEqual(lib.design_status(job["id"])["status"], "invalid")
        self.assertEqual(len(lib.library()["definitions"]), 1)

    def test_task_identity_is_unique_across_sessions(self):
        with patch.object(w, "dispatch_receipts", return_value={}):
            first = w.project(self.run, [], {}, [])["steps"]
            other = w.RUNS / "workflow-test-456"
            other.mkdir()
            w.write_json(other / "workflow-run.json",
                         {**self.manifest, "id": other.name, "run": str(other)})
            second = w.project(other, [], {}, [])["steps"]
        ids = [s["task_uid"] for s in first + second]
        self.assertEqual(len(ids), len(set(ids)))

    def test_design_consumes_native_assistant_json_without_file_tool(self):
        with patch.object(lib.threading, "Thread"):
            job = lib.start_design("先编码，再审查。", str(uuid.uuid4()))
        path = lib.design_path(job["id"])
        job["status"] = "submitted"
        w.write_json(path / "job.json", job)
        state = {"running": False, "messages": [
            {"role": "user", "content": job["intent"]},
            {"role": "assistant", "content": "```json\n" + json.dumps(EXAMPLE) + "\n```"}]}
        with patch.object(w.api, "request", return_value=state) as request:
            result = lib.design_status(job["id"])
            request.assert_called_once_with("/api/chat/slots/" + job["slot"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["config"]["id"], job["workflow_id"])
        self.assertTrue((path / "workflow.json").exists())
        self.assertEqual(len(lib.library()["definitions"]), 1)


class TemplateTests(unittest.TestCase):
    def test_catalogue_is_ready_for_existing_workflow_contract(self):
        templates = json.loads((Path(__file__).parent / "ui/workflow-templates.json").read_text())
        self.assertEqual(len({t["id"] for t in templates}), len(templates))
        self.assertEqual(len({w.name_key(t["name"]) for t in templates}), len(templates))
        self.assertEqual({t["category"] for t in templates},
                         {"quick", "web", "engineering", "existing"})
        for template in templates:
            with self.subTest(template=template["id"]):
                definition = w.validate({
                    "schema": 1, "id": template["id"], "name": template["name"],
                    "steps": template["steps"],
                })
                w.text(template["input"], "示例需求", minimum=3, maximum=12000)
                self.assertTrue(template["prerequisite"].strip())
                self.assertTrue(template["output"].strip())
                self.assertTrue(all(s["model"] == "auto" and s["effort"] == ""
                                    for s in definition["steps"]))
                # Template metadata stays outside the portable workflow schema.
                for format in ["json", "yaml", "markdown"]:
                    self.assertEqual(parse_document(emit_document(definition, format), format),
                                     definition)


if __name__ == "__main__":
    unittest.main()
