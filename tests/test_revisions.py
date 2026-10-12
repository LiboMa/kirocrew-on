"""Session revisions: a full snapshot at every change, and the differences between any two (D32)."""
import json
import unittest

from octopus import mcp, planning
from octopus.db import dumps
from octopus.security import Error
from tests import test_access

STEPS = [{"id": "plan", "name": "规划", "tool": "kiro", "prompt": "识别意图\n拆分任务"},
         {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现数字时钟\n不引入外部资源"}]
PLAN = json.dumps({"name": "数字时钟", "steps": [{"id": "review", "name": "审查", "tool": "codex", "prompt": "审查实现"}]})


class RevisionTests(test_access.AccessCase):
    def setUp(self):
        super().setUp()
        self.shared_env(users=("bob",))
        self.rid = self.service.start(self.b, {"request_id": "start-revision-01", "input": "做一个数字时钟\n每秒刷新",
                                               "source": {"type": "new"}, "steps": STEPS,
                                               "recovery": {"enabled": False, "max_cycles": 2}})["id"]

    def plan(self):
        with self.db.transaction() as db:
            planning.expand(db, self.rid, PLAN)

    def continue_code(self, version=2, **execution):
        with self.db.transaction() as db:
            db.execute("UPDATE tasks SET status='accepted' WHERE run_id=? AND id='plan'", (self.rid,))
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (self.rid,))
        return self.service.operate(self.b, self.rid, "continue", {
            "request_id": "continue-rev-01", "expected_version": version, "task_id": "code",
            "execution": {"tool": "codex", "prompt": "实现数字时钟\n12 小时制补零\n不引入外部资源", **execution}})

    def test_each_change_records_a_full_snapshot_and_its_kind(self):
        self.plan()
        self.continue_code()
        history = self.service.revisions(self.b, self.rid)
        self.assertEqual([(r["version"], r["kind"]) for r in history], [(1, "created"), (2, "planned"), (3, "continued")])
        self.assertEqual([r["summary"] for r in history], ["创建：2 个任务", "规划：追加 1 个任务", "续作「实现」：工具 claude → codex、提示词"])
        self.assertEqual({r["actor"]["username"] for r in history}, {"bob"})
        self.assertFalse(any(r["reconstructed"] for r in history))
        latest = self.service.revision(self.b, self.rid, 3)["snapshot"]
        self.assertEqual(latest["input"], "做一个数字时钟\n每秒刷新")
        self.assertEqual([t["id"] for t in latest["tasks"]], ["plan", "code", "review"])
        self.assertEqual({k: latest["tasks"][1][k] for k in ("tool", "prompt")},
                         {"tool": "codex", "prompt": "实现数字时钟\n12 小时制补零\n不引入外部资源"})
        self.assertEqual(self.service.revision(self.b, self.rid, 1)["snapshot"]["tasks"][1]["tool"], "claude")

    def test_diff_reports_requirement_task_fields_and_prompt_lines(self):
        self.plan()
        self.continue_code()
        diff = self.service.revision_diff(self.b, self.rid, 2, 3)
        self.assertEqual((diff["from"], diff["to"]), (2, 3))
        self.assertTrue(all(line["op"] == " " for line in diff["input"]))
        code = next(t for t in diff["tasks"] if t["id"] == "code")
        self.assertEqual((code["status"], code["fields"]), ("changed", {"tool": ["claude", "codex"]}))
        self.assertEqual(code["prompt"], [{"op": " ", "text": "实现数字时钟"}, {"op": "+", "text": "12 小时制补零"},
                                          {"op": " ", "text": "不引入外部资源"}])
        self.assertEqual(next(t for t in diff["tasks"] if t["id"] == "review")["status"], "same")
        added = self.service.revision_diff(self.b, self.rid, 1, 2)
        self.assertEqual([(t["id"], t["status"]) for t in added["tasks"] if t["status"] != "same"], [("review", "added")])
        self.assertEqual(self.service.revision_diff(self.b, self.rid)["from"], 2)   # default: the latest vs its predecessor

    def test_revisions_written_before_snapshots_are_reconstructed_where_possible(self):
        legacy_task = {**STEPS[1], "tool": "codex", "prompt": "实现数字时钟（旧）"}
        with self.db.transaction() as db:
            db.execute("UPDATE revisions SET content=? WHERE run_id=? AND version=1",
                       (dumps({"input": "做一个数字时钟\n每秒刷新", "steps": STEPS}), self.rid))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                       (self.rid, 4, dumps({"input": "做一个数字时钟", "task": legacy_task, "invalidated": ["code"]}),
                        self.b["id"], 1.0))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                       (self.rid, 5, dumps({"recovery_id": "r1", "decision": {"action": "rework", "reason": "缺少测试"},
                                            "invalidated": ["code"]}), self.b["id"], 2.0))
        history = self.service.revisions(self.b, self.rid)
        self.assertEqual([(r["version"], r["kind"], r["reconstructed"]) for r in history],
                         [(1, "created", True), (4, "continued", True), (5, "recovered", True)])
        self.assertEqual(history[2]["summary"], "自动纠错：缺少测试")
        old = self.service.revision(self.b, self.rid, 4)["snapshot"]
        self.assertEqual((old["input"], old["tasks"][1]["prompt"]), ("做一个数字时钟", "实现数字时钟（旧）"))
        self.assertEqual(self.service.revision_diff(self.b, self.rid, 1, 4)["tasks"][1]["fields"], {"tool": ["claude", "codex"]})

    def test_routes_check_access_and_validate_versions(self):
        self.plan()
        self.continue_code()
        with self.http() as call:
            status, history = call("GET", f"/v1/runs/{self.rid}/revisions", self.tokens["bob"])
            self.assertEqual((status, [r["version"] for r in history]), (200, [1, 2, 3]))
            self.assertNotIn("snapshot", history[0])          # the list stays small; one revision carries its snapshot
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/revisions", self.tokens["eve"])[0], 404)
            status, one = call("GET", f"/v1/runs/{self.rid}/revisions/3", self.tokens["bob"])
            self.assertEqual((status, one["snapshot"]["tasks"][1]["tool"]), (200, "codex"))
            status, diff = call("GET", f"/v1/runs/{self.rid}/revisions/diff?from=1&to=3", self.tokens["bob"])
            self.assertEqual((status, diff["from"], diff["to"]), (200, 1, 3))
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/revisions/diff?from=9", self.tokens["bob"])[0], 404)
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/revisions/diff?from=abc", self.tokens["bob"])[0], 400)
            self.assertEqual(call("GET", f"/v1/runs/{self.rid}/revisions/7", self.tokens["bob"])[0], 404)

    def test_routes_are_documented(self):
        from octopus.schema import openapi
        paths = openapi()["paths"]
        self.assertEqual({p["name"] for p in paths["/v1/runs/{id}/revisions/diff"]["get"]["parameters"]}, {"id", "from", "to"})
        self.assertEqual({p["name"] for p in paths["/v1/runs/{id}/revisions/{version}"]["get"]["parameters"]}, {"id", "version"})
        self.assertIn("get", paths["/v1/runs/{id}/revisions"])

    def test_mcp_lists_revisions_and_shows_only_what_changed(self):
        self.plan()
        self.continue_code()
        calls = []
        service, actor = self.service, self.b

        class Client:
            def request(self, method, path, body=None):
                calls.append((method, path))
                if path.endswith("/revisions"):
                    return service.revisions(actor, path.split("/")[3])
                return service.revision_diff(actor, path.split("/")[3], 2, 3)
        listed = mcp.call(Client(), "octopus_revisions", {"run_id": self.rid})
        self.assertEqual([r["version"] for r in listed["revisions"]], [1, 2, 3])
        changed = mcp.call(Client(), "octopus_revisions", {"run_id": self.rid, "from": 2, "to": 3})
        self.assertEqual(calls[-1], ("GET", f"/v1/runs/{self.rid}/revisions/diff?from=2&to=3"))
        code = next(t for t in changed["tasks"] if t["id"] == "code")
        self.assertEqual(code["prompt"], ["+ 12 小时制补零"])          # changed lines only, for an agent
        self.assertNotIn("review", [t["id"] for t in changed["tasks"]])
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_revisions")
        self.assertTrue(tool["annotations"]["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
