# Feature 报告 第 2 期（D37） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个会话可以导出一份 Feature 报告（Markdown 与 HTML）。报告汇总需求与计划、各任务最终 Attempt 的工具与门禁、候选提交、审查结论、纠错记录与发布的 PR。入口为控制台「报告」与 MCP `octopus_report`。

**Architecture:**
- `octopus/report.py` 由会话视图（`Service.run` 的结果）生成报告：
  - `model(view)` 生成与格式无关的数据；
  - `markdown(model)`、`html(model)` 分别渲染。
- HTML 自带样式，没有脚本与外部资源；所有来自会话的文字（需求、任务名、Agent 结论、门禁详情）都经过转义。
- API：`GET /v1/runs/<id>/report?format=md|html`，`read` 权限。
- 控制台：在会话页的对话框中用 `sandbox` iframe 显示 HTML，并提供下载 Markdown / HTML。
- MCP：返回 Markdown 文本。

**Tech Stack:** Python 3 标准库（`html`），React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-14-candidates-gates-report-design.md`（§10、§11 第 2 期）

## Global Constraints

- **报告内容**，按顺序：
  1. 标题（会话名称或需求首行）、会话 ID、状态、创建者、生成时间；
  2. 需求（`input`，以及规划补全的 `intent` / `summary`）；
  3. 计划：任务表，列为 序号、名称、类别、工具、状态、执行次数；
  4. 每个任务：
     - 最终 Attempt 的工具 / 模型（请求与实际）、结论与摘要、门禁表（名称、结果、详情）；
     - 候选（短 sha、文件数、增删行数）、检查命令结果（命令、退出码、耗时）、验收项、建议、审查绑定；
  5. 纠错记录：轮次、失败的任务、协调者的决定与原因；
  6. 发布：已验证候选、分支、PR / MR 链接与编号。
- **转义与链接**：
  - HTML 中所有文字用 `html.escape(..., quote=True)` 转义。
  - 只有发布记录的 `url` 渲染为链接，且必须以 `https://` 开头，否则只显示文字。
  - Markdown 表格单元格中的 `|` 与换行被替换（`\|`、空格）。
- **体积**：
  - Agent 的结论摘要每条最多 2000 字符，门禁详情与检查输出首行最多 300 字符，超出时以「…」截断；
  - 检查命令的输出末尾不进入报告，只写命令、退出码、耗时；
  - 每个任务只展开最终 Attempt，其余写次数。
- **旧会话**：没有门禁、候选或发布时对应部分写「—」或「无」，不报错。
- **HTML 响应头**：
  - `Content-Type: text/html; charset=utf-8`；
  - `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; img-src data:`；
  - `X-Content-Type-Options: nosniff`、`Cache-Control: no-store`；
  - `Content-Disposition: inline; filename="<run-id>-report.html"`。
- **Markdown 响应头**：`text/markdown; charset=utf-8`，`attachment; filename="<run-id>-report.md"`。
- **权限**：与查看会话相同，`read` 范围；看不到会话时为 404。审计 `read`（与会话详情同一机制）。

## Review Focus

1. **注入**：任务名、Agent 结论、门禁详情、需求中含 `<script>`、`"><img onerror=…>`、`javascript:` 时，HTML 报告中原样显示为文字。PR 链接以 `javascript:` 开头时不成为链接。（`test_session_text_is_escaped_and_only_https_links_are_links`）
2. **大会话**：50 个 Attempt、每个结论 50 KB 时，报告小于 200 KB。（`test_a_large_session_stays_small`）
3. **旧会话**：D37 之前、没有门禁与候选的会话照常生成。（`test_an_old_session_reports_without_gates`）
4. **权限**：外人 404；只有 `read` 范围的 Token 可以取。（`test_who_may_read_the_report`）
5. **Markdown 表格**：单元格中的 `|` 与换行不破坏表格。（`test_markdown_tables_survive_pipes_and_newlines`）

---

### Task 1: 报告的生成（`octopus/report.py`）

**Files:**
- Create: `octopus/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Produces：
  - `report.model(view, now=None) -> dict`；
  - `report.markdown(view) -> str`；
  - `report.html(view) -> str`。
  - `view` 是 `Service.run` 的返回值：`tasks[].spec`、`tasks[].attempts[].evidence`、`recovery`、`publish`、`verified`、`planning`、`input`、`name`、`created_by`、`status`、`id`。

- [ ] **Step 1: 写失败测试**（`tests/test_report.py`）。用一个手写的会话视图：
  - 两个任务，实现与审查；
  - 实现任务两次 Attempt，第一次 `checks_pass` 失败，第二次全部通过并有候选；
  - 审查绑定候选；
  - 一轮纠错；
  - 已发布 PR。

```python
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
```

  测试项：
  - `test_the_markdown_report_has_every_section`：
    - 依次包含「# 登录功能」「## 需求」「新增登录」「## 计划」、任务表两行；
    - 实现任务的门禁表 8 行均为 ✓，「候选 abc1234 · 3 个文件 +40 −2」，检查 `npm test` 退出码 0；
    - 「执行 2 次」；审查「绑定 abc1234」；
    - 「## 纠错记录」中有「第 1 轮 · code · retry · 测试失败」；
    - 「## 发布」中有「octopus/run-…」与 `[PR #3](https://github.com/o/r/pull/3)`。
  - `test_the_html_report_is_self_contained`：
    - 含 `<style>`；不含 `<script`、`src=`、`http://`；
    - 唯一的 `href=` 是 PR 链接。
  - `test_session_text_is_escaped_and_only_https_links_are_links`（Review Focus 1）：
    - 任务名为 `<script>alert(1)</script>`，结论摘要为 `"><img src=x onerror=alert(1)>`，PR `url` 为 `javascript:alert(1)`；
    - HTML 中不出现这些原文，出现 `&lt;script&gt;`；PR 只显示文字。
  - `test_markdown_tables_survive_pipes_and_newlines`（Review Focus 5）：门禁详情为 `a|b\nc` → 表格单元格中为 `a\|b c`，表格行数不变。
  - `test_a_large_session_stays_small`（Review Focus 2）：一个任务 50 次 Attempt、每个摘要 50 000 字符 → Markdown 与 HTML 均小于 200 000 字符，含「执行 50 次」。
  - `test_an_old_session_reports_without_gates`（Review Focus 3）：Attempt 证据只有 `actual_backend` → 门禁一节写「无记录」，候选写「—」，无发布时「## 发布」写「尚未发布」。
- [ ] **Step 2: 运行**：`$PY -m unittest tests.test_report -v`。Expected: FAIL（`No module named 'octopus.report'`）。
- [ ] **Step 3: 实现** `octopus/report.py`：
  - `model` 产出 `{title, id, status, creator, generated, request: {input, intent, summary}, tasks: [...], recovery: [...], publish, verified}`；
  - 每个任务含 `{ordinal, id, name, kind, tool, status, attempts, final: {...}}`；
  - 截断用 `_clip(text, limit)`；
  - `markdown` 用 `_cell` 转义表格单元格；
  - `html` 用 `_e = html.escape`，样式为一段固定的 CSS 字符串。
- [ ] **Step 4: 运行通过**：`$PY -m unittest tests.test_report`。
- [ ] **Step 5: 提交**：`feat(report): 会话的 Feature 报告（Markdown 与 HTML）——需求与计划、各任务门禁与候选、审查结论、纠错与发布；转义会话文字、只链接 https 的 PR`

### Task 2: API `GET /v1/runs/<id>/report`

**Files:**
- Modify: `octopus/server.py`（路由与 `respond_text`）、`octopus/domain.py`（`Service.report(actor, rid, fmt)`：取视图、审计 read、渲染）、`octopus/schema.py`（OpenAPI 路径）
- Test: `tests/test_report.py`（`ReportApiTests`，沿用 `tests.test_access.AccessCase`）

- [ ] **Step 1: 写失败测试**：
  - `test_who_may_read_the_report`（Review Focus 4）：
    - 创建者以 `format=md` 取得 200，`text/markdown`，`attachment`；
    - `format=html` 为 200，`text/html`，响应头含上面的 CSP 与 `nosniff`；
    - 外人 404；只有 `read` 范围的 Token 200；
    - `format=pdf` 400。
  - `test_the_report_route_needs_read_scope`：`required_scope("GET", "/v1/runs/r1/report") == "read"`。
- [ ] **Step 2: 运行**：`$PY -m unittest tests.test_report -v`。Expected: 新测试 FAIL（404 接口不存在）。
- [ ] **Step 3: 实现**：
  - `Service.report` 调用 `self.run(actor, rid)`（权限与审计同会话详情），再按格式渲染；
  - 路由解析查询参数 `format`，缺省为 `md`。
- [ ] **Step 4: 运行通过**：`$PY -m unittest tests.test_report tests.test_access tests.test_openapi`（OpenAPI 测试若存在）。
- [ ] **Step 5: 提交**：`feat(sessions): 会话报告接口 GET /v1/runs/<id>/report?format=md|html`

### Task 3: 控制台「报告」与 MCP `octopus_report`

**Files:**
- Create: `web/src/features/sessions/ReportDialog.tsx`
- Modify:
  - `web/src/features/sessions/SessionView.tsx`：会话操作区加「报告」按钮；
  - `web/src/api/client.ts`：如需取文本，增加 `apiText(path)`；
  - `octopus/mcp.py`：`octopus_report {run_id, format?: "md"}` 返回 `{"markdown": …}`；
  - `skills/kirocrew-on/SKILL.md`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`。
- Test: `web/src/features/sessions/SessionView.test.tsx`、`tests/test_mcp_status.py`（或新文件 `tests/test_mcp_report.py`）

- [ ] **Step 1: 写失败测试**：
  - 控制台：点「报告」→ 请求 `GET /v1/runs/<id>/report?format=html`。
    - 对话框中有一个 `iframe`：`sandbox` 属性为空字符串（不允许脚本），`srcdoc` 为返回的 HTML；
    - 「下载 Markdown」请求 `format=md` 并触发下载（`URL.createObjectURL` 被调用）。
  - MCP：`octopus_report {run_id}` 请求 `GET /v1/runs/<id>/report?format=md`，返回 `{"markdown": "<文本>"}`；工具列表中有 `octopus_report`，需要 `read`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`npm --prefix web test`、`$PY -m unittest tests.test_mcp_report tests.test_mcp_roles`）。
  - MCP 客户端取文本：`client.request` 只解析 JSON。增加 `client.text(method, path)`，并在 `octopus/client.py` 中测试。
- [ ] **Step 5: 提交**：`feat(console): 会话页「报告」（沙箱中显示 HTML、下载 Markdown / HTML）；MCP octopus_report`

### Task 4: 回归与 EC2 验收

- [ ] **Step 1:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 2:** 部署前确认没有进行中的会话；部署平台（报告只在平台上生成，Worker 不变）。
- [ ] **Step 3: 验收**：
  - `run-6de37646…`（D37 第 1 期的成功会话）的报告：门禁、候选、检查命令、审查绑定、PR #3 链接齐全；
  - `run-3662576a…`（失败会话）的报告：`checks_pass` 失败与纠错记录可见；
  - D37 之前的旧会话报告照常生成；
  - 外人 404；
  - 控制台中打开报告，链接可点；MCP 取得 Markdown。
- [ ] **Step 4:** 向负责人报告；确认后推送。
