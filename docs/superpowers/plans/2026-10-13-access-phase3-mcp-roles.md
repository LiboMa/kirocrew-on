# 访问管理 第 3 期「MCP 与角色调用」 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `octopus connect` 以本人账号签发 Coding Agent 的 Token（管理员可选 `--admin`），用完即登出临时登录；MCP 增加身份、执行环境、分配 Worker、变更记录与三个管理员工具，按 Token scope 列出；所有 API 错误以 `{status, code, error}` 结构返回，Skill 说明每种错误该怎么告诉用户。

**Architecture:** 只改客户端侧（`octopus/client.py`、`octopus/connect.py`、`octopus/mcp.py`、`octopus/__main__.py`）与 Skill；服务端的权限与语义已在第 1、2 期完成（MCP 只是同一 API 的调用方，A3）。`Client` 抛出带状态码的 `ApiError`，MCP 把它转成结构化错误。

**Tech Stack:** Python 3 标准库。

**Spec:** `docs/superpowers/specs/2026-10-12-access-management-design.md`（§7、§9 B7、§10 第 3 期）；D34 设计 §8（MCP 查询变更记录）。

## Global Constraints

- 不新增依赖；面向用户与 Agent 的文字使用中文。
- MCP 不创建用户、不设置密码（B7）；Token 与密码不出现在输出、Skill 或日志中。
- 管理员工具只在 Token 含 `admin` 时可用（A3）；服务端仍逐次校验（403），客户端列出与否只为减少干扰。
- 提交规则：功能分支 `feat/m6-mcp-roles` 本地提交；EC2 验收完成并经负责人确认后推送。
- Python：`$PY -m unittest <模块>`（`$PY` = `/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-on/.venv/bin/python`）；完整回归 `PYTHON=$PY tools/regress.sh`。

## 设计裁定

| 编号 | 裁定 | 原因 |
|---|---|---|
| M1 | `--admin` 让该客户端的 MCP Token 本身含 `admin`（一个 MCP 服务一个 Token），有效期 7 天（普通 30 天）；非管理员账号使用 `--admin` 直接报错 | A3「额外签发含 admin 的 Token」；客户端配置里的长期管理凭据风险更高 |
| M2 | `tools/list` 按 `/v1/me` 的 scope 列出：不含 `admin` 时不列管理员工具；`/v1/me` 不可用时列出全部 | 普通用户调用必然 403，列出只会干扰 Agent |
| M3 | 管理员工具按动作参数化：`octopus_admin_users`（list / deactivate / activate / promote / demote，按用户名）、`octopus_admin_teams`（list / create / rename / archive / restore / set_member / remove_member / environment）、`octopus_admin_environments`（list / share / add_space / remove_space / dedicate / enable / disable / rename / retire） | §7.2；不创建用户、不设密码（B7）；破坏性动作标 `destructiveHint` |
| M4 | 未提供 `--username` 且没有终端时 `connect` 报错，不再默认 `admin` | §7.1 |
| M5 | 增加 `octopus_changes`（D34 §8 延后到第 3 期） | 变更记录对 Agent 同样可查 |

## Review Focus

1. 旧 MCP 配置（`read, execute` 的 Token）在升级后的 MCP 服务下：普通工具照常工作，`tools/list` 不出现管理员工具（Task 3 测试 `test_admin_tools_are_listed_only_for_an_admin_token`）。
2. 登录后签发 Token 失败（如 scope 超出）时，临时登录 Token 仍被登出（Task 2 测试 `test_the_login_token_is_logged_out_even_when_issuing_fails`）。
3. 代理返回非 JSON 错误页（CloudFront 5xx）时，MCP 结构化错误仍有 `status`，`code` 为 `http_error`（Task 1 测试 `test_proxy_errors_keep_their_status`）。
4. `octopus_admin_users` 用不存在的用户名：返回 404 结构化错误，不误操作其他用户（Task 4 测试）。
5. `octopus_start` 不再预选 Worker：Team 空间有多个可用 Worker 时由服务端决定（返回 `waiting_worker` 时 Agent 用 `octopus_environments` + `octopus_assign_worker`）（Task 3 测试）。

---

### Task 1: 结构化 API 错误

**Files:** Modify `octopus/client.py`、`octopus/mcp.py:serve`；Test `tests/test_client_errors.py`、`tests/test_mcp_status.py`

**Interfaces:**
- Produces: `client.ApiError(RuntimeError)`，属性 `status: int`、`code: str`、`error: str`，`str()` 为 `"<status> <code>: <error>"`（与现在的文字相同）；非 JSON 响应 `code="http_error"`。MCP `tools/call` 失败时 `result = {"isError": True, "content": [{"type": "text", "text": str(exc)}], "structuredContent": {"status", "code", "error"}}`（`ApiError` 之外的异常 `status=0`、`code="client_error"`）。

- [ ] **Step 1: 写失败测试** — `ApiError` 的属性与文字（JSON 与非 JSON 两种）；MCP `serve` 对抛出 `ApiError(403, "forbidden", "Token 缺少 admin scope…")` 的工具调用返回上述结构；`test_proxy_errors_keep_their_status`：502 HTML 页 → `structuredContent.status == 502`、`code == "http_error"`。
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_client_errors tests.test_mcp_status`。
- [ ] **Step 3: 实现** — `http_error` 返回 `ApiError`；`serve` 捕获 `ApiError` 时填 `structuredContent`。
- [ ] **Step 4: 运行通过**；**Step 5: 提交** — `feat(mcp): API 错误以 {status, code, error} 结构返回`

### Task 2: `octopus connect` 以本人账号签发

**Files:** Modify `octopus/connect.py`（`issue_token`）、`octopus/__main__.py`（`connect` 参数与流程）；Test `tests/test_connect.py`

**Interfaces:**
- Produces: `connect.issue_token(client_factory, url, client, username, password, admin=False) -> str`：登录 → 检查 `/v1/me`（`admin=True` 时要求账号为管理员，否则 `ValueError("--admin 需要管理员账号")`）→ `POST /v1/tokens {label: f"{client} MCP" + (" admin" if admin), scopes: ["read","execute","manage"] + (["admin"] if admin), ttl: 7 天 if admin else 30 天}` → 在 `finally` 中以登录 Token `POST /v1/logout`。`__main__`：`--username` 无默认值；缺省时有终端则 `input("用户名：")`，否则报错；新增 `--admin`。

- [ ] **Step 1: 写失败测试** — 假 `client_factory` 记录请求：普通签发的 scope 与 TTL、标签；`--admin` 的 scope 含 `admin`、TTL 7 天；非管理员 `--admin` 报错且仍登出；`test_the_login_token_is_logged_out_even_when_issuing_fails`；命令行未给 `--username` 且 stdin 非终端时退出码非 0、提示 `--username`。
- [ ] **Step 2–4:** 运行失败 → 实现 → 通过（`$PY -m unittest tests.test_connect tests.test_cli`）。
- [ ] **Step 5: 提交** — `feat(connect): 以本人账号签发 30 天 MCP Token、可选 --admin（7 天），完成后登出临时登录`

### Task 3: 身份、执行环境、分配 Worker、变更记录工具；`octopus_start` 不再预选；按 scope 列出

**Files:** Modify `octopus/mcp.py`；Test `tests/test_mcp_roles.py`（新建）

**Interfaces:**
- Produces 工具：
  - `octopus_me`：`GET /v1/me` + `GET /v1/teams` → `{id, username, admin, scopes, client, elevated, teams: [{id, name, role, archived, environment}]}`（`role` 为 null 的 Team——管理员可见但非成员——不列入）。
  - `octopus_environments {team_id?}`：`GET /v1/workers`，按空间过滤（启用、非空闲；共享环境需服务该空间；专属环境属于该 Team 或本人）→ `[{id, name, scope, isolation, models}]`。
  - `octopus_assign_worker {run_id, request_id, expected_version, worker_id}` → `POST /v1/runs/<id>/assign-worker`。
  - `octopus_changes {run_id, who?, kind?, before?, limit?}` → `GET /v1/runs/<id>/changes`（默认 limit 50）。
  - `octopus_start`：删除客户端预选 Worker 的代码。
  - `tools_for(client)`：调用 `/v1/me`，scope 不含 `admin` 时去掉 `octopus_admin_*`；异常时返回全部。`serve` 的 `tools/list` 使用它（结果缓存到进程结束）。

- [ ] **Step 1: 写失败测试** — 用假 `Client`：`octopus_me` 的形状；`octopus_environments` 对个人空间与 Team 空间的过滤（含共享、专属、空闲、停用四种 Worker）；`octopus_assign_worker` 的路径与请求体；`octopus_changes` 的查询串；`octopus_start` 不再请求 `/v1/me` 与 `/v1/workers`；`test_admin_tools_are_listed_only_for_an_admin_token`（`read, execute` 的 Token 不列管理员工具，admin Token 列出）。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_mcp_roles tests.test_mcp_status tests.test_revisions`）。
- [ ] **Step 5: 提交** — `feat(mcp): 身份、执行环境、分配 Worker 与变更记录工具；提交不再在客户端预选 Worker；按 Token scope 列出工具`

### Task 4: 管理员工具

**Files:** Modify `octopus/mcp.py`；Test `tests/test_mcp_roles.py`

**Interfaces:**
- `octopus_admin_users {action, username?}`：`list` → `GET /v1/admin/users`；其余先 `GET /v1/admin/users` 找到用户名对应的 id（找不到 → `ApiError(404, "not_found", "用户不存在")`），再 `POST /v1/users/<id>/active {active}` 或 `/admin {admin}`。
- `octopus_admin_teams {action, team_id?, name?, owner?, worker_id?, username?, role?}`：`list` → `GET /v1/teams`；`create` → `POST /v1/teams`；`rename` → `PATCH /v1/teams/<id>`；`archive` / `restore` → `POST …/archive`；`set_member` / `remove_member` → `POST` / `DELETE …/members`；`environment` → `POST …/environment`。
- `octopus_admin_environments {action, worker_id?, team_id?, user_id?, name?, confirm?}`：`list` → `GET /v1/admin/environments`；`share` → `POST /v1/workers/<id>/share {confirm}`；`add_space` / `remove_space` → `POST` / `DELETE …/spaces`；`dedicate`；`enable` / `disable` → `…/enabled`；`rename` → `PATCH /v1/workers/<id>`；`retire` → `POST …/retire`。
- 三个工具的描述写明需要管理员 Token；`annotations.destructiveHint=True`。

- [ ] **Step 1: 写失败测试** — 每个动作的请求方法、路径、请求体（表驱动）；未知动作 `ValueError`；`octopus_admin_users` 不存在的用户名 → 404 结构化错误且未发出写请求；工具定义中没有创建用户或设置密码的动作。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: 提交** — `feat(mcp): 管理员工具——用户启停与升降、Team 管理、执行环境管理（需 admin Token，不创建用户、不设密码）`

### Task 5: Skill 与文档、回归、EC2 验收

**Files:** Modify `skills/kirocrew-on/SKILL.md`（新工具、错误处理、管理员 Token 风险）、`docs/CONNECT.md`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`（D31 第 3 期、§14.9）、`docs/BACKLOG.md`

- [ ] **Step 1:** Skill：`octopus_me` / `octopus_environments` / `octopus_assign_worker` / `octopus_changes` 的用法；`waiting_worker` 时先 `octopus_environments` 再 `octopus_assign_worker`；错误：401 → 重新 `connect`，403 → 原样说明缺什么、需要谁授权，404 → 不存在或无权查看，409 → 按 `attention` 处理；管理员工具只在用户明确要求时使用，`--admin` Token 7 天、仅本人设备。
- [ ] **Step 2:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 3: EC2 验收**（不执行任务；平台已是第 2 期与 D34 的代码，本期只改客户端，不需要部署）：以本机 MCP 服务（`python -m octopus mcp --url <平台>`，JSON-RPC 经 stdin）分别使用 (a) 测试用户 `e2e-member` 的普通 Token（以 `issue_token` 签发，验收后停用该用户）与 (b) 管理员 `--admin` Token（7 天，验收后吊销）：`tools/list`、`octopus_me`、`octopus_environments`、`octopus_changes`；普通 Token 调 `octopus_admin_users list` 得结构化 403；admin Token 的 `octopus_admin_environments list` 与 `octopus_admin_users list` 成功。签发后核对临时登录 Token 已登出（`GET /v1/tokens` 中只剩新签发的一枚）。
- [ ] **Step 4:** 向负责人报告；确认后推送，并按需要重新渲染本机 Skill。
