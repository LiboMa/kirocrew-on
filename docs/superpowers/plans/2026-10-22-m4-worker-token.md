# M4-2 Worker Token 移出数据库（K10）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据库中的 Worker 配置不再含明文 Token；平台调用 Worker 时才用 D35 的密钥解封。

**Architecture:** `transport` 接受 `token_sealed` 并在发请求前解封（模块级配置的密钥路径）；写入 Worker 配置的两条路由先密封；平台启动时把旧的明文密封。

**Tech Stack:** Python 3.12 标准库 + openssl（`octopus/vault.py`）。

**Spec:** `docs/superpowers/specs/2026-10-22-m4-worker-token-design.md`（D59）

## Global Constraints

- 解封结果不写回配置字典、不进日志、不出现在 API 回答中（`/v1/workers`、`/v1/admin/environments` 照旧不含 Token）。
- 迁移可重复执行；缺 `credentials_key_file` 时按 `vault.key_file` 自动生成。
- 测试与旧数据中的明文 `token` 仍可用（transport 接受两种形式），升级当次即被密封。

## Review Focus

- **密钥文件丢失或被换**：解封失败时 Worker 调用以「执行环境的访问凭据无法解开」报 503，不崩溃，不回退到明文（Task 1 测试）。
- **能力刷新改写配置**：引擎刷新 capabilities 后 `token_sealed` 仍在、没有 `token`（Task 2 测试）。
- **重复启动**：第二次启动不重复密封、不改变已密封的值（Task 3 测试）。

---

### Task 1: transport 解封

**Files:** Modify `octopus/transport.py`（`configure(key_path)`、`connection(config) -> {"url", "token"}`，三个请求函数经它取 Token）、`octopus/domain.py`（D56 diff、D58 sync 的键名挑选改为保留 `token_sealed`）；Test `tests/test_worker_token.py`

- [ ] Step 1 测试：`token_sealed` 的配置能请求 Worker（替身 HTTP 服务核对 Authorization）；明文配置照旧；密钥不符时 503 `worker_secret`；`connection` 不改动传入的字典。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过（含 `tests.test_attempt_diff tests.test_sync`）。
- [ ] Step 5 提交。

### Task 2: 写入即密封

**Files:** Modify `octopus/server.py`（注册与更新连接的写入）、`octopus/engine.py`（capabilities 刷新保留 `token_sealed`）；Create `octopus/workers.py`（`seal(config, key) -> dict`，去掉 `token`、加 `token_sealed`）；Test `tests/test_worker_token.py`

- [ ] Step 1 测试：经 API 注册与更新连接后，数据库中只有 `token_sealed`；`GET /v1/workers` 与环境列表不含任何 Token 字段；能力刷新后仍只有 `token_sealed`。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过（含 `tests.test_workers tests.test_access tests.test_contract`）。
- [ ] Step 5 提交。

### Task 3: 已有明文的密封

**Files:** Modify `octopus/server.py`（`server()` 启动时调用）、`octopus/workers.py`（`seal_stored(db, key) -> int`）；Test `tests/test_worker_token.py`

- [ ] Step 1 测试：含明文 Token 的数据库启动后全部密封、返回改写数；第二次为 0 且值不变；没有配置密钥时在数据目录生成。
- [ ] Step 2 失败；Step 3 实现；Step 4 通过。
- [ ] Step 5 提交。

### Task 4: 文档、回归、部署与 EC2 核对

- [ ] OPERATIONS（密钥与备份说明补 Worker Token）、MVP-RELEASE K10 行、progress（m4-k10）、BACKLOG。
- [ ] 全量回归；部署（先通知 D57 会话、确认主机空闲）；EC2：`workers` 表只有 `token_sealed`，一个会话完整跑通。
