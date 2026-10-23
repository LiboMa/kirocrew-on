# KiroCrew 原生能力调研：成员与小组为主（MVP 0.2.0 输入）

负责人 2026-10-23：「这部分是很好的洞见，请记录下来，调研……特别是『成员与小组』的部分，我们重点调研，如果一切顺利，这次的调研结果将做为
MVP0.2.0 INPUT 进行完善。」起点是 `docs/reports/2026-10-23-dispatch-mechanisms.html` 第 7 节列出的、平台还没用到的 KiroCrew 能力。

调研只读代码与磁盘，不改任何东西：
- EC2 上的 KiroCrew 0.10.0（`/opt/kirocrew/current/.../kiro_crew`，下文记作 K10）；
- 本机桌面版 0.8.0（K8）；
- 0.8.0 规格文档（`reference/KiroCrew-contributor-guide/docs/system-specs/modules/`）。

文中的文件与行号是 0.10.0 的，除非注明。标「未核实」的结论还需要在主机上试一次才能确定。

## 0. 结论

1. **成员（crewmate）是「带自己记忆和常驻对话的命名 Agent」，不是安全边界。** 它把一个 kiro-cli 模板、工作目录、私有记忆库、模型和
   「负责什么」绑在一起。KiroCrew 自己说成员之间的隔离只是「路由」，不防同一 OS 用户下的任意代码（`execution_context.py:1-5`）。
   真正的隔离仍要靠平台的「每人一个执行边界」（D49）。
2. **小组（crew team）只是成员名册的分组。** 没有记忆、没有对话、没有权限，也不参与路由（`crew_teams.py:1-10`）。不能拿它表达平台的 Team。
3. **频道（channel）才是「多个 Agent 一起协作」的地方。** 一个 Orchestrator 加几个角色 Agent，在同一个房间里按 @ 轮流发言，人批准修改类操作。
   但上限很紧：默认 3 个 Agent、1 个频道、200 条消息；频道里的 Agent 不能再派子 Agent。
4. **对 0.2.0 最值得做的两件事：**
   - **会话内并行**：互不依赖的任务各自独立 `POST /api/spawn`，不用 `batch_id`，KiroCrew 服务端会排队与限流。这件事价值高、风险低，正对「调度」方向。
   - **角色成员化的试点**：例如把 evaluator / challenge 建成成员，让它们跨会话积累自己的记忆与规则。要先解决记忆跨会话混用的问题（§5.1）。
5. **不建议接：**
   - Conductor：它自己派会话、自己判定完成，与平台的把关冲突；控制接口只对内部开放。
   - KiroCrew 的动态工作流：步骤不经 `/api/spawn`、自动批准、预算实际不生效。
   - 补充说明的 `follow_up` 模式：新一轮的 ID 不返回，平台跟不住。
   - 并行波次（`batch_id`）：只对聊天父会话有用，ID 不出现在状态接口里。

## 1. 成员（crewmate）

### 1.1 是什么、存在哪

成员是 `config.json` 里 `agents.<名字>` 的一条记录（`members.py:3-6`；`config/sections.py:3637-3785`）。字段如下：

| 字段 | 含义 |
|---|---|
| `member_id` | 不可变，建私有记忆库时生成 |
| `kiro_agent` | 绑定的 kiro-cli 模板（人格、提示、工具、技能都来自模板，成员本身没有这些字段） |
| `workspace` | 工作目录 |
| `memory_store` | 私有记忆库，`member-<slug>-<hex>` |
| `model`、`reasoning_effort` | 空为继承 |
| `approval_mode` | `normal` / `trust_reads` / `trust`，空则对话以 trust 打开（**0.10.0 新增**） |
| `display_name`、`description` | 显示名；「负责什么」 |
| `triggers` | 自由文本，给模型看的「哪些事归它」，不是匹配规则 |
| `starred`、`session_color`、`avatar`、看门狗覆盖 | 界面与运行细节 |

每个成员还有这些文件：
- `members/<slug>/briefing.md`：成员自己写的笔记，注入上限 4000 字；
- `trust/member-rules`：主人写的规则，上限 4000 字；
- `trust/member-bindings`：对话绑定，身份以它为准，放在 Agent 写不到的位置；
- 活动日志。

成员的名字（slug）须匹配 `^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?`。

### 1.2 增删改

| 操作 | 接口 |
|---|---|
| 建 | `POST /api/agents`：`name`、`kiro_agent`（必填）、`workspace`、`model`、`reasoning_effort`、`display_name`、`description`、`triggers` 等；自动建私有记忆库；重名 409 `agent_exists` |
| 改 | `PUT /api/agents/{name}`，改 `approval_mode`、`kiro_agent` 时要带期望的旧值 |
| 删 | `DELETE /api/agents/{name}`：不能删默认 Agent；从小组移除、回收日志、释放记忆库 |
| 其他 | `GET /api/members`、`POST /api/members/{slug}/thread`、`.../activity`、`.../briefing`、`GET/PUT .../rules`；0.10.0 新增 `.../greet`、`.../fresh-start`、`.../recap`、`.../dashboard` |

只有 Gateway 的主人能建、改、删成员（`dashboard/handlers/agents.py:385-401`）；应用 Token 一律 404。平台用的本机 Token（`/api/token/local`）是主人身份。

### 1.3 常驻对话（DM）

`POST /api/members/{slug}/thread` 取或建这个成员的常驻对话（`dashboard/handlers/members.py:1077-1477`）：
- 会话键是 `member-<slug>`，模式为 `member`，不出现在普通会话列表里；
- Gateway 重启后，下次打开时从记录恢复历史；
- 对话绑定在这个成员上，不能改绑别的 Agent（409 `member_thread_agent_pinned`）；
- 默认以 **trust** 打开，所以要显式设 `approval_mode`；
- `fresh-start` 会停下正在跑的一轮并清空上下文，记录仍保留在磁盘上。

### 1.4 把任务派给成员

`POST /api/spawn` 带 `crew`（或同义的 `target_member`）：
- **身份**：子 Agent 以这个成员的身份运行，用它的 `member_id`、私有记忆库与模板（`execution_context.py:476-506, 554-571`）；
- **记忆**：记忆模式只能更严，不能放宽；
- **归属**：返回的 `id`、`parent` 与普通派发相同，平台现有的归属证明不变；
- **续接**：仍保持同一个成员和记忆库。

**未核实**：
- 成员的 `model`、规则与笔记是否也作用于派出的子 Agent（规格只说对新会话生效）；
- 普通聊天里能否 @ 一个成员。

更正一处说法：早先有一份调研称「成员没有 triggers 时派发会得 403」，这次没有找到对应代码。`spawn_run` 的说明是「点名成员即可，triggers 只影响自动挑选」。
这条路径上真正会返回 403 的是这几种：
- 主人检查 `owner_only`；
- 成员范围 `member_scope_denied`；
- 应用 Token 的范围。

### 1.5 记忆与身份

- 每个成员一个私有 V2 记忆库，不能选共享库，也不能选全局库。
- 身份证明分两层：
  - MCP 调用带 `_meta["kirocrew.caller"]`（会话键、主体）；
  - 内部调用要么由内核核实对端，要么带能核对回会话键的 `X-Session-Token`，然后才读执行记录（`dashboard/handlers/_shared.py:927-994`）。
- 成员之间没有额外的访问控制：「记忆的归属本身不另加跨成员 ACL」。主人授权的派发可以指向任何成员的记忆库。

### 1.6 owner 边界上现在有什么

- `config.json` 里有两个 Agent 条目：
  - `default`：模板 `poc-coordinator`，全局记忆；
  - `mate`：KiroCrew 自动建的内置成员，模板 `poc-coordinator`，有私有记忆库，triggers 为空。
- `members/mate/` 只有 `welcome_owed.json`，说明它的常驻对话从没打开过。
- `crew-teams/` 下只有锁文件，没有小组。
- **未核实**：顶层 `default_agent` 为 `default`，而 `agent.default_agent` 为 `poc-coordinator`，哪个生效还不确定。

## 2. 小组（crew team）

- `crew-teams/teams.json` 里一条 `{id, name, members: [成员名]}`。KiroCrew 的说法是：「主人对名册的分组，仅此而已：没有记忆、没有对话、没有自己的提示」（`crew_teams.py:1-10`）。
- 一个成员同时只属于一个小组；目录对沙箱和 Agent 的文件工具都不可见，Agent 不能把自己调到别的组。
- 上限：名字 80 字，最多 200 个小组，每组最多 500 人。
- 接口：`GET/POST /api/teams`、`PUT/DELETE /api/teams/{id}`；只有主人能写。注意不要和 `/api/teams/config`（Microsoft Teams 渠道）混淆。
- 用在哪里：界面、增删成员时的维护、导入导出。**不用于**路由、@、频道或权限。

## 3. 频道（channel）：多个 Agent 一起协作

频道是「一个共享的沟通空间，多个专门的 Agent 各担其职、报告进展、响应 @；修改类操作由人最终批准」（`channel.py:1-8`）。

- **组成**：每个 Agent 有角色、模板、任务、自己的会话键 `channel:<频道>:<id>`、监听方式（`all` / `mention` / `silent`）与批准策略（`all` / `writes` / `trusted`）。
- **轮流**：
  - 人的每条顶层消息交给 Orchestrator，其他 Agent 只在被 @ 时、或回复它的线程时才收到；
  - 同一对 Agent 之间最多来回 3 次，人一发言就重新计数。
- **人怎么参与**：
  - `POST /api/channels/{id}/messages {content, mention, thread_id}`；
  - 批准：`.../agents/{aid}/approve`，可选 `approved`、`rejected`、`trust` 等。
- **建频道**：`POST /api/channels {topic, agents:[{role, agent, task, is_orchestrator, approval}]}`，没给 Orchestrator 时自动加一个。
- **上限**：默认每个频道 3 个 Agent、1 个频道、200 条消息；批准卡片 1 小时超时；频道里的 Agent 不能用 `spawn_run` 这类派发工具。
- **未核实**：
  - `agent` 写成员名时，是否用该成员的记忆；
  - 频道接口是否只对主人开放。

## 4. 其他能力

| 能力 | 要点 | 对平台 |
|---|---|---|
| 会话内并行（独立 spawn） | 并发上限与排队在服务端：只等空位的仍回 `spawned`，因内存推迟的回 `queued`，硬拒绝才是 429 | **值得做**：每个 Attempt 仍有自己的 `id` 与 `parent` |
| `/api/spawn/lanes`、`/api/spawn/adaptive` | 只读的通道与当前上限 | **值得用**：平台调度可以先看容量 |
| 并行波次（`batch_id`） | 结果攒成摘要（10 条一批或 120 秒）注入父会话，最后还有一轮汇总；丢了一个成员时，波次要开着等 1800 秒 | 不用：状态接口里看不到 `batch_id`；它和 `mark-collected` 的关系也未核实 |
| `spawn_sub_agents` | 阻塞等待、轮询、`mark-collected` | 平台已经按同样的方式做（a183d84） |
| Conductor | 目标 / 流水线 / 安全三种（台账 Conductor 是即将删除的别名）；驱动的是完整的聊天会话，用 `session-control` 与工作台账（只对内部开放），自己判定完成 | 不用：会和平台的把关争权 |
| 动态工作流 | Python 脚本（`META` + `async def workflow(ctx)`），步骤是 `wf:<run>:<i>` 的 ACP 会话，不出现在 `/api/spawn`；自动批准；`budget_total` 实际不生效；结束后结果注入发起的聊天 | 不用 |
| fork / 旁路问答 / 导出导入 | fork 只复制消息，不带子 Agent 上下文；旁路问答只存在内存；导出是 `.kcsession.json.gz` | 导出值得接：证据归档用。平台的 `gateway_client.export_session()` 已经写好，但还没有调用 |
| 补充说明 `follow_up` | 当前一轮结束后另起一轮，新 ID 不返回；多条会合并 | 不用：平台跟不住；需要时用 `/continue` |
| `/api/spawn/{id}/retry` | 只对失败的运行，新 ID，不带来源 | 不用：平台自己的重试更清楚 |

## 5. 对 KiroCrew ON 的启示（0.2.0 候选）

### 5.1 角色成员化

把规划时的角色（spec、sde、integration_test、security_test、challenge、evaluator、knowledge）建成成员：
- 每个成员绑定平台的 `poc-<工具>` 模板，`description` 写角色职责；
- 派发时带 `crew=<角色>`；
- 由平台以主人身份建和维护，用户不需要操作。

**得到的**：
- 每个角色跨会话积累自己的记忆；
- 有主人写的规则；
- 有自己的活动记录；
- 原生页面里能看到「这个审查者」做过的事。

**要先解决的**：
- **记忆混用**：同一个人所有会话、所有 Team 的工作都会进同一个角色的记忆，这与现在「每个会话 `include_memory: false`」的做法相反。可选的办法有三种：
  - 只让记忆提供「方法」，不提供「项目内容」；
  - 按仓库建成员；
  - 只用成员的规则与笔记，不用记忆。
- 成员的 `model`、规则与笔记是否作用于子 Agent（§1.4，未核实），决定了成员化能带来多少。
- 「已收取」、续接、看门狗在成员派发下是否照旧（未核实）。

### 5.2 会话内并行

现在一个会话一次只跑一个任务（`engine.py` 取第一个未完成的任务）。互不依赖的任务（`depends_on` 不相交，例如几个只读审查）可以同时派出：
- 各自独立 spawn；
- 先读 `/api/spawn/lanes` 看容量；
- 每个 Attempt 的归属、把关与纠错不变。

这件事改的是平台自己的调度，不依赖 KiroCrew 的新能力，最符合「调度、稳定、高性能」的方向。会改变会话的执行方式，做之前要负责人确认。

### 5.3 频道用于小规模的多 Agent 讨论

例如实现者与 Challenge 在一个频道里来回辩论一个设计，人做最终批准。适合小范围评审，不适合整条流水线（上限 3 个 Agent，频道里的 Agent 也不能派子 Agent）。
频道里的修改类操作要走人的批准，这本身就是一道关卡，引入前须负责人同意。

### 5.4 Team 知识

成员的记忆库只在一个边界内，成员之间不能共享。所以 Team 知识不能放在成员记忆里，仍按现有方式经仓库 PR 沉淀（D49、P4）；成员记忆最多当缓存。

### 5.5 不接的，和原因

Conductor、动态工作流、并行波次、补充说明的 `follow_up`、`/retry`：要么平台核实不了它们派出的工作，要么它们自己判定完成，会和平台的把关争权。

## 6. 待核实

1. 成员的 `model`、规则与笔记是否作用于 `crew=` 派出的子 Agent，还是只作用于它的常驻对话？
2. 用 `crew=` 派发时，`mark-collected`、`/continue`、看门狗是否照旧？
3. 频道的 `agent` 写成员名时，是否用该成员的记忆？频道接口是否只对主人开放？
4. 每个成员的事件日志存在哪、保留多久？成员数量有没有上限？删除成员会不会停下它正在跑的子 Agent？
5. 顶层 `default_agent` 与 `agent.default_agent` 哪个生效（owner 边界上两者不同）？
6. 0.10.0 界面里成员与小组的文案（这次只看了 0.8.0 的）。

建议下一步：在一个临时的测试边界（不是 owner）上建一个成员，用 `crew=` 派一个小任务，逐条回答第 1、2 题。这一步不改平台代码，结果决定 §5.1 值不值得做。

## 来源

- 0.10.0：`members.py`、`config/sections.py:3637-3785`、`dashboard/agent_admin/crew_records.py`、`crew_update.py`、`crew_removal.py`、
  `dashboard/handlers/members.py`、`dashboard/messaging_api/spawn.py`、`execution_context.py`、`dashboard/handlers/_shared.py`、`crew_teams.py`、
  `dashboard/handlers/teams.py`、`channel.py`、`dashboard/handlers_channel.py`、`slack/gateway.py`、`subagent_manager/*`、`mcp_tools/spawn.py`、
  `workflows/*`、`dashboard/chat_fork.py`、`dashboard/session_export.py`、`subagent_manager/continuation.py`、`agent_materialization/conductor_agents.py`。
- 0.8.0 规格：`crew-mode.md`、`subagent.md`、`workflows.md`、`pipeline-conductor.md`。
- owner 边界：`/var/lib/octopus/boundaries/owner/crew`（只列出、只读；值已打码）。
