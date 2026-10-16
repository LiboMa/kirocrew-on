# 准入第 1 期：计划确认（D44，M2）

## 1．目标

M2 交付 5「准入：确认模式与按仓库范围显式授权的自动准入，留存策略快照」，验收 1「提交需求后返回自动生成的 Pipeline、映射与门禁」，
M3 的 MCP「计划确认」。现状：会话提交后立即执行；KiroCrew 规划的会话在规划任务完成后直接派发其规划的任务，用户在执行前看不到、也不能确认计划。

本期：会话可以要求**确认后执行**——规划（若有）照常完成，第一个执行任务派发之前会话停在「计划待确认」，用户在控制台或经 MCP 核对任务、
工具与模型（含映射来源）、契约与门禁后确认开始，或修改后续作、或关闭。第 2 期（另行设计）：按仓库范围的自动准入与策略快照。

## 2．已确定（负责人方向：开发质量与 Agent Harness 优先，非必要不确认）

| 编号 | 决定 |
|---|---|
| A1 | 每个会话的 `admission`：`"confirm"`（确认后执行）或 `"auto"`（立即执行）；未写时取平台配置 `default_admission`，**默认 `auto`**——不改变现有客户端（Skill、MCP、控制台向导）的行为；负责人可把平台默认改为 `confirm`。 |
| A2 | 只确认一次：确认之后的返工、重试、换工具、重新规划不再停下（它们由协调者与预算约束）。 |
| A3 | 待确认的会话可以续作（修改任务后续作即视为确认）、停止、关闭；确认与续作写变更记录与操作者。 |
| A4 | MCP `octopus_confirm`：客户端须先向用户展示计划并得到同意再调用（与 `octopus_approve` 相同）。 |

## 3．设计

### 3.1 状态与闸门

- 新状态 `awaiting_confirmation`（「计划待确认」），属于已停下的状态（与 `blocked` 等相同，可续作 / 停止 / 关闭；Engine 不推进）。
- Engine 在为一个**非规划**任务创建 Attempt 之前：快照 `admission == "confirm"` 且没有 `confirmed` → 会话转为 `awaiting_confirmation`，
  记变更 `run.awaiting_confirmation`（「计划待确认」），不创建 Attempt。规划任务（`plan`、`replan-*`）不受闸门限制。
- `POST /v1/runs/<id>/confirm`（`expected_version`、`request_id`，与其他命令相同的幂等与版本核对）：只在 `awaiting_confirmation` 时可用，
  快照记 `confirmed = {by, at}`，会话 `ready`；记变更 `command.confirm`。
- 续作（continue）一个待确认的会话：照常重置任务，并记 `confirmed`（`via: "continue"`）。

### 3.2 设置与入口

- 启动 `POST /v1/runs` 的 `admission`（`confirm` / `auto`）；写入快照；平台配置 `default_admission`（`auto` / `confirm`，默认 `auto`）。
- MCP `octopus_start` 增加 `admission`；新工具 `octopus_confirm`（`run_id`）。OpenAPI 同步。
- Skill：说明确认模式——提交后展示计划（任务、工具与来源、验收、检查命令），用户同意后调用 `octopus_confirm`。

### 3.3 可见

- 会话 `attention`：「计划待确认」——「核对任务、工具与检查命令后确认开始；也可修改后续作或关闭」。
- 控制台：状态标签「计划待确认」；需要处理的卡片带「确认并开始」按钮（有执行权限的人可用）；时间线「计划待确认」「已确认计划」。
- MCP 精简状态照常（`status`、`attention`、任务的工具 / 模型 / 来源）；报告显示状态与确认者。

## 4．测试与验收

- **本机**：确认模式下显式任务的会话停在待确认、不派发；确认后执行完成；KiroCrew 规划的会话先完成规划、再停下；确认之后的返工不再停下；
  续作视为确认；`auto` 与未写时行为不变（平台默认）；非待确认时确认得 409；他人无权确认；MCP 工具与控制台按钮。
- **EC2**：一个 `admission: "confirm"` 的 KiroCrew 规划会话：规划完成后停在「计划待确认」并列出规划的任务；经 API 确认后执行完成。
