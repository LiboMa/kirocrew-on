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
2. 请用户在终端执行与当前客户端对应的命令（命令会登录并创建一枚 30 天有效、只有读取与执行权限的 Token，
   写入 MCP 配置并安装本 Skill）：

   ```bash
   cd {{KIROCREW_ON_REPO}}
   # Claude Code（user 范围，所有目录可用）
   .venv/bin/python -m octopus connect claude --url {{OCTOPUS_URL}}
   # Kiro CLI（workspace 范围，只在该项目目录生效）
   .venv/bin/python -m octopus connect kiro --url {{OCTOPUS_URL}} --project <项目目录>
   ```

   - 命令会提示输入密码。不便交互时，用户可先在控制台右上角菜单「API 与 Coding Agent 接入」创建 Token
     （权限选「读取与提交开发」），设为环境变量 `OCTOPUS_API_TOKEN` 后再执行，命令直接使用该 Token。
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
- `worker_id` 通常省略：空间内只有一个可用 Worker 时由后台自动选用。
- 提交到 Team 返回 409 `team_without_worker`（该 Team 尚未分配执行环境）或 `environment_disabled`（执行环境已停用）时，
  原样告诉用户需要平台管理员在控制台分配或启用执行环境；也可以建议改在个人空间提交。不要换一个 Team 重试。
- 返回 409 `team_archived` 时该 Team 已归档：不能提交新会话或续作，已有会话仍可查看与停止；告诉用户需要平台管理员恢复，或改在其他空间提交。
- 回执只代表登记成功。把 `run_id` 和会话链接告诉用户。

## 3．查看进度：`octopus_status`

- 传 `run_id`，默认返回精简视图：`session.status`、`session.version`、`attention`（暂停原因与下一步）、`planning`（意图与补全后的需求）、
  `recovery.latest`（最近一次纠错决定）、各任务 `status` 与最近一次尝试 `latest`（`decision`、`summary`、`findings`、交接文件），以及最近 10 条事件。
- 需要某次尝试的原始输出或完整证据时才传 `detail: true`；完整记录可能有十几万字符，超出客户端的输出上限。
- 任务通常需要几分钟到几十分钟；每 30–60 秒查询一次，向用户汇报真实状态。状态不是 `completed` 时不要宣称完成。
- `octopus_runs` 列出用户可见的全部会话。
- `octopus_revisions`（传 `run_id`）列出修订历史（创建、规划、续作、自动纠错）；加 `to`（以及可选的 `from`）返回两版之间需求与各任务的变化：工具、模型等字段的前后值，以及 Prompt 新增（+）与删除（-）的行。用户问“上一轮改了什么”时用它。
- `octopus_observe`（传 `run_id`）读取原生 KiroCrew 会话里协调者与子 Agent 的最近对话（默认 20 条，单条超过 4000 字截断；`has_more` 为 true 时传 `before: next_before` 读更早的），用来向用户说明 Agent 正在做什么；不需要打开 KiroCrew。

常见状态：

| 状态 | 含义与处理 |
|---|---|
| `running` / `diagnosing` | 正在执行或自动诊断返工，继续等待 |
| `waiting_worker` | 还没有 Worker 执行，或原来的执行环境已更换、已移出该空间、已停用或已退役（`error` 写明原因）：把 `attention.title` / `attention.action` 告诉用户。`attention.workers` 为 0 时该空间没有可用的执行环境（尚未分配或已停用），请管理员分配或启用，或改在个人空间重新提交；大于 0 时请用户选择，或在 `octopus_start` 中指定 `worker_id` |
| `blocked` / `failed` | 读 `attention.title` / `attention.action` 告诉用户；可用 `octopus_recover` 诊断修复，或用 `octopus_continue` 修改后重跑 |
| `completed` | 完成；用 `octopus_preview` 给出预览 |

## 4．迭代：`octopus_continue` / `octopus_recover`

- 在原 Session 与原 Workspace 上修改需求或某个任务，并重跑该任务及其后续任务：
  `run_id`、新的 `request_id`、`expected_version`（取自最近一次 `octopus_status` 的 `session.version`）、`task_id`、
  更新后的 `input`；可选 `execution`（`name`、`prompt`、`tool`、`model`、`effort`），例如某个工具失败时换用 `claude`。
- 任务暂停（`blocked` / `failed` / `stopped`）时，可用 `octopus_recover` 请 KiroCrew 协调者按失败证据诊断并安排修复。

## 5．预览产出

- `octopus_preview`（传 `run_id`）返回 Web 预览链接，静态页面可在浏览器中直接打开。
- 链接 10 分钟有效，每次打开都核对你仍能查看该会话；返回 403（`preview_expired` / `preview_invalid` / `preview_forbidden`）时重新调用 `octopus_preview` 取新链接（`preview_forbidden` 表示已无权查看，告诉用户）。
- 预览在与控制台隔离的沙箱中运行：页面里的 `localStorage`、`sessionStorage`、IndexedDB 与 Cookie 都不可用。
  依赖它们的功能（如保存设置、计数）在预览中失效不代表产出有缺陷；要验证这类功能，把文件下载到本机打开，或用下面的开发服务器方式。
- 需要运行开发服务器的项目：在 kirocrew-on 仓库执行 `deploy/aws/deploy.sh space <run_id>`，
  在本机 `localhost:<远端端口+10000>` 预览。

## 6．原则

- 停止、关闭、删除会话前先征得用户同意（`octopus_control`）。
- 只报告工具返回的真实状态与证据，不编造执行结果；工具报错时原样说明错误。
- 返回 401 / Token 失效时，请用户重新执行第 1 节的连接命令；返回「API 暂不可用」时，说明是平台不可达，稍后重试。
- 返回 404 表示对象不存在或你无权查看；返回 403 表示能看到但当前角色或 Token scope 不允许此操作——把消息原样告诉用户（例如「只有 Team owner 可以删除他人创建的会话」），不要换个方式重试。
