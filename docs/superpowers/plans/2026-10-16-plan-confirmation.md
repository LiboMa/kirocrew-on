# 准入第 1 期：计划确认（D44） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话可设 `admission: "confirm"`：规划照常完成，第一个执行任务派发前停在 `awaiting_confirmation`，经 `confirm`（API / MCP / 控制台）或续作后执行。

**Architecture:** 快照 `admission` / `confirmed`；Engine 创建非规划任务的 Attempt 前检查闸门；`Service.operate` 增加 `confirm`；状态加入 `SETTLED`；
MCP `octopus_confirm`、`octopus_start.admission`；控制台标签、需要处理卡片的按钮与时间线标签。

**Tech Stack:** Python 3 标准库、React + TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-16-plan-confirmation-design.md`

## Global Constraints

- `admission` ∈ {`confirm`, `auto`}；错误文字「admission 须为 confirm 或 auto」；平台配置 `default_admission`，默认 `auto`。
- 状态 `awaiting_confirmation`；变更 `run.awaiting_confirmation`（原因「计划待确认」）、`command.confirm`；快照 `confirmed = {by, at, via}`（`via`: `confirm` / `continue`）。
- `confirm` 不在待确认时：409 `invalid_state`「此 Session 不在等待确认」。
- `attention`：标题「计划待确认」，操作「核对任务、工具与检查命令后确认开始；也可修改后续作或关闭。」
- 控制台标签「计划待确认」，按钮「确认并开始」，时间线「计划待确认」「已确认计划」。

## Review Focus

1. **规划不被拦**：KiroCrew 规划的会话先完成 `plan`，再停在待确认（`test_an_agent_planned_session_plans_then_waits`）。
2. **只确认一次**：确认后的返工不再停下（`test_a_rework_after_confirmation_does_not_wait_again`）。
3. **默认不变**：未写 `admission` 时按平台默认 `auto` 立即执行（`test_without_admission_the_platform_default_applies`）。
4. **续作即确认**：待确认时续作，会话执行且记 `confirmed.via == "continue"`（`test_continuing_a_waiting_plan_confirms_it`）。
5. **权限**：只读用户不能确认（`test_only_those_who_may_execute_confirm`）。

---

### Task 1: 闸门与确认

**Files:** `octopus/domain.py`、`octopus/engine.py`、`octopus/config.py`、`octopus/server.py`（若需路由）、`octopus/schema.py`；Test: `tests/test_confirmation.py`

- [ ] 测试先行：Review Focus 1–5、`test_a_confirmed_plan_runs_to_completion`、`test_confirm_outside_the_wait_is_refused`、`test_admission_is_checked`。
- [ ] 实现。
- [ ] Commit `feat(admission): 会话可设确认后执行——规划后、第一个执行任务前停在「计划待确认」，确认或续作后执行（D44 §3.1–§3.2）`

### Task 2: MCP、Skill 与控制台

**Files:** `octopus/mcp.py`、`composition/skills/…`（Skill 文本）、`web/src/lib/labels.ts`、`web/src/features/sessions/SessionView.tsx`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`；
Test: `tests/test_mcp_confirm.py`（或并入现有 MCP 测试）、`web` 测试

- [ ] 测试先行：`octopus_confirm` 与 `octopus_start.admission`；控制台的标签、按钮与时间线。
- [ ] 实现、文档、全量测试、构建。
- [ ] Commit `feat(mcp,console),docs: 计划确认的 MCP 工具、Skill 与控制台按钮；运维与进度（D44 §3.2–§3.3）`
