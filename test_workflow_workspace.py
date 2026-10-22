"""Real local Git fixtures exercise checkout and dispatch; no model or network."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import workflow as w
import workflow_workspace as ws
import development_prompts as prompts
import poc


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source repo"
        self.source.mkdir()
        self.git(self.source, "init", "-b", "main")
        self.git(self.source, "config", "user.name", "Octopus Test Fixture")
        self.git(self.source, "config", "user.email", "fixture@example.invalid")
        (self.source / "app.py").write_text("print('source baseline')\n")
        self.git(self.source, "add", "app.py")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-m", "Fixture baseline")
        self.sha = self.git(self.source, "rev-parse", "HEAD")
        self.run = self.root / "workflow-fixture"
        self.run.mkdir()
        (self.run / "workspace").mkdir()
        self.definition = {"id": "fixture", "name": "研发任务", "schema": 1, "revision": 1,
            "steps": [{"id": "code", "name": "编码", "tool": "kiro", "model": "auto",
                       "effort": "", "prompt": "完成项目功能与验证。"}]}
        self.manifest = {"id": self.run.name, "slot": self.run.name, "run": str(self.run),
            "parent": "dashboard:" + self.run.name, "development_contract": 2,
            "workflow": self.definition, "workflow_sha256": w.fingerprint(self.definition)}
        w.write_json(self.run / "workflow-run.json", self.manifest)
        w.capture(self.run, "在明确指定的仓库上增加功能。")
        self.manifest_before = (self.run / "workflow-run.json").read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def git(cwd, *args):
        return subprocess.check_output(["git", "-C", str(cwd), *args],
                                       stderr=subprocess.STDOUT, text=True).strip()

    def test_clone_pins_baseline_and_repeated_call_preserves_edits(self):
        result = ws.prepare_project(self.run, source=str(self.source))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["commit"], self.sha)
        project = ws.project_dir(self.run)
        self.assertEqual((project / "app.py").read_text(), "print('source baseline')\n")
        (project / "app.py").write_text("print('development change')\n")
        self.assertEqual(ws.prepare_project(self.run, source=str(self.source)), result)
        self.assertEqual((project / "app.py").read_text(), "print('development change')\n")
        self.assertEqual((self.source / "app.py").read_text(), "print('source baseline')\n")
        self.assertEqual((self.run / "workflow-run.json").read_bytes(), self.manifest_before)
        with self.assertRaisesRegex(ValueError, "已经固定"):
            ws.prepare_project(self.run, source=str(self.source), ref=self.sha)

    def test_concurrent_preparation_has_one_checkout(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: ws.prepare_project(self.run, source=str(self.source)), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertFalse(list((self.run / "workspace").glob(".checkout-*")))

    def test_explicit_branch_records_its_commit(self):
        self.git(self.source, "checkout", "-b", "feature/example")
        (self.source / "feature.txt").write_text("feature\n")
        self.git(self.source, "add", ".")
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "-m", "Feature fixture")
        feature_sha = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.source, "checkout", "main")
        record = ws.prepare_project(self.run, source=str(self.source), ref="feature/example")
        self.assertEqual(record["commit"], feature_sha)
        self.assertTrue((ws.project_dir(self.run) / "feature.txt").is_file())

    def test_invalid_ref_keeps_evidence_and_can_retry(self):
        with self.assertRaisesRegex(ValueError, "源码准备失败"):
            ws.prepare_project(self.run, source=str(self.source), ref="missing-branch")
        failed = ws.read_record(self.run)
        self.assertEqual(failed["status"], "failed")
        attempt = self.run / failed["retained_attempt"]
        self.assertTrue(attempt.is_dir())
        self.assertEqual(ws.prepare_project(self.run, source=str(self.source))["status"], "ready")
        self.assertTrue(attempt.is_dir())

    def test_dirty_local_source_is_not_silently_lost(self):
        (self.source / "app.py").write_text("uncommitted work\n")
        with self.assertRaisesRegex(ValueError, "未提交"):
            ws.prepare_project(self.run, source=str(self.source))
        self.assertEqual((self.source / "app.py").read_text(), "uncommitted work\n")
        self.assertEqual(ws.read_record(self.run)["status"], "failed")
        result = ws.prepare_project(self.run, source=str(self.source), ref=self.sha)
        self.assertEqual(result["commit"], self.sha)
        self.assertEqual((self.source / "app.py").read_text(), "uncommitted work\n")

    def test_dispatch_rechecks_pinned_project_record(self):
        ws.prepare_project(self.run, source=str(self.source))
        record = ws.read_record(self.run)
        record["commit"] = "--help"
        w.write_json(self.run / "project.json", record)
        with self.assertRaisesRegex(ValueError, "commit 无效"):
            ws.project_dir(self.run)

    def test_new_project_and_existing_unregistered_files(self):
        (self.run / "workspace/project").mkdir()
        (self.run / "workspace/project/user.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "已有未登记文件"):
            ws.prepare_project(self.run, new=True)
        self.assertEqual((self.run / "workspace/project/user.txt").read_text(), "keep")

    def test_source_and_path_validation(self):
        for source in ["--upload-pack=evil", "https://token@github.com/org/repo.git",
                       "https://host/repo.git?token=x", "file:///etc", "relative/repo"]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                ws.normalize_source(source)
        self.assertEqual(ws.normalize_source("git@example.com:team/repo.git")[1], "git")
        (self.run / "workspace/project").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            ws.prepare_project(self.run, source=str(self.source))
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD"), self.sha)

    def test_gate_stops_dispatch_until_real_source_is_ready(self):
        with patch.object(w, "require_runtime"), patch.object(w, "dispatch_receipts", return_value={}):
            pending = w.next_step(self.run)
            self.assertEqual(pending["status"], "workspace_required")
            self.assertNotIn("spawn", pending)
            ws.prepare_project(self.run, source=str(self.source))
            dispatch = w.next_step(self.run)
        self.assertEqual(dispatch["spawn"]["cwd"], str(self.run / "workspace/project"))
        instructions = (self.run / "workspace/code-instructions.md").read_text()
        self.assertIn("项目级依赖安装", instructions)
        self.assertIn(str(self.run / "project.json"), instructions)
        self.assertEqual((self.run / "workflow-run.json").read_bytes(), self.manifest_before)

    def test_old_run_retains_original_directory_without_new_gate(self):
        old = copy.deepcopy(self.manifest)
        old.pop("development_contract")
        w.write_json(self.run / "workflow-run.json", old)
        with patch.object(w, "require_runtime"), patch.object(w, "dispatch_receipts", return_value={}):
            dispatch = w.next_step(self.run)
        self.assertEqual(dispatch["spawn"]["cwd"], str(self.run / "workspace"))

    def test_runtime_prompt_contains_source_preparation_and_authorized_development(self):
        prompt = w.control_prompt(self.manifest)
        self.assertIn("workspace_required", prompt)
        self.assertIn("--source", prompt)
        self.assertIn("已有授权", prompt)
        self.assertNotIn("当前 PoC 仅在指定工作目录", prompt)
        design = prompts.legacy_intent_prompt("{}", "改进用户仓库", self.run / "design")   # this runtime's own text (D46)
        self.assertIn("核实用户明确提供的仓库", design)
        self.assertIn("工作流设计者", design)
        self.assertNotIn("不要调用任何工具", design)


class AgentContractTests(unittest.TestCase):
    def test_seed_updates_prompts_without_resetting_user_tools_or_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crew, kiro = root / "crew", root / "kiro"
            crew.mkdir()
            (kiro / "agents").mkdir(parents=True)
            config = crew / "config.json"
            config.write_text('{"user_config":"preserve"}\n')
            session = crew / "session-fixture.json"
            session.write_text('{"history":"preserve"}\n')
            agent = kiro / "agents/poc-claude.json"
            previous = {"name":"poc-claude","description":"old","prompt":"old",
                        "model":"user-model","tools":["read","write","shell","user-tool"],
                        "resources":["skill://user-skill"],"mcpServers":{"user-mcp":{"command":"user-command"}},
                        "allowedTools":["read"],"includeMcpJson":True}
            agent.write_text(json.dumps(previous))
            stub = SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command":"native-managed-entry"})
            with patch.dict("sys.modules", {"kiro_crew.agent":stub}), \
                    patch.multiple(poc, CREW=crew, KIRO=kiro, STATE=root / "state"):
                poc.seed()
                poc.seed()
            updated = json.loads(agent.read_text())
            for key, value in previous.items():
                if key not in {"prompt","description"}:
                    self.assertEqual(updated[key], value)
            self.assertIn("真实研发工作", updated["prompt"])
            # the user's own config stays; the seed only adds KiroCrew's YOLO switch (crew_yolo, on by default)
            self.assertEqual(json.loads(config.read_text()), {"user_config": "preserve", "agent": {"dangerously_skip_permissions": True},
                                                              "session": {"eager_spawn": False}})   # D63
            self.assertEqual(session.read_text(), '{"history":"preserve"}\n')
            legacy = json.loads((kiro / "agents/poc-pipeline-code.json").read_text())
            self.assertIn("本地多 ACP PoC", legacy["prompt"])
            self.assertEqual(legacy["description"], "App PoC / claude worker")


if __name__ == "__main__":
    unittest.main()
