"""octopus_status for Coding Agents: a compact view by default, the full record on request."""
import json
import unittest

from octopus import mcp

RID = "run-" + "a" * 32
RAW = "原始输出" * 5000
ROUTING = {"agent": "poc-kiro", "cwd": "/var/lib/octopus/boundaries/personal/worker/" + RID}


def attempt(number, status, decision, summary, findings=()):
    return {"id": f"attempt-{number}", "number": number, "status": status, "error": None if status == "accepted" else summary,
            "result": RAW, "spec": {"prompt": RAW}, "context_hash": "h" * 64,
            "evidence": {"actual_backend": "codex", "actual_model": "gpt[low]", "routing": ROUTING,
                         "handoff_path": f"/attempts/attempt-{number}/handoff.md",
                         "outcome": {"decision": decision, "summary": summary, "findings": list(findings)}}}


FINDING = {"file": "index.html", "line": 129, "message": "非法 URL 编码导致初始化异常", "severity": "medium"}
SESSION = {
    "id": RID, "name": "番茄钟", "status": "completed", "desired": "active", "lifecycle": "open", "version": 3,
    "error": None, "attention": None, "progress": {"accepted": 2, "total": 2}, "input": "做一个番茄钟",
    "team_id": None, "worker_id": "worker-1", "workspace": "/w", "native_parent": "octopus-" + RID,
    "planning": {"applied": True, "mode": "agent", "intent": "单文件番茄钟", "summary": "## 目标\n番茄钟"},
    "recovery": {"used": 1, "policy": {"enabled": True, "max_cycles": 2},
                 "latest": {"number": 1, "status": "applied", "failed_task_id": "verify", "context": RAW, "result": RAW,
                            "decision": {"action": "rework", "target_task_id": "implement", "reason": "确属实现缺陷",
                                         "instructions": RAW}},
                 "history": [{"number": 1, "context": RAW, "result": RAW}]},
    "tasks": [
        {"id": "implement", "status": "accepted", "spec": {"name": "实现", "tool": "claude", "model": "auto", "prompt": RAW},
         "attempts": [attempt(1, "accepted", "pass", "首版"), attempt(3, "accepted", "pass", "修复两处缺陷")]},
        {"id": "verify", "status": "accepted", "spec": {"name": "验证", "tool": "codex", "model": "auto", "prompt": RAW},
         "attempts": [attempt(2, "blocked", "changes_requested", "复现两处缺陷", [FINDING]), attempt(4, "accepted", "pass", "复验通过")]},
        {"id": "deliver", "status": "pending", "spec": {"name": "交付", "tool": "claude", "model": "auto", "prompt": RAW},
         "attempts": []},
    ],
}
EVENTS = [{"id": i, "kind": "attempt.state", "created": 1000.0 + i, "run_id": RID,
           "payload": {"status": "running", "task_id": "verify", "evidence": {"routing": ROUTING}, "context": RAW}}
          for i in range(30)]


class Client:
    def request(self, method, path, body=None):
        return {f"/v1/runs/{RID}": SESSION, f"/v1/runs/{RID}/events": EVENTS}[path]


class CompactStatusTests(unittest.TestCase):
    def test_default_view_keeps_what_an_agent_acts_on_and_stays_small(self):
        view = mcp.call(Client(), "octopus_status", {"run_id": RID})
        session = view["session"]
        self.assertEqual((session["status"], session["version"], session["input"]), ("completed", 3, "做一个番茄钟"))
        self.assertEqual(session["planning"]["intent"], "单文件番茄钟")
        self.assertEqual(session["recovery"], {"used": 1, "policy": {"enabled": True, "max_cycles": 2},
                                               "latest": {"number": 1, "status": "applied", "failed_task_id": "verify",
                                                          "action": "rework", "target_task_id": "implement",
                                                          "reason": "确属实现缺陷"}})
        implement, verify, deliver = session["tasks"]
        self.assertEqual(implement, {"id": "implement", "name": "实现", "status": "accepted", "tool": "claude", "model": "auto",
                                     "attempts": 2, "latest": {"number": 3, "status": "accepted", "error": None,
                                                               "backend": "codex", "model": "gpt[low]", "decision": "pass",
                                                               "summary": "修复两处缺陷", "findings": [],
                                                               "handoff": "/attempts/attempt-3/handoff.md"}})
        self.assertEqual(verify["latest"]["number"], 4)
        self.assertEqual((deliver["attempts"], deliver["latest"]), (0, None))
        self.assertEqual([e["id"] for e in view["events"]], list(range(20, 30)))
        self.assertEqual(view["events"][-1]["payload"], {"status": "running", "task_id": "verify"})
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("原始输出", text)
        self.assertNotIn("poc-kiro", text)
        self.assertLess(len(text), 4000)

    def test_gates_candidates_and_the_verified_candidate_are_summarised(self):
        gated = attempt(5, "blocked", "pass", "实现完成")
        gated["evidence"].update(candidate={"sha": "abc1234" + "0" * 33, "parent": "b" * 40, "changed": True, "files": 2,
                                            "insertions": 12, "deletions": 3},
                                 gates=[{"name": "native_completed", "status": "pass"}, {"name": "diff_present", "status": "pass"},
                                        {"name": "checks_pass", "status": "fail", "detail": "检查命令失败：npm test（退出码 1）\n" + RAW},
                                        {"name": "review_bound", "status": "skip", "detail": "此前没有候选"}])
        session = {**SESSION, "verified": {"sha": "abc1234" + "0" * 33, "task_id": "implement"},
                   "tasks": [{**SESSION["tasks"][0], "attempts": [gated]}]}

        class One:
            def request(self, method, path, body=None):
                return {f"/v1/runs/{RID}": session, f"/v1/runs/{RID}/events": []}[path]
        view = mcp.call(One(), "octopus_status", {"run_id": RID})
        latest = view["session"]["tasks"][0]["latest"]
        self.assertEqual(latest["gates"], {"passed": 2, "failed": [{"name": "checks_pass", "detail": "检查命令失败：npm test（退出码 1）"}],
                                           "skipped": ["review_bound"]})
        self.assertEqual(latest["candidate"], {"sha": "abc1234" + "0" * 5, "files": 2, "insertions": 12, "deletions": 3})
        self.assertEqual(view["session"]["verified"]["task_id"], "implement")
        self.assertNotIn("原始输出", json.dumps(view, ensure_ascii=False))

    def test_a_switched_attempt_says_what_it_was_switched_from(self):
        session = json.loads(json.dumps(SESSION))
        switched = {"tool": "opencode", "model": "auto", "effort": "", "reason": "opencode 不可用", "by": "coordinator", "recovery": 1}
        session["tasks"][0]["attempts"][-1]["evidence"]["switched_from"] = switched
        tasks = mcp.compact_status(session, [])["session"]["tasks"]
        self.assertEqual(tasks[0]["latest"]["switched_from"], switched)
        self.assertNotIn("switched_from", tasks[1]["latest"])

    def test_a_stopped_stall_says_so(self):
        session = json.loads(json.dumps(SESSION))
        watchdog = json.loads('{"reason": "原生任务 31 分钟没有进展（最后工具：npm run build），看门狗已停止", "idle_minutes": 30, "attempt_minutes": 240, "last_progress": 1.0, "turns": 4, "last_tool": "npm run build", "stopped_at": 2.0}')
        session["tasks"][0]["attempts"][-1]["evidence"]["watchdog"] = watchdog
        latest = mcp.compact_status(session, [])["session"]["tasks"][0]["latest"]
        self.assertEqual(latest["watchdog"], watchdog["reason"])

    def test_detail_returns_the_full_record(self):
        view = mcp.call(Client(), "octopus_status", {"run_id": RID, "detail": True})
        self.assertEqual(view, {"session": SESSION, "events": EVENTS})

    def test_the_tool_schema_offers_detail(self):
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_status")
        self.assertEqual(tool["inputSchema"]["properties"]["detail"], {"type": "boolean"})
        self.assertEqual(tool["inputSchema"]["required"], ["run_id"])


if __name__ == "__main__":
    unittest.main()
