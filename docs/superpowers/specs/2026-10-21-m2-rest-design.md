# M2 剩余：Request、按仓库自动准入、个人 / Team 映射偏好（D55，设计稿）

日期：2026-10-21。负责人：「按我们 Milestone 的规划，继续执行」。范围是看板上的 M2-1、M2-4、M2-5。

## 1. 现状与差距

| 设计（2026-10-10 §3、§5、§7） | 现在 | 差距 |
|---|---|---|
| `requests` 是一等对象：先登记 Request，规划后确认，准入后才建 Session | 提交即建 Session；规划在会话内的 plan 任务完成，`admission: confirm` 停在「计划待确认」（D44） | 流程已由「会话 + 计划确认」承担。缺的是 Request 记录：来源客户端、显式指定、策略快照，以及与 Session 的关联 |
| `policies` 表：映射、准入、恢复预算，按个人 / Team 作用域 | 准入只有平台配置 `default_admission`（EC2 为 `auto`）和每次提交带的 `admission`；没有映射偏好 | 缺按仓库范围的准入规则，以及个人 / Team 的映射偏好 |
| 映射优先级：显式指定 → Workflow 阶段 → 个人 / Team 策略 → 平台默认 | 显式指定（D45）、Workflow / 模板、KiroCrew 规划、协调者换工具（D38），各自记录映射来源 | 缺「个人 / Team 偏好」这一层。负责人的规则是平台不内置默认链，由 KiroCrew Agent 推荐（no-hardcoded-chains） |

M2 验收第 1 条是：「在 Claude Code 中用自然语言提交需求，后端登记 Request 并返回自动生成的 Pipeline、映射与门禁」。
除了 Request 记录之外，其余现在都能做到：提交时带 `admission: confirm`，会话就停在「计划待确认」，列出任务、工具、映射来源与检查。

## 2. Request（M2-1）

**原则**：不改变现在的提交方式。每次提交（`POST /v1/runs`，以及控制台、MCP `octopus_start`、以后的 CLI）都登记一个 Request，Session 记下它的 `request_id`。

**字段**（新表 `requests`，迁移 9）：
- 身份与归属：`id`、提交者、Team。
- 幂等键：现在的 `request_id`，按提交者唯一，重复提交返回原有的 Request 与 Session。
- 来源客户端 `client`：取自提交所用 Token 的标签（`connect` 签发的 Token 写明客户端，如 claude MCP、codex MCP），控制台登录为 `console`，其余为 `api`。
- 需求原文、源码（类型、仓库、ref）。
- 显式指定：`workflow_id`、模板、`steps`、`document`、`plan` 方式，以及规划后记下的需求中指定的工具与模型（D45 `requested`）。
- 用户给出的检查命令。
- 策略快照：这次的准入（`confirm` / `auto`）与依据（提交时指定、命中某条仓库规则、平台默认），自动纠错配置，发布策略。
- 创建时间。

**状态**：不另设一套状态机，取它最近一个 Session 的状态（规划中、计划待确认、执行中、已完成……），避免两边不一致。

**一对多**：数据上允许一个 Request 对应多个 Session（`sessions` 列表）。本期只在「用同一需求重新发起」时产生第二个 Session（控制台「重新发起」与 MCP），续作不产生新 Request（与设计 §5 一致）。

**接口**：
- `GET /v1/requests`：与会话列表相同的过滤方式，即 `status`、`q`、`limit`。
- `GET /v1/requests/<id>`：附它的 Session 摘要。
- MCP `octopus_start` 的返回加 `request_id`；新增 `octopus_requests`。
- 控制台：会话页写明「来自请求 R-xxx（Claude Code 提交）」，列表可按来源客户端过滤。

## 3. 按仓库范围的准入（M2-5）

**规则**（新表 `policies`，kind `admission`）：
- 作用域：个人或 Team。
- 仓库匹配：`host/owner/repo`，可用 `*` 通配，如 `github.com/LiboMa/*`。
- 模式：`auto`（直接开始）或 `confirm`（停在计划待确认）。
- 创建者与时间。

**判定顺序**：提交时显式给出的 `admission` → 提交者的个人规则 → 会话所在 Team 的规则 → 平台默认 `default_admission`。
命中的那条写进 Request 的策略快照，会话页与报告写明「按规则 xxx 自动开始 / 等待确认」。

**管理**：
- 规则在控制台「设置」中由本人维护；Team 的规则由 Team owner 维护，写审计。
- MCP 只读（`octopus_policies`），不让 Coding Agent 改准入。

## 4. 个人 / Team 映射偏好（M2-4）

**是什么**：用户自己记下的偏好，比如「审查用 codex」「前端实现用 claude」。按任务类别（kind）或角色（role）对应工具 / 模型（新表 `policies`，kind `mapping`）。

**怎么用**（不做成平台默认链）：
1. **给规划者看**：规划提示多一节「用户的偏好（个人 / Team）」，规划者按任务选工具时参考，并在 `mapping` 里写明采用了哪条。
2. **补「自动」**：Workflow / 模板中写 `tool: auto` 的步骤按偏好补上；没有偏好时照旧，由 KiroCrew 规划决定。
3. **优先级**：需求中指定（D45）→ Workflow 阶段写定的工具 → 偏好 → KiroCrew 规划的选择。偏好从不替换前两者，也不是失败时的回退链（回退仍由协调者推荐，D38）。
4. **可见**：任务的映射来源多两种：「个人偏好」「Team 偏好」，控制台、MCP 与报告可见。

## 5. 不做

- 用户 CLI（M3-2）。
- Feature 表与推送核对（M2-9）。
- 独立于 Session 的 Request 状态机。
- 平台内置的工具回退链。
- 按费用的预算（D46 P6 后续）。

## 6. 负责人的决定（2026-10-21）

1. **默认准入改为 confirm**：计划先给人看；对信任的仓库用规则自动开始（设计 §5 的原意）。
2. **映射偏好给规划者参考**：规划者优先采纳，不采纳须写明理由；只对模板 / Workflow 中 `tool: auto` 的步骤直接补上。
3. **先一对一**：每次提交一个 Request 对一个 Session，数据上允许一对多，「重新发起」以后做。

### 原列出的问题

1. **平台默认准入**：EC2 现在的 `default_admission` 是 `auto`，所以仓库规则主要用来对某些仓库「要求确认」。是否改为默认 `confirm`，再用仓库规则对信任的仓库放行？设计 §5 的原意是后者。
2. **映射偏好的力度**：只给规划者参考（规划者可以不采纳，但须写明理由），还是对 `auto` 的任务强制补上（规划者给的工具与偏好不同时，以偏好为准）？
3. **「重新发起」**：本期就做（一个 Request 对应多个 Session），还是先只做一对一？
