# M4-4 第 1 期：排队可见、看门狗不计排队 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 被 KiroCrew 推迟的子 Agent 在会话中显示为排队（原因的类别），不再报「归属不可核实」；看门狗不计排队时间；`never started` 自动重派一次。

**Architecture:** Worker 的 `inspect` 在列表与状态文件都找不到任务时改问 `GET /api/spawn/{id}`，排队的回 `queued`；引擎把它记进 Attempt 证据（`queued.since/until`）与会话的 error，看门狗从排队结束起计时；`engine.transient` 认 `never started`。

**Tech Stack:** Python 3.12 标准库。

**Spec:** `docs/superpowers/specs/2026-10-22-m4-queue-visible-design.md`（D60）

## Global Constraints

- 使用者看到的文案只写原因的类别（内存不足 / 负载过高 / 暂停接收新任务 / 资源不足），不写内存数字；`detail` 只进事件。
- 归属：排队中的任务以任务文字中的 `[OCTOPUS-ATTEMPT:<attempt>]` 证明属于本 Attempt，否则照旧 `native_unavailable`。
- 不改其他门禁与纠错的语义（D57 由另一会话在做）。

## Review Focus

- **返工续接回执写 `spawned` 但实际排队**：`inspect` 仍判为排队（Task 1 测试）。
- **排队 200 分钟**：看门狗不停止；排队结束后从那一刻起计时（Task 2 测试）。
- **别人的排队任务**（任务文字没有本 Attempt 标记）：不被当成本 Attempt（Task 1 测试）。

---

### Task 1: Worker 认出排队

**Files:** Modify `octopus/worker.py`（`inspect`、`_queued`）；Test `tests/test_queue_visible.py`

- [ ] 测试：列表与状态文件都没有、`GET /api/spawn/{id}` 回 `queued, low_memory` 且任务文字含标记 → `{"native": {"done": False, "queued": True}, "queued": {"reason": "low_memory", "detail", "elapsed", "resuming"}}`；任务文字不含标记 → 409 `native_unavailable`；`GET` 回非排队的（运行中）→ 照旧路径。
- [ ] 失败；实现；通过（含 `tests.test_watchdog_worker tests.test_continue_worker`）；提交。

### Task 2: 引擎记录排队、看门狗不计排队、never started 自动重派

**Files:** Modify `octopus/engine.py`（排队标记与 error、看门狗起点、`transient`）；Test `tests/test_queue_visible.py`

- [ ] 测试：排队时 Attempt 证据 `queued.since`、事件 `attempt.queued`、会话 error「排队：等待执行环境资源（执行环境内存不足）」；排队结束时 `queued.until`、事件 `attempt.dequeued`、error 清空；排队 200 分钟看门狗不动作，排队结束后 30 分钟无进展才动作；`native.error` 以 `never started` 开头时自动重派一次（`attempt.retry`，transient `never_started`）。
- [ ] 失败；实现；通过（含 `tests.test_watchdog tests.test_engine` 等相关模块）；提交。

### Task 3: 控制台文案、文档、回归、部署

- [ ] `web/src/lib/labels.ts` 事件 `attempt.queued` / `attempt.dequeued`；OPERATIONS、MVP-RELEASE、progress（m4-quota 第 1 期）；全量回归；部署（先通知 D57 会话）；EC2 核对 Worker 新版本运行、无回归（制造排队须负责人同意）。
