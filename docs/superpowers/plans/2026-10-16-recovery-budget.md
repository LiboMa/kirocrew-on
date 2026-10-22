# 恢复阶梯第 3 期：会话预算（D42） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话的自动纠错预算（协调者轮数、自动重试次数、时长）从会话开始或用户最近一次续作 / 请求诊断起计算，用完时写明哪一项，各处显示消耗。

**Architecture:** `recovery.budget(db, run)` 计算三项消耗；`recovery.schedule` 与 Engine 的两处自动重试（D34、D39）据此放行；续作与请求诊断写快照 `budget_since`；
`summary`、`attention`、MCP、报告与控制台显示。

**Tech Stack:** Python 3 标准库、React + TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-16-recovery-budget-design.md`

## Global Constraints

- 设置：`max_retries` 默认 4（0–20 的整数）、`budget_minutes` 默认 720（30–4320 的整数）；默认值不保存；错误文字「max_retries 须为 0–20 的整数」「budget_minutes 须为 30–4320 的整数（分钟）」。
- `since` = 快照 `budget_since`，否则 `runs.created`。
- `cycles.used`：`recoveries.created ≥ since` 且 `context.manual` 不为真；`retries.used`：`attempts.created ≥ since` 且 evidence 有 `transient_retry_of`；`minutes.used = int((now − since) // 60)`。
- `exhausted`：按 cycles → retries → minutes 的顺序，第一个 `used ≥ limit`（minutes 为 `used ≥ limit`）的键，否则 `None`。
- 自动重试放行：`retries` 与 `minutes` 未用完；诊断放行：`cycles` 与 `minutes` 未用完。
- `attention.action` 后缀：cycles「自动纠错已达到 N 轮上限」、retries「自动重试已达到 N 次上限」、minutes「自动纠错时长已超过 N 分钟」，
  后接「（自上次续作起）；续作会重新计算预算，也可在续作时调大。」
- 控制台：「预算（自上次续作起）：纠错 a/b 轮 · 自动重试 c/d 次 · e/f 分钟」。

## Review Focus

1. **续作重置**：轮数用完、续作后下一次失败再次被诊断（`test_a_continue_gives_the_coordinator_new_rounds`）。
2. **手动诊断**：不计入、并重置窗口（`test_a_requested_diagnosis_is_not_counted_and_resets_the_window`）。
3. **会话内自动重试总数**：两个任务先后瞬时失败，`max_retries: 1` 时第二个不重试而进入诊断（`test_the_retry_budget_spans_the_session`）。
4. **时长**：超过时不重试、不诊断，`blocked` 并写明（`test_past_the_time_budget_nothing_automatic_starts`）。
5. **已有会话**：无 `budget_since` 时按创建时间计（`test_an_existing_session_counts_from_its_creation`）。

---

### Task 1: 预算的计算与使用

**Files:** `octopus/recovery.py`、`octopus/engine.py`、`octopus/domain.py`（续作 / 请求诊断写 `budget_since`）；Test: `tests/test_budget.py`

- [ ] 测试先行：上面 Review Focus 1–5 与 `test_the_budget_settings_are_checked_and_defaults_not_stored`、`test_the_watchdog_retry_is_within_the_budget`。
- [ ] 实现：`policy` 接受两个新键；`budget(db, run, now=None)`；`schedule` 的条件改为 `budget`；Engine `_stalled` 与瞬时重试处加放行判断；
  `operate` 的 continue 与 recover 写 `budget_since`。
- [ ] Commit `feat(recovery): 会话预算——协调者轮数、自动重试次数与时长，从会话开始或最近一次续作 / 请求诊断起计算（D42 §3.1–§3.2）`

### Task 2: 可见

**Files:** `octopus/recovery.py`（`summary`）、`octopus/domain.py`（`attention`）、`octopus/mcp.py`、`octopus/report.py`、`octopus/schema.py`、
`web/src/api/types.ts`、`web/src/features/sessions/RecoveryPanel.tsx`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`；
Test: `tests/test_budget.py`、`web/src/features/sessions/SessionView.test.tsx`

- [ ] 测试先行：摘要的 `budget`；`attention` 的三种后缀；MCP 精简状态与报告的预算行；控制台的预算行与用完时的标记。
- [ ] 实现、文档、全量测试、构建。
- [ ] Commit `feat(console),docs: 预算消耗可见；运维与进度（D42 §3.3）`
