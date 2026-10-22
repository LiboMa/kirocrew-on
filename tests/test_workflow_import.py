"""Workflows generated ahead of time by an Agent are parsed and normalized by code, then run as submitted."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from octopus.db import Database
from octopus.domain import Service
from octopus.security import Error, authenticate, create_user, issue_token
from octopus.server import server
from octopus.workflow_import import import_workflow

STEPS = [
    {"id": "code", "name": "实现与测试", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现功能并运行测试"},
    {"id": "review", "name": "独立审查", "tool": "codex", "model": "auto", "effort": "high", "prompt": "独立审查前序交接"},
]


class ImportWorkflowTests(unittest.TestCase):
    def test_console_export_round_trips(self):
        result = import_workflow(json.dumps({"name": "编码与审查", "steps": STEPS}))
        self.assertEqual(result, {"name": "编码与审查", "steps": STEPS, "notes": []})

    def test_legacy_workflow_studio_document_is_accepted(self):
        legacy = {"schema": 1, "id": "flow-2147c84b", "revision": 3, "name": "旧版流程",
                  "steps": [{"id": "first", "name": "分析与实现", "tool": "kiro", "model": "auto", "effort": "",
                             "prompt": "根据用户需求分析任务并完成实现"}]}
        result = import_workflow(json.dumps(legacy))
        self.assertEqual(result["name"], "旧版流程")
        self.assertEqual(result["steps"][0]["tool"], "kiro")
        self.assertEqual(result["notes"], [])

    def test_markdown_with_one_code_block_and_yaml_are_detected(self):
        markdown = "Agent 生成的工作流如下：\n\n```json\n" + json.dumps({"name": "MD", "steps": STEPS}) + "\n```\n"
        self.assertEqual(import_workflow(markdown)["steps"], STEPS)
        yaml_text = "name: YAML 流程\nsteps:\n  - id: code\n    name: 实现\n    tool: claude\n    prompt: 实现功能\n"
        result = import_workflow(yaml_text)
        self.assertEqual(result["name"], "YAML 流程")
        self.assertEqual(result["steps"][0]["model"], "auto")

    def test_agent_generated_steps_are_normalized_by_code(self):
        generated = [
            {"title": "需求分析", "agent": "Kiro CLI", "description": "分析需求并拆分任务"},
            {"name": "Implement Feature", "tool": "claude-code", "instructions": "实现并测试", "depends_on": ["需求分析"]},
            {"id": "Code Review", "name": "审查", "tool": "Codex", "prompt": "独立审查"},
            {"id": "code-review", "name": "交付", "tool": "opencode", "effort": "high", "prompt": "整理交付"},
        ]
        result = import_workflow(json.dumps(generated))
        self.assertEqual([s["id"] for s in result["steps"]], ["task-1", "task-2", "code-review", "task-4"])
        self.assertEqual([s["tool"] for s in result["steps"]], ["kiro", "claude", "codex", "opencode"])
        self.assertEqual(result["steps"][0]["prompt"], "分析需求并拆分任务")
        self.assertEqual(result["steps"][3]["effort"], "")
        self.assertEqual(result["name"], "导入的工作流")
        self.assertTrue(any("depends_on" in note for note in result["notes"]))
        self.assertTrue(any("OpenCode" in note for note in result["notes"]))

    def test_invalid_documents_explain_why(self):
        cases = [
            ("", "非空"),
            ("name: 流程\nsteps: [\n  {name: a", "YAML 第"),
            ("a: b: c", "YAML 第"),
            ("x: !!python/object:os.system ls", "YAML"),
            (json.dumps([{"name": "x", "tool": "gpt", "prompt": "p"}]), "Coding 工具"),
            (json.dumps({"steps": [{"name": f"t{i}", "tool": "claude", "prompt": "p"} for i in range(13)]}), "1–12"),
            ("```json\n[]\n```\n```json\n[]\n```", "一个完整"),
            (json.dumps({"name": "无任务"}), "steps"),
            (json.dumps([{"name": "x", "tool": "claude"}]), "任务说明"),
        ]
        for content, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(Error) as caught:
                    import_workflow(content)
                self.assertIn(reason, str(caught.exception))
                self.assertEqual(caught.exception.status, 400)


class ImportWorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name))
        with self.database.transaction() as db:
            owner = create_user(db, "importer", "Import-password-123!", True)
            self.token = issue_token(db, owner["id"])["token"]
            self.actor = authenticate(db, self.token)
        self.service = Service(self.database)

    def test_a_submitted_document_runs_exactly_as_generated(self):
        document = {"content": "```json\n" + json.dumps({"name": "预生成", "steps": STEPS}) + "\n```"}
        run = self.service.start(self.actor, {"request_id": "import-1", "input": "按预生成的工作流开发",
                                              "source": {"type": "new"}, "document": document})
        tasks = [t["id"] for t in self.service.run(self.actor, run["id"])["tasks"]]
        self.assertEqual(tasks, ["code", "review"])

    def test_document_excludes_other_selections(self):
        with self.assertRaisesRegex(Error, "只指定"):
            self.service.resolve_workflow(self.actor, {"input": "按需求开发", "steps": STEPS,
                                                       "document": {"content": json.dumps(STEPS)}})

    def test_parse_endpoint_previews_without_creating_a_session(self):
        httpd = server(self.database, 0, host="127.0.0.1")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        body = json.dumps({"content": json.dumps({"name": "预览", "steps": STEPS})})
        connection.request("POST", "/v1/workflows/parse", body=body, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "Host": f"127.0.0.1:{httpd.server_address[1]}"})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(result["steps"], STEPS)
        self.assertEqual(self.service.runs(self.actor), [])


class ImportWorkflowMcpTests(unittest.TestCase):
    def test_clients_can_parse_and_submit_generated_workflows(self):
        from octopus.mcp import TOOLS
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertIn("octopus_parse_workflow", tools)
        self.assertIn("document", tools["octopus_start"]["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
