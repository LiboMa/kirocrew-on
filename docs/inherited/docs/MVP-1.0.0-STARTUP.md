# MVP-1.0.0 启动与操作

本版入口是 Web / HTTP API。下面的命令用于管理员启动服务，不要求开发者编辑 JSON 来提交研发需求。
代码在 `octopus/`，与原来的 8917 工作台并存。不要删除或重置旧 KiroCrew HOME、Session、Workspace。
本轮分支为 `codex/coordinator-recovery`；登录页与顶部均保留“旧版工作台”入口。
推荐采纳、自动纠错及状态解释的 [完整流程与架构图](COORDINATOR-RECOVERY.md) 单独说明。

本地统一入口：双击仓库根目录的 [`打开工作台.command`](../打开工作台.command)，
或打开 [`入口.html`](../入口.html)。三个应用的独立 macOS 快捷方式放在 [`入口/`](../入口/)。
入口只打开页面；新版 API、Worker 与 Gateway 的启动方式见下文。

## 本机已启动的环境

| 服务 | 地址 / 位置 |
|---|---|
| 新版 Team 工作台与 API | `http://127.0.0.1:8918/` |
| 新版个人验证 Worker | `http://127.0.0.1:8928/` |
| 复用的原生 KiroCrew Gateway | `http://127.0.0.1:5476/` |
| 原工作台 | `http://127.0.0.1:8917/`，保持独立 |
| 新版用户与运行数据库 | `state/mvp-1.0.0/octopus.sqlite3` |
| 本机初始管理员 | `malibo`；密码在本机私有的 `state/mvp-1.0.0/bootstrap.json` |
| Worker 凭据 | `state/mvp-1.0.0/worker.token`；不要提交或发给 Coding Agent |
| 本机验证 Workspace 根目录 | `/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc/state/runs/mvp-1.0.0-worker` |

本机 Worker 位于现有 Gateway 已允许的研发目录下，用一个新的子目录保存本版运行。
`bootstrap.json` 和 Worker Token 文件权限为 `0600`，运行数据目录不纳入 Git。
初始账户并非原 KiroCrew 登录账户；新版暂不迁移旧会话归属。旧会话继续在原 App / 工作台访问。

## 从干净目录启动

要求：POSIX 环境（本次验证为 macOS）、Python 3.10+、Git；Worker 所在机器另需 KiroCrew 与相应 ACP Harness。
平台 API 本身使用 Python 标准库，不依赖 Redis、消息队列或独立向量数据库。

在仓库根目录执行一次：

```bash
python3 -m octopus init --username admin --data state/mvp-1.0.0
```

命令交互读取至少 12 位密码。已有用户时会拒绝覆盖；后续在 Web 的“团队与成员”中由管理员创建用户。

启动平台：

```bash
python3 -m octopus serve --data state/mvp-1.0.0 --port 8918
```

另一个终端启动 Worker。本机复用现有 Gateway 的实际命令：

```bash
python3 -m octopus worker \
  --data /Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc/state/runs/mvp-1.0.0-worker \
  --token-file state/mvp-1.0.0/worker.token \
  --gateway-port 5476 \
  --routing-log /Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-app-poc/evidence/provider-routing.jsonl \
  --isolation trusted-local \
  --port 8928
```

Worker Token 文件不存在时自动生成。启动日志不输出 Token。已有文件与运行不会被覆盖。
将 Worker 登记在工作台“执行环境”中：名称、URL、Token、绑定的用户或 Team。登记需要平台管理员权限。

## KiroCrew 底座要求

本机 Gateway 已按 0.8.0 重启，并通过四后端工厂路由、模型 / Effort 和原生 MCP 身份挂载的六项契约检查。
版本检查集中于 `poc.SUPPORTED_VERSIONS`；App 更新后应核对实际安装版本，不能只看此前仍在运行的进程。

本版 Worker 调用现有 HTTP 接口，不直接创建 Claude/Codex/OpenCode 进程。
Gateway 需加载项目原有的 `poc.PocProviderRegistry` 组合入口，提供以下模板映射：

| 原生 Agent 模板 | ACP backend |
|---|---|
| `poc-coordinator` | Kiro 默认 backend；新父会话与失败诊断 |
| `poc-kiro` | Kiro 默认 backend |
| `poc-claude` | `claude` |
| `poc-codex` | `codex` |
| `poc-opencode` | `opencode` |

这是原 PoC 已有的 provider factory 扩展点。单独启动一个未加载该组合入口的标准 Gateway，不会自动具备这四个模板的路由。
新安装环境先按原仓库 `docs/STARTUP.md` 完成 Harness / Gateway 安装；不要在本机正在运行的 Gateway 上重复启动占用 5476 的进程。

Worker 的 `--routing-log` 指向该 Gateway 实际写入的路由证据。
没有路由证据时不会把请求中的工具名称当成实际后端；任务即使返回也可能因无法核实后端而阻塞。
`--crew-home` 可指定该 Worker 专属的 KiroCrew 数据目录。
其 Workspace 根目录需被 Gateway 的 `agent.subagent_cwd_allowed_roots` 允许。

## 两种执行部署

**个人本机验证**

管理员个人空间可以使用同一宿主账号的 `trusted-local` Worker。
此模式复用管理员已有工具登录，不代表多租户沙箱。

**Team / 普通用户开发**

先由管理员准备独立 OS 账号、容器或机器，以及该环境自己的 Gateway、源码目录、工具认证、Skills 与 MCP 配置。
Worker 使用 `--isolation dedicated`，登记时绑定 Team 或普通用户。

- `dedicated` 是部署声明，不会自动创建容器或隔离文件系统。
- Team Worker 不能运行在平台 API 的同一个宿主账号下。
- 不同用户 / Team 不能复用同一个宿主账号；仅更改 HOME 路径不足以隔离。
- Worker 端口及 Token 只面向平台控制面；Coding 用户使用 Octopus 用户 Token。
- 不向普通用户下发 Gateway owner cookie、Worker Token 或平台数据库。
- 团队成员能共享该 Team 的运行、交接与 Workspace，视为同一可信开发团队。
- 本次未部署独立 Team 容器或 EC2；不能据此声称跨主机隔离已完成验收。

平台及 Worker 默认监听 loopback。跨机器部署使用 HTTPS 反向代理与受控网络，才配置远程 URL。
Python 内置 HTTP 服务不提供 TLS 终结、SSO 或互联网入口防护，这些属于部署层的后续验收项。

## 开发者操作

1. 登录后选择“我的开发空间”或一个 Team。
2. 点击“发起开发任务”，先在唯一的需求输入框描述目标；不需要先创建工作流。
3. 系统即时匹配模板并填写名称。默认编排方式是“Agent 推荐编排 → 审阅并采用”：点击“让 KiroCrew 推荐”，审阅结果后采用；也可明确选择“手动编排 / 直接采用预设任务”。本版共 15 个模板。
4. 展开任务可改名称、说明、工具、模型和 Effort，也能增加、移除、排序。明确选择“创建全新项目”或“开发已有 Git 仓库”，不能空选。确认 Worker 和自动纠错轮次后，点击“确认任务并提交”。
5. 未分配 Worker 时需求进入等待状态；分配后才准备源码和派发。
6. 中间 Tasks 显示真实状态，进度按已交接 Task / 总 Task 计算，没有单任务伪百分比。
7. 打开“执行证据与历史 Attempt”查看请求配置、实际后端、实际模型、原生 ID 与结果。
8. 新业务运行默认允许两轮 Coordinator 自动诊断。审查不通过时可以退回编码并复审；缺少源码、认证或明确停止时先说明原因。已暂停运行可点“诊断并修复”，或“配置并原地重跑”改任务、模型与纠错策略；保留原 Run / Session 与 Workspace。

需求示例：“修复登录失败的 Bug”匹配修复流程；“进行安全审计”匹配审查流程；
“快速验证一个最小想法”匹配最小实现与审查。也可以在需求中写出自己有权运行的工作流完整名称。
同名工作流同时匹配时需在下拉列表选定具体对象。Viewer 共享权限不足以运行，不能自动匹配为可执行流程。

即时匹配是可解释的关键词 / 模板规则，不是后台 LLM 生成。匹配阶段不创建 Workspace、Session 或 Coding 任务；
正式提交仍经过同一 Team API、源码准备与 KiroCrew ACP 调度。需求里明确写出的 GitHub / GitLab HTTPS 仓库 URL 可自动填入代码来源，用户可修改确认；不从文字猜测宿主机目录。

手动选择的流程不会被需求变化替换；修改任务后保留自定义配置。草稿按当前用户、空间和来源 Session 保存在浏览器当前标签页，
刷新后可恢复；不同 Session 互不混用。迟到的匹配响应不能覆盖新输入。

从工作流库打开一个流程后，可点“用此工作流发起任务”；Session 的任务列表也可“按此任务列表发起新开发”，
复制当前 Task 配置到新草稿，不修改原运行。要保留当前 Session / Workspace，请使用“继续此 Session”。
Chat 客户端可先调用 `octopus_match_workflow`，手动指定工作流或自行编排 `steps`，再调用 `octopus_start`。

## 由 KiroCrew 推荐工作流与提示词

“工作流库 → ＋ → 按需求让 KiroCrew 推荐”和“发起开发任务”均可使用后台推荐：

1. 填写需求，选择一个模板或编辑当前任务作为设计基线。
2. 在“生成建议的 Worker”中选择当前空间的执行环境，点击“让 KiroCrew 推荐”。
3. 后台通过现有 Engine / Worker / KiroCrew SubagentManager 发起真实 Kiro 设计任务。
4. 查看状态、原生 Task ID 和推荐内容；模型没有报告时显示“未报告”。
5. 点击“采用建议”，填入工作流名称、任务、工具、模型 / Effort 与各 Task 的具体提示词。
6. 继续手动修改。“由需求设计工作流”保存为可复用定义；“发起开发任务”提交本次开发。

**模板预设随时可用**：即使尚未输入需求或没有可用 Worker，选择模板也会立即显示它自己的 Task 与 Agent 提示词。
填写需求后，模板提示词会带入本次目标和步骤位置；完整需求另随上下文传递。
后台推荐进一步根据需求和当前基线改写任务的输入、实施要求、产物和验证方法。
手动编辑后进入自定义模式，不自动覆盖用户文字。

推荐结果不会自动采用或自动保存。生成期间改变了需求、任务或执行空间，旧建议会标为过期，禁止覆盖新草稿。
关闭窗口不会取消后台任务；原草稿保留请求 ID 和结果位置，重新打开可继续查询。需要取消时点“停止生成”。
每次推荐也是一条标注“工作流建议”的平台 Session，可以在列表中查看执行证据；旧建议保留，不作为可重复续作的业务任务。

此路径复用旧版 `development_prompts.intent_prompt` 和 `workflow_files.parse_document`。
Team 版使用当前空间的 Worker 身份，不调用旧版全局身份的 `/api/design`。
API 和 Worker 都需要更新；旧 Worker 会返回 `worker_upgrade_required`，不会悄悄忽略设计基线。

修改工作流库会创建新版本，不修改正在执行的运行。
续作是显式的 Session 修订：选中 Task 与下游 Task 重跑，上游交接保留，历史 Attempt 不覆盖。
首版每个 Attempt 使用新的原生子任务；平台 Session 保持稳定，并不意味着复用了同一个工具内部 Session。

## 在 KiroCrew 中打开 Session

在左侧选中一条 Session，点击任务进度下方的 **“在 KiroCrew 中打开 ↗”**。
系统在新标签页完成原生登录，打开该运行已绑定的父对话；工作台保留当前选中项。
工作流推荐窗口中的 **“在 KiroCrew 查看 ↗”** 打开本次推荐任务的生成会话。

- 跳转使用已保存的 `native_parent`。平台 `run-…`、KiroCrew 父对话和 Coding Tool 内部 Session 是不同标识。
- 未分配 Worker / 原生对话尚未建立时入口禁用；已关闭的 Session 先点“重新打开”。
- 打开入口不创建 Run、Workspace 或 Task，不自动继续执行、不重跑任务。
- Worker 连接失败时显示错误页，回到工作台可以重试；浏览器拦截新标签页时会提示放行。
- API 和 Worker 都需更新。旧 Worker 明确提示升级；无需修改工作流或补写 Token。

认证复用旧版 `api.chat_entry_url` → KiroCrew `/api/token/local` → 原生 HttpOnly Cookie。
登录链接按点击生成，只用于本次导航，不写入工作台浏览器存储、数据库、事件、导出或 Agent 上下文。
原生页面负责链接换 Cookie；当前 Gateway 可能保留地址中的已消费 `token` 参数，不把“地址清理”当作认证完成条件。

本机默认入口是 `http://localhost:<gateway-port>`。
远程 Worker 需由管理员设置浏览器可访问的 HTTPS origin，例如启动 Worker 时追加：

```bash
--crew-url https://crew.example.com
```

该地址应已由部署方指向此 Worker 的原生 Gateway；此选项不部署代理或替你开放网络端口。
浏览器入口不接受 `/chat` 路径、Token 或任意重定向 URL。
原生登录权限覆盖该 Worker 的 Gateway 边界，不是单个 Run 的受限查看票据。
私人 Worker 只向所属用户开放；Team 入口只向当前成员开放并要求 Team 的专属执行边界。
从平台移除成员会阻止后续入口请求；已经建立的原生浏览器登录由 KiroCrew 自身管理，不能宣称平台撤权会自动注销它。

## 共享

打开工作流 → “共享授权” → 选择 Team 或用户：

- `viewer`：查看定义。
- `runner`：查看并创建自己的运行。
- `editor`：查看、运行、保存工作流新版本。
- Owner：额外管理授权与归档。

共享工作流不共享 Owner 的私人运行。创建运行时显式选择 Team，才让该运行对当前 Team 成员可见。
撤销成员资格会同步影响运行详情、导出与已连接的 SSE 状态流。
同一 Owner 下拒绝规范化后的重名工作流；不同 Owner 的工作流以 Owner 与 ID 区分。

## 停止、关闭、导出和删除

- **停止**：请求停止父对话与其子任务；原生报告仍在运行时继续显示“停止中”。
- **关闭**：停止后，先验证原生 gzip JSON 备份，再关闭原生 UI 会话；保留历史与源码。
- **重新打开**：调用原生 history resume；随后选择 Task 续作。
- **导出记录**：下载平台 Session、Task、Attempt 与修订记录，不是源码包。
- **删除会话**：输入完整 Session ID；先备份、关闭，再删除原生父对话历史。保留 Worker 源码、子任务证据及平台审计。
- **归档工作流**：停止从它创建新运行；原版本与历史 Session 保留。可通过 API `archived:false` 恢复。

原生明确返回 `export_bundle_empty` 时，表示只有说明性 note 等不可导出消息；
此时保存单独标记的 `empty-session-observation.json`，再关闭。它是平台观察记录，不冒充可导入的原生会话包。
其他导出错误（包括 incognito / temporary 的隐私拒绝）会阻止关闭和删除。

若原生仍有待批准的子任务，停止可能保持待确认；首版由有权操作该 Gateway 的人员在原生权限界面处理。
这里没有通过“标记成功”跳过真实进程停止，也没有提供“清空全部历史”的操作。

## 重启与备份

升级这轮功能需要更新并重启 **API 与 Worker**，继续使用原数据目录和 Token。
数据库启动时仅增加纠错记录表；不要重新 init、删除 SQLite 或清理 Workspace。
旧运行没有纠错策略时保持原状态，不会自动重试。用户明确启用或人工诊断后才使用新结果契约。
先停止平台派发，等待 Worker 当前请求结束，再重启 Worker 和平台；不要中断未知派发后另建 Attempt。

在运行平台 / Worker 的对应终端使用 Ctrl+C；重新执行同一条命令、使用同一数据目录。
不要用全局 `pkill` 停止所有 KiroCrew 或 Coding 工具进程。
平台重启后按 SQLite 记录恢复观察；Worker 日志先记录 Attempt，再调用原生接口。回执不明的派发会保持待核实。
只重启平台不会自动终止原生 Coding 任务。

运行中备份 SQLite 使用 SQLite backup API，不能只复制正在写入的主数据库文件：

```python
from pathlib import Path
import os
import sqlite3

Path("backups").mkdir(exist_ok=True, mode=0o700)
with sqlite3.connect("state/mvp-1.0.0/octopus.sqlite3") as source:
    with sqlite3.connect("backups/octopus.sqlite3") as target:
        source.backup(target)
os.chmod("backups/octopus.sqlite3", 0o600)
```

数据库包含 Worker 连接凭据与研发内容，备份按私密数据保管。
完整恢复还需要对应 Worker 的运行目录、原生 KiroCrew HOME，以及由管理员独立保管的工具认证。

## 模型能力与当前限制

模型目录来自 Worker 本机 KiroCrew 的 ACP 公布缓存，空缓存时只提供 `auto`，不猜测模型清单。
“执行环境 → 刷新模型”重新读取该 Worker 的目录。
模型公布仅表示后端列出了它，不保证账号有额度或组织允许调用。

本次发现：当前 KiroCrew `spawn` 的模型正则不接受 `/`、`:`、`[]`。
因此 OpenCode 的 `provider/model` ID 无法从此新建子任务接口直接指定；前端目录过滤这些 ID，完整公布列表仍在 capabilities 的 `advertised_models` 字段中。
OpenCode `auto` 的实际运行返回过空结果，不能当作成功交付。
这项兼容问题记录在 Release 中，待原生契约修复与再次实测后开放。

## 测试

```bash
python3 -m unittest discover -s tests -p 'test_mvp.py' -v
python3 -m unittest discover -s tests -p 'test_crew_entry.py' -v
python3 -m unittest discover -s tests -p 'test_recovery.py' -v
node --check octopus/ui/app.js
node --check octopus/ui/launch.js
node --check octopus/ui/recommend.js
node --check octopus/ui/crew.js
node --check octopus/ui/recovery-ui.js
python3 -m compileall -q octopus
```

浏览器测试使用 `tests/mvp_browser_fixture.py` 的临时 SQLite 数据库与两个测试用户，**不启动 Engine，也不调用原生 Coding Agent**。
运行该 fixture 后，依次用 Playwright 执行 `tests/mvp-team.browser.js`、`tests/mvp-intent.browser.js`。
每一轮使用新的 fixture 临时数据库。其结果不得替代真实 ACP 执行验收。
`tests/mvp-recommend.browser.js` 另外验证生成、过期保护、迟到回执、采用和工作流保存；
其中生成端点使用明确标注的模拟响应，工作流保存使用真实测试 API。真实 Kiro 推荐证据单独记录。
`tests/mvp-recovery.browser.js` 验证诊断与恢复交互、明确源码来源、采用确认和原地重跑配置。
