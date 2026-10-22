# 保留上下文的返工（D46 P5a）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台安排的返工（协调者修复目标任务、返工后的复验与重新执行）在上一个 Attempt 的原生对话上续接，保留上下文；不能续接时退回全新派发。

**Architecture:** 引擎在创建派发请求时判断是否续接（`continuation`），把 `continue: {conversation, from}` 随 `/dispatch` 发给 Worker；Worker 核对对话属于本会话后调 KiroCrew `POST /api/spawn/{conversation}/continue`，被拒（400 / 404 / 409）时全新派发并记 `continue_refused`；结果（`continued` / `continue_refused`）并入 Attempt 证据，控制台、MCP、报告显示。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-19-rework-continue-design.md`

## Global Constraints

- 适用：执行契约 ≥4、Worker 能力 `continue`。
- 续接条件：`repair` 存在（修复目标 / `recheck` / `rerun`）；同一任务存在上一个有原生 ID 的 Attempt；工具、模型、Effort 相同；本 Attempt 不是 `switched_from`、不是 `transient_retry_of`；上一个 Attempt 之后没有用户续作（修订 `kind: continued`）。
- 对话 ID：上一个 Attempt 证据中的 `continued.conversation`，没有时为它的原生 ID。
- 续接请求体：`{task, parent_session, agent}`，显式模型时加 `model`；不发 `reasoning_effort`、`approval_mode`、`include_*`。
- 续接消息另加：「这是同一任务的返工：你上一轮的工作与对话都在，先核对本轮要求，在已有改动上修复，不要从头重写。」全新派发（含回退）不加。

## Review Focus

- 续接被拒后回退的全新派发：消息不含「上一轮的工作与对话都在」（Task 1 `test_a_refused_continue_spawns_afresh_with_its_own_words`）。
- 续接的回执丢失：对账仍按标记找到新一轮（Task 1 `test_a_lost_continue_receipt_is_reconciled`）。
- 对话 ID 来自另一个会话：Worker 拒绝（Task 1 `test_a_conversation_of_another_session_is_refused`）。
- 用户续作之后的返工：不续接（Task 2 `test_after_a_user_continue_it_starts_afresh`）。
- 第三次返工：对话 ID 仍是第一个 Attempt 的（Task 2 `test_the_conversation_is_the_first_of_the_chain`）。

---

### Task 1: Worker 续接与回退

**Files:** Modify `octopus/worker.py`（`dispatch` 的 `continue`、对话核对、回退、`health()["continue"]`）、`tests/kirocrew_contract.json`（`POST /api/spawn/{}/continue`：模块 `dashboard/messaging_api/spawn.py`，reads `task, parent_session, agent, model, max_turns`）；Create `tests/test_continue_worker.py`。

- [ ] 失败的测试：`test_a_rework_continues_its_conversation`（请求路径与请求体、消息含返工说明、`value.continued`）、`test_a_refused_continue_spawns_afresh_with_its_own_words`（404 / 409 / 400 → `/api/spawn`、`continue_refused`、消息不含返工说明）、`test_a_conversation_of_another_session_is_refused`、`test_a_lost_continue_receipt_is_reconciled`、`test_the_worker_says_it_continues`、`test_the_contract_lists_the_continue_endpoint`。
- [ ] 运行确认失败 → 实现 → 通过（加 `tests.test_kirocrew_contract tests.test_mvp tests.test_knowledge_worker`）。
- [ ] Commit：`feat(continue): Worker 在原生对话上续接返工，被拒时全新派发并记录；契约加 /api/spawn/{id}/continue（D46 P5a）`

### Task 2: 平台判断续接与显示

**Files:** Modify `octopus/engine.py`（`continuation()`、派发体 `continue`、证据并入）、`octopus/mcp.py`（`latest.continued` / `continue_refused`）、`octopus/report.py`（Attempt 注明）、`web/src/features/sessions/TaskList.tsx`；Create `tests/test_continue.py`、vitest。

- [ ] 失败的测试：`test_a_coordinators_rework_continues`、`test_a_recheck_after_rework_continues`、`test_a_first_dispatch_a_switch_a_new_effort_or_a_retry_starts_afresh`、`test_after_a_user_continue_it_starts_afresh`、`test_the_conversation_is_the_first_of_the_chain`、`test_an_older_worker_is_never_asked`、`test_the_attempt_says_it_continued`（证据、MCP、报告），vitest 任务卡片文案。
- [ ] 运行确认失败 → 实现 → 通过（加 `tests.test_recovery tests.test_contract tests.test_evaluator`）。
- [ ] Commit：`feat(continue): 平台安排的返工续接上一轮的原生对话（同工具、模型与 Effort，未经用户续作），任务卡片、MCP 与报告显示（D46 P5a）`

### Task 3: 文档、回归、终审与 EC2

- OPERATIONS「保留上下文的返工（D46 P5a）」；`tools/regress.sh`；opus 终审一次修复；EC2：审查要求修改 → 返工续接原对话并通过。
