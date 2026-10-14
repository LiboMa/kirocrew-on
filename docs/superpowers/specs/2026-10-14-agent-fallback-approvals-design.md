# 换工具与批准代理（D38，Agents）

## 1．目标

1. **换工具**：任务失败的原因在于**工具本身**时，会话不再停下等人改配置。
   - 失败原因包括：模型不可用或不支持、工具未安装或未登录、实际运行的后端不是所请求的、同一工具的原生错误在自动重试后再次发生。
   - 由 KiroCrew 的协调者（Kiro Agent）根据任务与执行环境**推荐**换用的工具与模型；平台只核对并记录。
   - 没有任何内置的回退链或对照表。
2. **批准代理（K04）**：KiroCrew 中 Agent 发出的权限批准请求（要运行的命令、要写的文件），在控制台与 MCP 中可见并可答复，答复留有审计。

成功标准：
- 一个任务请求的工具在执行环境中不可用时，会话自动进入诊断，协调者推荐另一个可用的工具，任务以它重跑并完成；会话页、MCP 状态与报告都写明「由协调者推荐从 X 换为 Y，原因 …」。
- 一个等待批准的会话在会话页显示「待批准」卡片（工具、命令、用途），创建者点「批准」后继续执行。

## 2．已确定（负责人，2026-10-14）

| 编号 | 决定 |
|---|---|
| A1 | 不使用内置的默认回退链：由 KiroCrew 的协调者推荐换用的工具，或由 Coding Agent 经 Skill 完成。平台核对、记录，不替用户或 Agent 选择工具。 |
| A2 | 换工具默认开启，必须记录且可见；会话（与工作流）可以关闭。 |
| A3 | 可以答复批准请求的人：会话创建者、Team owner、管理员（与发布相同），每次答复写审计。 |
| A4 | 先做第 1 期（换工具），再做第 2 期（批准代理）。 |
| A5 | 规划阶段把 OpenCode 换成 Claude 的内置对照（`planning.REASSIGN`）一并去掉：规划时 Kiro 看到各工具在执行环境中是否可用，自行选择。 |

## 3．第 1 期：由协调者推荐换工具

### 3.1 工具是否可用

Worker 的 `/health` 增加 `tools`：
```json
{"kiro": {"available": true}, "claude": {"available": true},
 "codex": {"available": false, "reason": "codex 命令不存在"}, "opencode": {"available": true}}
```
- `available`：该工具的命令在 Worker 的 `PATH` 中存在（`kiro-cli` / `claude` / `codex` / `opencode`）；Kiro 还要求登录有效（现有 `tool_auth`）。
- 平台连接或刷新 Worker 时随能力一起保存。
- 规划与诊断时传给 Kiro：
  - 规划提示中写明哪些工具可用；
  - 协调者上下文带上 `tools` 与已发布的模型。

### 3.2 何时交给协调者

工具层面的失败。工作本身有问题的失败（实现、测试、契约、门禁）照常诊断，不在此列。
| 情形 | 现状 | 改为 |
|---|---|---|
| 派发被拒：`model_unavailable` / `model_transport_unsupported` | 会话 `blocked`，等用户改配置 | 进入诊断（`failure_type: tool`） |
| 实际后端与请求不符（`identity_match` 失败） | `failure_type: model`，不诊断 | `failure_type: tool`，进入诊断 |
| 请求的工具在 `tools` 中不可用 | 派发后以原生错误失败 | 派发前就进入诊断，不派发 |
| 同一任务、同一工具，自动重试之后仍为 `native_error` | 诊断，但协调者只能在原工具上重试 | 诊断，可推荐换工具 |
- `auth`（Kiro 登录过期，需管理员）与 `native_stopped`（有人在原生页面停止）仍属于需要人处理的类型。

### 3.3 协调者的新决定 `switch_tool`

协调者的决定在 `retry` / `rework` / `wait_for_user` 之外增加：
```json
{"action": "switch_tool", "target_task_id": "code", "tool": "codex", "model": "auto", "effort": "",
 "reason": "claude 在此执行环境未安装；codex 可用且适合实现类任务", "instructions": "以 codex 重新执行本任务"}
```
- **协调者的提示**：去掉「不换模型」，改为：
  - 工具本身失败时可以推荐换工具；
  - 只从 `tools` 中可用的工具与已发布的模型中选择，写明理由；
  - 审查类任务应选择与被审查任务实际所用不同的工具；
  - 不确定时用 `wait_for_user`。
- **平台核对**（不通过时会话 `blocked` 并写明原因，与现有无效决定相同）：
  - `target_task_id` 是失败的任务；
  - `tool` 在 `TOOLS` 中、`available` 为真，且不是刚失败的工具；
  - `model` 为 `auto` / `default` 或已发布，`effort` 合法；
  - 审查任务的 `tool` 不是其被审查任务最终实际所用的后端；
  - 会话的 `recovery.switch_tools` 未关闭。
- **生效**：
  - 该任务的 `spec` 改为新的工具 / 模型 / effort，任务与其后的任务照常重置；
  - 新 Attempt 的证据写 `switched_from: {tool, model, effort, reason, by: "coordinator", recovery: <轮次>}`；
  - 修订历史记一次 `recovered`（现有机制），变更记录 `task.tool_switched`。
- 每轮诊断最多换一次；总次数受会话的 `max_cycles` 限制。

### 3.4 可见

- **控制台任务列表**：Attempt 的「请求」旁显示「由协调者从 claude 换为 codex：<原因>」。
- **MCP**：`octopus_status` 的任务 `latest` 带 `switched_from`。
- **Feature 报告**：任务一节写「换工具：claude → codex（协调者推荐）：<原因>」。
- **Skill**：会话因工具原因等待时（`attention` 写明），Coding Agent 可向用户建议换用可用的工具，经用户同意后用 `octopus_continue` 的 `execution.tool` 续作；不要自行决定。

### 3.5 关闭

- 会话：`recovery.switch_tools: false`（启动或续作时设置），协调者的上下文中写明不可换工具，`switch_tool` 被拒。
- 工作流：版本中的 `recovery` 默认值随之保存。

## 4．第 2 期：批准代理（K04）

### 4.1 来源

KiroCrew Gateway 的 `GET /api/approvals`（列出待批准：`id`、`source`、`tool`、`tool_input`、`tool_purpose`、`slot`、`ts`）与 `POST /api/approvals/{id}/{approve|reject|reject_once}`。
- 实施前先在 EC2 的 KiroCrew 版本上核实这两个接口与「运行中工具请求」是否出现在列表中（本机 0.8.0 只在派生批准时设置 `awaiting_approval`）；不一致时以实测为准修订本节。

### 4.2 接口

- **Worker**：
  - `POST /approvals {run_id}`：返回本会话（其父会话与子 Agent 的 slot）的待批准项；
  - `POST /approvals/answer {run_id, id, decision}`：核对该项属于本会话后转交 Gateway。
- **平台**：
  - `GET /v1/runs/<id>/approvals`（`read`）；
  - `POST /v1/runs/<id>/approvals/<aid> {decision: approve|reject}`（`execute`，人员见 A3）。
  - 答复写审计 `approval.answer`（会话、工具、命令摘要、决定、答复人）与变更记录。
- **Engine**：Attempt 为 `waiting_approval`，或巡检发现待批准项时，会话的 `attention` 为「等待批准：<工具> <用途>」，并给出操作。

### 4.3 界面

- **控制台**：会话页「待批准」卡片，列出工具、命令或文件（截断显示）、用途、剩余时间，提供「批准」与「拒绝」。批准前展开完整内容。
- **MCP**：`octopus_approvals {run_id}` 列出待批准项。是否提供 `octopus_approve` 由负责人在第 2 期开始前决定（见 §6）。

## 5．测试与验收

- **第 1 期**：
  - 本机：
    - `tools` 的检测；
    - 各情形进入诊断；
    - `switch_tool` 的核对（不可用工具、同一工具、审查独立、关闭）；
    - 生效后新 Attempt 用新工具、`identity_match` 按新工具判定；
    - 记录在控制台 / MCP / 报告中可见；
    - 规划不再替换 OpenCode。
  - EC2：在 `LiboMa/bedrock-claude-call` 上提交一个请求的工具在执行环境中不可用的任务（或其模型未发布），会话经协调者推荐换工具后完成，记录可见。
- **第 2 期**：
  - 本机：Worker 只返回本会话的待批准项；答复他人会话的项被拒；人员权限；审计。
  - EC2：一个需要批准的任务在控制台批准后继续。

## 6．待第 2 期开始前确认

- Coding Agent 是否可以经 MCP 答复批准（`octopus_approve`）：Coding 客户端在调用前会询问用户；或只列出、答复只在控制台。
- KiroCrew 批准接口在 EC2 版本上的实测结果（§4.1）。
