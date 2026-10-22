# 代码仓库凭证 第 2 期「平台代发布」（D35） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话的成果以分支 `octopus/<run>` 推送到源仓库，并创建 Draft PR（GitHub）或 Draft MR（GitLab）。由平台用绑定的写凭证代为执行，写凭证不进入执行环境；结果写入会话与变更记录。

**Architecture:**
- **Worker**：新增 `POST /publish/bundle`。
  - 先把工作区未提交的改动提交到 `octopus/<run>`：排除 `.env`、依赖与缓存目录；发现疑似凭证时拒绝。
  - 再导出以克隆基线为前置的 git bundle，流式返回（同下载）。
- **平台**：
  - 在数据目录的临时裸仓库中校验 bundle 并取出分支，只推送这一个 ref：不 force，不推默认分支；远端已有同名分支且不是其祖先时拒绝。
  - 推送用 `GIT_ASKPASS` 临时文件（沿用 `sources.askpass`）。
  - 以 REST 创建或沿用同一分支的 Draft PR / MR。
- **入口**：会话页「发布」，MCP `octopus_publish`。

**Tech Stack:** Python 3 标准库 + 主机 git、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-13-repo-credentials-design.md`（§5.2、§6、§8 第 2 期）

## Global Constraints

- **凭证**
  - 写凭证（`access: "write"`）只在平台进程中使用；Worker 只提供 bundle。
  - 发布须由会话创建者、Team owner 或管理员发起，且会话已结束执行（SETTLED）。
  - 绑定的凭证须可用、`access == "write"`、仍覆盖该仓库，否则为 409 `credential_unavailable` 或 `credential_read_only`。
- **分支与推送**
  - 分支名默认 `octopus/<run-id>`，只允许 `octopus/` 前缀加 `[A-Za-z0-9._/-]{1,100}`，不得是远端默认分支或会话的 `ref`。
  - 只做快进推送；远端分支被他人改动时为 409 `publish_conflict`。
  - 同一分支重复发布时沿用已有的 Draft PR / MR，不重复创建。
- **自动提交**（工作区有未提交改动时）
  - 作者为 `KiroCrew ON`，提交信息为 `KiroCrew ON：<会话名称>（<run-id>）`。
  - 排除 `.env` / `.env.*`（保留 `.env.example` / `.env.sample`）与下载时排除的依赖、缓存目录，即使它们没有被 `.gitignore` 忽略。
- **疑似凭证**
  - 新增内容中出现 `github_pat_`、`ghp_`、`gho_`、`glpat-`、`AKIA[0-9A-Z]{16}`、`-----BEGIN … PRIVATE KEY-----` 时拒绝发布：409 `publish_secret_found`，列出文件（不回显内容）。
- **记录**
  - 结果写入会话快照 `publish`：`{branch, commit, url, number, kind, at, by}`，并写变更记录 `run.published`。
  - 每次使用写凭证审计 `credential.use`（`access: "write"`）。
- **规模**：bundle 最大 100 MB。
- **提交与部署**：功能分支本地提交；EC2 验收并经负责人确认后推送；部署前确认没有进行中的会话。

## Review Focus

1. **秘密不外流**：
   - 写凭证不出现在 Worker 的请求体、执行环境文件、临时裸仓库的 `config` 与错误信息中（`test_the_write_credential_never_reaches_the_worker`）。
   - 工作区里的 `.env` 与疑似凭证不会被提交或推送（`test_secrets_in_the_workspace_are_never_published`）。
2. **不破坏远端**：
   - 不推默认分支，不 force；远端同名分支被他人改动时拒绝（`test_a_branch_changed_by_someone_else_is_not_overwritten`）。
3. **重复发布**：沿用已有的 PR / MR，不重复创建（`test_publishing_again_reuses_the_pull_request`）。
4. **权限**：
   - 只读凭证、无绑定、他人的会话（成员非创建者）、执行中的会话都被拒绝。
5. **bundle 校验**：
   - 被篡改、前置提交不符或指向其他分支的 bundle 被拒绝。
   - 中途断开的 bundle 不会被当作完整（`test_a_broken_bundle_publishes_nothing`）。

---

### Task 1: Worker 导出待发布的提交

**Files:**
- Modify: `octopus/worker.py`（`publish_bundle(body)`、`serve` 中的流式分支）、`octopus/sources.py`（`commit_pending(project, message, branch)`、`suspicious(project, base)`）
- Test: `tests/test_publish.py`

**Interfaces:**
- `sources.commit_pending(project, message, branch) -> str | None`：
  - 在 `branch` 上提交未提交的改动，排除规则同下载；返回新 commit，没有改动时返回 None。
  - 只用 `-c user.name=KiroCrew ON -c user.email=kirocrew-on@localhost -c commit.gpgsign=false`，并加 `--no-verify`。
- `sources.suspicious(project, base) -> [path]`：`git diff base..HEAD` 新增行中匹配疑似凭证模式的文件。
- Worker `POST /publish/bundle {run_id, message}`：
  1. `commit_pending`；
  2. HEAD 与基线相同时返回 409 `nothing_to_publish`；
  3. `suspicious` 有结果时返回 409 `publish_secret_found`；
  4. 返回 `200 application/x-git-bundle` 流，响应头 `X-Publish-Commit`、`X-Publish-Base`。
  - 1–3 步的拒绝仍以 JSON 回答。

- [ ] **Step 1: 写失败测试**
  - 有改动时自动提交，并排除 `.env`、未被 ignore 的 `node_modules/`；
  - 没有改动且没有新提交时 409；
  - 新增 `token = "ghp_…"` 时 409，且只列文件不回显内容；
  - bundle 可被 `git bundle verify`，以基线为前置，HEAD 为新 commit；
  - 未认证 401。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_publish tests.test_archive tests.test_sources`）。
- [ ] **Step 5: 提交** — `feat(worker): 导出待发布的提交——自动提交未提交改动（排除 .env 与依赖）、拒绝疑似凭证、以基线为前置的 bundle`

### Task 2: 平台推送分支并创建 Draft PR / MR

**Files:**
- Create: `octopus/publish.py`
- Modify: `octopus/domain.py`（`Service.publish`）、`octopus/server.py`（`POST /v1/runs/<id>/publish`）、`octopus/schema.py`
- Test: `tests/test_publish.py`

**Interfaces:**
- `publish.receive(bundle_stream, workdir, branch, base) -> commit`：写入临时文件后执行 `git bundle verify`，再 `git fetch <bundle> <branch>:<branch>` 到 `workdir` 的裸仓库；前置不符或中途断开时抛 `Error(409, "publish_invalid")`。
- `publish.push(workdir, url, branch, auth) -> {"created": bool}`：
  - 先 `git ls-remote` 查看远端分支：不存在则推送；存在且是新提交的祖先则快进推送；否则抛 409 `publish_conflict`。
  - 不用 `--force`，不用 `+` refspec。
- `publish.pull_request(kind, host, path, branch, base_branch, title, body, secret, api=api_base) -> {"url", "number", "reused"}`：
  - GitHub：先 `GET /repos/{path}/pulls?head={owner}:{branch}&state=open`，没有时 `POST /repos/{path}/pulls`（`draft: true`）。
  - GitLab：`GET /projects/{quote(path)}/merge_requests?source_branch=…&state=opened`，没有时 `POST`（标题前缀 `Draft: `）。
  - 默认分支取 `GET /repos/{path}` 的 `default_branch` 或 GitLab 项目的 `default_branch`；会话有 `ref` 时以 `ref` 为目标。
- `Service.publish(actor, rid, body) -> publish 记录`：
  - 权限、状态与凭证的检查见 Global Constraints；
  - 调用 Worker `worker_stream("/publish/bundle")`；
  - `receive` → `push` → `pull_request`；
  - 写快照与变更记录 `run.published`，审计 `credential.use`（`access: "write"`，`repo`，`branch`）。
- 错误：Worker 的拒绝原样透传（409 `nothing_to_publish` / `publish_secret_found`）；托管方 401/403 → 409 `credential_rejected`；网络错误 → 502。

- [ ] **Step 1: 写失败测试**（本机 HTTPS 需认证仓库，沿用 `tests/test_private_clone.py` 的 `GitHost`，但开启 receive-pack；另有假 GitHub / GitLab API）
  - 首次发布：远端出现 `octopus/<run>`，PR 以 draft 创建，快照与变更记录写入；
  - `test_publishing_again_reuses_the_pull_request`；
  - `test_a_branch_changed_by_someone_else_is_not_overwritten`：远端分支被另一提交推进 → 409，远端不变；
  - 分支名为 `main` / 会话 `ref` / 不带 `octopus/` 前缀 → 400；
  - 只读凭证 → 409 `credential_read_only`；成员非创建者 → 403；执行中 → 409；
  - `test_the_write_credential_never_reaches_the_worker`：假 Worker 收到的请求体中没有秘密，临时裸仓库的 config 中也没有；
  - `test_a_broken_bundle_publishes_nothing`；
  - GitLab 路径下以 `Draft:` 标题创建 MR。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_publish tests.test_credentials tests.test_private_clone`）。
- [ ] **Step 5: 提交** — `feat(credentials): 平台代发布——校验 bundle、只快进推送 octopus/<run>、创建或沿用 Draft PR / MR、写入会话与审计`

### Task 3: 控制台与 MCP

**Files:**
- Modify:
  - `web/src/features/sessions/SessionView.tsx`、`useSessionActions.ts`：「发布」按钮与 PR / MR 链接；
  - `web/src/api/types.ts`；
  - `octopus/mcp.py`：`octopus_publish {run_id, branch?, title?}`；
  - `skills/kirocrew-on/SKILL.md`。
- Test: `web/src/features/sessions/SessionView.test.tsx`、`tests/test_mcp_roles.py`

**Interfaces:**
- 会话页：
  - 绑定的凭证为写权限且会话已结束时，显示「发布到仓库」；
  - 已发布时显示「已发布：<分支> · PR #<n>（链接）」；
  - 只读凭证时显示「凭证为只读，无法发布」。
- 点击后发出 `POST …/publish {}`，成功后提示并显示链接；`publish_secret_found` 时列出文件。

- [ ] **Step 1–4:** 测试 → 实现 → 通过（`npm --prefix web test`、`$PY -m unittest tests.test_mcp_roles`）。
- [ ] **Step 5: 提交** — `feat(console): 会话页「发布到仓库」与 PR / MR 链接；MCP octopus_publish`

### Task 4: 回归与 EC2 验收

- [ ] **Step 1:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 2:** 部署前确认没有进行中的会话；部署平台与 `w1`。
- [ ] **Step 3: 验收**：
  - 负责人提供的测试仓库 `LiboMa/bedrock-claude-call`（公开仓库；凭证需改为写权限）：一个最小会话发布到 `octopus/<run>` 并创建 Draft PR；
  - 再次发布时沿用同一 PR；
  - 远端分支被改动时被拒；
  - `.env` 与疑似凭证不被推送；
  - 审计与变更记录写入。
  - 负责人醒来确认前不在公开仓库实际创建 PR：确认前只验收到「推送分支之前」，或改用私有测试仓库。实施时在 ledger 记 Ruling。
- [ ] **Step 4:** 向负责人报告；确认后推送。
