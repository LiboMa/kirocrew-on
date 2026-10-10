# MVP-1.0.0 API / MCP 接入手册

REST API、Web、Python 客户端和 MCP 都经过同一个服务端权限模型。
客户端可以是 Claude Code、Kiro CLI/KiroCrew，也可以是团队已有的门户或自动化程序；不必通过平台 CLI。

## 身份与发现

- `POST /v1/login`：`{"username":"…","password":"…"}`，返回用户 Token。
- 后续请求头：`Authorization: Bearer <USER_TOKEN>`。
- `GET /v1/me`：当前用户、是否管理员、当前 Token scopes。
- `GET /v1/openapi.json`：无需登录的机器可读接口与请求 schema。
- `POST /v1/tokens`：创建可撤销 Token，scopes 为 `read`、`execute`、`manage` 的组合。
- `DELETE /v1/tokens/{id}`：撤销自己的一枚 Token，JSON body `{}`。
- `POST /v1/logout`：撤销当前 Token，JSON body `{}`。

默认登录 Token 有效期一天；API Token 最长 30 天，Web 创建的客户端 Token 为 7 天。
Token 只在创建时返回明文，数据库保存 SHA-256 摘要。Worker 凭据是平台连接用的另一种凭据，不是用户 API Token。
Team / Workflow 授权每次请求重新核对，不编码为长期不变的 Token 权限副本。

## 开发运行

可先 `GET /v1/workflows` 和 `GET /v1/workers`，从当前用户可访问的结果选 ID；
也可直接输入需求，让共享的匹配服务返回工作流与任务。

### 从需求匹配

`GET /v1/workflow-templates` 返回 15 个预定义模板及任务、示例。
`POST /v1/workflows/resolve` 使用 `read` scope，输入如下：

```json
{"input":"修复登录失败的 Bug，并补充回归测试。"}
```

返回 `name`、原始 `input`、`workflow_name`、`steps`、`selection`、`reason` 与备选列表。
`mode=rules` 明确表示关键词 / 模板匹配；没有调用 LLM、原生 Session 或源码准备。
只从当前用户具备 runner 或更高权限的工作流中匹配；没有匹配时明确返回通用流程。
需求包含可访问工作流的完整名称或 ID 时优先选取；多个名称同时匹配返回 `ambiguous_workflow`，让用户指定 ID。

手动指定时，可在同一请求加入以下三项中的**一项**：

- `workflow_id`，可附带 `workflow_version`。
- `template_id`，例如 `proof`、`bugfix`、`review`。
- `steps`，由用户或 Chat 自行编排的完整任务列表。

明确指定的配置优先，`mode=manual`。指定 workflow 时返回固定的 `workflow_version`。
用户确认后，将返回的 `selection` 展开到 `POST /v1/runs` 请求，并加入 `request_id`、`input`、`source`；
可附带手动编辑的 `name`、`team_id`、`worker_id`。若修改了任务，改为只提交 `steps`，不要同时保留 `workflow_id`。
这样工作流库之后产生新版本，也不会改变已确认的任务配置。

也支持直接 `POST /v1/runs`，仅给定需求及代码来源、不指定上述三项，此时在提交时自动匹配：

```json
{
  "request_id": "intent-demo-001",
  "input": "快速验证一个小功能，完成最小实现与独立测试。",
  "source": {"type": "new"}
}
```

该请求不会创建工作流库条目；任务配置直接保存为本次 Session 的快照。未选 Worker 时返回 `waiting_worker`。
已有工作流授权在正式提交时再次核对。匹配服务不会绕过 Team 权限或 ACP 的工具 / 模型校验。

### 指定工作流或任务

使用已有工作流：

```json
{
  "request_id": "client-demo-001",
  "workflow_id": "flow-实际ID",
  "workflow_version": 1,
  "team_id": "team-实际ID",
  "worker_id": "worker-实际ID",
  "input": "为当前项目增加导出功能，提供接口测试及启动说明。",
  "source": {
    "type": "git",
    "url": "git@github.com:YOUR_ORG/YOUR_REPO.git",
    "ref": "main"
  }
}
```

请求为 `POST /v1/runs`。省略 `team_id` 即私人运行；省略 `worker_id` 则保存需求并等待分配。
`source` 必填，明确选择 `new` 或 `git`，避免把用户指定的仓库任务静默放入空目录。
Git URL 不允许嵌入密码 / Token、查询参数或本机目录；Git 认证使用 Worker 原有 credential helper / SSH 配置。
仓库检出、提交基线记录及 Workspace 检查先完成，然后才调用 Coding Agent。

也可以不创建工作流，直接提交定制任务：

```json
{
  "request_id": "client-custom-001",
  "input": "在新项目中实现一个 Python 加法模块，编写并运行 unittest。",
  "source": {"type": "new"},
  "worker_id": "worker-实际ID",
  "steps": [
    {
      "id": "code",
      "name": "实现与验证",
      "tool": "claude",
      "model": "auto",
      "effort": "",
      "prompt": "创建模块和测试，运行真实测试，完成交接文件。"
    },
    {
      "id": "review",
      "name": "独立审查",
      "tool": "codex",
      "model": "auto",
      "effort": "",
      "prompt": "独立读取实际代码和交接，复跑测试，报告安全与边界问题。"
    }
  ]
}
```

返回 `id`、`session_id`、`version`、`status`。这表示需求已登记，**不表示任务执行完成**。
每个运行包含 1–8 个顺序 Task，Task ID 在运行内唯一，对外完整 ID 为 `run_id/task_id`。
工具支持 `kiro`、`claude`、`codex`、`opencode`；模型 / Effort 的实际可用性仍受对应 Harness 与 Gateway 约束。

## KiroCrew 后台推荐

即时规则匹配之外，可发起真实的 Kiro 工作流设计任务，需要 `execute` scope：

```http
POST /v1/designs
```

```json
{
  "request_id": "recommendation-001",
  "input": "为退款接口增加幂等和金额校验，Claude 编码，Codex 独立审查。",
  "template_id": "proof",
  "worker_id": "worker-实际ID"
}
```

`template_id` 可替换为 `workflow_id` / `workflow_version` 或用户当前完整 `steps`，三者只能选一项。
省略时使用规则匹配作为初始基线；`team_id` 指定团队空间。
服务端验证 Worker 归属、原生执行边界与生成能力，再建立一条 `purpose=workflow_design` 的持久化 Run。
请求 ID 幂等；不会创建、保存或执行被推荐的业务工作流。

`GET /v1/designs/{id}` 使用 `read` scope，返回：

- `status`：`preparing`、`ready_to_dispatch`、`running`、`waiting_approval` 等真实进度。
- `ready` 表示原生执行和交接已完成，工作流 JSON 通过校验，此时提供 `config.name`、`config.steps`。
- `invalid` 保留无法解析的 `raw_result` 及错误，不用预设模板冒充 Agent 输出。
- `native_task_id`、`native_parent`、`evidence`：实际 ACP 执行证据；无模型观察时不猜测。
- `input`：生成时的原始需求，供客户端拒绝迟到或过期结果。

收到 `ready` 后由用户采用并修改 `config`，再调用 `POST /v1/workflows` 保存，
或把编辑后的 `steps` 交给 `POST /v1/runs` 执行。不要把“推荐完成”当成业务代码已经完成。
可用现有 `/v1/runs/{id}/stop` 停止生成，也可导出、关闭相应 Session；设计任务不能直接 `continue`，
新需求使用新的设计请求，避免旧基线与新输入混用。

模板的动态提示词与 Agent 生成分开记录：模板提示词由预设、当前目标和步骤位置组成；
Agent 接收完整需求、当前 Task 基线和 Worker 模型目录后生成具体提示词。

## 原生 KiroCrew 浏览器入口

`POST /v1/runs/{id}/crew-entry`，JSON body `{}`，需要当前用户的 `execute` scope。
适用于开发 Session 和 `purpose=workflow_design` 的推荐 Session。
响应包含 `url`、`origin`、`native_parent`；`url` 是本次原生登录链接，应立即用于浏览器导航。
响应使用 `Cache-Control: no-store`，不会进入 commands 幂等缓存、运行快照或导出记录。

服务端先校验运行访问权限与 Worker 归属，再读取 Worker 当前边界及入口能力。
Worker 核对本地父会话绑定和 Gateway 的实际 Session 后，复用原生登录机制生成链接。
返回前再次检查成员资格、运行生命周期及 Worker 配置。客户端不能自选 `sid` 或目标 URL。
此接口不修改运行版本、不触发源码准备、恢复、停止或任务派发。

| 返回 | 含义 |
|---|---|
| `404 not_found` | 运行不存在或当前用户无权访问 |
| `409 not_prepared` | 原生父会话尚未建立 |
| `409 session_closed` | 已关闭 / 正在关闭；先通过已有 reopen 操作恢复 |
| `409 worker_upgrade_required` | Worker 未公布 `crew_entry` 能力 |
| `409 worker_changed` / `entry_mismatch` | 当前执行边界或返回 Session 与绑定不符 |
| `409 crew_origin_required` | 远程 Worker 仍指向 localhost，需配置 `--crew-url` |
| `503 worker_unavailable` | 无法连接 Worker，可稍后重试 |

该入口授予原生 Gateway 的浏览器登录，**不等同于只读或单 Session 权限**。
Team 隔离依赖专属 Worker / Gateway 边界；已建立的原生登录撤销由 KiroCrew 管理。
MCP 工具不自动申请或把此链接送入 Coding Agent 上下文。

## 状态与事件

```http
GET /v1/runs
GET /v1/runs/{id}
GET /v1/runs/{id}/events?after=0
GET /v1/runs/{id}/events?stream=1&after=123
GET /v1/runs/{id}/export
```

详情包含 `tasks[].spec`、`tasks[].attempts[]`、请求配置、`native_id`、实际后端/模型/Effort、Context hash、结果和错误。
没有原生证据的实际值为 `null`。请求的 `model` 不是执行成功证明。
详情新增 `attention`（具体原因、当前任务及操作建议）和 `recovery`（策略、已用轮次、当前及历史诊断）。
`diagnosing` 表示协调者诊断中；返工期间仍是 `ready/running`，可用最新 Recovery 的 `applied` 状态识别。
SSE 增加 `recovery.started`、`recovery.applied`、`recovery.blocked`。

SSE 每条更新包含递增事件 ID：

```text
id: 124
event: update
data: {"id":124,"kind":"attempt.state","run_id":"run-…","payload":{"status":"running"}}
```

连接最多持续约 30 秒，客户端用最后收到的 `id` 作为 `after` 或 `Last-Event-ID` 重连。
服务端每秒重新检查 Token 和 Session 权限；撤权时发送 `access_revoked` 并断开。
只展示自己有权访问的运行；Team 成员被移除后不能继续读历史或导出。

可直接使用 curl 流式读取：

```bash
curl -N \
  -H "Authorization: Bearer $OCTOPUS_API_TOKEN" \
  "$OCTOPUS_URL/v1/runs/$OCTOPUS_RUN_ID/events?stream=1"
```

可选 Python 客户端的 `events()` 表示一次有界 SSE 连接；调用方保存最后事件 ID 并重连。
平台 CLI 的 `events` 命令已自动重连，Ctrl+C 结束；`--once` 只读一段连接。

## 原 Session 续作

先读取 Session 最新 `version`，然后：

```http
POST /v1/runs/{id}/continue
```

```json
{
  "request_id": "client-continue-001",
  "expected_version": 3,
  "task_id": "code",
  "input": "补充异常输入处理并重新运行测试。",
  "execution": {
    "name": "修复与验证",
    "tool": "codex",
    "model": "auto",
    "effort": "high",
    "prompt": "依据上轮失败记录修复，并运行相关回归测试。"
  }
}
```

`execution` 字段可以只传需要改变的配置。运行中需先停止；不能跳过未完成的上游步骤。
保留平台 Run / Session ID、Workspace 和上游交接；选中 Task 与其下游生成新的执行 Attempt。
首版不复用工具内部 Session：新 Attempt 由完整需求、当前任务、前序交接和同一 Workspace 重新建立上下文。
旧 Attempt 的配置与返回值保持不变，最初工作流快照也保持不变，Session 修订单独记录。
可附 `recovery: {"enabled":true,"max_cycles":2}` 修改本次 Session 的纠错策略，保存到修订记录。
旧运行明确启用纠错后，新的业务 Attempt 使用结构化结果契约，不改写旧 Attempt。

## Coordinator 诊断与自动返工

新建运行可附 `recovery: {"enabled":true,"max_cycles":2}`，默认启用两轮，可设置 1–5 或关闭。
策略属于具体运行，不跟随工作流库的新版本变化。未携带策略的历史运行默认关闭自动处理。

对已暂停的当前运行，手动发起一次诊断：

```http
POST /v1/runs/{id}/recover
```

```json
{"request_id":"diagnose-001","expected_version":3}
```

需要当前用户有权执行该 Run，并且运行已绑定 Worker / 原生父会话、有失败 Attempt、处于可恢复状态。
返回的 `accepted` 只是收到请求，不表示已修复。诊断期间不能重复创建诊断或改写任务；
相同 request_id 可以核实原回执。设计建议 Run 不支持此操作，请重新生成建议。

Coordinator 经同一 Worker 调用 KiroCrew 原生 `poc-coordinator`，返回 `retry`、`rework` 或 `wait_for_user`。
Engine 校验目标和运行版本，安排当前或上游任务的新 Attempt，清除受影响下游的当前验收并重新审查。
Run、父 Session、Workspace、工作流与工具配置保留。需要改变模型时由用户使用 `continue` 显式修订。
缺少源码 / 输入、权限或认证，以及明确停止不进行盲目自动循环。
详见 [架构与流程](COORDINATOR-RECOVERY.md)。

## 运维命令与幂等

```http
POST /v1/runs/{id}/stop
POST /v1/runs/{id}/close
POST /v1/runs/{id}/reopen
POST /v1/runs/{id}/delete
POST /v1/runs/{id}/assign-worker
```

所有命令都要求 `request_id` 与 `expected_version`。
`delete` 还要求 `confirmation` 为完整 Run ID；`assign-worker` 还要求 `worker_id`。

同用户、同 `request_id`、同请求返回原回执；同 ID 配不同内容返回 `409 idempotency_conflict`。
版本过时返回 `409 version_conflict`，先刷新状态再生成新命令。
Worker 派发有“先记账后执行”和唯一 Attempt 标记；回执丢失时先核实，不承诺跨两个系统绝对 exactly-once。
运行处于 `dispatch_unknown` 时不能直接续作，避免生成第二个不可核实的任务。

## Workflow 与 Team

```http
POST /v1/teams
POST /v1/teams/{id}/members
DELETE /v1/teams/{id}/members
POST /v1/workflows
POST /v1/workflows/{id}/versions
POST /v1/workflows/{id}/shares
DELETE /v1/workflows/{id}/shares
POST /v1/workflows/{id}/archive
```

创建 Team：`{"name":"Payments"}`。Owner 添加成员：`{"username":"alice","role":"member"}`。
最后一个 Owner 不能被删除或降级。

创建工作流：`{"name":"研发流程","steps":[...]}`。保存新版本额外提供 `expected_version`。
指定用户或 Team 共享：

```json
{"kind":"team","principal":"team-实际ID","role":"runner"}
```

`kind` 为 `user` 时，`principal` 使用平台用户 ID，来自 `GET /v1/users`。
仅 Owner 可管理共享，editor 不能继续转授权。
归档使用 `{"expected_version":2,"archived":true}`；恢复同一路径传 `false`。
工作流的归档不删除历史 Run、版本或源码。

## MCP 接入 Coding 工具

服务端实现标准输入输出 MCP：`initialize`、`tools/list`、`tools/call`，每行 JSON-RPC。
HTTP API 可直接使用；MCP 只是让支持 MCP 的 Coding 客户端更方便地调用同一组命令。

使用仓库提供的绝对路径启动器，无需客户端当前目录位于仓库：

```bash
python3 /ABSOLUTE/PATH/kirocrew-octopus/octopus_cli.py mcp
```

在客户端已有的 MCP 设置入口登记一个 stdio Server：

```json
{
  "command": "/ABSOLUTE/PATH/TO/python3",
  "args": ["/ABSOLUTE/PATH/kirocrew-octopus/octopus_cli.py", "mcp"]
}
```

由客户端进程的私有环境提供：

```text
OCTOPUS_URL=http://127.0.0.1:8918
OCTOPUS_API_TOKEN=<该用户的一枚 API Token>
```

这是一个 Server 定义片段；外层配置格式使用客户端已有的 MCP 管理机制。
对于 KiroCrew，可作为原生 Agent 的 MCP Server 接入；不要替换它内置的 crew MCP，也不要向普通客户端复制 Gateway 凭据。
本次已验证 MCP 协议处理与共享 HTTP 路径；没有修改用户全局 Claude/Kiro MCP 配置或声称完成所有客户端版本的自动安装。

| MCP Tool | 用途 |
|---|---|
| `octopus_workflows` | 列出可访问的工作流 |
| `octopus_match_workflow` | 预览需求匹配，或读取明确指定的工作流 / 模板 / 自定义任务 |
| `octopus_recommend_workflow` | 调用真实 Kiro Agent 推荐工作流与各 Task 提示词 |
| `octopus_workflow_recommendation` | 查询推荐状态、已校验草稿与 ACP 证据 |
| `octopus_teams` | 列出自己的 Team 与成员 |
| `octopus_runs` | 列出可访问 Session |
| `octopus_status` | Session 详情与事件 |
| `octopus_start` | 自动匹配需求，或按指定工作流 / 模板 / 定制 steps 发起研发 |
| `octopus_continue` | 原 Session 修订与续作 |
| `octopus_recover` | 原 Session 的 Coordinator 诊断、返工与复审；与 Web 使用同一 API |
| `octopus_control` | 停止、关闭、重新打开、删除 |
| `octopus_save_workflow` | 创建或保存工作流版本 |
| `octopus_share_workflow` | Owner 管理用户 / Team 共享 |

例如在 Coding 客户端中表达意图：

> 查看我可运行的工作流与 Team。使用“研发流程”处理这份需求；将选定的工具、任务说明和验收条件提交为新运行。之后查询状态并告诉我阻塞在哪个 Task。

也可以说：

> 用 octopus_match_workflow 匹配“修复登录状态丢失并补充回归测试”。先展示任务，把编码交给 Claude、审查交给 Codex；保留我手动指定的模型，确认后用定制 steps 提交到我的 Team，并持续查询状态。

客户端 LLM 负责把自然语言转成上述工具参数；服务端仍校验身份、授权、版本与工作区。
MCP Token 代表 Octopus 用户，不会自动继承当前 Claude/Kiro Chat 的原生 Session 身份。
API 创建并绑定新的平台运行及原生父对话，二者的 ID 都会返回在状态中。

## 错误处理

| 状态 / 错误 | 处理 |
|---|---|
| `401 unauthorized` | 登录或更换未过期 Token |
| `403 forbidden` / 不可访问对象 `404` | 核对 Token scopes、Team 成员与共享角色 |
| `409 version_conflict` | 刷新对象，确认差异后提交新命令 |
| `waiting_worker` | 分配当前空间的 Worker |
| `model_unavailable` | 刷新 Worker 公布目录；不猜测模型 ID |
| `model_transport_unsupported` | 当前原生 spawn 格式不支持该 ID，见 Release |
| `waiting_approval` | 原生权限流程待处理，首版由 Gateway 有权操作者处理 |
| `dispatch_unknown` | 保留原 Attempt 并核实；不自动新建重复任务 |
| `blocked` | 查看结果/证据，修订当前 Task 后续作 |
