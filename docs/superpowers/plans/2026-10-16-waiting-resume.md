# 恢复阶梯第 4 期：等待与条件满足后自动恢复（D43） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台定期读取 Worker 健康并保存能力；Kiro 登录恢复、Worker 重新启用、执行环境重新服务、凭证重新可用时，等待中的会话自动继续。

**Architecture:** 新模块 `octopus/waiting.py`：`conditions(db, run)` 识别会话在等什么，`resume(db)` 对满足的会话按阶段继续并记 `run.resumed`；
`Engine.probe()` 读取 `/health`、写回能力、为等待 Kiro 登录的 Attempt 记下「见过过期」；Engine 周期在 `reconcile` 之后调用 `waiting.resume`；
`domain._attention` 显示可自动恢复的等待。

**Tech Stack:** Python 3 标准库、React + TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-16-waiting-resume-design.md`

## Global Constraints

- 健康检查：`/health` 时限 5 秒；等待 Kiro 登录的 Worker 每 60 秒，其余已启用、未退役的 Worker 每 600 秒；`boundary` 一致才写回 `capabilities`（附 `checked_at`）。
- `kiro_login`：`blocked`、失败 Attempt `failure_type == "auth"`、任务 `tool == "kiro"`；Attempt 证据 `waiting = {"for": "kiro_login", "expired_at": t}` 由检查写入；
  见过过期后检查到 `ok` → 记 `waiting.recovered_at`，`resume` 据此继续。
- 继续：失败任务及其后未验收的任务 `pending`、`current_attempt = NULL`（变更 `task.reset`），会话 `ready`（或 3.2 表中的状态），`error = NULL`，
  变更 `run.resumed`（`journal.SYSTEM` / `ENGINE`），原因文字见设计 §3.2。不写 `budget_since`。
- `attention` 标题：「等待 Kiro 登录恢复」「执行环境已停用」；操作文字见设计 §3.3。

## Review Focus

1. **不反复重跑**：登录一直正常时，认证失败不自动重跑（`test_an_auth_failure_with_a_good_login_waits_for_a_person`）。
2. **只在见过过期之后**：失败后第一次检查就是 `ok`（没见过过期）不继续（同上）。
3. **停用期间的运行中任务**：重新启用后会话回到 `running`、任务结果照常判定（`test_a_worker_enabled_again_resumes_the_running_session`）。
4. **边界变化**：健康检查不覆盖边界不同的能力（`test_the_probe_saves_capabilities_only_for_the_same_boundary`）。
5. **预算**：自动继续不重置也不消耗预算（`test_resuming_is_not_a_users_word`）。

---

### Task 1: 健康检查与 Kiro 登录

**Files:** `octopus/waiting.py`（新）、`octopus/engine.py`（`probe`、线程）；Test: `tests/test_waiting.py`

- [ ] 测试先行：登录过期 → 恢复后自动重跑并完成；Review Focus 1、2、4；Claude 任务不等待；Worker 不可达只记日志。
- [ ] 实现 `Engine.probe(now=None)`、`waiting.kiro_waiting(db)`、`waiting.resume(db)` 的 `kiro_login` 分支；Engine 线程每 60 秒 `probe`。
- [ ] Commit `feat(engine): 定期读取 Worker 健康并保存能力；Kiro 登录恢复后自动继续因登录失败的任务（D43 §3.1–§3.2）`

### Task 2: Worker、执行环境与凭证

**Files:** `octopus/waiting.py`、`octopus/engine.py`（周期调用）；Test: `tests/test_waiting.py`

- [ ] 测试先行：Review Focus 3、5；`waiting_worker` 重新启用后继续；凭证停用致准备失败 → 重新启用后自动准备。
- [ ] 实现 `worker_enabled`、`environment`、`credentials` 分支。
- [ ] Commit `feat(engine): Worker 重新启用、执行环境重新服务与凭证可用后自动继续（D43 §3.2）`

### Task 3: 可见与文档

**Files:** `octopus/domain.py`（`_attention`）、`web/src/lib/labels.ts`（`run.resumed`）、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`；
Test: `tests/test_waiting.py`、`web` 相应测试

- [ ] 测试先行：两种新的 `attention`；凭证操作的后缀；时间线标签。
- [ ] 实现、文档、全量测试、构建。
- [ ] Commit `feat(console),docs: 可自动恢复的等待写明在等什么；运维与进度（D43 §3.3）`
