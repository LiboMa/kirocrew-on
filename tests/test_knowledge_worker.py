"""The Worker's side of Knowledge (D46 P4): a repository mark the platform's lessons are scoped to, lessons injected only
when the platform asks, and lessons written and deleted through KiroCrew's own /api/lessons."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composition import templates
from octopus import gateway_client as crew_api
from octopus.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
RID = "run-" + "1" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现加法并运行真实测试"}


class KnowledgeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        self.calls = []

        def native(path, body=None, method=None, timeout=None, headers=None):
            self.calls.append((path, body, method, headers))
            if path == "/api/chat/slots/octopus-" + RID and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + RID}
            if path == "/api/spawn":
                return {"id": "aabb1234"}
            return self.answer(path, body)
        self.answer = lambda path, body: {"ok": True}
        self.node.native = native

    def prepare(self, **extra):
        self.node.prepare({"run_id": RID, "source": {"type": "new"}, **extra})
        return Path(self.tmp.name) / "worker" / RID / "workspace/project"

    def test_a_prepared_repository_carries_its_mark_outside_git(self):
        project = self.prepare(knowledge_scope="h_o_r")
        self.assertTrue((project / ".octopus-kb/h_o_r").is_file())
        self.assertIn("/.octopus-kb/", (project / ".git/info/exclude").read_text().splitlines())
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True).stdout, "")
        self.prepare(knowledge_scope="h_o_r")   # a second prepare adds no second line
        self.assertEqual((project / ".git/info/exclude").read_text().splitlines().count("/.octopus-kb/"), 1)

    def test_a_symlinked_mark_or_exclude_file_is_not_followed(self):
        project = self.prepare(knowledge_scope="h_o_r")
        outside = Path(self.tmp.name) / "outside.txt"
        for name in (".octopus-kb/h_o_r", ".git/info/exclude"):
            with self.subTest(name):
                outside.write_text("keep\n")
                original = (project / name).read_bytes()
                (project / name).unlink()
                (project / name).symlink_to(outside)
                with self.assertRaises(Exception):
                    self.prepare(knowledge_scope="h_o_r")
                self.assertEqual(outside.read_text(), "keep\n")
                (project / name).unlink()
                (project / name).write_bytes(original)

    def test_no_mark_without_a_scope_and_a_bad_scope_is_refused(self):
        project = self.prepare()
        self.assertFalse((project / ".octopus-kb").exists())
        for bad in ("../x", ".hidden", "a/b", "x" * 121, 3):
            with self.subTest(bad), self.assertRaises(Exception):
                self.prepare(knowledge_scope=bad)

    def dispatch(self, **extra):
        self.prepare()
        self.node.dispatch({"run_id": RID, "attempt_id": "attempt-" + "2" * 32, "input": "需求", "spec": SPEC,
                            "upstream": [], **extra})
        return next(body for path, body, _, _ in self.calls if path == "/api/spawn")

    def test_lessons_are_injected_only_when_asked(self):
        spawn = self.dispatch(lessons=True)
        self.assertIs(spawn["include_lessons"], True)
        self.assertIs(spawn["include_memory"], False)

    def test_lessons_are_not_injected_by_default(self):
        spawn = self.dispatch()
        self.assertIs(spawn["include_lessons"], False)
        self.assertIs(spawn["include_memory"], False)

    def test_knowledge_is_written_as_scoped_always_lessons(self):
        self.prepare()
        result = self.node.knowledge_write({"run_id": RID, "scope": "h_o_r",
                                            "items": [{"rule": "用 pnpm 安装依赖", "avoid": "不要用 npm"}, {"rule": "测试在 tests/"}]})
        writes = [(body, method, headers) for path, body, method, headers in self.calls if path == "/api/lessons"]
        self.assertEqual(writes[0][0], {"rule": "用 pnpm 安装依赖", "category": "knowledge", "applies": "always",
                                        "repo_scope": ".octopus-kb/h_o_r", "negative": "不要用 npm"})
        self.assertNotIn("negative", writes[1][0])
        self.assertEqual({w[1] for w in writes}, {"POST"})
        self.assertEqual({w[2]["X-Session-Key"] for w in writes}, {"dashboard:ui"})
        self.assertEqual([r["result"] for r in result["results"]], ["written", "written"])
        self.assertEqual(result["results"][0]["rule"], "用 pnpm 安装依赖")

    def test_refusals_are_reported(self):
        self.prepare()
        answers = iter([{"ok": False, "outcome": "refused", "reason": "volatile_session_fact"},
                        crew_api.GatewayError(403, '{"error":"Lesson was NOT saved","code":"persistence_disabled"}'),
                        {"ok": True, "outcome": "written", "superseded": ["旧规则"]}])

        def answer(path, body):
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value
        self.answer = answer
        result = self.node.knowledge_write({"run_id": RID, "scope": "h_o_r", "items": [{"rule": "a"}, {"rule": "b"}, {"rule": "c"}]})
        self.assertEqual([r["result"] for r in result["results"]], ["refused", "refused", "superseded"])
        self.assertEqual(result["results"][0]["reason"], "volatile_session_fact")
        self.assertIn("persistence_disabled", result["results"][1]["reason"])
        self.assertIn("旧规则", result["results"][2]["reason"])

    def test_forgetting_deletes_the_exact_scoped_lesson(self):
        self.prepare()
        self.assertEqual(self.node.knowledge_forget({"run_id": RID, "scope": "h_o_r", "rule": "用 pnpm 安装依赖"}), {"ok": True})
        path, body, method, headers = self.calls[-1]
        self.assertEqual((path, body, method), ("/api/lessons", {"rule": "用 pnpm 安装依赖", "exact": True,
                                                                 "repo_scope": ".octopus-kb/h_o_r"}, "DELETE"))
        self.assertEqual(headers["X-Session-Key"], "dashboard:ui")

    def test_writes_and_deletes_check_what_they_are_given(self):
        self.prepare()
        for body in ({"run_id": RID, "scope": "../x", "items": [{"rule": "a"}]},
                     {"run_id": RID, "scope": "h_o_r", "items": [{"rule": ""}]},
                     {"run_id": RID, "scope": "h_o_r", "items": [{"rule": "a" * 501}]},
                     {"run_id": RID, "scope": "h_o_r", "items": [{"rule": "a"}] * 6}):
            with self.subTest(body=str(body)[:60]), self.assertRaises(Exception):
                self.node.knowledge_write(body)
        with self.assertRaises(Exception):
            self.node.knowledge_forget({"run_id": RID, "scope": "h_o_r", "rule": ""})

    def test_a_restore_puts_back_what_a_read_only_task_changed(self):
        project = self.prepare()
        (project / "README.md").write_text("初始\n")
        subprocess.run(["git", "add", "README.md"], cwd=project, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"], cwd=project, check=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip()
        (project / "README.md").write_text("Knowledge 任务改了它\n")
        (project / "KNOWLEDGE.md").write_text("新文件\n")
        subprocess.run(["git", "add", "KNOWLEDGE.md"], cwd=project, check=True)   # staged, not in HEAD
        result = self.node.candidate({"run_id": RID, "attempt_id": "attempt-" + "3" * 32, "mode": "restore", "expected": head})
        self.assertEqual((project / "README.md").read_text(), "初始\n")
        self.assertFalse((project / "KNOWLEDGE.md").exists())
        self.assertTrue(result["clean"])

    def test_a_restore_never_moves_the_branch(self):
        project = self.prepare()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip()
        with self.assertRaises(Exception):
            self.node.candidate({"run_id": RID, "attempt_id": "attempt-" + "3" * 32, "mode": "restore", "expected": "c" * 40})
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip(), head)

    def test_the_worker_says_it_writes_knowledge(self):
        with patch.object(Worker, "tools", lambda self: {}), patch.object(Worker, "tool_auth", lambda self: "ok"):
            self.assertIs(self.node.health()["knowledge"], True)


class CoordinatorTests(unittest.TestCase):
    def test_the_coordinator_cannot_write_lessons_itself(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
            root = Path(temporary)
            templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"], legacy_pipeline=False)
            spec = json.loads((root / "kiro/agents/poc-coordinator.json").read_text())
        self.assertTrue({"learn_add", "learn_remove"} <= set(spec["managedToolPolicy"]["exclude"]))


class ContractTests(unittest.TestCase):
    def test_the_contract_lists_the_lessons_endpoint(self):
        http = json.loads((ROOT / "tests/kirocrew_contract.json").read_text())["http"]
        self.assertTrue({"rule", "category", "negative", "repo_scope", "applies"} <= set(http["POST /api/lessons"]["reads"]))
        self.assertTrue({"rule", "exact", "repo_scope"} <= set(http["DELETE /api/lessons"]["reads"]))
        self.assertEqual({http[k]["module"] for k in ("POST /api/lessons", "DELETE /api/lessons")}, {"dashboard/handlers/cron.py"})


if __name__ == "__main__":
    unittest.main()
