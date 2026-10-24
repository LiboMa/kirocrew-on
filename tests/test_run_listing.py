"""M3-3 / M3-5 (owner 2026-10-21): the session list filtered on the server, and a compact list for Coding Agents — the
whole list (101 Sessions, about 550 000 characters on EC2) was too large for an MCP result."""
import json
import unittest

from octopus import mcp
from octopus.security import Error
from tests.test_confirmation import ConfirmationCase


class ServiceListingTests(ConfirmationCase):
    def setUp(self):
        super().setUp()
        self.ids = [self.start(input=words) for words in ("实现登录页", "修复导出报表", "实现登录接口")]
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked' WHERE id=?", (self.ids[0],))

    def test_the_list_is_filtered_on_the_server_newest_first(self):
        self.assertEqual([r["id"] for r in self.service.runs(self.actor, status="blocked")], [self.ids[0]])
        self.assertEqual([r["id"] for r in self.service.runs(self.actor, q="登录")], [self.ids[2], self.ids[0]])
        self.assertEqual([r["id"] for r in self.service.runs(self.actor, q=self.ids[1][:12])], [self.ids[1]])
        self.assertEqual([r["id"] for r in self.service.runs(self.actor, limit=2)], [self.ids[2], self.ids[1]])
        self.assertEqual(len(self.service.runs(self.actor)), 3)   # without filters: as before

    def test_the_list_view_leaves_out_what_only_the_session_page_shows(self):   # tasks' prompts were two thirds of it
        full, slim = self.service.runs(self.actor), self.service.runs(self.actor, view="list")
        self.assertEqual([r["id"] for r in slim], [r["id"] for r in full])
        for run in slim:
            self.assertTrue(run["tasks"])
            for task in run["tasks"]:
                self.assertEqual(set(task), {"id", "status", "spec"})
                self.assertLessEqual(set(task["spec"]), {"id", "name", "tool", "model", "kind", "role"})
            self.assertNotIn("summary", run.get("planning") or {})
            for key in ("id", "name", "input", "status", "lifecycle", "progress", "updated", "team_id", "owner_id"):
                self.assertIn(key, run)
        self.assertLess(len(json.dumps(slim, ensure_ascii=False)), len(json.dumps(full, ensure_ascii=False)))

    def test_the_list_view_is_slim_and_skips_what_only_the_session_page_reads(self):
        # EC2 2026-10-24: 50 rows were 223 KB — the requirement alone 28 %, the replan's instructions and reason 10 %, and
        # usage, knowledge, credentials and verification (each read from every Attempt) that no list shows
        long = self.start(input="实现登录页并补充回归测试。" * 60)
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (long,)).fetchone()["snapshot"])
            snapshot["replan"] = {"mode": "replan", "instructions": "按新的需求重新规划" * 200, "reason": "需求变了" * 200,
                                  "intent": "登录" * 100, "tasks": ["code"]}
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (json.dumps(snapshot, ensure_ascii=False), long))
        full = next(r for r in self.service.runs(self.actor) if r["id"] == long)
        slim = next(r for r in self.service.runs(self.actor, view="list") if r["id"] == long)
        self.assertTrue(full["input"].startswith(slim["input"].removesuffix("…")))
        self.assertLessEqual(len(slim["input"]), 201)
        self.assertTrue(slim["input"].endswith("…"))
        self.assertEqual(slim["replan"], {"mode": "replan", "tasks": ["code"]})
        for key in ("usage", "knowledge", "credentials", "verified"):
            self.assertIn(key, full)
            self.assertNotIn(key, slim)
        short = next(r for r in self.service.runs(self.actor, view="list") if r["id"] == self.ids[0])
        self.assertEqual(short["input"], "实现登录页")   # a short requirement is whole
        page = self.service.runs(self.actor, view="list", page=True, limit=50)   # the page the console reads
        self.assertNotIn("usage", page["items"][0])

    def test_a_bad_filter_is_refused(self):
        for kwargs in ({"status": "nope"}, {"limit": 0}, {"limit": 201}, {"q": "x" * 201}, {"view": "everything"}):
            with self.subTest(kwargs), self.assertRaises(Error):
                self.service.runs(self.actor, **kwargs)


class Client:
    def __init__(self, views):
        self.views, self.paths = views, []

    def request(self, method, path, body=None):
        self.paths.append(path)
        return self.views


VIEW = {"name": "会话", "status": "blocked", "created": 1.0, "updated": 2.0, "worker_id": "w", "input": "需" * 3000,
        "error": "协调者需要你处理具体阻塞\n" + "详情" * 2000, "attention": {"title": "协调者需要你处理具体阻塞", "detail": "x" * 5000},
        "progress": {"accepted": 1, "total": 3}, "placement": {"kind": "own"}, "tasks": [{"spec": {"prompt": "p" * 5000}}]}


class McpListingTests(unittest.TestCase):
    def test_the_list_is_compact_filtered_and_says_when_there_is_more(self):
        client = Client([{**VIEW, "id": f"run-{n}"} for n in range(3)])
        result = mcp.call(client, "octopus_runs", {"status": "blocked", "q": "登录", "limit": 2})
        self.assertEqual(client.paths, ["/v1/runs?status=blocked&q=%E7%99%BB%E5%BD%95&limit=3&view=list"])
        self.assertEqual([i["id"] for i in result["items"]], ["run-0", "run-1"])
        self.assertTrue(result["more"])
        self.assertEqual(set(result["items"][0]), {"id", "name", "status", "created", "updated", "progress", "attention",
                                                   "error", "placement", "worker_id"})
        self.assertEqual(result["items"][0]["attention"], "协调者需要你处理具体阻塞")
        self.assertLessEqual(len(result["items"][0]["error"]), 200)
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 1500)

    def test_by_default_the_newest_twenty(self):
        client = Client([{**VIEW, "id": "run-a"}])
        result = mcp.call(client, "octopus_runs", {})
        self.assertEqual(client.paths, ["/v1/runs?limit=21&view=list"])
        self.assertFalse(result["more"])
        schema = next(t for t in mcp.TOOLS if t["name"] == "octopus_runs")["inputSchema"]["properties"]
        self.assertEqual(set(schema), {"status", "q", "limit", "space", "repo", "branch", "stage", "tool", "client", "submitter",
                                       "since", "until", "shared"})   # D64: shared with the user

    def test_the_filters_are_passed_to_the_server(self):   # D56
        client = Client([{**VIEW, "id": "run-a"}])
        mcp.call(client, "octopus_runs", {"repo": "github.com/o/r", "client": "codex", "submitter": "bob", "since": "2026-10-20",
                                          "stage": "review", "tool": "codex", "space": "personal", "q": "登录 缓存"})
        self.assertEqual(client.paths, ["/v1/runs?q=%E7%99%BB%E5%BD%95+%E7%BC%93%E5%AD%98&space=personal&repo=github.com%2Fo%2Fr"
                                        "&stage=review&tool=codex&client=codex&submitter=bob&since=2026-10-20&limit=21&view=list"])


if __name__ == "__main__":
    unittest.main()
