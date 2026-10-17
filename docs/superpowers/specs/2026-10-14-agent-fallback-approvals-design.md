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
| A6 | Coding Agent 可以经 MCP 答复批准（`octopus_approve`）：调用前向用户展示内容并得到同意（负责人，2026-10-14）。 |

## 3．第 1 期：由协调者推荐换工具

### 3.1 工具是否可用

Worker 的 `/health` 增加 `tools`：
```json
{"kiro": {"available": true}, "claude": {"available": true},
 "codex": {"available": false, "reason": "codex 命令不存在"}, "opencode": {"available": true}}
```
- `available`：该工具的命令可以找到：
  - 先找 Worker 配置 `[tools]` 中的路径（`kiro_cli` / `claude` / `codex` / `opencode`）；
  - 再在 Gateway 使用的搜索路径中找（`adapters_dir`、`path_prepend`、`PATH`）。
- Kiro 另报 `login`（`ok` / `expired` / `unknown`，即现有 `tool_auth`）。登录过期仍按现状等待管理员处理（`auth`），不换工具。
- 平台登记或刷新 Worker 时，随能力一起保存（`capabilities.tools`）。
- 规划与诊断时传给 Kiro：
  - 规划提示中写明各工具是否可用，以及 Worker 的已知限制（`limitations`）；
  - 协调者上下文的 `environment`：`tools`、已发布的模型、`limitations`、是否允许换工具。它随诊断记录保存，平台按同一份核对。

### 3.2 何时交给协调者

工具层面的失败。工作本身有问题的失败（实现、测试、契约、门禁）照常诊断，不在此列。
| 情形 | 现状 | 改为 |
|---|---|---|
| 派发被拒：`model_unavailable` / `model_transport_unsupported` | 会话 `blocked`，等用户改配置 | 进入诊断（`failure_type: tool`） |
| 实际后端与请求不符（`identity_match` 失败） | `failure_type: model`，不诊断 | `failure_type: tool`，进入诊断 |
| 请求的工具在平台保存的 `tools` 中不可用 | 派发后以原生错误失败 | 平台派发前就进入诊断，不派发 |
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
  - `tool` 在 `TOOLS` 中，且在 `environment.tools` 中 `available` 为真；
  - 工具、模型与 Effort 不能与失败的那次完全相同（模型未发布时，同一工具改用 `auto` 是有效的推荐）；
  - `model` 为 `auto` / `default` 或在 `environment.models` 中已发布，`effort` 合法；
  - 审查任务的 `tool` 不是被审查代码的实际后端（会话分支当前候选所属的 Attempt；没有候选时取最近完成的上游实现任务）；
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

- 会话：`recovery.switch_tools: false`（启动或续作时设置；控制台的自动纠错设置中有对应选项）。
  - 协调者的上下文写明不可换工具，`switch_tool` 被拒。
  - 默认开启；关闭时才在 `recovery` 中保存 `switch_tools: false`，已有会话的设置形状不变。

## 4．第 2 期：批准代理（K04）

### 4.1 来源

KiroCrew Gateway 的 `GET /api/approvals`（列出待批准：`id`、`source`、`tool`、`tool_input`、`tool_purpose`、`slot`、`ts`）与 `POST /api/approvals/{id}/{approve|reject|reject_once}`。
- EC2 实测（2026-10-14，KiroCrew `57df464…`，`dashboard/handlers/sessions.py`）：
  - `GET /api/approvals` 只列出**状态级**批准（cron、autonudge、subagent、taskrunner），即 `state._pending_approvals`；当时列表为空。
  - 会话（slot）内的工具批准在各 slot 的 `_approval_futures` 中，由 `POST /api/chat/slots/{slot}/approve` 答复，不在上面的列表里。
  - 按 id 答复（`POST /api/approvals/{id}/{action}`）时，要么带上列表记录的 `origin=coordinator&slot=…&instance=…`，
    要么必须是 Dashboard owner 的浏览器会话（`bare_approval_decide_allowed`）。
  - 第 2 期据此设计：Worker 同时读两处（状态级列表中属于本会话 slot 的项；本会话父 slot 与子 Agent 的 slot 内待批准项），
    答复时带上记录的目标（`origin` / `slot` / `instance`）或走 slot 路由。实施前在一个真实的待批准请求上再核对一次记录的字段。

### 4.2 接口

- **两类待批准项**（EC2 实测的字段）：
  - `native`：本会话父 slot 的会话内工具请求，来自 `GET /api/chat/slots` 中该 slot 的 `pending_approval_info`
    （`tool`、`tool_input`、`tool_kind`、`tool_purpose`、`request_id`、`request_mid`）；
  - `coordinator`：`GET /api/approvals` 中 `slot` 为本会话父 slot 的状态级记录（`id`、`instance`、`source`、`tool`、`tool_input`、
    `tool_purpose`、`ts`）。`pending_approval_info` 为 `coordinator` 时与其中一条相同，不重复列出。
- **Worker**：
  - `POST /approvals {run_id}`：只返回本会话父 slot 的待批准项，每项带 `origin`、`request_id`，以及 `request_mid`（native）或 `instance`（coordinator）；
  - `POST /approvals/answer {run_id, origin, request_id, request_mid | instance, decision: approve | reject}`：
    先重新列出本会话的待批准项，所答复的一项必须在其中（标识完全一致），否则 404 `approval_not_pending`；
    - native：`POST /api/chat/slots/{slot}/approve {"origin":"native","request_id","request_mid","action":"approved|rejected"}`；
    - coordinator：`POST /api/approvals/{id}/{approve|reject}?origin=coordinator&slot=<slot>&instance=<instance>`。
- **平台**：
  - `GET /v1/runs/<id>/approvals`（`read`）：Worker 的列表；Worker 版本不支持时返回空列表与 `supported: false`；
  - `POST /v1/runs/<id>/approvals/answer {origin, request_id, request_mid | instance, decision}`（`execute`，人员见 A3）；
  - 答复写审计 `approval.answer`（会话、来源、工具、命令摘要、决定、答复人）与变更记录 `approval.answered`。
- **Engine / 会话视图**：会话为 `waiting_approval` 时，`attention` 为「等待批准」，操作为在会话页或经 MCP 查看并答复。

### 4.3 界面

- **控制台**：会话打开且未结束时，会话页每 10 秒读取待批准项；有待批准项时显示「待批准」卡片：
  - 列出来源、工具、命令或文件（截断显示，可展开完整内容）、用途；
  - 「批准」与「拒绝」，批准前展开完整内容并确认。
- **MCP**：
  - `octopus_approvals {run_id}` 列出待批准项；
  - `octopus_approve {run_id, origin, request_id, request_mid | instance, decision}`（A6）。工具说明与 Skill 要求：调用前向用户展示工具、命令或文件与用途，
    得到用户的明确同意；不替用户决定。

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
  - EC2：在一个会话的原生父会话中触发一次工具请求（K20 的情形：全局批准为 interactive），控制台出现「待批准」卡片，
    批准后该请求被执行；另一次请求被拒绝，原生侧收到拒绝；MCP 列出并答复一项。

## 6．已确认

- `octopus_approve`：提供（A6）。
- KiroCrew 批准接口：EC2 实测见 §4.1、§4.2。
