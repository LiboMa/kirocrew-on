# 保留上下文的返工：返工在原生会话上续接（D46 P5a）

## 1．背景

D46 §5 P5：返工 / 纠错的 Attempt 用 KiroCrew 的 `POST /api/spawn/{id}/continue`，让返工的 Agent 能引用上一轮自己的工作；中途纠正用 `steer`。
今天每个 Attempt 都是一次全新的 spawn：返工的 Agent 只看到 instructions.md 里的失败摘要，要重新读代码、重新理解上一轮做了什么。

KiroCrew 0.10.0（`dashboard/messaging_api/spawn.py:397`，2026-10-19 核对）：
- `POST /api/spawn/{conversation}/continue {task, parent_session, agent?, model?, max_turns?}`：在该对话（第一次 `keep: true` 运行的 ID）的同一原生会话上
  新开一轮（session/load），沿用其累计上下文与原 cwd；返回 `{id: 新一轮的 ID, conversation, status: "spawned"}`。
- 拒绝：`conversation_busy` 409（上一轮仍在运行）、不可续接 409、`conversation_gone` 404、容量已满 429、其他 400。
- 不接受 `reasoning_effort`、`approval_mode`、`include_*`：续接沿用原会话的设定。
- 平台的派发都是 `keep: true`，第一次派发的原生 ID 即对话 ID。

## 2．已确定（第 1 期：续接；`steer` 为 P5b）

| 编号 | 决定 |
|---|---|
| R1 | 适用：执行契约 ≥4、Worker 能力 `continue`。 |
| R2 | 何时续接：同一任务的新 Attempt 是**平台安排的返工**——协调者安排修复该任务（`repair` 指向它，非复验），或上游返工后的审查 / 测试复验（`repair.recheck` / `repair.rerun`）——且上一个 Attempt 有原生 ID、工具 / 模型 / Effort 与本次相同、没有换工具（`switched_from`）。其余（第一次派发、用户续作改了任务、换工具、改了 Effort、瞬时失败的自动重试）照旧全新派发。 |
| R3 | 对话：续接的对话 ID 是该任务这一串续接中第一个 Attempt 的原生 ID；每个续接的 Attempt 在证据中记 `continued: {conversation, from: 上一个 Attempt}`；新一轮的 ID 即其原生 ID（检查、停止、对账照旧）。 |
| R4 | 消息：标记 `[OCTOPUS-ATTEMPT:<id>]` 与「完整阅读 instructions.md」不变，另加一句「这是同一任务的返工：你上一轮的工作与对话都在，先核对本轮要求，在已有改动上修复，不要从头重写」。instructions.md 照常写（含本轮修复要求）。 |
| R5 | 回退：续接被拒（404 / 409 / 400）时改为全新派发，证据记 `continue_refused: <code>`，时间线说明；429 与全新派发的容量处理相同（稍后重试）。Worker 只续接本会话中某个 Attempt 的原生 ID（否则拒绝），防止串到别的会话。 |
| R6 | 可见：任务卡片的 Attempt 显示「续接上一轮（保留上下文）」或「续接被拒，已重新派发：原因」；MCP 精简状态 `latest.continued`；报告的 Attempt 行注明。 |

## 3．P5b（另行排期）

- `steer`：运行中的任务补充说明（控制台与 MCP，创建者 / Team owner / 管理员，写审计）；看门狗在停止前先 `follow_up` 提醒一次。
- 会话关闭 / 删除时 `POST /api/spawn/{id}/release` 释放可续接的对话（删除磁盘上的会话文件）。

## 4．测试与验收

- **本机**：R2 的每个条件（返工续接；首次 / 用户续作 / 换工具 / 改 Effort / 瞬时重试不续接）；续接的请求体（契约测试的 `POST /api/spawn/{}/continue` 键）；
  对话 ID 取这一串的第一个；Worker 拒绝别的会话的对话 ID；被拒回退与证据；429；控制台 / MCP / 报告显示。
- **EC2**：一个实现任务被审查要求修改 → 返工的 Attempt 续接原对话（原生会话中可见上一轮），在原改动上修复并通过；续接后的检查、停止照常。
