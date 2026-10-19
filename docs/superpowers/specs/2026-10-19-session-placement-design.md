# 会话的执行位置：Team 会话在提交者自己的边界执行（D49 第 2 期）

## 1．背景

负责人 2026-10-19 定的方向（D49）：每人一个 KiroCrew 边界，**Team 会话在提交者自己的边界执行，Team 只共享平台层**（会话、工作流、凭证、
审查与发布）。第 1 期做了边界本身（同主机多边界、端口、资源上限、升级）与个人会话的自动选择（R6）。负责人：「后面我们来看看，如何更合理的完成调度」。

EC2 核对（2026-10-19）：
- admin 的个人空间有 3 个可用环境（自己的 owner 边界、共享的 personal、共享的 w1）；当天 admin 的 4 个会话都由控制台提交、都在 personal 执行。
  服务端只在客户端不带 `worker_id` 时优先此人的边界（R6），而控制台向导在可用环境多于一个时预选或让人选，KiroCrew 建议默认取列表第一个。
- Team 会话只能在 Team 的环境执行：kirocrew-on 的 Team 会话 `run-7821620b…` 在共享的 personal 上，与方向不符。
- 共享的 personal 同时承载 admin、malibo2026 与一个 Team 的会话：`max_subagents`、kiro 额度与部署排空都是共用的——这正是 D49 要解决的瓶颈。
- 「某空间可用哪些环境」的规则有三份：服务端 `space_workers`、控制台 `spaceWorkers`、MCP `usable`。

## 2．已确定

| 编号 | 决定 |
|---|---|
| R1 | **可用的环境**（服务端 `space_workers` 唯一实现）：个人空间——此人的专属边界与服务此人的共享环境（不变）；Team 空间——Team 的环境（专属或服务该 Team 的共享）**加上提交者本人的专属边界**（须为该 Team 的成员，Team 未归档）。 |
| R2 | **默认选择**（不带 `worker_id`）：提交者恰好有一个启用的专属边界 → 它（`own`，个人与 Team 会话相同）；否则空间只有一个可用环境 → 它（`only`）；Team 会话且提交者没有自己的边界 → Team 的环境（`team`，附 R5 的提示；**负责人 2026-10-19 选 A：过渡期回退到 Team 的环境，不拒绝**）；否则停在等待选择（同现状）。Team 没有环境而提交者有边界时照常执行（Team 专属环境退为可选）；两者都没有时 409 `team_without_worker`，提示需要 Team 的环境或自己的边界。 |
| R3 | **会话固定在所选环境**（`runs.worker_id`，不变）：续作、返工、协调者诊断、重新规划都在那里执行；其他成员续作也留在创建者的边界（工作区与上下文一致）。是否仍「服务」按**会话创建者**判断：创建者离开该 Team、边界停用或退役后，下次派发前进入「等待执行环境」（`unserved`），提示由 Team owner 或管理员把会话分配到 Team 的环境；进行中的 Attempt 照常结束。 |
| R4 | **分配执行环境**（`assign`）的可选项：该会话空间对**创建者**可用的环境（Team 的环境、创建者的边界）；操作者自己的边界不在其中（现有按创建者校验，保持）。 |
| R5 | **记录与提示**：会话记 `placement`——`own`（本人的边界）、`team`（Team 的环境）、`only`（空间唯一的环境）、`requested`（客户端指定）；回退到 Team 环境时 `hint`：「你还没有自己的执行边界：本次在 Team 的环境执行，与其他成员共用并发与 kiro 额度；可请平台管理员为你建立（deploy.sh boundary add <名称> --owner <用户名>）」。创建的返回值、会话页与 MCP 精简状态都带上；时间线 `run.created` 记 `placement`。 |
| R6 | **客户端不再各自选择**：新接口 `GET /v1/placement?team_id=<id>`（省略为个人空间）返回 `{default: {id, name, scope, placement} 或 null, hint, options: [...]}`，即 R1、R2 的结果。控制台向导默认「自动：将在 X 执行」，不再预选；手动选择用 `options`；KiroCrew 建议（`/designs`）用默认选择。MCP `octopus_environments` 改用该接口并标出默认。控制台的 `spaceWorkers` 与 MCP 的 `usable` 不再自行实现规则。Skill：通常不传 `worker_id`。 |
| R7 | **原生入口与可见**：Team 成员照常看到会话的状态、报告、预览与下载（平台层，经会话的 Worker）；原生 KiroCrew 入口只开放给边界主人（即创建者）——原生登录覆盖整个 Gateway 与此人的 kiro 登录、记忆；其他成员与管理员在会话页「KiroCrew 会话」查看（现有规则，按操作者可用的环境判断，无须改动，加测试）。 |
| R8 | **知识与用量**：Team 会话在提交者边界上按 Team 空间的标识沉淀与注入（P4 的仓库标记已含空间散列，与此人的个人会话互不注入），存于此人的 Gateway：其他成员边界上的 Team 会话看不到——团队层知识按计划经仓库 PR（P4 验收后）。用量与 Credit 计入提交者的 kiro 账号。 |
| R9 | **兼容**：客户端仍可传 `worker_id`（须在 R1 的可用列表内，`placement: requested`）；已有会话不迁移；旧客户端不传时得到新的默认。 |

## 3．以后（第 3 期）

- 每个边界的公开原生入口（nginx server + CloudFront 分发）；目前新边界经 `deploy.sh tunnel --boundary`。
- 跨主机放置（`boundary add` 按主机容量选择主机，含 Worker 主机）、空闲停 Gateway。
- 退役共享 `personal`：其服务的用户逐个迁到自己的边界；按人的用量视图（D46 P6）。

## 4．测试与验收

- **本机**：R1 Team 空间含成员本人的边界（非成员、已归档 Team 不含）；R2 每个分支（own、only、team 回退与提示、等待选择、Team 无环境但有边界、两者都无）；
  R3 创建者离开 Team / 边界停用后 `unserved`，分配到 Team 环境后继续；R4 分配的可选项；R5 `placement` 与提示的记录与显示；R6 `/v1/placement`
  与控制台向导默认自动、KiroCrew 建议用默认、MCP 标出默认；R7 其他成员看得到报告与预览、打不开原生入口。
- **EC2**：admin（有 owner 边界）向一个 Team 提交 → 在 owner 边界执行并完成（`placement: own`）；没有边界的成员向同一 Team 提交 → 在 Team 的环境执行，
  会话页显示提示；另一成员能看报告与预览、原生入口被拒；控制台向导显示「自动：将在 owner 执行」。
