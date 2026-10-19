# 运行中补充说明与释放对话（D46 P5b）

## 1．背景

D46 §5 P5：中途纠正用 `steer`。P5a 已让返工续接原生对话；对话保留在 KiroCrew 的磁盘上，平台从不释放。

KiroCrew 0.10.0（2026-10-19 核对 `dashboard/messaging_api/run_control.py`）：
- `POST /api/spawn/{id}/steer {message, mode?}`：`mode = interrupt`（默认，插入正在运行的这一轮）或 `follow_up`（这一轮结束后作为续接送达）；
  不在运行 409 `not_running`、正在启动 503 `session_starting`、找不到 404。
- `POST /api/spawn/{conversation}/release`：结束一个可续接的对话，删除其会话映射与磁盘文件；有运行在途时 409，不存在 404。

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | 补充说明：会话中正在运行（`running` / `waiting_approval`）的任务，可由创建者、Team owner、管理员发一段补充说明（≤4000 字符），默认 `interrupt`，可选 `follow_up`（「本轮结束后再告诉它」）。平台经 Worker 调 KiroCrew `steer`，写审计与时间线（`task.steered`，含说明前 500 字符与方式）。已归档 Team 的会话不可。 |
| R2 | 拒绝如实转达：任务不在运行（409）、原生任务正在启动（503，稍后再试）、Worker 未升级（409「执行环境尚未支持」）。 |
| R3 | 入口：控制台运行中任务卡片「补充说明」（文本框 + 方式）；MCP `octopus_steer {run_id, task_id, message, mode?}`；API `POST /v1/runs/{id}/steer`。 |
| R4 | 释放对话：会话**删除**时 Worker 释放本会话的可续接对话（各续接链的第一个原生 ID），尽力而为（404 / 409 忽略），记在 lifecycle；关闭不释放（重新打开后仍可续接）。 |

## 3．测试与验收

- **本机**：Worker 的 steer 请求体、只接受本会话 Attempt、错误映射；平台的权限（他人 403、归档 Team）、只对运行中的任务、审计与时间线；MCP；控制台对话框；删除时的 release 与忽略的错误。
- **EC2**：一个运行中的任务收到补充说明并在交接中体现；删除一个续接过的会话后，KiroCrew 中该对话不再可续接。
