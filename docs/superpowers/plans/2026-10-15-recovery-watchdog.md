# 恢复阶梯第 1 期：看门狗（D39） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 原生任务长时间没有进展（或总时长超限）时，平台停止它、自动重试一次，仍卡住则交给协调者诊断；原因在控制台、MCP 与报告中可见。

**Architecture:** Worker 的 `/inspect` 报告 `state.json` 中的进展，新接口 `/attempt/stop` 以 `DELETE /api/spawn/{id}` 停止单个原生任务；Engine 在推进运行中的 Attempt 时按会话的限时判定，停止成功后当场把 Attempt 判为 `blocked` 并创建自动重试或交给诊断。

**Tech Stack:** Python 3 标准库、React + TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-15-recovery-watchdog-design.md`

## Global Constraints

- 限时：`recovery.idle_minutes` 默认 30（5–240），`recovery.attempt_minutes` 默认 240（30–720）；默认值不写入 `recovery`。
- 原因文案：「原生任务 N 分钟没有进展（最后工具：X），看门狗已停止」「原生任务运行超过 N 分钟，看门狗已停止」。
- 停止失败（非 404）下次推进再试；404 → 证据 `watchdog: {"unsupported": true}`，不再尝试，不改变 Attempt。
- 等待批准、检查命令（`checks` / `baseline` 运行中）期间不判定。
- 自动重试的证据：`{"transient_retry_of": <id>, "transient": "stalled"}`（与 D34 相同的标记，保留 `switched_from`）。
- `recovery.enabled: false`：停止并 `blocked`，不重试、不诊断。

## Review Focus

1. **双 Agent**：停止未成功时绝不创建重试。（`test_an_old_worker_is_never_doubled`）
2. **长命令**：`updated_at` 只在每轮更新；空闲 29 分钟的任务不被停止。（`test_a_quiet_task_inside_the_limit_runs_on`）
3. **等待批准与检查命令期间**不判定。（`test_waiting_for_approval_or_checks_is_not_stalled`）
4. **重试的重试**：自动重试再次卡住时进入诊断而不是第三次运行。（`test_a_retry_that_stalls_again_goes_to_diagnosis`）
5. **时钟**：`state.json` 的 `updated_at` 是 Unix 秒；Attempt 的 `created` 也是 Unix 秒；Engine 用可注入的 `wall` 时钟。

---

### Task 1: Worker 的进展与单个任务的停止

**Files:** `octopus/worker.py`；Test: `tests/test_watchdog_worker.py`

- [ ] **Step 1: 写失败的测试**——用 `tests/test_worker_approval.py` 的方式构造 Worker 与 `node.native` 替身：
  - 派发一个 Attempt（替身对 `POST /api/spawn` 返回 `{"agents": [{"id": "abc123de"}]}` 或现有派发测试所需的回答），
    在 `crew_home/subagents/abc123de/state.json` 写入 `{"id": "abc123de", "parent_session": <parent>, "status": "running",
    "updated_at": 1000.0, "turns": 7, "last_tool": "npm test"}`；
  - `inspect` 未结束时返回 `progress == {"updated_at": 1000.0, "turns": 7, "last_tool": "npm test", "status": "running"}`；
  - `attempt_stop` 调用 `("/api/spawn/abc123de", None, "DELETE")`，返回 `{"stopped": True}`；Gateway 404 时同样返回 `{"stopped": True}`；
  - `health()["attempt_stop"]` 为真。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`$PY -m unittest tests.test_watchdog_worker tests.test_worker_approval tests.test_runtime`）。
  - `inspect`：`state` 已读取处，若 `not native.get("done")`，返回值加
    `"progress": {"updated_at": state.get("updated_at"), "turns": state.get("turns"), "last_tool": str(state.get("last_tool") or "")[:200], "status": state.get("status")}`（`state` 为空时不加）；
  - `attempt_stop(body)`：`run, parent = self.binding(...)`，读 `dispatch.json` 的 `native_id`（缺失 → `{"stopped": True}`），
    `self.native("/api/spawn/" + native_id, None, "DELETE")`，`GatewayError` 404 视为成功，其余抛出；路由 `/attempt/stop`。
- [ ] **Step 5: Commit** `feat(worker): 运行中 Attempt 的进展（state.json）与停止单个原生任务（D39 §3.1）`

### Task 2: 限时设置与 Engine 的看门狗

**Files:** `octopus/recovery.py`（`policy` 接受 `idle_minutes`、`attempt_minutes`）、`octopus/engine.py`（`wall` 时钟、`_watchdog`）、`octopus/mcp.py` 与 `octopus/schema.py`（`RECOVERY` / `RecoveryPolicy`）；Test: `tests/test_watchdog.py`

- [ ] **Step 1: 写失败的测试**（`tests/test_switch_tool.py` 的 `SwitchCase` 夹具方式：Service + Engine + 替身 `request`，Engine 以 `Engine(db, request, wall=lambda: self.now)` 创建）：
  - `test_the_policy_keeps_its_shape_and_checks_the_limits`：默认形状不变；`{"idle_minutes": 10}` 保存；`idle_minutes: 3`、`attempt_minutes: 1000` 被拒；
  - `test_a_stalled_task_is_stopped_and_retried_once`：替身 `/inspect` 返回未结束 + `progress.updated_at = now − 31 min`；一次 `tick` 后：
    `/attempt/stop` 被调用，第 1 次 Attempt `blocked`，`error` 含「31 分钟没有进展」与最后工具，证据 `watchdog.reason`，
    任务的当前 Attempt 是新建的自动重试（`transient: "stalled"`），`recovery.used == 0`；
  - `test_a_retry_that_stalls_again_goes_to_diagnosis`：重试也卡住 → 第 2 次 Attempt `blocked`，会话进入 `diagnosing`（诊断被安排）；
  - `test_a_quiet_task_inside_the_limit_runs_on`：`updated_at = now − 29 min` → 不停止；
  - `test_a_task_running_too_long_is_stopped`：进展新鲜，但 Attempt 创建于 `now − 241 min` → 停止，原因含「运行超过 240 分钟」；
  - `test_an_old_worker_is_never_doubled`：`/attempt/stop` 回答 404 → Attempt 仍 `running`，证据 `watchdog.unsupported`，没有新 Attempt；再次 `tick` 不再调用 `/attempt/stop`；
  - `test_waiting_for_approval_or_checks_is_not_stalled`：`native.awaiting_approval` 为真，或 Attempt 证据中检查命令正在运行 → 不停止；
  - `test_without_recovery_it_only_stops`：`recovery.enabled: false` → 停止并 `blocked`，没有重试，会话 `blocked`；
  - `test_the_session_settings_set_the_limits`：`recovery.idle_minutes: 5` 的会话在 6 分钟时停止。
- [ ] **Step 2: 运行，确认失败**。
- [ ] **Step 3: 实现**
  - `policy`：键集合加 `idle_minutes`、`attempt_minutes`；整数且在范围内；与默认值不同时才写入返回值。
  - `Engine.__init__(..., wall=time.time)`，`self.wall = wall`。
  - `advance` 中，`native` 未结束（`not native.get("done")`）、不是等待批准时，在写回运行状态之前调用
    `if self._watchdog(run, config, task, attempt, result, snapshot): return`。
  - `_watchdog`：
    - 证据中已有 `watchdog.unsupported` → `False`；
    - 限时取 `recovery.policy(snapshot.get("recovery"), legacy=True)` 中的值或默认；
    - 计算原因（空闲 / 总时长）；没有原因 → `False`；
    - `/attempt/stop`：`Error` 404 → 写证据 `watchdog: {"unsupported": True}`（Attempt 状态不变）并返回 `True`；其他 `Error` → 返回 `True`（下次再试）；
    - 成功 → 在一个事务中：Attempt `blocked`（`error`、证据 `watchdog`、`failure_type: native_error`），
      然后按「已是自动重试 / 自动纠错关闭」分支：创建重试（同 D34 的瞬时重试，`transient: "stalled"`）或不创建；
      事务后 `update` 会话状态，需要诊断时调用 `recovery.automatic`。
  - `mcp.RECOVERY` 与 `schema.RecoveryPolicy` 加两个整数字段。
- [ ] **Step 4: 运行，确认通过**：`$PY -m unittest tests.test_watchdog tests.test_switch_tool tests.test_recovery tests.test_engine_gates tests.test_runtime tests.test_mvp`。
- [ ] **Step 5: Commit** `feat(engine): 看门狗——原生任务没有进展或运行超时时停止、自动重试一次，仍卡住交给诊断（D39 §3.2）`

### Task 3: 可见与文档

**Files:** `octopus/mcp.py`（`latest.watchdog`）、`octopus/report.py`（任务一节「看门狗：…」）、`web/src/features/sessions/TaskList.tsx`（「看门狗」一行）、`web/src/api/types.ts`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`；Test: `tests/test_mcp_status.py`、`tests/test_report.py`、`web/src/features/sessions/SessionView.test.tsx`

- [ ] 测试先行（MCP `latest.watchdog` 等于证据；报告含「看门狗：原生任务 31 分钟没有进展」；控制台显示该行），再实现。
- [ ] OPERATIONS：「看门狗（D39）」一节——限时、行动、旧 Worker、关闭自动纠错时的行为、长命令时调大 `idle_minutes`。
- [ ] MVP-RELEASE：§14.2 第 6 项与新的 §14.15。
- [ ] 全量测试与控制台测试、构建。
- [ ] Commit `feat(console,mcp,report),docs: 看门狗的记录可见；运维与进度（D39）`

## EC2 验收

见 spec §4：任务 `sleep 900`、会话 `idle_minutes: 5`：约 5 分钟后停止并自动重试；第二次卡住时进入诊断。
