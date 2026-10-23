# 对话与结构化迭代：两种工作方式，一份状态（D73）

状态：设计与技术方案，待负责人确认；确认前不实现。先做 §8 的 P0 验证，结果写回本文后再进入 P1。
D72（`@任务` 续接某个子 Agent，`2026-10-23-chat-follow-up-design.md`）并入本方案，作为 P4 的快捷方式。

负责人原话（2026-10-23）：
- 「让用户可以继续在 KiroCrew chat sessions 中继续聊天；如果不想聊了，也可以通过目前最新的状态，继续由 KiroCrew ON 这个平台记录的 Session
  继续创作……做状态的归纳总结，然后继续迭代当前项目；这样，用户不但可以直接在主进程交互，也可以采用结构化迭代，这是理想模型，验证其有效性，可行性。」
- 「我们可能不需要撤回什么，只需要找到相关的历史。」
- 「这样的机制，是否存在着某些弊端和问题？还有它的实现机制，请提前把技术、接口、调用规范规划清楚，形成文档，之后再做。」

## 1. 模型

| 方式 | 在哪 | 谁干活 | 平台做什么 |
|---|---|---|---|
| 自由对话 | KiroCrew 原生聊天：会话的父会话 `octopus-run-<id>` | 主会话 Agent。随时回答问题；会话停下时也可以动手，自己改或派子 Agent | 告诉它现在能不能动手；记下它派了谁、改了什么 |
| 结构化迭代 | 控制台、CLI、MCP 的「续作」 | 平台 Engine 按任务派发 | 把关、纠错、记录、发布 |

两种方式读写**同一份状态**：
- **工作区**：会话分支上的候选提交，加上尚未提交的改动；
- **平台记录**：任务、每次尝试的结论、交接、修订历史；
- **对话记录**：父会话的聊天，包括平台写进去的每条结果说明，以及聊天里派出的子 Agent 的结果。

两种方式之间切换时，做一次**状态归纳**。不撤回 a183d84，也不恢复「每个任务的观察员回复」：每个任务的结果说明本来就在聊天里，用户问到时，Agent 从这些历史里找。

## 2. 三条规则

1. **同一时间只有一个写者。**
   - 平台在跑任务（会话状态为运行类）时，聊天里的 Agent 只回答问题，不改文件、不派子 Agent。
   - 会话停下后（完成、需要处理、失败、停止），聊天里的 Agent 可以动手。
   - 每次切换写者，平台都在聊天里写一行说明（`note`，不触发模型）。
2. **聊天里做的事都留痕。** 聊天里的 Agent 派的子 Agent 挂在本会话的父会话下，改动落在会话的工作区；平台能把它们和自己派的区分开（§6.3）。
3. **平台只在用户要求时接手。** 用户续作时，平台先把对话里的工作归纳为一次记录（§6.6），再继续派发。

## 3. 两个归纳

- **平台 → 对话**：会话停下时，平台写一条「当前状态」说明，内容由平台记录确定性生成，不跑模型（§6.2）。
- **对话 → 平台**：续作时，平台先核对事实（git、子 Agent 列表、对话记录），再请聊天 Agent 写一份结构化总结，然后把对话里的改动提交为新的候选，
  照常检查，记一条修订；之后的任务以平台核对的事实为准重新执行（§6.6）。

## 4. 弊端与问题

| # | 问题 | 严重度 | 怎么缓解 | 剩下的风险 |
|---|---|---|---|---|
| 1 | **「单写者」只靠提示约束。** 平台在跑时，Agent 仍可能改文件；用户也可能在平台跑时让它动手 | 中 | 写者切换时写说明（规则 1）；续作时，若对话里还有正在跑的一轮或子 Agent，平台拒绝并说明原因（§6.5，不排队等待）；每次平台尝试结束时核对工作区，出现不是本次尝试的改动就记提示 | 不能硬性阻止 Agent 写文件；最坏情况是一次尝试的候选里混进了对话的改动，有提示可查 |
| 2 | **对话里的工作没有逐步把关。** 聊天里派的子 Agent 没有平台的指令、角色卡片、交接与结论文件；独立审查要到续作之后才跑 | 高 | 归纳时做秘密检查、跑检查命令；之后的审查与 Evaluator 针对新候选重跑；控制台显示「有未归纳的对话改动」 | 用户只聊不续作，这些改动就一直没被审查；发布本来就要求门禁通过，所以不会被发布 |
| 3 | **总结可能漏、可能错。** | 中 | 改了哪些文件、派了谁，以 git 与 `/api/spawn` 为准，不以总结为准；总结只作说明；「需求变化」由用户在续作时确认 | 「做了什么决定」只能靠总结 |
| 4 | **成本上升。** 聊天里每条用户消息都会让 Agent 跑一轮；归纳要多跑一轮总结 | 中 | 这是用户主动的；平台自己不额外触发轮次，除了归纳的那一轮；总结限定在上次归纳之后的对话 | 与 a183d84 省下的方向相反，但只发生在用户聊天时 |
| 5 | **分不清是谁发的。** 原生登录覆盖整个 Gateway（K03），聊天消息不带身份 | 中 | 归纳由续作的人发起，记为此人；对话部分只记「原生对话（入口持有人）」 | 多人持有入口时，对话内容无法逐条归属到人 |
| 6 | **安全底线。** 聊天里的 Agent 有 shell，可能推送、调用外部服务、读到凭证 | 高 | 提示写明不推送、不建 PR、不碰 `.octopus/` 与平台文件；KiroCrew 的沙箱与工具门禁照旧；归纳时做秘密检查（命中凭证的改动不进候选，与现在相同）；平台的 Token 不进 Agent 的环境 | 与现在平台派的子 Agent 相同的边界（同一执行边界、同一沙箱），没有更低 |
| 7 | **状态漂移。** 对话改了工作区但还没归纳时，预览与下载读的是新文件，控制台的结论却是旧的 | 中 | `GET /v1/runs/<id>/chat` 给出未归纳的改动，控制台、MCP、报告据此显示提示 | 用户不看提示时仍会混淆 |
| 8 | **写者不止两个。** 本机同步（D58）、聊天、平台续作可能同时改同一个分支 | 中 | 同步与续作一样，先检查有没有未归纳的对话改动，有就拒绝，并说明先续作归纳 | 本机提交与对话改动同一时期发生时，需要用户先选一边 |
| 9 | **对话很长。** | 低 | 只读上次归纳之后的消息，分页读取；总结的输入有上限 | 极长的对话，总结会更粗 |
| 10 | **依赖 KiroCrew 的接口细节。** 聊天记录格式、`/api/chat` 的回执、`/api/spawn` 的 `parent` 字段 | 中 | 全部登记进 `tests/kirocrew_contract.json`，部署时用原生测试核对 | 升级 KiroCrew 时，要先在测试边界上验证 |
| 11 | **聊天里派子 Agent 时，工具不受平台约束。** 例如它可能派已停用的 OpenCode | 中 | 派发白名单去掉停用的工具（Gateway 模板，需重启）；提示里列出当前可用的工具 | 模型仍可能选不合适的工具，但只能在白名单之内 |
| 12 | **平台请求总结的那条消息，会作为一条「用户消息」出现在聊天里。** KiroCrew 没有「系统发起一轮」的接口 | 低 | 消息以「（KiroCrew ON 平台）」开头，写明用途 | 外观上像用户发的 |
| 13 | **提示改动要重启 Gateway。** 父会话 Agent 的系统提示在 Gateway 模板里 | 低 | 随某次部署一并重启；新会话也可以先靠 Worker 写入的背景说明 | 旧会话里的 Agent 仍按旧提示行事，只回答、不动手 |

## 5. 取舍

| 方案 | 说明 | 为什么不选 / 选 |
|---|---|---|
| A 撤回 a183d84 | 每个任务结束都让观察员回复 | 只恢复「看」，解决不了「接着干」；还会带回汇总轮和常驻进程 |
| B 只做 D72 | 聊天里只能用 `@任务` 让平台续接某个子 Agent | 每次交互都受平台把关，最安全；但不是「直接在主进程交互」 |
| C 对话里的一切都转成平台任务 | 聊天 Agent 不动手，把用户的话转成续作 | 等于把聊天变成续作表单，失去自由对话 |
| **D 双模 + 归纳（本方案）** | 停下时可以在聊天里自由干，续作时归纳 | 满足负责人的理想模型；代价见 §4，主要是 #2 与 #6 |

B 作为 D 的补充保留（P4）：需要平台把关的小改动用 `@任务`，探索性的用自由对话。

## 6. 技术方案

### 6.1 组件与职责

| 组件 | 新增职责 |
|---|---|
| 平台 Engine（`octopus/engine.py`） | 写者切换时让 Worker 写状态说明；执行「对话中的工作」这次 Attempt：请求总结、等总结、提交候选、把关 |
| 平台服务（`octopus/domain.py`，新模块 `octopus/chat.py`） | 续作与同步前检查对话活动；`GET /v1/runs/<id>/chat`；登记归纳；修订与审计 |
| Worker（`octopus/worker.py`） | 新接口：`/chat/note`、`/chat/activity`、`/chat/ask`、`/chat/reply`（§6.4 b） |
| KiroCrew Gateway | 不改。用已有的 `/api/chat/slots/{slot}`、`/note`、`/api/chat?ws=1`、`/api/spawn` |
| 父会话 Agent（`poc-coordinator`） | 新提示：两种写者状态下各能做什么、怎么派子 Agent、怎么写总结（§6.7） |

### 6.2 写者状态与状态说明

写者状态不另存，从会话状态推出：

| 会话状态 | 写者 |
|---|---|
| `preparing`、`ready`、`dispatching`、`running`、`waiting_approval`、`dispatch_unknown`、`diagnosing`、`stopping`、`reopening` | 平台 |
| `completed`、`blocked`、`failed`、`stopped`、`waiting_worker` | 对话 |
| 已关闭、删除中 | 无（父会话已停用或删除） |

Engine 在两个时刻让 Worker 写说明：

- **平台开始执行时**：本会话第一次派发，或从停下转为运行。内容：「KiroCrew ON 平台开始执行 task-2（第 1 次尝试）。平台停下之前，请只回答问题，不修改文件、不派子 Agent。」
- **平台停下时**：写「当前状态」，见下。

「当前状态」由 `chat.state_note(run)` 生成，不超过 3800 字（`note` 的上限是 4000），依次是：
1. 会话名、现在的状态与停下的原因；
2. 已完成的任务：ID、名称、工具、一句话结论、交接文件路径；
3. 当前候选提交（短 SHA、改动文件数）与分支；
4. 还没完成的任务；
5. 工作区路径；
6. 规则：「现在可以按用户的要求修改工作区或派子 Agent；改动会在用户续作时由平台归纳。」

去重：`snapshot.chat.last_note = {"writer": "platform" | "chat", "version": <会话版本>, "at": <时间>}`。同一写者、同一版本只写一次。

### 6.3 数据

- **会话快照** `snapshot.chat`：
  - `last_note`：见 §6.2；
  - `cursor`：`{"total": <父会话消息总数>, "at": <时间>}`，表示上次归纳读到哪里；
  - `absorbed`：最近 20 次归纳的 `{attempt_id, total_from, total_to, children: [原生 ID]}`。
- **「对话中的工作」这次 Attempt**：挂在会话里最后一个写代码的任务上；没有写代码的任务时，挂在最后一个任务上（与 D58 本地同步相同）。
  - `evidence.chat`：
    ```json
    {"by": {"id": "…", "username": "…"}, "entry_holders": ["…"],
     "messages": {"from": 12, "to": 47},
     "children": [{"id": "ab12…", "agent": "poc-claude", "done": true, "summary": "…（截断）"}],
     "files": ["src/a.js", "…"],
     "summary": {"asked": [], "done": [], "decisions": [], "open": [], "requirement_change": {"changed": false, "text": ""}},
     "summary_missing": false}
    ```
  - 门禁：`native_completed` 为 skip（「对话中的工作」）、`diff_present`、`no_secrets`、`checks_pass`；总结缺失或格式不对时记为 `outcome_valid` 的 warn，不失败。
- **修订** `kind: "chat"`：摘要为「对话中的工作：N 个文件，M 个子 Agent」；`chat` 字段带总结与事实清单。
- **事件**：`run.chat_note`、`run.chat_absorbed`、`attempt.chat_summary`；控制台 `EVENT_LABELS` 加中文名称。
- **审计**：`run.chat_absorb`，记发起人。

### 6.4 接口

#### a. 平台对外（`/v1`）

| 方法与路径 | scope | 请求 | 应答 | 错误 |
|---|---|---|---|---|
| `GET /v1/runs/<id>/chat` | read | — | `{"writer": "platform"\|"chat"\|null, "running_turn": bool, "unabsorbed": {"messages": n, "children": [...], "files": [...]}, "absorbed_at": t}` | 404、`409 native_unavailable` |
| `POST /v1/runs/<id>/absorb` | execute | `{"request_id", "note"?}` | `{"id", "version", "attempt_id", "task_id", "status"}` | `409 chat_busy`（对话里还有正在跑的一轮或子 Agent）、`409 nothing_to_absorb`、`409 busy`（平台在跑）、`403 boundary_owner_only` |
| `POST /v1/runs/<id>/continue`（已有） | execute | 新增可选字段 `"absorb": true\|false`，默认 true | 已有字段，另加 `"absorbed": {"attempt_id"}` | 同上，另加已有的错误 |
| `POST /v1/runs/<id>/sync`（已有，D58） | execute | — | — | 新增 `409 chat_unabsorbed`：先续作或归纳 |

- 谁能用：与续作、同步相同，即能执行这个会话的人；D49 个人边界上只有边界的主人；Team 未归档；执行环境仍为这个空间服务。
- MCP：
  - `octopus_status` 带上 `chat` 一节；
  - `octopus_continue` 默认归纳；
  - 新工具 `octopus_absorb`。
- CLI：`octopus status` 显示未归纳的对话改动；`octopus continue --no-absorb`。

#### b. 平台 → Worker（Bearer Worker Token，JSON）

| 路径 | 请求 | 应答 | 超时 |
|---|---|---|---|
| `/chat/note` | `{"run_id", "native_parent", "content", "source": "octopus-state"}` | `{"ok": true}` | 15 秒 |
| `/chat/activity` | `{"run_id", "native_parent", "since_total", "candidate"}` | `{"total", "running_turn", "messages": [{"index", "role", "ts", "content"(截断、打码)}], "children": [{"id", "agent", "done", "result"(截断、打码)}], "workspace": {"changed", "files", "head_moved"}}` | 15 秒 |
| `/chat/ask` | `{"run_id", "native_parent", "attempt_id", "message"}` | `{"ok": true, "receipt": {...}}`，回执落盘到 Attempt 目录，同一 Attempt 不重复发 | 15 秒 |
| `/chat/reply` | `{"run_id", "native_parent", "attempt_id", "after_ts"}` | `{"done": bool, "text"(打码), "json": {...}\|null}`：请求之后的第一条 assistant 消息；轮次还没结束时 `done: false` | 15 秒 |

- **区分子 Agent**：`/chat/activity` 的 `children` 只列 `GET /api/spawn` 中 `parent` 等于本会话父会话键、且任务文字不含 `[OCTOPUS-ATTEMPT:` 标记的运行，即聊天里派的。
- **能力协商**：Worker 的 `/health` 加 `"chat": 1`。没有这一项的旧 Worker 不写状态说明、不归纳，续作照旧。

#### c. Worker → KiroCrew（本机 Token）

| 调用 | 用途 | 现状 |
|---|---|---|
| `GET /api/chat/slots/{slot}?limit=&before=` | 读消息、`running`、`total` | 已有（`/observe` 在用） |
| `POST /api/chat/slots/{slot}/note {content, source, ephemeral}` | 状态说明 | 已有（结果说明在用） |
| `POST /api/chat?ws=1 {slot, message}` | 请求总结的那一轮，立即返回回执 | **新用到**：登记进契约，读取 `slot`、`message` |
| `GET /api/spawn` | 列出聊天里派的子 Agent | 已有 |

### 6.5 调用规范

- **幂等**：平台对外接口带 `request_id`（`_command`）；`/chat/ask` 以 `attempt_id` 去重；状态说明以 `last_note` 去重。
- **打码**：Worker 返回的对话文字、子 Agent 结果、总结，在离开 Worker 之前按 D57 打码（凭证、Worker Token）；平台写入事件、修订之前再打码一次。
- **不等待**：
  - 续作、同步、归纳遇到对话里正在跑的一轮或子 Agent：当场 `409 chat_busy`，说明「对话里还有正在进行的工作：…，等它结束后再续作」。
  - Engine 等总结时按普通 Attempt 的节奏查看（2 / 5 / 10 / 20 秒），最长 10 分钟。超时就不再等，记 `summary_missing`，按事实继续归纳。
- **失败**：
  - Worker 不可用：与普通派发相同（`dispatch_unknown`，先核实再重试）；
  - `/chat/ask` 被 KiroCrew 拒绝（例如父会话正忙）：记提示，跳过总结；
  - 提交候选失败：这次 Attempt 失败，进入平台的纠错。
- **顺序**：读事实 → 请求总结 → 等总结 → 提交候选 → 秘密检查 → 检查命令 → 记修订 → 重置之后的任务 → 更新 `cursor`。
- **之后的任务**拿到的说明（`repair`）：`{"rerun": true, "chat": {"files": [...], "summary": "<done 的前 5 条>"}, "instruction": "用户在原生对话里改动了工作区（见 chat.files），本任务在新的源码上重新执行：以源码与平台记录为准，总结只作参考。"}`
- **错误码**：

  | 码 | 含义 |
  |---|---|
  | `chat_busy` | 对话里还有正在跑的工作 |
  | `chat_unabsorbed` | 有未归纳的对话改动 |
  | `nothing_to_absorb` | 自上次归纳以来对话没有新内容、工作区也没有新改动 |
  | `chat_unavailable` | 父会话不可用 |
  | `worker_upgrade_required` | 旧 Worker，不支持这一功能 |

### 6.6 归纳的时序

```
用户（Web 续作）  平台服务            Engine                Worker               KiroCrew
  │ 续作 ───────►│ /chat/activity ───────────────────────►│ 读消息、子 Agent、git ─►│
  │              │◄── 有未归纳的改动；没有正在跑的轮次 ─────│                      │
  │              │ 登记：「对话中的工作」Attempt、版本加一  │                      │
  │◄─ 已续作 ────│                    │ /chat/ask ────────►│ POST /api/chat?ws=1 ─►│ Agent 写总结
  │              │                    │ /chat/reply（轮询）►│ 读最新回复 ──────────►│
  │              │                    │ /candidate commit ─►│ 提交工作区改动        │
  │              │                    │ 秘密检查、检查命令   │                      │
  │              │                    │ 记修订；之后的任务重置为待执行，按 repair.chat 重跑
```

### 6.7 提示

**父会话 Agent 的系统提示**（`prompts.COORDINATOR`，Gateway 模板）由「Owner 观察员」改为「会话协作者」，要点：
- 平台写的说明会告诉你现在谁是写者。平台在跑时只回答问题；平台停下后，可以按用户的要求修改工作区，或派子 Agent。
- 派子 Agent 用 `spawn_run`：`agent` 从 `poc-kiro`、`poc-claude`、`poc-codex` 里选，`cwd` 为说明里的工作区，`keep: true`。不派已停用的工具。
- 不推送、不建 PR / MR、不改 `.octopus/` 下的平台文件，不读平台或 Worker 的凭证。
- 平台请求总结时，只回复一个 JSON 代码块，字段见下。

**请求总结的消息**（`/chat/ask`）：

> （KiroCrew ON 平台）用户要回到平台继续迭代。请总结自 {from} 以来这段对话：用户要了什么、你做了什么、做了哪些决定、还剩什么、需求有没有变。
> 只回复一个 json 代码块：`{"asked": [...], "done": [...], "decisions": [...], "open": [...], "requirement_change": {"changed": bool, "text": "..."}}`。
> 改了哪些文件由平台从 git 读取，你不必列出。

**校验**：
- 五个字段都要有，各项为字符串；每个列表最多 20 项，每项最多 300 字；`requirement_change.text` 最多 2000 字；
- 不符合的记 `summary_invalid` 提示，按事实继续归纳。

## 7. 测试与验收

- **服务**：
  - 写者状态推导；状态说明去重；
  - 续作或同步遇到 `chat_busy`、`chat_unabsorbed`；
  - 归纳的登记：Attempt、修订、审计、预算重新计算；
  - 之后的任务重置并带 `repair.chat`；
  - `nothing_to_absorb`；权限（与续作相同）；幂等。
- **Engine**：
  - 「对话中的工作」的推进：请求总结、等待、超时；
  - 总结有效、缺失、格式不对三种情况；
  - 候选提交与门禁；旧 Worker 跳过。
- **Worker**：
  - `/chat/activity` 只列不带平台标记的子 Agent；消息分页与打码；工作区改动；
  - `/chat/ask` 去重；`/chat/reply` 只取请求之后的第一条回复。
- **契约**：`POST /api/chat` 的请求体登记进 `tests/kirocrew_contract.json`，部署时的原生测试核对 KiroCrew 读取这些键。
- **控制台**：未归纳的对话改动的提示；续作对话框显示总结里的需求变化（预填，由用户确认）。
- **EC2 验收**：在一个测试会话上走完一遍——
  1. 会话完成；
  2. 在原生聊天里让 Agent 改一个文件并派一个 `poc-claude` 子 Agent；
  3. 控制台显示有未归纳的改动；
  4. 续作；
  5. 聊天里出现总结请求与 JSON 回复；
  6. 新候选里有对话的改动，秘密检查与检查命令通过；
  7. Evaluator 针对新候选重跑；
  8. 修订历史出现「对话中的工作」。

## 8. 分期

| 期 | 内容 | 改动 |
|---|---|---|
| P0 验证 | E1 上下文、E2 对话里动手、E3 总结与事实对照、E4 续作后重跑（见下） | 不改平台代码；在测试会话上用原生页面与 Gateway 接口手工完成 |
| P1 平台 → 对话 | 写者状态说明、「当前状态」说明；父会话 Agent 的新提示；派发白名单去掉停用的工具 | Worker `/chat/note`；Engine 两个时刻；`prompts.COORDINATOR` 与模板（重启 Gateway） |
| P2 对话 → 平台 | 「对话中的工作」Attempt、`/chat/activity`、`/chat/ask`、`/chat/reply`；续作自动归纳；同步检查 | §6.3–6.6 |
| P3 可见 | `GET /v1/runs/<id>/chat`、控制台提示、MCP 与 CLI | §6.4 a |
| P4 快捷方式 | D72 `@任务`：在聊天里让平台续接某个子 Agent | 见 D72 文档 |

P0 的四个实验：

| 实验 | 做什么 | 看什么 |
|---|---|---|
| E1 上下文 | 会话完成后，在原生聊天里问「做到哪了、改了什么、为什么这样做」 | 只凭聊天里的结果说明与交接，答得准不准 |
| E2 对话里动手 | 只在测试会话里，用一条用户消息放开「不派发」：让 Agent 派一个 `poc-claude` 子 Agent 改一个文件 | 改动是否落在工作区；子 Agent 是否挂在父会话下、不带平台标记；Agent 自己改文件时在哪个目录 |
| E3 总结 | 按 §6.7 的请求让 Agent 写总结 | 与 git diff、子 Agent 列表逐项对照：漏了什么、错了什么、JSON 是否合格 |
| E4 续作 | 手工把对话改动当作一次续作的输入，重跑 Evaluator | 后续任务能否在对话之后的状态上正确工作 |

记录三项：总结与事实的吻合程度、额外的 Kiro / Claude 轮次、每步耗时。结论写回本文 §9。

## 9. 待负责人决定

1. 模型、三条规则，以及 §4 中两项「高」风险（#2 对话里的工作没有逐步把关、#6 安全底线）的缓解方式，是否可以接受？
2. 是否先做 P0？
   - 会用掉若干轮 Kiro / Claude；
   - 实验会话放在 owner 边界上，或者新建一个临时测试边界（后者要改 EC2，单独征求同意）。
3. P1 要改父会话 Agent 的系统提示，并重启 Gateway，可以随下一次部署一起做吗？
