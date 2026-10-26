# KiroCrew 升到正式版 v0.10.0 的调研（MVP 0.2.0 P1 Task 12，S32）

日期：2026-10-26。

**方法。** 用 `gh` 读上游的发版、提交和对比。两个提交各浅克隆一份到 `/tmp/kc-tmp-clean/kcup-1026/`，只读源码、只做文本和 AST 比对，上游代码一行没有运行（没有 `pip install`，也没有 `npm ci`）。所以凡是要实际运行才知道的行为，下文都标「未核实」。

**结论。**
- 会改变用户体验，但只在原生 KiroCrew 页面里（「在 KiroCrew 中打开」之后看到的界面）。控制台、会话、MCP 不变。
- 我们调用的 Gateway HTTP 接口全部还在。
- 原生契约照现在的写法会失败，部署会在主机上中止。这是护栏在起作用，不是事故。要改 4 个文件（见 §4.2）。
- K03 所依赖的 SPA 刷新行为没有变。
- 建议按 S32 拆成 P1b，交负责人确认原生页面的变化。

---

## 1 版本与范围

| | 现在钉的 | 目标 |
|---|---|---|
| 提交 | `57df464b2b4ce3fe4e71d108281ff6c3ad22c40f`（main，2026-10-11 03:02 UTC，#18999） | `7b5d9b385764b41459bb4187403032da8983013e`（tag `v0.10.0`，分支 `release/0.10.0`，提交于 10-23，发布于 10-25） |
| 报告的版本 | `0.10.0`（`src/kiro_crew/__init__.py:10`，`pyproject.toml:7`） | `0.10.0`（同一位置） |
| CHANGELOG 最新一节 | `[0.8.0]` | `[0.10.0] - 2026-10-15` |
| 正式发布记录 | — | `stable-publication.json` 的 `source_sha` 就是 `7b5d9b38`。另有 wheel `kirocrew-0.10.0-py3-none-any.whl`（sha256 `bea234a0…0196`）和 sdist（`43b30782…a299`） |

- **差距：** 目标领先 1009 个提交，落后 0 个，钉的提交就是两者的 merge-base。共改了 4402 个文件（新增 1381、修改 2965、删除 50）。compare API 最多只列 300 个文件，所以完整清单来自本地 `git diff --name-status`。
- **v0.10.0 怎么来的：** 它是 10-15 从 main 切出的 rc.1（`249333689`，#20947），再加上 `release/0.10.0` 上的 6 个提交：
  - `6a1e29eb1` #21091：CI，文件改用 LF 换行；
  - `f8dd77e6c` #21195：CI 分片；
  - `0449f676f` #21390：CHANGELOG 补上 0.9.0 一节；
  - `e9bfada91` #21443：Windows 签名密钥；
  - `c917733a4` #23211：去掉 rc 后缀；
  - `7b5d9b385` #23505：dompurify 升到 3.4.17。

  所以它的内容约等于 10-15 的 main。
- **提交构成：** fix 548、test 174、feat 143、refactor 47、docs 45、ci 24、perf 20、chore 6。日期集中在 10-10 至 10-16。
- **不考虑的版本：** `v0.11.0-rc.1`（预发布）。`release/0.10.0` 的头现在是 `294fa0696`，比 tag 多 1 个测试提交（#23672），不影响结论。这条分支没有保护。
- **版本号区分不了：** 钉的提交和正式版都报 `0.10.0`，所以下面这些检查都分不出两者，真正能拦住的只有主机上的原生测试（`deploy/install/boundary-lib.sh:91-106`）：
  - `composition/registry.py:8` 的 `SUPPORTED_VERSIONS`，加上 `KIROCREW_ACCEPTED_VERSION`；
  - `tests/kirocrew_contract.json:2`；
  - `boundary_versions`。

## 2 上游变化摘要

### 2.1 v0.10.0 发版说明（主题）

发版说明覆盖从 0.9.0 分叉（10-08，`69dc0ce`）以来的整条 0.10.0 线。我们钉的提交已经包含其中 262 个。下面只列在 `57df464..7b5d9b38` 范围内、能对上 PR 的项。

- **成员（Crewmates，仍是预览）：** 第一次进 Crewmates 页面时由 Mate 引导（#18904）；目标、里程碑与报到（#19539）；成员提议改动，用户以卡片确认；新成员有自己的工作区（#19230）。
- **Dashboard 对所有人开放：** 去掉了预览开关（#19659）。聊天输入框上方的状态区、侧栏的 Dashboard 视图、Dashboard 标签页都默认出现。
- **派发：** 新增默认 Agent `kirocrew-team-lead`（#18128）；`spawn_run` 可以按任务指定 `backend`（#18150）；Subagents 卡片列出子 Agent 的每次工具调用（#17699）。
- **聊天：**
  - 回复可以复制为富文本（#18838）；
  - Alt+Up / Alt+Down 在提问之间跳转（#19139）；
  - 可以把会话导出为可读的 Markdown（#16614）；
  - 设置里可以改单轮时限，超时卡片可以给单个会话单独设时限（#18733、#19133）；
  - 对话太满时提供新会话，不再无限重试（#19786）。
- **其他后端：** Claude Code 在对话里支持运行中补充说明（#19751，只是聊天，见 §3 c8）；内置 KAS 引擎，默认关闭。
- **侧栏：** 正在工作的会话名前有动态圆点（#19166）。
- **设置：** 新增 `agent.env`（#18649）、`dashboard.github_hosts`（#19450）、`secret://` 引用（#19239）；批准可以按「1 小时 / 今天之内」信任（#19220）。
- **记忆：** 新增 `kirocrew memory forget`（#19780）；新增 `memory.writes_enabled` 与 `memory.lesson_extraction_enabled`，默认都开（#19311）。
- **减负：**
  - glibc malloc arena 最多 4 个（#19564），多核主机上 Gateway 占用的内存明显减少；
  - 嵌入模型空闲 60 分钟后卸载（#19562）。
- **安全：** 见 §3 h。

### 2.2 中间的 insider / rc 版本

| 版本 | 日期 | 内容 |
|---|---|---|
| v0.9.0-insider.6 / .7 / .9、v0.9.0 | 10-12 ~ 10-16 | `release/0.9.0` 上的回补：#19309、#20631，桌面版捆绑 kiro-cli 2.31.1（#20927）。原 PR #19243、#18841 都在我们的升级范围内 |
| v0.10.0-rc.1 | 10-16 | 从 main 切出（`249333689`） |
| v0.10.0-insider.2 / .3 / .4 | 10-16 ~ 10-17 | #21091、#21195、#21390、#21443（CI、CHANGELOG、Windows） |
| v0.10.0-insider.5 | 10-24 | #23211（去掉 rc 后缀）、#23505（dompurify 3.4.17） |
| v0.10.0 | 10-25 | 与 insider.5 同一提交 `7b5d9b38` |

## 3 逐项影响

「UX」一列指 KiroCrew ON 用户看到或要做的事有没有变化。

| 方面 | 变化 | 证据 | 是否改变用户体验 | 我们要改什么 |
|---|---|---|---|---|
| **a1** 原生登录、访问 Cookie 与刷新链 | 后端没有变化：<br>• `handlers/auth_refresh.py` 完全相同：401 `no_refresh_cookie` 和 `refresh_chain_revoked` 的语义不变；<br>• `refresh_tokens.py` 只删了一行 typing import；<br>• `MAX_SESSION_TTL_SECS` 仍是 20 h，`MAX_REFRESH_TTL_SECS` 仍是 30 d，`REFRESH_COOKIE_PATH` 仍是 `/api/auth`；<br>• `/api/token/local` 的 `ttl` 解析与准入不变，只在审计里多记一项「由哪个 app 铸造」（#20368）；<br>• `token_auth.py` 只在审计里加了 `forwarded` 字段，并对内部密钥为空单独给出拒绝码（#19732） | `dashboard/handlers/auth_refresh.py`、`refresh_tokens.py:53/66`、`token_auth.py:736`、`handlers/core.py:3895`、`member_memory_auth.py:229-298` | 否 | 无 |
| **a2** SPA 的刷新调度与 401 处理（K03） | 没有变化：<br>• `hooks/useRefreshScheduler.ts` 与 `api/refreshOnce.ts` 逐字节相同：到期前 1 h 刷新（`LEAD_MS`），失败按 1 / 4 / 16 / 60 min 退避，只有 `refresh_chain_revoked` 才 `location.assign('/')`；<br>• `DashboardBootstrap.tsx` 相同；<br>• `api/client.ts` 只多了一个处理「已受理但读不到回执」的包装（`jAccepted`），401 路径不变；<br>• 全站 `location.reload / assign / replace / href=` 调用都是 28 处，与原来相同 | 上述 SPA 文件逐个 diff | 否。K03 的过渡修复照样成立 | 无。主机上复验（§5） |
| **a3** 原生页面的界面 | 有用户看得见的变化：<br>• Dashboard 状态区与侧栏 Dashboard 默认出现（#19659）；<br>• 正在工作的会话前有圆点（#19166）；<br>• 复制为富文本（#18838）、Alt+↑/↓（#19139）、导出 Markdown（#16614）；<br>• 单轮时限设置与超时卡片（#18733、#19133；默认仍是 `chat_turn_timeout_secs=14400`）；<br>• 批准可以按时限信任（#19220）；<br>• 对话太满时提供新会话（#19786）；<br>• Subagents 卡片列出工具调用（#17699）；<br>• 排队卡片写明在等多少内存（#20503、#20776）；<br>• 「会话反复起不来」的错误卡片上多了「重启 Gateway」按钮（#20490，风险见 r1）；<br>• Agent 列表里多了 `kirocrew-team-lead`（#18128，每次启动写入 `KIRO_HOME/agents`） | 发版说明，加上各 PR | **是**（只在原生页面） | 代码不用改；交负责人确认（P1b） |
| **b** 成员、Team、owner token | 平台不调 `/api/members`：<br>• Crewmates 页面仍在 `PREVIEW_CREW` 预览开关后面（`utils/previewFlags.ts:89`），默认关；<br>• 内置成员 Mate 只建一次（`create_first_crewmate_once`）。新边界上的 Mate 有自己的工作区（`first_crewmate.py`，#19230），已有边界上的不受影响；<br>• owner token 的准入不变（`local_owner_bootstrap_provenance` 只多记出处），`.local_secret` 的位置不变；<br>• **新：** `token_signing.key` 不足 32 字节时，Gateway 拒绝启动（#14457，`token_secret.py:40-41`） | 同左 | 否 | 无。主机上核对签名密钥的长度（§5） |
| **c1** 派发时指定后端 | `POST /api/spawn` 与 `spawn_run` 多了可选的 `backend`（#18150）：<br>• 后端不可用时回 400：`unknown_backend` / `backend_not_installed` / `backend_restart_required`；<br>• 它作为 `backend_override` 传给 provider factory（`subagent_manager/run.py:2357-2358`）。<br><br>Worker 不发这个键。但 `composition/registry.py:47-64` 会把 kwargs 原样传给 `poc-*` 的固定后端 factory。如果有人在原生聊天里让协调者调用 `spawn_run(agent="poc-kiro", backend="claude")`（D73 允许用户这样说），`select_provider_backend` 会优先用这个覆盖值，结果 `actual != backend`，抛出 `ACP routing mismatch` | `dashboard/messaging_api/spawn.py:71-179, 230-246`、`config/loader.py`（`_acp` 的 `backend_override`）、`mcp_tools/spawn.py:418` | 只影响一种边缘情况：显式指定了与模板不一致的后端，这次派发会失败，报错也不好懂 | `composition/registry.py:47-64`：对 `poc-*` 去掉 `backend_override`，或者明确拒绝并说明原因；补一条原生测试 |
| **c2** 模型 ID 校验 | `spawn_run` 现在接受带提供方前缀的 ID，如 `opencode/big-pickle`、`ollama/qwen3:8b`；`provider:model`（没有 `/`）仍然拒绝（#19275，`f8dee6b1a`）。不支持这种 ID 的后端由接口自己回 400 `model_unavailable` | `validation.py:328-341`；本地用同一正则实测 | 否。Worker 自己仍会拦下 `/`、`:`、`[]`（K02），OpenCode 也已停用（K01） | `tests/native/test_native_contract.py:84-93`：断言 `opencode/big-pickle` 被拒，在新版上会失败，要改为按新规则断言；`octopus/worker.py:1410-1413` 的提示「当前 KiroCrew spawn 接口不接受…」与事实不符了，改成平台自己的限制 |
| **c3** 派发白名单 | 父会话所在项目里的 `.kiro/agents/<模板>.json` 若也声明了 `availableAgents`，要与全局声明同时满足（#20686，`subagent.py:1033-1050` 的 `all(...)`） | 同左 | 否。只有用户仓库里正好有 `.kiro/agents/poc-coordinator.json` 时才会收紧 | 无。§5 记一条观察项 |
| **c4** 排队与没有开始的任务 | 有改进：<br>• 对排队中的派发调 `DELETE /api/spawn/{id}` 或 `/release`，现在真正取消它，回 `{"ok":true,"cancelled":true,"queued":true}`（#20456、#19095）；<br>• 正在复查、尚无行的派发，`GET /api/spawn/{id}` 也答 queued（#20639）。<br><br>「没有开始」的文字改为 `never started: waiting for memory (not enough memory freed up in time); retry once memory frees up`，仍以 `never started` 开头 | `messaging_api/run_control.py:318-346, 699-786`、`subagent_wait_reasons.py:65-87` | 略有改善：看门狗或用户停止一个排队中的 Attempt，现在不会再在之后自己开始。`worker.py:1866` 照常处理 | 无。`engine.py:22` 的 `^\s*never started` 仍能匹配；可以把这句加进契约的 `runtime_errors` |
| **c5** 续接 | 同一对话的第二个并发续接回 409 busy（#19009） | `subagent_manager/continuation.py` | 否 | 无（`worker.py:1553` 已把 409 当作「改为全新派发」） |
| **c6** 内存态势、推迟、cgroup | 以下逐字节相同：<br>• `adaptive/` 整个目录；<br>• `resource_status.py`（按 cgroup 截顶的内存探测）；<br>• `admission/waits.py`、`memory_wake.py`、`fairness.py`、`types.py`；<br>• `gate.py` 里 `Subagent spawn deferred` 的两条日志。<br><br>`resource_pressure_gb` 等阈值的默认值也没变。Gateway 本身更省内存（#19564、#19562） | `subagent_manager/admission/gate.py:1089-1092`，配置 diff（§3 e） | 否。可能少一些推迟（未核实） | 无 |
| **c7** 批准 | 有三处变化：<br>• `/api/approvals` 与 `/api/approvals/{id}/{action}` 的处理函数相同；<br>• `.../approve` 接受的 action 不变（`approved` / `rejected` / `rejected_once` / `trust*`），只多了 `ttl_seconds`（#19220）；<br>• `git` 不再算作只读命令而自动批准（#20770） | `chat_api/slot_approval.py:788-814`、`handlers/sessions.py` | 只在关了 YOLO 的边界上：git 命令现在会出现在「待批准」里。默认开着 YOLO（`crew_yolo=true`），所以没有影响 | 无 |
| **c8** 运行中补充说明 | `ACP_BACKENDS_STEER` 仍是 `{kiro, kas}`，`/api/spawn/{id}/steer` 对 codex / claude 仍回 `steer_unsupported`。Claude 的运行中补充说明只用在聊天里（`ACP_BACKENDS_STEERING_REQUEST`） | `agent_sdk/backends.py:1207, 1281-1283` | 否 | 无。`octopus/domain.py:1430-1431` 的「只支持 Kiro」保持 |
| **d1** 我们调用的 Gateway 接口 | 29 个 API 端点在 v0.10.0 里方法、路径都没变。另外两个：nginx 拦下的 `/api/auth/refresh` 在，`/chat` 是 SPA 的路由。<br><br>路由表从 826 条变成 855 条，只删了一条 `POST /api/guide/agent/rename`，我们没用它。<br><br>我们读取的响应字段都还在：<br>• slot 列表的 `key`、`pending_approval_info.*`；<br>• slot 详情的 `messages`、`total`、`has_more`、`next_before`、`queue`、`queue_depth`、`memory_mode`；<br>• spawn 行的 `id`、`parent`、`task`、`done`、`queued`、`status`、`reason`、`reason_detail`、`elapsed`、`resuming`、`turns`、`last_tool`、`started`、`cancelled`；<br>• 导出的 `export_bundle_empty`、`export_slot_not_persistent`；<br>• lessons 的 `ok`、`reason`、`outcome`、`superseded`；<br>• `usage/turns` 的行字段与 `days` 参数（另外多支持 `since` / `until`） | 两版路由表比对，加上各处理模块的键检索 | 否 | 无 |
| **d2** 契约里的处理模块 | `dashboard/chat_handlers.py` 从 11148 行拆成 1189 行，处理函数搬到 `dashboard/chat_api/*`（#20333 等）。契约里 4 个端点写的模块不对了，主机上的 `HttpContractTests` 会失败，部署中止。键都还在新模块里，逐个核对过 | `chat_api/slot_create.py:86`、`pending_context.py:314`、`slot_note.py:272`、`slot_approval.py:769` | 否 | `tests/kirocrew_contract.json`：<br>• `:80` → `dashboard/chat_api/slot_create.py`；<br>• `:97` → `dashboard/chat_api/pending_context.py`；<br>• `:106` → `dashboard/chat_api/slot_note.py`；<br>• `:122` → `dashboard/chat_api/slot_approval.py` |
| **d3** 其他接口行为 | 以下都兼容：<br>• Gateway 重启后，旧的 slot 先只恢复成侧栏的一行，第一次打开时才读入对话（#18945）。每个按 slot 的路由都先经 `slot_ownership` 中间件读入，所以对我们透明；<br>• lessons 写入被去重时多回 `winner`（#20411），是新增字段；<br>• 新增 `GET /api/chat/slots/unrestored`，用来报告重启后没能恢复的标签页数 | `slot_ownership.py:590-600`、`handlers/cron.py:3195-3218` | 否 | 无 |
| **e** 我们写入的配置键与环境变量 | 契约里 21 个配置键都还是配置字段；agent / session / dashboard / memory 各节的默认值都没变。<br><br>新增的键：<br>• agent：`env`、`kas_bundle`、`kas_bundle_url`、`model_context_tokens`；<br>• session：`file_changes_keep_turns`；<br>• dashboard：`browser_local_origins`、`github_hosts`、`session_folder_root`、`upload_max_mb`；<br>• memory：`writes_enabled`（默认 true）、`lesson_extraction_enabled`、`embedding_idle_evict_secs`；<br>• 顶层：`apps`、`aws`。<br><br>我们设的环境变量都还在读：`KIROCREW_HOME / PORT / CORS_ORIGINS / ALLOWED_LOOPBACK_PORTS / KIRO_BIN / LIVE_EXECED`，以及 `CLAUDE_AGENT_ACP_BIN`、`CODEX_ACP_BIN`、`OPENCODE_BIN`、`CLAUDE_CODE_EXECUTABLE`。`dashboard/urls.py`、`origin.py` 没有变化。`acp_backend=""` 仍然表示 kiro-cli | 配置 AST diff、`config/sections.py:1595`、`memory_sections.py:243` | 否 | 无 |
| **f** Python、Node 与构建 | 要求不变：`requires-python >=3.12`，`engines.node >=22.12.0`（我们用 v22.23.3）。<br><br>构建步骤也一样：<br>• `website` 仍是 `npm ci && npm run build`，只多了 `--warn-if-stale`；<br>• `kiro_crew/frontend.py`（`stage`）与 `setup.py` 都没变。<br><br>依赖的变化：<br>• Python 运行时新增 `markdown-it-py>=3,<5`（从 PyPI 装）；<br>• 前端新增 `mdast-util-to-hast 13.2.1`，`dompurify` 3.4.13 → 3.4.17；<br>• 没有新增带 install 脚本的包，锁文件里仍是 1318 个包。<br><br>注意：目标提交在 `release/0.10.0` 上，不在 main 上。`al2023.sh:153-154` 先 `git clone` 再 `checkout <sha>`，只要这条分支或 tag 还在就能取到。分支没有保护 | `pyproject.toml:14`、`website/package.json:5,9-10`、`setup.cfg`、`al2023.sh:146-162` | 否 | `deploy/versions.env:4`（以及第 1-2 行的说明）。另有一项可选的加固：`al2023.sh:153-154` 改为按 tag 或 `git fetch origin <sha>` 取。安装和构建能否跑通**未核实**，在主机上跑一次就知道 |
| **g** 能否在 URL 子路径下运行（S32） | 只能在**构建时**指定，运行时不行：<br>• 构建时设 `KIROCREW_BASE_PATH=/前缀`，SPA 就挂在这个子路径下（#18377，`c1158ed7c`，10-09）。我们钉的提交已经有这个功能，v0.10.0 没有改动（`lib/basePath.ts`、`scripts/lib/basePath.mjs` 与文档都相同）；<br>• Gateway 不读这个变量，原样提供构建好的页面，所以每个前缀要单独构建一次；<br>• 反向代理要去掉前缀，改写 `Location`；Cookie 也要按前缀改路径：访问 Cookie `mc_token_<端口>` 的 Path 是 `/`，刷新 Cookie 是 `/api/auth`；<br>• 官方 wheel 是按根路径构建的 | `docs/guides/remote-and-mobile.md:868-888`（上游）、`refresh_tokens.py:66`、`auth_refresh.py:232-287` | 否（只做记录） | 不改代码，仍按 Host 路由。F7 的说法要改为「没有运行时子路径，只有构建时」（`docs/superpowers/specs/2026-10-25-…-design.md:38, 59`）。若以后改用子路径：`octopus/crew_entry.py:31-46`（只接受 origin）和 `:118`（`path == "/chat"`）都要改，而且不能再用官方 wheel 里的前端。都**未实测** |
| **h** 安全修复 | • dompurify 3.4.17：GHSA-cg2g-xh64-v4q7（严重）、GHSA-mrv2-3pqr-j5jr（高），#23505。原生页面经 CloudFront 对外，这一条相关；<br>• `git` 不再算作只读命令而自动批准（#20770）；<br>• 对某个程序的信任不再覆盖写重定向（#19212），重定向判断能识别引号（#19968）；<br>• webapp 预览的 CSP 去掉 `unsafe-eval`（#20655）；sandbox-doc 的 CSP 禁止连接类请求（#20708）；<br>• 签名密钥被截短时拒绝启动（#14457）；<br>• MCP gateway 的探测标记和批准存储只有主人能读写（#20614）；<br>• 沙箱：已安装的包目录对 Agent 只读（#18615），crew-home 下的 models 目录只读（#16054）。<br><br>没有修复的：P0 #11576（Gateway 事件循环卡死）与 #13062（崩溃后丢会话）仍然开着，只有部分缓解：打开的标签页登记改为崩溃后不丢（#18262） | 各 PR；`gh api repos/kirodotdev/KiroCrew/issues/11576`、`13062` | 否 | 无。安全上值得升 |
| **r1** 风险：原生页面里的「重启 Gateway」 | `POST /api/restart`（主人 Cookie 就能调，经代理也一样）会 `os.execv` 成 `python -m kiro_crew gateway --no-open --port N`（`handlers/updates.py:1889-1895`、`gateway_restart.py`），**不经过我们的组合**：`poc-*` 不再按后端分流，`close_all()` 还会切断正在运行的原生任务。<br><br>这个接口在钉的提交里就有。v0.10.0 在「会话反复起不来」的错误卡片上加了按钮（#20490），用户更容易点到。<br><br>`execv` 保留 pid，`gateway-runtime.json` 与 `gateway-factory.json` 也都还在，所以 `octopus/ops.py:37-47` 的 `runtime_marker_ok` 可能察觉不到组合已经丢了（**未实测**）。<br><br>上游留了一个口子：`gateway_lifecycle.restart_launcher()`（`gateway_restart.py` 的 `resolve_restart_launcher`），组合可以通过它指定重启时运行的程序 | 同左 | 用户点了按钮，之后的会话派发会出错 | 要负责人决定，因为任一做法都会改变按钮的效果：<br>• (1) 组合提供 `restart_launcher`，重启时回到 `composition.gateway`；<br>• (2) 在 `deploy/nginx/kirocrew-native-guard.conf` 里拦下 `POST /api/restart`，按钮会报错。<br><br>建议 (1)。另外，readiness 要能看出 execv 后组合已丢 |

## 4 结论与建议

### 4.1 判定

**会改变用户体验：是。** 全部在原生 KiroCrew 页面里。最显眼的 5 项：

1. Dashboard 状态区与侧栏 Dashboard 默认出现（#19659）。
2. 错误卡片上多了「重启 Gateway」按钮（#20490）。点了会让组合失效，见 r1。
3. 批准可以「1 小时 / 今天之内」信任（#19220）；关了 YOLO 的边界上，git 命令要批准（#20770）。
4. Subagents 卡片列出工具调用；排队卡片写明在等多少内存（#17699、#20503）。
5. Agent 列表多了 `kirocrew-team-lead`；侧栏有工作中的圆点；可以复制为富文本、导出 Markdown、设单轮时限（#18128、#19166、#18838、#16614、#18733）。

**控制台、会话、MCP 不变**，前提是做了 §4.2 的修改。

**建议：** 按 S32 拆成 **P1b**。代码改动不大，可以先在分支上做好，但等负责人确认上面 5 项、并对 r1 选定做法之后再部署。P1 其余各项照常进行。负责人也可以直接接受这些原生页面的变化，那就留在 P1 里，按排空规则部署。

### 4.2 升级要改的文件

| 文件 | 改什么 |
|---|---|
| `deploy/versions.env:4` | `KIROCREW_GIT_REF=7b5d9b385764b41459bb4187403032da8983013e`，第 1-2 行的说明改为「官方正式版 v0.10.0（tag，2026-10-25）」。`KIROCREW_ACCEPTED_VERSION=0.10.0` 不变 |
| `tests/kirocrew_contract.json:80, 97, 106, 122` | 4 个 `module` 改为 `dashboard/chat_api/{slot_create, pending_context, slot_note, slot_approval}.py`。可选：在 `runtime_errors` 里加 `never started`（`octopus/engine.py:22` 依赖它） |
| `tests/native/test_native_contract.py:84-93` | 按 v0.10.0 的规则断言：`provider:model` 仍被拒；`opencode/big-pickle` 被接受，只打印出来，或断言 K02 由 Worker 拦截。改掉 `:85-86` 的注释 |
| `composition/registry.py:47-64` | 对 `poc-*` 处理 `backend_override`：去掉，或者在与固定后端不一致时明确拒绝；同时补测试（`tests/test_composition.py` 与原生测试） |
| `octopus/worker.py:1410-1413` | 提示改为平台自己的限制（K02），不再写「KiroCrew 接口不接受」。可选 |
| 文档 | `docs/handover/FACTS.md` 加一条：钉的是正式版 tag 提交；子路径只能在构建时指定。`docs/OPERATIONS.md:62` 写明钉的是官方 tag。改正 F7（`docs/superpowers/specs/2026-10-25-mvp-0.2.0-deployment-and-worker-pool-design.md:38, 59`）。顺带改两处过期的行号：`octopus/worker.py:1334`（`session_export.py:380` → 约 `:440`）、`tests/test_kirocrew_p0.py:100`（`chat_handlers.py:1997`） |
| 可选 | `deploy/install/al2023.sh:153-154` 改为按 tag 或 SHA 取；r1 按负责人选定的做法实现（组合提供 `restart_launcher`，或改 `deploy/nginx/kirocrew-native-guard.conf`） |

### 4.3 风险

- **r1**：原生页面的「重启 Gateway」会让组合失效，readiness 可能察觉不到。这个问题钉的提交就有，新版让它更容易被触发。
- **签名密钥**：哪个边界的 `crew/token_signing.key` 不足 32 字节，它的 Gateway 在新版上起不来（#14457）。
- **重启**：升级会重启所有 Gateway，必须先排空（`boundary-lib.sh` 的 `boundary_restart`：版本目录一变就重启）。
- **分支**：目标提交只在没有保护的 `release/0.10.0` 分支上，也在 tag 上，要靠 tag 才能长期取到。
- **P0 问题仍在**：#11576 与 #13062 没有修好，升级不能替代 EC2 上对卡顿的处置。

## 5 需要在主机上重新验证的项目

1. **临时边界上的原生契约。** 用新的 ref 执行 `deploy.sh push`（或 `worker-host add`），确认：
   - `KiroCrew 源码 … @ 7b5d9b38` 构建通过：`npm ci`、`npm run build`（6 GB 堆）、`frontend stage`、`pip install`（会拉 `markdown-it-py`）；
   - `composition.gateway --check` 列出每个 `poc-*` 的后端；
   - `tests/native` 全部通过，特别是 `HttpContractTests` 和模型 ID 规则那一条。
2. **Gateway 起得来。** 每个边界先看 `wc -c /var/lib/octopus/boundaries/<名>/crew/token_signing.key`，应 ≥ 32。然后 `wait-ready --gateway` 通过，`gateway-runtime.json` 写着 `composition: PocProviderRegistry`。
3. **一个真实会话跑通**：
   - kiro / claude / codex 各派一个任务，路由日志的 `actual_backend` 与模板一致；
   - `mark-collected` 生效，父会话不多跑一轮；
   - 用量行能读到；
   - 关闭时导出备份，删除时释放对话；
   - 可能的话造一次排队，确认看门狗停止排队任务后它不再开始。
4. **原生入口不会循环刷新。** 从控制台点「在 KiroCrew 中打开」：
   - `/api/auth/refresh` 答 401 `{"error":"no_refresh_cookie"}`；
   - 浏览器控制台出现 `[refresh] backoff: retry in 60s`，之后 4 / 16 / 60 min；
   - 10 分钟以上没有整页重新加载；
   - 1 h 后出现登录已过期的提示。
5. **`/api/auth/refresh` 仍被拦下。** 经每个公网中转（`kirocrew-native.conf`、`kirocrew-relay-*.conf`）`curl -X POST https://<原生域名>/api/auth/refresh`，都回 401 `no_refresh_cookie`。
6. **原生页面的新界面。** 逐条对照 §4.1 的 5 项截图，交负责人确认。按 r1 选定的做法，验证「重启 Gateway」之后组合仍在（或者请求被拦下）。
7. **关了 YOLO 的边界**（`crew_yolo=false`）：让原生会话跑一条 git 命令，控制台「待批准」里能看到，并且能答复。
8. **观察项：**
   - 升级前后 Gateway 的 RSS 对比；
   - 派发推迟（`Subagent spawn deferred`）的频次；
   - 仓库里如果有 `.kiro/agents/poc-coordinator.json`，派发是否被收紧（c3）。
9. **现有 E2E**：接手、API、同站、浏览器、公开分享。

**未能核实的**（都要靠上面的主机验证回答）：
- 新版能否构建、安装；
- 原生测试的实际结果；
- `backend_override` 是否真会触发 `ACP routing mismatch`；
- execv 之后 readiness 是否失效；
- 构建时子路径实际能不能用；
- 内存是否真的少了；
- wheel 在 KiroCrew CDN 上是否有签名清单（S26 的前提 ②）。
