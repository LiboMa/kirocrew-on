# M3-2 用户 CLI 与 M3-4 本地 / 远端联动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用者能在本地仓库提交需求、把会话分支与上下文拉回本地、修改后同步回远端并让远端重新检查与审查；CLI 与 MCP 共用同一套本地逻辑。

**Architecture:** Worker 新增 `/pull/bundle`（复用发布的 bundle 与凭证核查）与 `/sync`（导入快进的 bundle）；平台新增 bundle 下载票据与 `POST /v1/runs/<id>/sync`（二进制上传），sync 为最后一个写代码的任务记一个「本地同步」Attempt，复用检查轮询与下游重跑；`octopus/local.py` 承担本地的 git 与会话关联，CLI 与 MCP 都调用它。

**Tech Stack:** Python 3.12 标准库 + git，React + TypeScript，unittest、vitest。

**Spec:** `docs/superpowers/specs/2026-10-22-m3-local-remote-design.md`（D58）

## Global Constraints

- bundle 中的提交与发布同样核查疑似凭证与 `.env`（`sources.unpublishable`）；不通过就拒绝，pull 与 sync 都是。
- sync 只接受快进；会话工作区有未纳入候选的改动时拒绝，不丢弃它们。
- `.octopus/session.json` 与 `.octopus/sessions/` 写入 `.git/info/exclude`，从不进入提交。
- 使用者看到的文字是中文；错误只写原因（D53）。旧 Worker 返回 409 `worker_outdated`。
- sync 的权限与续作相同（会话可操作、D49 R10 边界主人规则、已归档 Team 不行）。

## Review Focus

- **sync 不是快进**（本地基于旧的会话 head）：409 `sync_not_fast_forward`，会话分支不动（Task 1 测试）。
- **sync 的提交里有 `.env` 或疑似凭证**：409 `sync_secret_found`，会话分支不动（Task 1 测试）。
- **会话工作区有未纳入候选的改动时 sync**：拒绝，不丢弃（Task 1 测试）。
- **同一 `request_id` 重复 sync**：只记一个 Attempt（Task 2 测试）。
- **本地缺基线提交时 pull**：提示 `--full`，或自动改取完整 bundle（Task 3 测试）。

---

### Task 1: Worker 的 pull bundle 与 sync

**Files:** Modify `octopus/sources.py`（`sync_apply(project, branch, bundle, old, new) -> dict`）、`octopus/worker.py`（`pull_plan`、`POST /pull/bundle`、`POST /sync` 二进制请求体，上限 20 MB）；Test `tests/test_local_remote_worker.py`

- [ ] Step 1 测试：pull bundle 含会话分支、`git bundle list-heads` 为分支 head；有 `.env` 提交时 409 `bundle_secret_found`；sync 一个基于会话 head 的本地提交后分支与工作区到新提交、返回 sha / parent / files / insertions / deletions / names；非快进 409；含 `.env` 409；会话工作区有未提交改动 409；bundle 的 head 与声明的 `head` 不符 409；超过 20 MB 413。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过（含 `tests.test_candidates tests.test_publish`）。
- [ ] Step 5 提交 `feat(worker): 会话分支的 pull bundle 与 sync（只接受快进，核查凭证与 .env）（D58）`

### Task 2: 平台的 bundle 下载与 sync

**Files:** Modify `octopus/transport.py`（`worker_upload`）、`octopus/server.py`（`POST /v1/runs/<id>/bundle`、`GET /bundle/<票据>/<id>.bundle`、`POST /v1/runs/<id>/sync`）、`octopus/domain.py`（`Service.sync`）、`octopus/requests.py`（client `cli`）、`octopus/schema.py`、`octopus/revisions.py`（kind `synced`）；Test `tests/test_sync.py`

**Interfaces:** `Service.sync(actor, rid, data: bytes, head: str, request_id: str, expected_version: int, note: str | None) -> dict`（`{id, version, attempt_id, task_id, head, commits}`）。

- [ ] Step 1 测试：sync 后最后一个写代码的任务有新的 Attempt（证据含候选与 `synced`），其后的任务为待执行，之前的不变，修订「本地同步」；有检查命令时 Attempt 为检查中、引擎轮询后验收；没有时直接验收；同一 `request_id` 重复只记一次；会话运行中 409 `busy`；非成员 404；他人边界上的会话 403（D49 R10）；旧 Worker 409 `worker_outdated`；bundle 下载链接一次性、他人无权；`client_of("octopus CLI") == "cli"`。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过（含 `tests.test_confirmation tests.test_access tests.test_contract tests.test_requests`）。
- [ ] Step 5 提交 `feat(sync): POST /v1/runs/<id>/sync——本地提交成为新候选，重新检查并让下游任务重新审查；pull 的 bundle 下载（D58）`

### Task 3: 本地逻辑 `octopus/local.py`

**Files:** Create `octopus/local.py`；Test `tests/test_local.py`

**Interfaces:** `repository(dir) -> Path | None`、`source(dir) -> dict`（origin 地址与当前分支）、`unpushed(dir) -> int | None`、`push_base(dir)`、`link(dir, values)` / `linked(dir) -> dict | None`、`pull(client, rid, dir, checkout=False, full=False) -> dict`、`sync(client, rid, dir, note=None) -> dict`。

- [ ] Step 1 测试（真实 git 仓库与 `Client` 替身）：source 取 origin 与分支；未推送提交数；`link` 写 `.octopus/session.json` 并把它与 `.octopus/sessions/` 加入 `.git/info/exclude`；pull 把 bundle 取为 `octopus/<run>` 分支、写上下文目录、已存在且有本地提交的同名分支不覆盖；缺基线时改取完整 bundle；sync 生成 `<head>..HEAD` 的 bundle 上传、工作区有未提交改动时拒绝、当前分支不包含会话 head 时拒绝。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过。
- [ ] Step 5 提交 `feat(local): 本地仓库与会话的关联、pull、sync 与未推送提交检测（D58）`

### Task 4: 用户 CLI

**Files:** Modify `octopus/__main__.py`；Create `octopus/cli.py`（命令实现与人读的输出）；Test `tests/test_user_cli.py`

- [ ] Step 1 测试：`login` 写 0600 配置；`submit` 在仓库内取源码、未推送时拒绝并提示 `--push-base`、成功后写关联；`status` / `plan` / `confirm` / `report` / `continue` / `approvals` / `search` 调对应接口并输出中文摘要，`--json` 输出 JSON；`watch` 在会话停下时退出；`pull` / `sync` 调 `local`；未登录时提示 `octopus login`。
- [ ] Step 2 失败；Step 3 实现（命令复用 `mcp.call` 的同名工具）；Step 4 通过（含 `tests.test_cli`）。
- [ ] Step 5 提交 `feat(cli): 使用者的 octopus 命令（login、submit、plan、confirm、status、watch、report、pull、sync、continue、approvals、search）（D58 M3-2）`

### Task 5: MCP 与 Skill

**Files:** Modify `octopus/mcp.py`（`octopus_link`、`octopus_pull`、`octopus_sync`；`octopus_start` 的未推送检测与 `push_base`；缺 `run_id` 时读关联）、`skills/kirocrew-on/SKILL.md`；Test `tests/test_mcp_local.py`

- [ ] Step 1 测试：三个工具调用 `local`；`octopus_start` 在仓库内且有未推送提交时返回提示而不提交，`push_base: true` 时先推送；`octopus_status {}` 读关联。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过（含 `tests.test_admin_usage_split`）。
- [ ] Step 5 提交 `feat(mcp): octopus_link、octopus_pull、octopus_sync 与提交前的未推送检测（D58）`

### Task 6: 控制台、文档、回归、部署与 EC2 验收

**Files:** Modify `web/src/lib/labels.ts`（事件 `run.synced`、修订 kind `synced`、客户端 `cli`）、`web/src/features/sessions/TaskList.tsx`（Attempt 的「本地同步」）、`docs/OPERATIONS.md`、`docs/CONNECT.md`、`docs/MVP-RELEASE.md` §14.3、`docs/progress.json`、`docs/BACKLOG.md`

- [ ] Step 1 控制台测试与实现；Step 2 文档；Step 3 全量回归；Step 4 部署（先通知 D57 会话、确认主机空闲）。
- [ ] Step 5 EC2：Claude Code（MCP）与 CLI 各走一遍 submit → status → pull → 本地修改并提交 → sync → 远端检查与复审通过；非快进与含 `.env` 的 sync 被拒绝；验收会话关闭。§7 验收 1 记录（四个客户端中 Codex、Kiro IDE 待负责人在其客户端核对）。
