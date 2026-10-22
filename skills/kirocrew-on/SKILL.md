---
name: kirocrew-on
description: 把开发任务交给 KiroCrew ON（octopus）后台执行、查看进度、在原 Session 上迭代并预览产出；也负责把当前 Coding 工具（Claude Code、Kiro CLI、Codex、OpenCode 等）连接到 KiroCrew ON。用户提到 KiroCrew ON / octopus、要把任务交给后台或远端做、查看 run-xxx 的进度、继续迭代或要预览链接时使用。
---

# KiroCrew ON

KiroCrew ON 在远端执行开发任务：KiroCrew 的 Kiro Agent 先识别意图、补全需求、拆分任务并分配 Coding Agent，
随后各任务在同一个 Session、同一份源码检出上依次执行，失败时自动诊断返工。你通过 MCP 服务器 `kirocrew-on`
提供的 `octopus_*` 工具与它交互；用户也会在 Web 控制台查看同一批会话。

- 平台与控制台：{{OCTOPUS_URL}}（会话链接：`{{OCTOPUS_URL}}/?run=<run_id>`）
- kirocrew-on 仓库（MCP 服务器与连接命令所在）：`{{KIROCREW_ON_REPO}}`

上面两行如果仍是 `{{…}}` 占位符，说明本 Skill 是直接复制的未渲染版本：先向用户确认平台地址和仓库路径。

## 1．先确认已连接

能看到 `octopus_status`、`octopus_start` 等工具时，跳到第 2 节。看不到时，当前客户端还没连接：

1. 不要自己读取密码文件或创建 Token。连接要用用户的账号，由用户输入密码或提供 Token。
2. 请用户在终端执行与当前客户端对应的命令（命令以用户本人的账号登录，创建一枚 30 天有效的 Token（用户可加 `--expires 7d|365d|never` 另选，管理员 Token 最长 30 天）
   ——查看、提交与管理自己的工作流——然后登出这次登录，写入 MCP 配置并安装本 Skill）：

   ```bash
   cd {{KIROCREW_ON_REPO}}
   # Claude Code（user 范围，所有目录可用）
   .venv/bin/python -m octopus connect claude --url {{OCTOPUS_URL}} --username <用户名>
   # Kiro CLI（workspace 范围，只在该项目目录生效）
   .venv/bin/python -m octopus connect kiro --url {{OCTOPUS_URL}} --username <用户名> --project <项目目录>
   # Codex（合并进 ~/.codex/config.toml）
   .venv/bin/python -m octopus connect codex --url {{OCTOPUS_URL}} --username <用户名>
   # Kiro IDE（合并进 <项目>/.kiro/settings/mcp.json，不需要 kiro-cli）
   .venv/bin/python -m octopus connect kiro-ide --url {{OCTOPUS_URL}} --username <用户名> --project <项目目录>
   ```

   - 命令会提示输入密码。不便交互时，用户可先在控制台右上角菜单「API 与 Coding Agent 接入」创建 Token，
     设为环境变量 `OCTOPUS_API_TOKEN` 后再执行，命令直接使用该 Token。
   - 平台管理员需要经 Agent 管理用户、Team 或执行环境时，可加 `--admin`：Token 额外含 admin、7 天有效。
     这枚 Token 能停用用户、更换 Team 的执行环境，只在本人设备上使用；不需要管理时用普通连接。
   - 在 Claude Code 中，可以建议用户输入 `! <命令>` 在当前会话里运行。
   - Codex、OpenCode、Kiro IDE 或其他 MCP 客户端：按 `{{KIROCREW_ON_REPO}}/docs/CONNECT.md` 手动配置。
3. 连接后要**新开一个会话（或重启客户端）**才会加载这些工具；告诉用户这一点，不要假装已经可以调用。

## 2．提交任务：`octopus_start`

- `request_id`：每个新需求生成新的 UUID；同一请求重试时沿用，后台只执行一次。
- `input`：完整需求——要做什么、约束、验收标准。用户给的仓库地址、分支与限制原样写进去。
- `source`：新项目 `{"type": "new"}`；已有仓库 `{"type": "git", "url": "<仓库地址>", "ref": "<分支，可省略>"}`。
- 默认不传 `steps` / `workflow_id` / `template_id` / `document`：由 KiroCrew 的 Kiro Agent 规划
  （Session 第 1 个任务「需求分析与任务规划」，完成后自动追加并执行生成的任务）。
- 用户已经有写好的工作流（JSON / YAML / 含一个代码块的 Markdown）时，放进 `document: {"content": "..."}` 按原样运行；
  可先用 `octopus_parse_workflow` 预览规范化结果。
- 自己写 `steps` 时，每个任务可带 `role`（`spec`、`sde`、`integration_test`、`security_test`、`challenge`、`evaluator`）：Agent 按对应的
  角色卡片工作，`kind` 仍决定门禁；审查类角色（`security_test` / `challenge` / `evaluator`）不要用实现该改动的工具（会被拒绝）。
  续作时 `execution.role` 给 `null` 或空串即去掉任务的角色（`kind` 不变）。
  还可带 `skills`（至多 4 个，取自平台技能包：`test-driven-development`、`systematic-debugging`、`verification-before-completion`、
  `receiving-code-review`、`frontend-design`）：Agent 开始前读取并按其方法工作，如界面任务带 `frontend-design`、实现任务带
  `test-driven-development`。由 KiroCrew 规划时规划者自己推荐，不必替它写。
- `worker_id` 通常省略：平台先用你自己的执行边界（Team 会话也一样），没有时用 Team 的环境或空间内唯一的环境；返回的 `placement.hint`（如「你还没有自己的执行边界…」）原样告诉用户。想先知道会在哪执行，用 `octopus_environments`（`default: true` 的那个）。
- 403 `boundary_owner_only`：会话在他人的执行边界上运行（Agent 以此人的身份执行），只有边界主人能续作、诊断、补充说明或批准；原样告诉用户，请会话创建者操作，不要换别的工具或参数重试。
- 提交到 Team 返回 409 `team_without_worker`（该 Team 尚未分配执行环境）或 `environment_disabled`（执行环境已停用）时，
  原样告诉用户需要平台管理员在控制台分配或启用执行环境；也可以建议改在个人空间提交。不要换一个 Team 重试。
- 返回 409 `team_archived` 时该 Team 已归档：不能提交新会话或续作，已有会话仍可查看与停止；告诉用户需要平台管理员恢复，或改在其他空间提交。
- 规划完成后直接开始（D70，负责人 2026-10-22）：没有「计划待确认」，不要问用户是否开始，也不要等。用户想先看计划时，
  提交后用 `octopus_status` 把任务列给他看；要修改就用 `octopus_continue`。Evaluator 判为「待人确认」的验收项记为提示，会话照常完成（D68）。
- 回执带 `request_id`（`request-…`）：每次提交登记一个 Request（来源客户端、需求与显式选择），`octopus_requests` 可查。
- 用户明确要求完成后提交 PR / MR 时传 `publish: {"auto": true}`：会话完成并通过审查后平台自动推送 `octopus/<run>` 分支并创建 Draft PR / MR
  （需要绑定有写权限的凭证）。没有要求时不传，完成后先问用户，同意后再用 `octopus_publish`。不要安排 Agent 自己推送或建 PR 的任务——Agent 没有仓库写凭证。
  会话中途可用 `octopus_publish_policy {run_id, auto}` 开关（用户同意后）。
- 需求要求**新建仓库**（会话的源码是新建的）时：用户明确给出 owner 与仓库名时，`octopus_start` 传
  `publish: {"auto": true, "repository": {"owner": …, "name": …}, "credential": "<凭证 ID>"}`（凭证须有写权限、范围覆盖该仓库、能建仓库；
  默认建私有仓库），完成后平台建仓库、基线进 main、工作走 Draft PR；用户没给出时不传，完成后先问用户仓库名与凭证，同意后
  `octopus_publish {run_id, repository, credential}`。不要安排 Agent 自己建仓库或推送。
- 回执只代表登记成功。把 `run_id` 和会话链接告诉用户。

## 3．查看进度：`octopus_status`

- 传 `run_id`，默认返回精简视图：`session.status`、`session.version`、`attention`（暂停原因与下一步）、`planning`（意图与补全后的需求）、
  `recovery.latest`（最近一次纠错决定）、各任务 `status` 与最近一次尝试 `latest`（`decision`、`summary`、`findings`、交接文件），以及最近 10 条事件。
- 需要某次尝试的原始输出或完整证据时才传 `detail: true`；完整记录可能有十几万字符，超出客户端的输出上限。
- 任务通常需要几分钟到几十分钟；每 30–60 秒查询一次，向用户汇报真实状态。状态不是 `completed` 时不要宣称完成。
- `octopus_runs` 列出用户可见的会话（最新的在前，每个只有状态、进度、需要处理的事与错误首行；默认 20 个，`status` 过滤；还可按 `space`、`repo`（如 `github.com/o/r`）、`branch`、`stage`（当前进行到的任务类别，`done` 为全部验收）、`tool`、`client`、`submitter`、`since` / `until`（`YYYY-MM-DD`）组合过滤；`q` 为全文搜索（需求、规划、任务说明、最近的结论与错误，多个词须全部命中）；`more: true` 时缩小条件或加大 `limit`）；详情用 `octopus_status`；`octopus_me` 返回当前身份、是否管理员、Token scope 与所在 Team。
- `octopus_requests` 列出用户提交过的需求（Request：来源客户端 `client`、需求前 200 字、对应会话；`status`、`client`、`q`
  过滤，`more` 同上）。`octopus_policies`（可传 `team_id`）只读地列出用户的映射偏好；偏好只能由用户在控制台维护。
- 别人共享给用户的会话（D64）：`octopus_runs` 加 `shared: true` 列出；`octopus_status` 的 `access` 说明用户以什么身份访问（`via: share` 时 `role` 为 `viewer` 只能查看、`collaborator` 可续作与停止）。查看者不要尝试续作、停止或批准；共享只能由会话创建者、Team owner 或平台管理员在控制台设置，不要替用户共享。
- `octopus_quotas` 只读：用户与其 Team 同时运行的任务数上限与占用。「需要处理」写「排队：已达到并发上限」时无需重新提交：有任务结束后按排队先后自动开始；上限由平台管理员或 Team owner 在控制台设置，不要替用户改。
- `octopus_changes`（传 `run_id`）列出会话的变更记录：谁（用户与客户端，或 Engine / Worker / KiroCrew / 协调者）在什么时候改了什么、
  前后值与原因；`who: "user"` 只看人的操作。用户问“谁停止了这个会话”“上一轮改了什么设置”时用它。
- `octopus_revisions`（传 `run_id`）列出修订历史（创建、规划、续作、自动纠错、平台追加的 Evaluator / Knowledge 任务）；加 `to`（以及可选的 `from`）返回两版之间需求与各任务的变化：工具、模型等字段的前后值，以及 Prompt 新增（+）与删除（-）的行。用户问“上一轮改了什么”时用它。
- `octopus_observe`（传 `run_id`）读取原生 KiroCrew 会话里协调者与子 Agent 的最近对话（默认 20 条，单条超过 4000 字截断；`has_more` 为 true 时传 `before: next_before` 读更早的），用来向用户说明 Agent 正在做什么；不需要打开 KiroCrew。

- 门禁（D37，新会话）：每个任务的 `latest.gates` 给出通过的个数、失败的门禁（`failed`，带原因首行）与跳过的门禁。
  - 改代码的任务结束后，平台在会话分支上留下候选提交（`latest.candidate`）。
  - 平台亲自运行任务的 `verify` 检查命令：`checks_pass` 失败时会自动返工，把失败的命令与原因告诉用户。
  - 审查任务须在 `outcome.json` 写 `reviewed_commit`，且不修改代码（`review_bound`）。
  - `session.verified` 是将被发布的已验证候选。

- `octopus_report`（传 `run_id`）返回会话的 Feature 报告（Markdown）：需求与计划、各任务的工具与结论、门禁、候选、检查命令、
  审查绑定、纠错记录与发布的 PR / MR。用户要「报告」「总结这个会话」或交付说明时，把它（或其中要点）交给用户。

常见状态：

| 状态 | 含义与处理 |
|---|---|
| `running` / `diagnosing` | 正在执行或自动诊断返工，继续等待 |
| `waiting_worker` | 还没有 Worker 执行，或原来的执行环境已更换、已移出该空间、已停用或已退役（`error` 写明原因）：把 `attention.title` / `attention.action` 告诉用户。`attention.workers` 为 0 时该空间没有可用的执行环境（尚未分配或已停用），请管理员分配或启用，或改在个人空间重新提交；大于 0 时用 `octopus_environments`（传 `run_id`）列出这个会话可分配的环境，经用户确认后用 `octopus_assign_worker` 分配 |
| `blocked` / `failed` | 读 `attention.title` / `attention.action` 告诉用户；可用 `octopus_recover` 诊断修复，或用 `octopus_continue` 修改后重跑。`attention.title` 为「需要换用工具或模型」时，向用户说明原因（`attention.detail`）并建议换用一个可用的工具；经用户同意后用 `octopus_continue` 的 `execution.tool` / `execution.model` 续作，不要自行决定 |
| `waiting_approval` | 原生任务在等待批准一次工具调用：用 `octopus_approvals` 列出，把工具、完整的命令或文件与用途告诉用户；用户同意后用 `octopus_approve` 答复（approve），不同意则 reject。不要替用户决定 |
| `awaiting_acceptance` | 验收待确认：改代码的会话由 Evaluator 逐项验收，它把需要人判断的项（Evaluator 任务的 `latest.pending`：验收项与证据）留给用户；把这些项列给用户，同意后用 `octopus_accept {run_id, request_id, expected_version}`，不同意时用 `octopus_continue` 返工：`task_id` 用 Evaluator 之前那个改代码的任务（kind 为 implement / deliver），把没通过的项写进它的 `prompt`；不要替用户确认 |
| `completed` | 完成；用 `octopus_preview` 给出预览。`session.knowledge` 列出本次沉淀的项目知识（见下） |

## 4．迭代：`octopus_continue` / `octopus_recover`

- 在原 Session 与原 Workspace 上修改需求或某个任务，并重跑该任务及其后续任务：
  `run_id`、新的 `request_id`、`expected_version`（取自最近一次 `octopus_status` 的 `session.version`）、`task_id`、
  更新后的 `input`；可选 `execution`（`name`、`prompt`、`tool`、`model`、`effort`，以及任务契约 `acceptance`、`verify` 等），
  工具本身失败时，自动纠错会请 KiroCrew 协调者推荐换用的工具（`octopus_status` 的 `latest.switched_from` 写明从哪个工具换来、原因）；
  会话关闭了换工具（`recovery.switch_tools: false`）或协调者等待用户时，由你向用户建议并经同意后续作。
  `artifacts_locked`（改了此前已验收任务的文件）时，协调者可为失败的任务扩大可改动路径（修订摘要「扩大「X」的可改动路径」）；它没有这样做而在等待时，经用户同意后用 `octopus_continue` 的 `execution.paths` 补上这些文件。
  自动纠错的返工会接着该任务上一轮的原生对话做（保留上下文）：`latest.continued: true` 表示这一次接上了——它与你的 `octopus_continue`（续作）无关；`latest.continue_refused` 是没能接上的原因，这一次从新对话开始。
- 只重跑受影响的任务：用 `rerun` 列出这次修改会影响的后续任务（例如只改前端时不列后端），省略则重跑之后的全部任务。
  依赖重跑任务的任务、其后的审查 / 测试任务、尚未完成的任务会由平台自动一并重跑；其余沿用上一版结果。
  回复用户时说明重跑了哪些任务、沿用了哪些（`octopus_status` 的修订摘要与 `kept`）。
- 任务暂停（`blocked` / `failed` / `stopped`）时，可用 `octopus_recover` 请 KiroCrew 协调者按失败证据诊断并安排修复。

## 沉淀的项目知识

- 来自仓库、改了代码的会话在专属执行环境中完成前，平台追加「Knowledge 沉淀」任务（`mapping`「平台追加（沉淀项目知识）」），
  把长期有效的项目知识（约定、决策、踩过的坑）写成该执行环境 KiroCrew 的 lessons，只给同一仓库、同一空间今后的任务。
  **不要自己规划 knowledge 角色的任务**，也不要替平台写 lessons。
- `octopus_status` 的 `session.knowledge.items` 列出每条及结果（`written` / `superseded` / `refused` / `skipped` / `removed` 与 `reason`）；
  `note` 为「共享执行环境不沉淀、不注入知识」时，该会话在共享环境中，什么也不写也不注入。
- 用户认为某条不对时，请其在控制台会话页「本次沉淀的知识」中删除（创建者、Team owner、管理员）；删掉的条目以后不会再写回。

## 给运行中的任务补充说明

- 用户要纠正或补充一个**正在运行的 Kiro 任务**时，用 `octopus_steer {run_id, task_id, message}`，说明插入它正在进行的这一轮。把要发送的原文给用户确认后再发；
  只能由会话创建者、Team owner 或管理员发送，写入审计与时间线。
- `409 not_running`：任务已结束，改用 `octopus_continue`（尚未开始时等它开始）；`steer_unsupported`：任务不是 Kiro 后端（claude / codex 不支持），
  或执行环境的 Worker 尚未升级——等它结束后用 `octopus_continue` 修改。

## 批准原生工具请求

- 会话为 `waiting_approval`，或原生父会话想运行工具时，`octopus_approvals {run_id}` 列出待批准项（`origin`、`tool`、`tool_input`、`tool_purpose`）。
- 先把工具、**完整的**命令或文件与用途展示给用户，得到明确同意后再调用
  `octopus_approve {run_id, origin, request_id, request_mid | instance, decision: "approve" | "reject"}`（标识取自列表）。
- 答复只能由会话创建者、Team owner 或管理员做出，并写入审计；`409 approval_not_pending` 表示该请求已被答复或过期，重新列出。
- `truncated: true` 的内容已截断：告诉用户看到的不是全部，需要时请其在原生 KiroCrew 中查看完整内容后再决定。
- `supported: false` 表示执行环境的 Worker 尚未升级、看不到待批准项（不代表没有）：请用户在原生 KiroCrew 中处理。
- 用户也可以在控制台会话页的「待批准」卡片中答复。

## 私有仓库与凭证

- 私有仓库需要代码仓库凭证（GitHub fine-grained Token、GitLab Token）。凭证只能由用户在控制台「设置 → 代码仓库凭证」创建：
  **不要向用户索要 Token**，不要把 Token 写进对话、命令或项目文件。
- `octopus_credentials`（可传 `host`）列出可用的凭证（只有名称、归属与末 4 位）。提交时可以用 `credentials: {"source": "<ID>"}`
  指定；不指定时，平台按仓库自动匹配：Team 会话依次为 Team 凭证、授权给 Team 的平台凭证、提交者个人凭证。
- 409 `credential_ambiguous`：有多枚同样适用的凭证，`error` 里列出了名称，请用户选一枚后用 `credentials.source` 重新提交。
- 403 `credential_forbidden` / 409 `credential_scope`：指定的凭证不能用于这个仓库（不是你的、未授权、主机或仓库范围不符）。
- 会话停在「仓库凭证不可用」：绑定的凭证被停用、删除、授权撤销或所有者已离开，请用户在会话页「更换凭证」，
  或用 `octopus_continue` 的 `credentials: {"source": "<ID>"}` 先换凭证再续作。
- 发布：会话结束后，用户同意时调用 `octopus_publish`（`run_id`，可选 `branch`、`title`），把成果推送到 `octopus/<run-id>` 并创建或沿用
  Draft PR / MR，把返回的链接告诉用户。需要会话绑定一枚写权限的凭证（409 `credential_read_only` 时请用户换一枚）；
  409 `publish_secret_found` 表示改动或已提交的历史中有疑似凭证（或提交了 `.env`），按列出的文件处理后再发布——在历史中的
  需改写这些提交（错误里给出做法）；409 `publish_conflict` 表示远端分支被他人改动，不要强推，改用新的 `branch` 或请用户合并。
  409 `credential_permission` 表示令牌缺少权限：请用户在托管方给令牌补上（GitHub 细粒度令牌需对该仓库有 Contents 与 Pull requests
  读写，GitLab 需 `api` / `write_repository`），令牌值不变，无需重新录入；错误写着「分支 … 已推送，但 Draft PR / MR 未创建」时，
  分支已在远端，补上权限后再次发布会沿用该分支。

## 5．预览产出

- 想在本机运行、查看或继续开发：`octopus_download`（传 `run_id`，可选 `dest`）把源码（含 `.git`，不含依赖目录与 `.env`）
  下载解压到本机目录，返回启动步骤 `start`。征得用户同意后按步骤安装依赖并启动，把 `localhost` 地址告诉用户；
  不要把任何凭证写进下载的项目。下载链接 10 分钟有效，用户也可以在控制台会话页点「下载源码」。
- 想在浏览器里看到运行起来的应用（开发服务器、前后端两个进程都可以）：`octopus_app`（`run_id`、`action`）。
  - `start` 后每 10 秒左右 `status` 一次，直到 `running` 或 `failed`。安装依赖可能要几分钟。
  - `failed` 时把 `error` 与日志要点告诉用户，并建议如何修改 `.octopus/preview.json`（入口进程的 `port` 须为 `"$PORT"`）。
  - `running` 后 `open`，把链接交给用户本人：10 分钟内有效、只能打开一次，打开后浏览器保持 8 小时；不要转发到公开渠道。
  - 看完后 `stop`。每个执行环境最多 3 个（409 `app_limit`）；会话执行任务时不能启动（409 `app_busy`），续作或关闭会话会停止预览；
    空闲 30 分钟自动停止。409 `app_unsupported` / `app_preview_unconfigured` 表示执行环境或平台尚未支持，改用 `octopus_download`。
- `octopus_preview`（传 `run_id`）返回 Web 预览链接：`url` 打开最合适的一页，`pages` 列出会话产出的全部 HTML 页面（如 `report/`、`artifacts/<名称>/` 下的报告）及各自的链接，都给用户。
- 链接 10 分钟有效，每次打开都核对你仍能查看该会话；返回 403（`preview_expired` / `preview_invalid` / `preview_forbidden`）时重新调用 `octopus_preview` 取新链接（`preview_forbidden` 表示已无权查看，告诉用户）。
- 预览在与控制台隔离的沙箱中运行：页面里的 `localStorage`、`sessionStorage`、IndexedDB 与 Cookie 都不可用。
  依赖它们的功能（如保存设置、计数）在预览中失效不代表产出有缺陷；要验证这类功能，用 `octopus_download` 下载到本机打开，
  或用 `octopus_app` 在执行环境启动应用。

## 本地与远端联动：`octopus_pull` / `octopus_sync`

- 在用户的 Git 仓库里提交时（`octopus_start` 的 `source` 就是这个仓库的 origin），本地有 origin 没有的提交会先停下，返回
  `unpushed` 与 `hint`：把提交数告诉用户；用户同意推送才带 `push_base: true` 重新提交，或带 `allow_unpushed: true` 按 origin 已有的提交提交。
  提交成功后仓库自动关联会话（`.octopus/session.json`，不会提交）；`octopus_status`、`octopus_pull`、`octopus_sync` 可省略 `run_id`。
  别的目录用 `octopus_link {run_id, dir}` 关联。
- 想在本机接着改：`octopus_pull`（`checkout: true` 切换到本地分支 `octopus/<run>`），上下文（报告、计划、各任务交接）在
  `.octopus/sessions/<run>/`。在这个分支上修改并 **提交** 后 `octopus_sync`（`note` 写给审查者）：平台把它作为新候选，重新运行检查，
  其后的审查 / 测试任务重新执行；会话页的修订历史记「本地同步」。同步前把要同步的提交告诉用户并得到同意。
- `octopus_pull` 返回 `diverged: true` 时（本地分支与会话分支都有新提交）：把 `hint` 告诉用户，在本地分支上 `git rebase <remote_ref>`
  （或 merge）后再 `octopus_sync`；返回 `ahead` 表示只有本地领先，可直接同步。`octopus_start` 返回 `missing_branch` 时当前分支不在 origin 上。
- 拒绝时原样告诉用户：`sync_not_fast_forward`（远端又有了新提交：先 `octopus_pull` 再改）、`sync_secret_found` / `bundle_secret_found`
  （提交里有疑似凭证或 `.env`：用户自己移除后再提交）、`sync_dirty`（会话的工作区还有未纳入候选的改动：等会话结束或续作）、`busy`。
- 用户也可以用命令行：`octopus login`，之后 `octopus submit / status / plan / pull / sync / watch / search …`。

## 6．原则

- 停止、关闭、删除会话前先征得用户同意（`octopus_control`）。
- 只报告工具返回的真实状态与证据，不编造执行结果；工具报错时原样说明错误。
- 工具出错时返回结构化的 `{status, code, error}`：
  - 401：Token 失效或已撤销，请用户重新执行第 1 节的连接命令；
  - 403：能看到但当前角色或 Token scope 不允许——把 `error` 原样告诉用户，并说明需要谁授权（Team owner、平台管理员，
    或用 `--admin` 重新连接），不要换个方式重试；
  - 404：对象不存在或你无权查看，不要猜测其他 ID；
  - 409：状态不允许（如 `team_archived`、`environment_changed`、`busy`），按 `error` 与会话的 `attention` 处理；
  - 503 `worker_unavailable`：执行环境不可达（Worker 停机、证书或网络问题），重试无效——请平台管理员检查该执行环境；
  - 502 / 503 / 504 或 `http_error`：平台暂不可达，稍后重试；
  - `status` 为 0、`code` 为 `client_error`：请求没有到达平台（参数缺失或格式不对、本机网络不通）——按 `error` 修正参数，
    或请用户检查网络，不要原样重试。
- 管理员工具（`octopus_admin_users`、`octopus_admin_teams`、`octopus_admin_environments`）只在 Token 含 admin 时出现；
  只在用户明确要求时使用，执行前复述影响并征得同意（停用用户会立即吊销其全部 Token；设为共享环境后环境内会话互相可读）。
  这些工具不创建用户、不设置密码，需要时请用户在控制台「用户管理」操作。
