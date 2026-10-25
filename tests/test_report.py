"""D37 phase 2: a Session's Feature report — the request and plan, each task's gates and candidate, the review, recovery
and the published pull request; session text escaped, no external resources, bounded in size."""
import re
import threading
import unittest
import urllib.error
import urllib.request

from octopus import report
from octopus.server import required_scope, server
from tests.test_access import AccessCase

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


def outside_fences(lines):
    """The Markdown lines outside fenced code blocks."""
    fence, out = None, []
    for line in lines:
        opening = re.match(r"^(`{3,})", line)
        if fence is None and opening:
            fence = opening.group(1)
        elif fence is not None and line.rstrip() == fence:
            fence = None
        elif fence is None:
            out.append(line)
    return out


class ReportTests(unittest.TestCase):
    def test_the_plan_says_where_each_tool_came_from(self):
        value = view()
        value["tasks"][0]["mapping"] = {"source": "planner", "label": "KiroCrew 规划"}
        md = report.markdown(value)
        self.assertIn("| # | 任务 | 类别 | 工具 | 来源 | 状态 | 执行次数 |", md)
        self.assertIn("| claude | KiroCrew 规划 |", md)
        self.assertIn("<th>来源</th>", report.html(value))

    def test_the_report_says_why_it_started_or_waited(self):   # D55 §3, final review
        md = report.markdown(view(admission_basis={"mode": "auto", "basis": "rule", "pattern": "github.com/o/*"}))
        self.assertIn("准入：按准入规则「github.com/o/\\*」自动开始", md)   # Markdown escapes the *, rendered as written
        self.assertIn("按准入规则「github.com/o/*」自动开始", report.html(view(admission_basis={"mode": "auto", "basis": "rule",
                                                                                         "pattern": "github.com/o/*"})))
        self.assertIn("准入：按平台默认等待确认", report.markdown(view(admission_basis={"mode": "confirm", "basis": "default"})))
        self.assertNotIn("准入：", report.markdown(view(admission_basis={"mode": "auto", "basis": "submitted"})))
        self.assertNotIn("准入：", report.markdown(view()))

    def test_a_watchdog_stop_is_written_in_the_task_section(self):
        value = view()
        value["tasks"][0]["attempts"][-1]["evidence"]["watchdog"] = {"reason": "原生任务 31 分钟没有进展，看门狗已停止"}
        self.assertIn("- 看门狗：原生任务 31 分钟没有进展，看门狗已停止", report.markdown(value))
        self.assertIn("看门狗：原生任务 31 分钟没有进展，看门狗已停止", report.html(value))

    def test_a_switch_is_written_in_the_task_section(self):
        value = view()
        value["tasks"][0]["attempts"][-1]["evidence"]["switched_from"] = {
            "tool": "opencode", "model": "auto", "effort": "", "reason": "opencode <不可用>", "by": "coordinator", "recovery": 1}
        md, page = report.markdown(value), report.html(value)
        self.assertIn("- 换工具：opencode / auto → claude / auto（协调者推荐）：opencode \\<不可用\\>", md)
        self.assertIn("换工具：opencode / auto → claude / auto（协调者推荐）：opencode &lt;不可用&gt;", page)
        self.assertNotIn("换工具", report.markdown(view()))

    def test_the_markdown_report_has_every_section(self):
        text = report.markdown(view())
        order = ["# 登录功能", "## 需求", "新增登录", "## 计划", "## 任务", "## 纠错记录", "## 发布"]
        self.assertEqual([text.index(part) for part in order], sorted(text.index(part) for part in order))
        self.assertIn("| 1 | 实现登录 | implement | claude | — | accepted | 2 |", text)
        self.assertIn("| 2 | 审查 | review | codex | — | accepted | 1 |", text)
        self.assertEqual(text.count("| ✓ |"), 9)   # eight gates of the code task, review_bound
        self.assertIn("候选 abc1234 · 3 个文件 +40 −2", text)
        self.assertIn("`npm test` · 退出码 0 · 4.0 s", text)
        self.assertIn("执行 2 次", text)
        self.assertIn("绑定 abc1234", text)
        self.assertIn("第 1 轮 · code · retry · 测试失败", text)
        self.assertIn("octopus/" + RID, text)
        self.assertIn("[PR #3](<https://github.com/o/r/pull/3>)", text)
        self.assertIn("✓ 登录成功", text)
        self.assertIn("补 CHANGELOG", text)

    def test_the_html_report_is_self_contained(self):
        page = report.html(view())
        self.assertIn("<style>", page)
        self.assertIn('<base target="_blank">', page)   # the PR link leaves the console's sandboxed frame
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

    def test_markdown_keeps_its_structure_whatever_the_agent_writes(self):
        hostile = view()
        fence = "```python\n" + "x = 1\n" * 600 + "```"          # crosses the summary limit: the clip must not leave it open
        hostile["tasks"][0]["attempts"][1]["evidence"]["outcome"]["summary"] = (
            "## 改动\n<!-- hidden\n[PR #3](https://evil.example/pull/3) <img src=https://evil.example/t.gif>\n" + fence)
        hostile["tasks"][0]["spec"] = {**IMPLEMENT, "name": "实现 ## [x](https://evil.example)"}
        text = report.markdown(hostile)
        lines = text.splitlines()
        outside = "\n".join(outside_fences(lines))   # inside a code block, headings, links and HTML are inert
        self.assertEqual([l for l in outside.splitlines() if l.startswith("## ")], ["## 需求", "## 计划", "## 任务", "## 纠错记录", "## 发布"])
        self.assertEqual(re.findall(r"(?<!\\)\[[^\]]*\]\(", outside), ["[PR #3]("])   # the only link the report makes
        self.assertEqual(re.findall(r"(?<!\\)<(?:img|!--)", outside), [])
        start = next(i for i, l in enumerate(lines) if "## 改动" in l)
        opener = lines[start - 1]
        self.assertTrue(opener.startswith("````"), opener)   # longer than any backtick run inside
        close = lines.index(opener.rstrip("text").rstrip(), start)
        self.assertLess(close, lines.index("| 门禁 | 结果 | 详情 |"))   # the gate table is outside the block

    def test_the_task_contract_and_unreported_acceptance_items_are_shown(self):
        contracted = view()
        contracted["tasks"][0]["spec"] = {**IMPLEMENT, "deliverables": ["src/login.ts"], "acceptance": ["登录成功", "错误提示清晰"],
                                          "verify": ["npm test"], "paths": ["src/**"]}
        contracted["tasks"][0]["attempts"][1]["spec"] = contracted["tasks"][0]["spec"]   # what the Attempt was asked to do
        text = report.markdown(contracted)
        for part in ("交付物：src/login.ts", "检查命令（契约）：`npm test`", "可改动：src/\\*\\*", "✓ 登录成功", "— 错误提示清晰（未报告）"):
            self.assertIn(part, text)
        self.assertIn("错误提示清晰（未报告）", report.html(contracted))

    def test_the_small_things_a_reader_relies_on(self):
        v = view(input="需求" * 5000)
        v["tasks"][0]["attempts"][1]["spec"] = {**IMPLEMENT, "tool": "codex"}          # what this Attempt really ran with
        v["tasks"][0]["attempts"][1]["evidence"]["candidate"]["rewritten"] = True
        v["tasks"][0]["attempts"][1]["evidence"]["check_results"] = [{"command": "npm ci", "exit": None, "seconds": 0.0, "tail": ""}]
        v["recovery"]["history"].append({"number": 2, "failed_task_id": "review", "status": "blocked", "decision": None})
        v["publish"]["commit"] = "fedcba9" + "0" * 33
        text = report.markdown(v)
        self.assertIn("需求" * 5000, text)                       # the request is not cut short
        self.assertIn("请求：codex / auto", text)
        self.assertIn("（历史已改写）", text)
        self.assertIn("`npm ci` · 无法运行", text)
        self.assertIn("第 2 轮 · review · 未决定（blocked）", text)
        self.assertIn("推送的提交：fedcba9", text)
        self.assertNotIn("None", text)
        self.assertIn("<pre>实现登录并补测试</pre>", report.html(view()))

    def test_a_large_session_stays_small(self):
        big = view()
        attempt = big["tasks"][0]["attempts"][1]
        big["tasks"][0]["attempts"] = [{**attempt, "number": n, "evidence": {**attempt["evidence"], "outcome": {
            **attempt["evidence"]["outcome"], "summary": "很长" * 25000}}} for n in range(1, 51)]
        for render in (report.markdown, report.html):
            text = render(big)
            self.assertLess(len(text), 200000, render.__name__)
            self.assertIn("执行 50 次", text)

    def test_a_damaged_skills_record_does_not_stop_the_report(self):
        damaged = view()
        damaged["tasks"][0]["attempts"][-1]["evidence"]["skills"] = 3   # e.g. a hand-edited dispatch.json (D50)
        self.assertIn("## 任务", report.markdown(damaged))
        report.html(damaged)

    def test_a_warned_gate_is_marked_apart_from_a_pass_and_a_fail(self):   # D57 §4
        value = view()
        value["tasks"][0]["attempts"][-1]["evidence"]["gates"][5] = {"name": "diff_present", "status": "warn",
                                                                     "detail": "本任务没有改动入库文件"}
        self.assertIn("| diff_present | ⚠ | 本任务没有改动入库文件 |", report.markdown(value))
        self.assertIn('<tr class="warn"><td>diff_present</td><td>⚠</td>', report.html(value))

    def test_the_report_counts_the_final_attempts_warnings(self):   # D57 §4: 「提示 N 项」, only when there are any
        value = view()
        code, review = value["tasks"]
        code["attempts"][0]["evidence"]["gates"][0] = {"name": "native_completed", "status": "warn"}   # not the final one
        code["attempts"][-1]["evidence"]["gates"][5] = {"name": "diff_present", "status": "warn", "detail": "本任务没有改动入库文件"}
        review["attempts"][-1]["evidence"]["gates"].append({"name": "review_bound", "status": "warn", "detail": "已撤回 a.py"})
        md, page = report.markdown(value), report.html(value)
        self.assertIn("- 提示 2 项", md)
        self.assertLess(md.index("- 提示 2 项"), md.index("## 需求"))   # a summary, before the sections
        self.assertIn('<p class="warn">提示 2 项</p>', page)
        for text in (report.markdown(view()), report.html(view())):
            self.assertIsNone(re.search(r"提示 \d+ 项", text))

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


class ReportApiTests(AccessCase):
    def fetch(self, path, token):
        app = server(self.db, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{app.server_address[1]}{path}",
                                             headers={"Authorization": "Bearer " + token})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read().decode()
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read().decode()
        finally:
            app.shutdown()
            app.server_close()

    def test_who_may_read_the_report(self):
        rid = self.start(self.b, name="订单系统")["id"]
        status, headers, body = self.fetch(f"/v1/runs/{rid}/report?format=md", self.tokens["bob"])
        self.assertEqual(status, 200, body)
        self.assertTrue(headers["content-type"].startswith("text/markdown"))
        self.assertIn(f'attachment; filename="{rid}-report.md"', headers["content-disposition"])
        self.assertIn("# 订单系统", body)
        status, headers, body = self.fetch(f"/v1/runs/{rid}/report?format=html", self.token("bob", ["read"]))
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        # 2026-10-25: nor shown inside another page (a same-site preview could frame it with the console's cookie)
        self.assertEqual(headers["content-security-policy"], "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'")
        self.assertEqual((headers["x-content-type-options"], headers["cache-control"]), ("nosniff", "no-store"))
        self.assertIn("<h1>订单系统</h1>", body)
        self.assertEqual(self.fetch(f"/v1/runs/{rid}/report", self.tokens["bob"])[0], 200)   # Markdown by default
        self.assertEqual(self.fetch(f"/v1/runs/{rid}/report?format=md", self.tokens["eve"])[0], 404)
        self.assertEqual(self.fetch(f"/v1/runs/{rid}/report?format=pdf", self.tokens["bob"])[0], 400)

    def test_the_report_route_needs_read_scope(self):
        self.assertEqual(required_scope("GET", "/v1/runs/r1/report"), "read")


if __name__ == "__main__":
    unittest.main()
