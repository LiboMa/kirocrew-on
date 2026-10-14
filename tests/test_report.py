"""D37 phase 2: a Session's Feature report — the request and plan, each task's gates and candidate, the review, recovery
and the published pull request; session text escaped, no external resources, bounded in size."""
import re
import unittest

from octopus import report

RID = "run-" + "5" * 32
IMPLEMENT = {"id": "code", "name": "实现登录", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现", "kind": "implement"}
REVIEW = {"id": "review", "name": "审查", "tool": "codex", "model": "auto", "effort": "", "prompt": "审查", "kind": "review"}


def view(**extra):
    gates = [{"name": n, "status": "pass"} for n in ("native_completed", "identity_match", "handoff_present",
                                                     "outcome_valid", "outcome_pass", "diff_present", "no_secrets", "checks_pass")]
    first = {"number": 1, "status": "blocked", "error": "检查命令失败：npm test（退出码 1）", "spec": IMPLEMENT,
             "evidence": {"actual_backend": "claude", "actual_model": "claude-sonnet-4",
                          "gates": gates[:7] + [{"name": "checks_pass", "status": "fail", "detail": "检查命令失败：npm test（退出码 1）"}],
                          "check_results": [{"command": "npm test", "exit": 1, "seconds": 3.2, "tail": "1 failing"}]}}
    second = {"number": 2, "status": "accepted", "error": None, "spec": IMPLEMENT,
              "evidence": {"actual_backend": "claude", "actual_model": "claude-sonnet-4", "gates": gates,
                           "outcome": {"decision": "pass", "summary": "实现登录并补测试",
                                       "acceptance": [{"item": "登录成功", "result": "pass"}], "suggestions": ["补 CHANGELOG"]},
                           "candidate": {"sha": "abc1234" + "0" * 33, "files": 3, "insertions": 40, "deletions": 2},
                           "check_results": [{"command": "npm test", "exit": 0, "seconds": 4.0, "tail": "ok"}]}}
    review = {"number": 1, "status": "accepted", "error": None, "spec": REVIEW,
              "evidence": {"actual_backend": "codex", "reviewed": "abc1234" + "0" * 33,
                           "gates": [{"name": "review_bound", "status": "pass", "detail": "abc1234000000"}],
                           "outcome": {"decision": "pass", "summary": "无阻断问题", "reviewed_commit": "abc1234"}}}
    return {"id": RID, "name": "登录功能", "status": "completed", "input": "做登录", "created_by": {"username": "carol"},
            "planning": {"applied": True, "intent": "新增登录", "summary": "## 验收\n- 登录成功"},
            "tasks": [{"id": "code", "status": "accepted", "spec": IMPLEMENT, "attempts": [first, second]},
                      {"id": "review", "status": "accepted", "spec": REVIEW, "attempts": [review]}],
            "recovery": {"used": 1, "history": [{"number": 1, "failed_task_id": "code", "status": "applied",
                                                 "decision": {"action": "retry", "target_task_id": "code", "reason": "测试失败"}}]},
            "verified": {"sha": "abc1234" + "0" * 33, "task_id": "review"},
            "publish": {"branch": "octopus/" + RID, "url": "https://github.com/o/r/pull/3", "number": 3, "kind": "pull_request"},
            **extra}


class ReportTests(unittest.TestCase):
    def test_the_markdown_report_has_every_section(self):
        text = report.markdown(view())
        order = ["# 登录功能", "## 需求", "新增登录", "## 计划", "## 任务", "## 纠错记录", "## 发布"]
        self.assertEqual([text.index(part) for part in order], sorted(text.index(part) for part in order))
        self.assertIn("| 1 | 实现登录 | implement | claude | accepted | 2 |", text)
        self.assertIn("| 2 | 审查 | review | codex | accepted | 1 |", text)
        self.assertEqual(text.count("| ✓ |"), 9)   # eight gates of the code task, review_bound
        self.assertIn("候选 abc1234 · 3 个文件 +40 −2", text)
        self.assertIn("`npm test` · 退出码 0 · 4.0 s", text)
        self.assertIn("执行 2 次", text)
        self.assertIn("绑定 abc1234", text)
        self.assertIn("第 1 轮 · code · retry · 测试失败", text)
        self.assertIn("octopus/" + RID, text)
        self.assertIn("[PR #3](https://github.com/o/r/pull/3)", text)
        self.assertIn("✓ 登录成功", text)
        self.assertIn("补 CHANGELOG", text)

    def test_the_html_report_is_self_contained(self):
        page = report.html(view())
        self.assertIn("<style>", page)
        for absent in ("<script", "src=", "http://"):
            self.assertNotIn(absent, page)
        self.assertEqual(re.findall(r'href="([^"]*)"', page), ["https://github.com/o/r/pull/3"])
        self.assertIn("候选 abc1234 · 3 个文件 +40 −2", page)

    def test_session_text_is_escaped_and_only_https_links_are_links(self):
        hostile = view(name="<script>alert(1)</script>",
                       publish={"branch": "octopus/x", "url": "javascript:alert(1)", "number": 3, "kind": "pull_request"})
        hostile["tasks"][0]["attempts"][1]["evidence"]["outcome"]["summary"] = '"><img src=x onerror=alert(1)>'
        page = report.html(hostile)
        for raw in ("<script>alert(1)", "<img src=x", "javascript:alert(1)\""):
            self.assertNotIn(raw, page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("href=", page)

    def test_markdown_tables_survive_pipes_and_newlines(self):
        odd = view()
        odd["tasks"][0]["attempts"][1]["evidence"]["gates"][0]["detail"] = "a|b\nc"
        odd["tasks"][0]["spec"] = {**IMPLEMENT, "name": "实现|登录\n第二行"}   # a task name in the plan table
        text = report.markdown(odd)
        self.assertIn("| native_completed | ✓ | a\\|b |", text)   # a detail keeps its first line, pipes escaped
        row = [line for line in text.splitlines() if "native_completed" in line and "a\\|b" in line]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].count(" | "), 2)
        self.assertIn("| 1 | 实现\\|登录 | implement |", text)   # a name keeps its first line too

    def test_a_large_session_stays_small(self):
        big = view()
        attempt = big["tasks"][0]["attempts"][1]
        big["tasks"][0]["attempts"] = [{**attempt, "number": n, "evidence": {**attempt["evidence"], "outcome": {
            **attempt["evidence"]["outcome"], "summary": "很长" * 25000}}} for n in range(1, 51)]
        for render in (report.markdown, report.html):
            text = render(big)
            self.assertLess(len(text), 200000, render.__name__)
            self.assertIn("执行 50 次", text)

    def test_an_old_session_reports_without_gates(self):
        old = view(verified=None, publish=None, recovery=None, planning=None)
        for task in old["tasks"]:
            for attempt in task["attempts"]:
                attempt["evidence"] = {"actual_backend": "claude"}
        text = report.markdown(old)
        self.assertIn("门禁：无记录", text)
        self.assertIn("候选：—", text)
        self.assertIn("尚未发布", text)
        self.assertIn("无纠错", text)
        report.html(old)


if __name__ == "__main__":
    unittest.main()
