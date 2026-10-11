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
- `worker_id` 通常省略：空间内只有一个可用 Worker 时自动使用。
- 回执只代表登记成功。把 `run_id` 和会话链接告诉用户。

## 3．查看进度：`octopus_status`

- 传 `run_id`，读取 `session.status`、各任务 `status`、`planning`（意图与补全后的需求）、`attention`（暂停原因与下一步）和最近事件。
- 任务通常需要几分钟到几十分钟；每 30–60 秒查询一次，向用户汇报真实状态。状态不是 `completed` 时不要宣称完成。
- `octopus_runs` 列出用户可见的全部会话。

常见状态：

| 状态 | 含义与处理 |
|---|---|
| `running` / `diagnosing` | 正在执行或自动诊断返工，继续等待 |
| `waiting_worker` | 还没有 Worker 执行：把 `attention.title` / `attention.action` 告诉用户。`attention.workers` 为 0 时该空间没有执行环境（常见于尚未分配执行环境的 Team），请管理员分配或改在个人空间重新提交；大于 0 时请用户选择，或在 `octopus_start` 中指定 `worker_id` |
| `blocked` / `failed` | 读 `attention.title` / `attention.action` 告诉用户；可用 `octopus_recover` 诊断修复，或用 `octopus_continue` 修改后重跑 |
| `completed` | 完成；用 `octopus_preview` 给出预览 |

## 4．迭代：`octopus_continue` / `octopus_recover`

- 在原 Session 与原 Workspace 上修改需求或某个任务，并重跑该任务及其后续任务：
  `run_id`、新的 `request_id`、`expected_version`（取自最近一次 `octopus_status` 的 `session.version`）、`task_id`、
  更新后的 `input`；可选 `execution`（`name`、`prompt`、`tool`、`model`、`effort`），例如某个工具失败时换用 `claude`。
- 任务暂停（`blocked` / `failed` / `stopped`）时，可用 `octopus_recover` 请 KiroCrew 协调者按失败证据诊断并安排修复。

## 5．预览产出

- `octopus_preview`（传 `run_id`）返回 Web 预览链接，静态页面可在浏览器中直接打开。
- 需要运行开发服务器的项目：在 kirocrew-on 仓库执行 `deploy/aws/deploy.sh space <run_id>`，
  在本机 `localhost:<远端端口+10000>` 预览。

## 6．原则

- 停止、关闭、删除会话前先征得用户同意（`octopus_control`）。
- 只报告工具返回的真实状态与证据，不编造执行结果；工具报错时原样说明错误。
- 返回 401 / Token 失效时，请用户重新执行第 1 节的连接命令；返回「API 暂不可用」时，说明是平台不可达，稍后重试。
