# 恢复阶梯第 1 期：看门狗（D39，M2）

## 1．目标

M2 的验收要求故障注入在预算内自动恢复，其中「Attempt 卡住」目前没有处理：原生任务长时间没有进展时，会话一直停在 `running`。

本期：
- Worker 报告运行中 Attempt 的进展：最后一次活动的时间、轮数、最后一个工具；
- 平台在没有进展超过限时（或总时长超过上限）时停止该原生任务，自动重试一次；仍卡住则交给协调者诊断（可推荐换工具，D38）；
- 原因写入 Attempt，控制台、MCP 与报告可见。

第 2 期（另行设计）：协调者的 `replan` 决定（重新规划失败任务及其后的任务）。

## 2．已确定（负责人方向：开发质量与 Agent Harness 优先，非必要不确认）

| 编号 | 决定 |
|---|---|
| W1 | 进展信号取 KiroCrew 子任务的 `state.json`：`updated_at`、`turns`、`last_tool`、`status`（EC2 实测字段）。`/api/spawn` 的行只有 `started`。 |
| W2 | 停止单个原生任务用 `DELETE /api/spawn/{id}`（KiroCrew 的「用户停止」）。 |
| W3 | 看门狗停止的 Attempt 由平台当场判定，不等原生报告：否则会按 `native_stopped`（等待人处理）判定，不会重试。 |
| W4 | Worker 不支持停止单个任务（旧版本，404）时不采取行动：在旧任务可能仍在写工作区时开始重试，会让两个 Agent 同时修改同一工作区。 |

## 3．设计

### 3.1 Worker

- `/inspect` 在原生任务未结束时带 `progress: {updated_at, turns, last_tool, status}`（来自 `state.json`；`last_tool` 截断为 200 字符；文件不存在时不带）。
- 新接口 `POST /attempt/stop {run_id, attempt_id}`：按该 Attempt 的 `dispatch.json` 中的 `native_id` 调用 `DELETE /api/spawn/{id}`；Gateway 回答 404（已结束或已清理）视为已停止；返回 `{"stopped": true}`。
- `/health` 带 `attempt_stop: true`。

### 3.2 平台

- **限时**（会话的 `recovery` 设置，默认值不保存，与 `switch_tools` 相同）：
  - `idle_minutes`：没有进展的上限，默认 30，范围 5–240；
  - `attempt_minutes`：一次 Attempt 的总时长上限，默认 240，范围 30–720。
  - 长时间运行的单个命令（大型构建、完整测试）期间 `updated_at` 不更新，因此空闲上限不宜过短；用户可以按会话调大。
- **判定**（Engine 推进运行中的 Attempt 时）：
  - 最后进展 = `max(progress.updated_at, Attempt 创建时间)`；
  - `now − 最后进展 > idle_minutes` → 「原生任务 N 分钟没有进展（最后工具：…）」；
  - `now − Attempt 创建时间 > attempt_minutes` → 「原生任务运行超过 N 分钟」；
  - 等待批准（`waiting_approval`）与检查命令运行期间不判定。
- **行动**：
  1. 调用 Worker `/attempt/stop`；404（旧 Worker）→ 不行动，只在 Attempt 证据中记一次 `watchdog: {unsupported: true}`，之后不再尝试；其他错误 → 下次推进再试；
  2. Attempt `blocked`，`error` 为上面的原因，证据 `watchdog: {reason, idle_minutes | attempt_minutes, last_progress, turns, last_tool, stopped_at}`、`failure_type: native_error`；
  3. 该 Attempt 不是自动重试（无 `transient_retry_of`）→ 创建自动重试（证据 `transient_retry_of` 与 `transient: "stalled"`），与 D34 的瞬时重试相同，不消耗纠错轮次；
  4. 已是自动重试 → 交给协调者诊断（`recovery.automatic`）。协调者看到 `native_error` 与 `transient_retry_of`，按 D38 可推荐换工具。
- **可见**：
  - Attempt 的 `error` 写明原因；控制台任务列表显示「看门狗：…」一行；
  - MCP `latest` 带 `watchdog`；
  - 报告的任务一节写「看门狗：…」；
  - 会话的 `attention` 在交给诊断后照常显示。

### 3.3 关闭

- `recovery.enabled: false` 的会话：看门狗仍停止卡住的任务并标为 `blocked`（不创建重试、不诊断），会话等待用户处理。
  停住一个不再进展的任务本身不消耗任何纠错预算。

## 4．测试与验收

- **本机**：
  - Worker：`progress` 来自 `state.json`；`/attempt/stop` 调用 `DELETE`，404 视为已停止；
  - Engine：
    - 空闲超时 → 停止、`blocked`、自动重试；重试再超时 → 诊断；
    - 总时长超时；
    - 旧 Worker 不行动；
    - 等待批准与检查命令期间不判定；
    - 自动纠错关闭时只停止不重试；
    - 限时设置的校验与默认值不保存。
- **EC2**：一个会话的任务运行 `sleep 900`，会话设 `idle_minutes: 5`：约 5 分钟后看门狗停止并自动重试；
  任务第二次仍卡住时进入诊断（协调者给出决定）。
