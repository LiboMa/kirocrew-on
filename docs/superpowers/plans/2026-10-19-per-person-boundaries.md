# 每人一个执行边界（D49 第 1 期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台主机上可以开通多个 KiroCrew 边界，每个边界专属于一个人（自己的 OS 用户、Gateway、Worker、kiro 登录、记忆与资源上限），升级覆盖全部边界，个人会话自动落到自己的边界。

**Architecture:** 每个边界的安装步骤抽成 `deploy/install/boundary-lib.sh` 的函数（端口分配、准备、资源上限、契约检查、排空重启）；`al2023.sh` 对主机上全部边界做检查、切换后逐个重启；新的 `deploy/install/boundary.sh` 在当前发布上开通一个边界并登记为该用户的专属 Worker；`deploy.sh` 增加 `boundary add`、`boundaries` 与按边界的 `kiro-login` / `space` / `tunnel`；平台的个人会话优先选择唯一的专属 Worker。

**Tech Stack:** bash（AL2023）、Python 3.12 标准库、unittest。

**Spec:** `docs/superpowers/specs/2026-10-19-per-person-boundaries-design.md`

## Global Constraints

- 边界名 `[a-z][a-z0-9-]{1,19}`；OS 用户 `crew-<名称>`；数据 `/var/lib/octopus/boundaries/<名称>`；配置 `/etc/kirocrew-on/boundary-<名称>.toml`。
- 新边界端口：`gateway_port` 5481–5580、`worker_port` 9001–9100，跳过已有边界配置用过的与主机上正在监听的；`personal` 不变。
- 新边界 `worker_isolation = "dedicated"`、`crew_url = "http://localhost:<10000+gateway_port>"`、`crew_entry_ttl = "1h"`。
- 资源上限（新边界）：Gateway `MemoryHigh` = 3/4 × `MemoryMax`（默认 8 GiB）、`CPUQuota` 默认 300%、`TasksMax=4096`；Worker `MemoryMax=2G`。
- 升级：全部边界的原生契约检查都通过才切换 `current`；之后逐个排空重启。
- 不在任何输出中打印 Worker Token、Gateway 密钥或管理员密码。

## Review Focus

- 已有主机只有 `personal`：`push` 的行为与现在逐字一致（只检查、重启 `personal`）（Task 1 `test_a_host_with_only_personal_upgrades_as_before`）。
- 第二个边界的契约检查失败：不切换 `current`，两个边界都留在旧发布（Task 1 `test_any_failing_boundary_keeps_the_old_release`）。
- `boundary add` 重复执行：不改端口、不重复登记，只补缺（Task 2 `test_adding_again_only_fills_in`）。
- 用户名不存在或已有专属 Worker：`boundary add` 以非零退出并说明（Task 3 `test_registering_for_an_unknown_user_is_refused`）。
- 一个人有两个专属 Worker：自动选择不猜，会话等待选择（Task 3 `test_two_dedicated_workers_wait_for_a_choice`）。

---

### Task 1: 每个边界的安装步骤与多边界升级

**Files:**
- Create: `deploy/install/boundary-lib.sh`、`tests/test_boundaries.py`
- Modify: `deploy/install/al2023.sh`（`# gateway:begin`…`# gateway:end` 移入函数 `boundary_restart`；主边界契约检查后、切换前对其余边界 `boundary_check`；systemd 之后对其余边界 `boundary_env` + `enable` + `boundary_restart`）、`tests/test_deploy_assets.py`（网关片段测试改为从库函数取）

**Interfaces:**
- Produces（bash，ETC / DATA / SYSTEMD 可由环境覆盖，默认 `/etc/kirocrew-on`、`/var/lib/octopus/boundaries`、`/etc/systemd/system`）：
  `other_boundaries <主边界>`（其余边界名，按名称排序）、`free_port <low> <high> <key>`、`boundary_env <名称>`、
  `boundary_limits <名称> <内存GiB> <CPU%>`、`boundary_check <名称> <TARGET> <KC>`、`boundary_restart <名称> <KC>`。

- [ ] **Step 1: 失败的测试** —— `tests/test_boundaries.py`：
  - `test_free_ports_skip_what_boundaries_use_and_what_listens`：ETC 下 `boundary-a.toml`（`gateway_port = 5481`）、`ss` 桩报告 5482 在监听 → `free_port 5481 5580 gateway_port` 输出 5483；区间用尽时非零退出。
  - `test_other_boundaries_are_every_config_but_the_primary_and_checks`：`boundary-personal.toml`、`boundary-alice.toml`、`boundary-bob.toml`、`boundary-alice.check.toml` → `other_boundaries personal` 输出 `alice bob`。
  - `test_limits_are_drop_ins_the_gateway_fingerprint_reads`：`boundary_limits alice 8 300` 写 `octopus-gateway@alice.service.d/limits.conf`（`MemoryHigh=6G`、`MemoryMax=8G`、`CPUQuota=300%`、`TasksMax=4096`）与 `octopus-worker@alice.service.d/limits.conf`（`MemoryMax=2G`）。
  - `test_restart_drains_then_restarts_that_boundary`：以桩运行 `boundary_restart alice`（systemctl / runuser 记录调用）→ 首次重启 `octopus-gateway@alice` 与 `octopus-worker@alice`、写指纹、删除 `.maintenance`；未变化时只重启 Worker。
  - `test_a_host_with_only_personal_upgrades_as_before`：al2023.sh 中 `other_boundaries` 的两个循环在主边界步骤之后；只有 personal 时循环体不执行（静态 + 以桩运行循环片段）。
  - `test_any_failing_boundary_keeps_the_old_release`：al2023.sh 中对其余边界的 `boundary_check` 循环位于 `ln -sfn "$TARGET" /opt/kirocrew-on/current` 之前，且脚本 `set -e`。
  - 现有 `test_gateway_restarts_only_when_what_it_loads_changed` 改为调用库函数，断言不变。
- [ ] **Step 2: 运行确认失败**（`.venv/bin/python -m unittest tests.test_boundaries tests.test_deploy_assets`）。
- [ ] **Step 3: 实现** —— 库函数按 Interfaces；`boundary_check` 即 al2023.sh 现有契约检查（以 `crew-<名称>`、check toml、`composition.gateway --check` + `tests/native`）并在版本一致时写 `kirocrew_versions`；al2023.sh 主边界行为不变。
- [ ] **Step 4: 运行确认通过**（加 `tests.test_deploy_safety tests.test_maintenance tests.test_service_lifecycle`）。
- [ ] **Step 5: Commit** —— `feat(deploy): 每个边界的安装步骤抽为 boundary-lib.sh；升级对主机上全部边界做原生契约检查后才切换，再逐个排空重启（D49）`

### Task 2: 开通边界与按边界的运维命令

**Files:**
- Create: `deploy/install/boundary.sh`
- Modify: `deploy/aws/deploy.sh`（`boundary add <名称> --owner <用户名> [--memory <GiB>] [--cpu <%>]`、`boundaries`、`kiro-login --boundary <名称>`、`space` 在 `boundaries/*/worker/<run-id>` 中定位、`tunnel --boundary <名称>`）、`tests/test_boundaries.py`

**Interfaces:**
- Consumes: Task 1 的库函数。
- Produces: `boundary.sh <名称> --owner <用户名> [--memory N] [--cpu N]`（root，用 `/opt/kirocrew-on/current` 与 `/opt/kirocrew/current`）：用户与目录 → `boundary_env` → codex 配置 → 边界配置（`free_port`、dedicated、回环 crew_url）→ `boundary_limits` → `boundary_check` → `systemctl enable --now` → `bootstrap-worker --owner`。

- [ ] **Step 1: 失败的测试**：
  - `test_boundary_add_runs_the_boundary_script_through_ssm`：`DEPLOY_DRY_RUN=1 deploy.sh boundary add alice --owner alice` 的输出含 `deploy/install/boundary.sh alice --owner alice`、经 SSM 在平台实例执行；名称或用户名不合规（`Alice`、`a;b`、空）退出码 2。
  - `test_boundary_script_writes_its_own_ports_and_dedicated_config`：以桩（useradd、runuser、systemctl、chown、`ss`）与临时 ETC / DATA 运行 `boundary.sh alice --owner alice`（`BOUNDARY_SKIP_CHECK=1`、`BOUNDARY_SKIP_REGISTER=1` 仅供测试）→ 配置的端口在区间内、`worker_isolation = "dedicated"`、`crew_url = "http://localhost:<10000+端口>"`、路径都在 `/boundaries/alice/`；单元 `octopus-gateway@alice`、`octopus-worker@alice` 被 enable。
  - `test_adding_again_only_fills_in`：再次运行端口不变、配置不被覆盖。
  - `test_kiro_login_space_and_tunnel_take_a_boundary`：dry-run 的 `kiro-login --boundary alice` 以 `crew-alice` 登录平台实例；`tunnel --boundary alice` 转发该边界的 gateway_port 到 10000+；`space <run>` 的远端脚本在 `boundaries/*/worker/<run>` 中查找。
  - `test_boundaries_lists_name_owner_ports_and_state`：dry-run 输出的远端脚本读取每个 `boundary-*.toml` 的端口与 `systemctl is-active`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（加 `tests.test_deploy_assets`）。
- [ ] **Step 5: Commit** —— `feat(deploy): deploy.sh boundary add 在平台主机开通专属于一个人的边界（独立端口、资源上限、登记为其专属 Worker）；boundaries、按边界的 kiro-login / space / tunnel（D49）`

### Task 3: 登记给个人与自动选择

**Files:**
- Modify: `octopus/bootstrap.py`（`register_boundary_worker(..., owner=None)`：按用户名查 `GET /v1/admin/users`，登记 `owner_id` 为该用户；重复执行按名称 + 所有者匹配并刷新连接；该用户已有其他启用的专属 Worker 时拒绝）、`octopus/__main__.py`（`bootstrap-worker --owner`）、`octopus/domain.py`（个人会话自动选择：恰好一个启用的专属 Worker 时选它）
- Create: `tests/test_personal_boundaries.py`

- [ ] **Step 1: 失败的测试**：`test_registering_for_a_user_makes_it_their_dedicated_worker`、`test_registering_again_refreshes_the_connection`、`test_registering_for_an_unknown_user_is_refused`、`test_their_own_worker_is_chosen_over_a_shared_one`、`test_two_dedicated_workers_wait_for_a_choice`、`test_team_sessions_are_unchanged`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（加 `tests.test_teams_workers tests.test_bootstrap` 若存在、`tests.test_mvp`）。
- [ ] **Step 5: Commit** —— `feat(workers): 边界可登记为某个用户的专属 Worker（bootstrap-worker --owner）；个人会话优先选择唯一的专属 Worker（D49）`

### Task 4: 文档、回归、终审与 EC2

- OPERATIONS「每人一个边界（D49）」、WORKERS.md 的同主机边界一节更新为已实现。
- `tools/regress.sh` → OK；opus 终审，Critical / Important 一次修复，Minor 记 BACKLOG。
- EC2（负责人确认后）：`deploy.sh push`（只有 personal 时行为不变）→ `boundary add <名称> --owner <负责人>` → 负责人 `kiro-login --boundary <名称>` → 隔离核对 → 负责人的会话落到新边界并完成 → D46 P4 验收。
