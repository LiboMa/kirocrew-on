# 代码仓库凭证 第 1 期（D35） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 个人、Team 与平台各自保存 GitHub / GitLab 凭证（加密、秘密只写），会话提交时解析并固定绑定一条凭证，Worker 用它克隆私有仓库；秘密不出现在任何响应、日志、工作区、事件与审计中，每次使用都审计。

**Architecture:**
- **加密存储**：新模块 `octopus/vault.py` 用主机 `openssl enc -aes-256-cbc -pbkdf2`（主密钥从文件读取，`-pass file:`，不上命令行）加密，再以 HMAC-SHA256 校验完整性（先加密后认证）。
- **凭证管理**：新模块 `octopus/credentials.py` 负责凭证记录、作用域与授权、仓库范围匹配、提交时的解析顺序和 `test`（调用托管方 API）。
- **会话绑定**：绑定写入会话快照 `credentials`（经 `journal.change`），控制台与 API 显示创建者与凭证（无秘密）。
- **拉取**：Engine 在调用 Worker `/prepare` 的那一刻解密，把 `git_auth` 交给 Worker。Worker 用 `GIT_ASKPASS` 读取 0600 临时文件完成克隆后立即删除；remote 不含凭证，push 地址设为不可用；git 报错写入前去除凭证片段。

**Tech Stack:** Python 3 标准库 + 主机 `openssl`（OpenSSL 3，平台主机 3.5.8、本机 3.6.4 已核实支持 `-pbkdf2 -pass file:`）、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-13-repo-credentials-design.md`（§3、§4、§5.1、§6、§7、§8 第 1 期）

## Global Constraints

- 不新增 Python / npm 依赖；面向用户与 Agent 的文字使用中文。
- 第一版类型：`github_pat`、`gitlab_token`（`github_app` 在第 3 期）。主机只接受 HTTPS 主机名，不含路径、端口以外的部分。
- **秘密只写**：
  - 创建或轮换时提交，之后任何接口（含导出、变更记录、审计、错误信息、OpenAPI 示例）只给元数据与 `hint`（末 4 位）。
  - MCP 不创建凭证、不接受秘密。
- 主密钥：配置项 `credentials_key_file`。EC2 上为 `/etc/kirocrew-on/credentials.key`（root:octopus 0640，安装脚本生成，不进 SSM、不进备份包）；未配置时为数据目录下的 `credentials.key`（0600，首次使用时生成）。
- **权限语义**沿用访问管理：看不到为 404，能看到但不能管理为 403，状态不允许为 409。
  - 个人凭证：由本人管理与使用。
  - Team 凭证：由 Team owner 与平台管理员管理，该 Team 的会话使用。
  - 平台凭证：由平台管理员管理，被授权的 Team 与用户使用。
- 绑定解析顺序（R1）：
  1. 请求明确指定；
  2. 工作流版本的默认值（提交者有权使用时）；
  3. 按主机与仓库路径自动匹配，层级依次为：
     - Team 会话：Team → 授权给该 Team 的平台 → 提交者个人；
     - 个人会话：个人 → 授权给本人的平台。
  - 同一层级中 `repos` 更具体者优先；仍并列时 409 `credential_ambiguous`，并列出候选。
  - 没有可用凭证时不绑定（公开仓库）。
- 停用或删除立即生效：下一次使用得到 409 `credential_unavailable`。
- 现状：续作不重新拉取，只有首次准备与改派到其他 Worker 时克隆。第 1 期的「使用」即这两处。
- 提交规则：功能分支本地提交；EC2 验收并经负责人确认后推送。部署前确认没有进行中的会话。
- Python：`$PY -m unittest <模块>`；控制台：`npm --prefix web test`；完整回归：`PYTHON=$PY tools/regress.sh`。

## Review Focus

1. **秘密泄露**：创建后经 `GET /v1/credentials`、会话视图、导出、变更记录、审计、错误响应、Worker 的 `project.json` 与 `.git/config` 都找不到秘密原文（Task 2、4 测试 `test_the_secret_never_comes_back`、`test_a_private_clone_leaves_no_secret_behind`）。
2. **混淆代理**：Team 成员 A 不能借别人的个人凭证或未授权的平台凭证提交；凭证的 `repos` 范围之外的仓库被拒（Task 3 测试 `test_one_cannot_use_a_credential_one_may_not_use`）。
3. **主密钥**：不出现在任何子进程的命令行参数中（Task 1 `test_the_key_never_reaches_a_command_line`）；密文被篡改或换了主密钥时解密报错，而不是返回错误的明文。
4. **git 报错**：错误信息（含 URL 中的用户名、`Authorization`、askpass 输出）在写入 `project.json`、错误响应与会话 `error` 前被清理（Task 4 `test_git_errors_never_carry_the_secret`）。
5. **生效时机**：停用、删除或从会话撤下凭证后，改派 Worker 重新克隆时被拒（409 `credential_unavailable`），会话进入需要处理的状态并说明原因（Task 4）。

---

### Task 1: 加密存储（`octopus/vault.py`）

**Files:** Create `octopus/vault.py`、`tests/test_vault.py`；Modify `octopus/config.py`（`credentials_key_file`）

**Interfaces:**
- `vault.key_file(settings_path, data_dir) -> Path`：配置的路径，或 `<data_dir>/credentials.key`（不存在时生成 32 字节随机值的 base64，0600）。
- `vault.seal(key_file, plaintext: str) -> str`：返回 `v1.<base64 密文>.<base64 HMAC>`。
  - 密文由 `openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -pass file:<key_file>` 生成，明文经 stdin 传入。
  - HMAC 的密钥为 `HMAC-SHA256(主密钥, "kirocrew-credentials-mac")`，对密文计算。
- `vault.open_(key_file, sealed) -> str`：先校验 HMAC（`compare_digest`），不符时抛 `VaultError("凭证密文校验失败")`；再解密。openssl 失败时抛 `VaultError`，消息不含明文与密钥。
- `vault.hint(secret) -> str`：`"…" + secret[-4:]`。

- [ ] **Step 1: 写失败测试**
  - 往返加解密；
  - 同一明文两次 `seal` 结果不同（随机盐）；
  - 改动密文或 HMAC 中的一个字符 → `VaultError`；换一把主密钥 → `VaultError`；
  - `test_the_key_never_reaches_a_command_line`：patch `subprocess.run`，记录 argv，断言 argv 中没有主密钥内容与明文，明文只经 `input=` 传入；
  - 未配置时在数据目录生成 0600 的密钥文件，再次调用沿用同一把。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_vault tests.test_config`）。
- [ ] **Step 5: 提交** — `feat(credentials): 凭证加密存储——openssl AES-256-CBC（主密钥从文件读取）+ HMAC 先加密后认证`

### Task 2: 凭证记录、作用域与接口

**Files:**
- Create `octopus/credentials.py`、`tests/test_credentials.py`
- Modify:
  - `octopus/migrations.py`：第 6 版 `credentials`、`credential_grants`、`workflow_versions.credentials`；
  - `octopus/domain.py`：Service 方法；
  - `octopus/server.py`：路由与 `required_scope`；
  - `octopus/schema.py`。

**Interfaces:**
- 迁移 6：
  - `credentials(id, name, kind, host, scope, owner_id, repos TEXT '[]', access 'read'|'write', inject INTEGER 0, hint, secret TEXT, expires REAL, created_by, created, updated, last_used REAL, disabled INTEGER 0)`；
  - `credential_grants(credential_id, team_id, user_id)`，两者恰有其一，唯一；
  - `ALTER TABLE workflow_versions ADD COLUMN credentials TEXT NOT NULL DEFAULT '{}'`。
- `credentials.view(row) -> dict`：元数据，无 `secret`。
- `credentials.repo_path(url) -> (host, path)`：从 `https://host/owner/repo(.git)` 取主机与路径；非 HTTPS 时 `ValueError`。
- `credentials.covers(repos, path) -> int | None`：返回匹配的具体程度（精确 3、`a/b/*` 前缀 2、`a/*` 1、空列表 0），不匹配为 None。
- `credentials.usable(db, actor_id, row, team_id) -> bool`：未停用；个人凭证属于本人；Team 凭证属于该会话的 Team 且本人是成员；平台凭证授权给本人或该 Team。
- `credentials.manageable(db, actor, row) -> bool`：个人凭证由本人管理，Team 凭证由 Team owner 或提升后的管理员，平台凭证由提升后的管理员。
- HTTP：
  - `GET /v1/credentials?host=&scope=`（read）：本人可使用或可管理的凭证，每条带 `can_manage`。
  - `POST /v1/credentials {name, kind, host, scope, team_id?, repos?, access?, secret}`（manage；平台作用域需要提升后的管理员）→ 视图。
  - `PATCH /v1/credentials/<id> {name?, repos?, access?, disabled?, secret?}`（manage）：有 `secret` 时为轮换，ID 不变，`hint` 更新。
  - `DELETE /v1/credentials/<id>`（manage）。
  - `POST /v1/credentials/<id>/test`（manage）：调用托管方 API，返回 `{ok, account, expires}` 并更新 `expires`。GitHub 为 `GET https://api.github.com/user`（`Authorization: Bearer`，过期时间取 `github-authentication-token-expiration` 头）；GitLab 为 `GET https://<host>/api/v4/user`（`PRIVATE-TOKEN`）。API 地址经 `credentials.API_BASE(kind, host)`，测试中可替换。
  - `POST|DELETE /v1/credentials/<id>/grants {team_id|username}`（admin）。
- 审计：`credential.create|update|rotate|disable|delete|grant|revoke|test`，`detail` 只有元数据。

- [ ] **Step 1: 写失败测试**
  - 三种作用域的创建与可见性；
  - 他人个人凭证 → 404；Team 成员管理 Team 凭证 → 403；非管理员创建平台凭证 → 403；
  - 平台凭证授权给 Team 后，该 Team 成员可见且可用，未授权者 → 404；
  - 轮换后 ID 不变、`hint` 更新、旧密文不可再解出旧值；
  - `test` 经假托管方服务器：GitHub 与 GitLab 两种，过期头被读出；401 → `{ok: false}`，不回传秘密；
  - 审计有元数据、无秘密；
  - `covers` 的四种具体程度；`repo_path` 拒绝 ssh / scp 地址；
  - `test_the_secret_never_comes_back`：创建后在列表、单条、审计、错误响应（故意提交无效 `repos`）的全部 JSON 中查找秘密 → 没有。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_credentials tests.test_migrations tests.test_access tests.test_server_shadowing`）。
- [ ] **Step 5: 提交** — `feat(credentials): 代码仓库凭证——个人 / Team / 平台作用域、授权、只写秘密、托管方测试与审计`

### Task 3: 提交时解析与会话绑定

**Files:**
- Modify:
  - `octopus/credentials.py`：`resolve`；
  - `octopus/domain.py`：`start` 接受 `credentials`、`save_workflow` 接受 `credentials`、`_view` 显示 `created_by` 与绑定、`rebind`；
  - `octopus/server.py`：`POST /v1/runs/<id>/credentials`；
  - `octopus/schema.py`。
- Test: `tests/test_credentials.py`

**Interfaces:**
- `credentials.resolve(db, actor, source, team_id, explicit, default) -> binding | None`：
  - 返回 `{"id", "name", "kind", "scope", "owner", "hint", "bound_by", "at"}`。
  - 按 Global Constraints 的顺序解析。
  - 错误：明确指定但不能用 → 403 `credential_forbidden`，不在 `repos` 范围 → 409 `credential_scope`，SSH 地址 → 409 `credential_https_only`；自动匹配并列 → 409 `credential_ambiguous`，`error` 中列出候选名称。
- `POST /v1/runs`：可选 `credentials: {"source": "<id>"}`。绑定写入快照 `credentials.source`，并写 `run.credentials` 变更记录（`journal.change` 的 `snapshot` 字段，记录凭证名称与 `hint`）。
- `POST /v1/workflows` / `PUT`：可选 `credentials: {"source": "<id>"}`，保存到该版本（只是引用；保存者须有权使用）。
- `POST /v1/runs/<id>/credentials {"source": "<id>" | null, "expected_version"}`（execute）：
  - 会话创建者、Team owner 或管理员可以改绑（须有权使用新凭证）；
  - 个人凭证的所有者可以把自己的凭证从会话撤下（`null`）；
  - 写入变更记录。
- 会话视图：`created_by: {"id", "username"}`；`credentials: {"source": 绑定元数据}`，停用或删除时带 `"unavailable": true`。

- [ ] **Step 1: 写失败测试**
  - 明确指定 → 绑定；
  - 工作流默认值：提交者有权使用时绑定，无权时按自动匹配；
  - 自动匹配三层的顺序；同层更具体者优先；同层同具体程度 → 409 并列出两个名称；
  - 公开仓库无匹配 → 不绑定；SSH 地址加明确凭证 → 409；
  - `test_one_cannot_use_a_credential_one_may_not_use`：bob 指定 alice 的个人凭证 → 403；未授权的平台凭证 → 403；`repos` 范围外的仓库 → 409；
  - 改绑的权限：创建者、Team owner、管理员可以，其他成员 → 403；个人凭证所有者可撤下；
  - 会话视图与导出中有绑定元数据、无秘密；变更记录有 `run.credentials`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_credentials tests.test_mvp tests.test_journal tests.test_revisions tests.test_workflow_import`）。
- [ ] **Step 5: 提交** — `feat(credentials): 提交时解析并固定绑定凭证（明确指定 → 工作流默认 → 按仓库自动匹配），会话显示创建者与凭证，可改绑`

### Task 4: 用凭证克隆私有仓库

**Files:**
- Modify:
  - `octopus/engine.py`：准备时解密并传 `git_auth`，不可用时进入 `blocked` 并说明；
  - `octopus/worker.py`：`prepare` 接受 `git_auth`，`ValueError` 的源码错误以 409 `source_failed` 原文透传（清理后）；
  - `octopus/sources.py`：`prepare_project(..., auth=None)` 使用 `GIT_ASKPASS`，push 地址设为不可用，错误清理。
  - `octopus/credentials.py`：`material(db, run) -> git_auth | None`，并写 `credential.use` 审计与 `last_used`。
- Test: `tests/test_private_clone.py`

**Interfaces:**
- `git_auth = {"kind", "host", "username", "secret"}`：`username` 在 GitHub 为 `x-access-token`，在 GitLab 为 `oauth2`。只在 `/prepare` 的请求体中出现，不写入任何持久位置。
- `sources.prepare_project(run, source=…, ref=…, new=…, auth=None)`：
  - 有 `auth` 时，在运行目录写 `.askpass/`（0700），内含 `askpass.sh`（0700）与 `secret`（0600）；
  - 设置 `GIT_ASKPASS`、`GIT_TERMINAL_PROMPT=0`，并以 `-c credential.helper=` 禁用其他 helper；
  - 克隆完成后（成功或失败）删除整个 `.askpass/`；
  - 克隆后执行 `git remote set-url --push origin DISABLED_NO_PUSH`。
- `sources.scrub(text, secret=None) -> str`：去掉秘密原文、`https://user:pass@` 形式的 userinfo、`Authorization:` / `PRIVATE-TOKEN:` 行。`project.json` 的 `error` 与异常消息都经过它。
- Worker：源码准备失败时返回 409 `source_failed` 与清理后的原因（原为「Worker 操作未完成 (ValueError)」，BACKLOG 中的已知问题）。
- Engine：
  - 会话有绑定时调用 `credentials.material`；不可用时会话进入 `blocked`，`error` 为「凭证「<名称>」已停用或删除：请改绑后续作」，`attention` 给出动作。
  - 成功时审计 `credential.use`（凭证、会话、仓库、`read`、操作者为会话创建者），并更新 `last_used`。

- [ ] **Step 1: 写失败测试**（本机 `git` + 带 Basic 认证的本地 HTTP Git 服务。用 `git http-backend` 不便时，用 `ThreadingHTTPServer` 包一层 `git-http-backend` CGI；不可用时用 `GIT_ASKPASS` 断言加 `file://` 仓库的 `insteadOf` 规则模拟——实现时选能真实验证认证的那种，并在 ledger 记 Ruling）
  - 正确凭证 → 克隆成功；
  - 错误凭证 → 409 `source_failed`，消息为认证失败且不含秘密；
  - `test_a_private_clone_leaves_no_secret_behind`：克隆后运行目录、`.git/config`、`project.json` 中都没有秘密原文，`.askpass/` 不存在，push 地址为 `DISABLED_NO_PUSH`；
  - `test_git_errors_never_carry_the_secret`：`scrub` 处理 URL 中的 userinfo、`Authorization` 行与秘密原文；
  - Engine 用假 Worker：有绑定时请求体带 `git_auth`，无绑定时不带；绑定的凭证停用后 → 会话 `blocked`，`error` 说明需改绑，`git_auth` 不发出；审计有 `credential.use`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_private_clone tests.test_credentials tests.test_engine_concurrency tests.test_runtime tests.test_archive tests.test_sources`）。
- [ ] **Step 5: 提交** — `feat(credentials): 用绑定的凭证克隆私有仓库——GIT_ASKPASS 临时文件、push 地址不可用、错误去除凭证、使用写审计`

### Task 5: 控制台、MCP 与 Skill

**Files:**
- Create `web/src/features/settings/CredentialsDialog.tsx`
- Modify:
  - `web/src/features/console/TopBar.tsx`、`context.tsx`、`DialogHost.tsx`：设置菜单「代码仓库凭证」；
  - `web/src/features/launch/steps.tsx`、`state.ts`：源码一步按仓库自动选好凭证并可更换；
  - `web/src/features/sessions/SessionView.tsx`：创建者与凭证；
  - `web/src/api/hooks.ts`、`types.ts`；
  - `octopus/mcp.py`（`octopus_credentials`；`octopus_start` 透传 `credentials`）、`skills/kirocrew-on/SKILL.md`。
- Test: `web/src/features/settings/CredentialsDialog.test.tsx`、`web/src/features/launch/LaunchWizard.test.tsx`、`SessionView.test.tsx`、`tests/test_mcp_roles.py`

**Interfaces:**
- 设置 →「代码仓库凭证」：
  - 「个人」「各 Team」（owner 可管理）「平台」（管理员）三栏；列表显示名称、类型、主机、范围、权限、`hint`、到期、最近使用、停用；
  - 创建表单的秘密输入框为 `type=password`、只写，提交后清空；
  - 按钮「测试」「停用 / 启用」「轮换」「删除（确认）」。
- 向导：Git 来源填写 HTTPS 地址后，按主机列出可用凭证，默认「自动匹配」，可选具体一条；提交时只在选了具体一条时发送 `credentials.source`。
- 会话页：「创建者 alice · 凭证：alice 的个人 GitHub Token（…1a2b）」，凭证不可用时以警示样式显示。
- MCP `octopus_credentials {host?}`：只列出元数据，不提供创建；`octopus_start` 接受 `credentials`。Skill 说明：
  - 私有仓库要在控制台创建凭证；
  - 不要向用户索要 Token，不要把 Token 写进对话或项目；
  - 遇到 409 `credential_ambiguous` 时请用户选择。

- [ ] **Step 1–4:** 测试（创建表单发送的请求体；列表不显示秘密；向导在选择后发送 `credentials`、自动匹配时不发送；会话页文案；MCP 工具列表与透传）→ 实现 → 通过（`npm --prefix web test`、`$PY -m unittest tests.test_mcp_roles`）。
- [ ] **Step 5: 提交** — `feat(console): 代码仓库凭证的设置页、向导中的凭证选择与会话页显示；MCP octopus_credentials`

### Task 6: 部署、回归与 EC2 验收

- [ ] **Step 1: 安装脚本与配置**
  - 平台主机首次安装时以 `openssl rand -base64 32` 生成 `/etc/kirocrew-on/credentials.key`（root:octopus 0640，已存在时不覆盖），并在切换后写入 `credentials_key_file`；
  - `docs/OPERATIONS.md`：主密钥的保管与恢复（备份包只含密文；恢复到新主机需同一主密钥），以及凭证的轮换；
  - `docs/MVP-RELEASE.md` 写入 D35 与验收记录；
  - 测试 `tests/test_deploy_assets.py`：生成步骤、权限、不覆盖、切换后写入。
- [ ] **Step 2:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 3:** 部署前确认没有进行中的会话；`deploy.sh push`、`deploy.sh worker-host push w1`。
- [ ] **Step 4: 验收**（需要负责人提供：一个私有 GitHub 测试仓库与 fine-grained PAT、一个 GitLab 测试仓库与 Token；负责人在控制台自己录入秘密，秘密不经对话）
  - 个人凭证与 Team 凭证各克隆一次（会执行一个最小任务，执行前征得负责人同意）；
  - `test` 显示账号与到期；
  - 越权提交被拒；停用后改派 Worker 被拒；
  - 在 API 响应、导出、审计、Worker 的 `project.json` 与 `.git/config` 中查找秘密原文 → 没有。
- [ ] **Step 5:** 向负责人报告；确认后推送。
