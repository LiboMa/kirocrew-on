# MVP-1.0.0 研发版报告

本轮从 2026-10-09 开始研发。版本定位：可操作的本地 Team API / Web 研发基线，尚未完成生产多租户部署验收。
旧工作台、原 KiroCrew Gateway 与历史会话继续保留。

## 本轮：推荐采纳与 Coordinator 纠错

在新分支 `codex/coordinator-recovery` 实施。旧工作台 `8917`、原生 Gateway `5476` 和历史数据保留；
新版 `8918` 登录页及顶部提供旧版入口。

- 新需求默认引导 KiroCrew 推荐 → 审阅 → 采用 → 编辑 → 提交；用户也可明确选择手动编排和预设。
- 代码来源必须明确选择；需求中的明确 GitHub / GitLab HTTPS 仓库 URL 可填入来源，不再默认空项目。
- 新业务运行默认两轮自动诊断，可设 1–5 轮或关闭；旧运行没有策略时保持关闭，不自动重启。
- Worker 复用现有原生接口，调用 `poc-coordinator` 分析失败证据；Engine 按决定重试当前任务或退回上游。
- 原 Run、父 Session、Workspace 和工具配置保留；新建 Attempt，并重新执行受影响的下游审查。
- `outcome.json` 区分通过、审查要求修改和外部阻塞；支持文字或结构化审查项，不把“有交接”当作质量通过。
- SQLite 保存失败证据、诊断任务、决定、修订与事件。停止、重启、迟到回执都有状态校验。
- 状态卡说明当前任务、具体错误和下一步；人工入口为“诊断并修复”和“配置并原地重跑”。
- REST `POST /v1/runs/{id}/recover` 与 MCP `octopus_recover` 使用相同权限和命令幂等。

[完整架构、交互流程、状态与使用方法](COORDINATOR-RECOVERY.md)。

验证：**66 项后端回归通过**（42 项 Team / Worker、8 项原生入口、16 项纠错）；
**85 项浏览器检查通过**（原有 68 项与新增 17 项）。浏览器诊断响应使用替身，正式原生实验独立记录。
真实实验证据见本机 `evidence/mvp-1.0.0/coordinator-native-run.json`，运行 ID 为
`run-51e8c99c67714452b59d82519f8e5844`。最终执行状态和审查结论在后续小节记录。

### 真实 Coordinator 返工验收

最终状态为 `completed`、进度 `2/2`、revision `3`。这是单独新建项目中的受控故障注入实验，
不是在用户既有项目上制造故障。源码为一个加法函数，测试为两项准确的加法断言。
首次 code 步骤的任务契约是“构造故障基线”；该步的 pass 只表示基线构造完成，明确记录功能测试仍失败。
后续 review 的任务契约才是业务加法必须正确，因此必须拒绝注入的错误。

| 顺序 | 实际后端 / 原生 Task | 实际结果 |
|---|---|---|
| code / 1 | Claude Code · `dc7d9083` | 已建立故障基线；结构化 findings 被旧解析器拒绝，保留失败 |
| 诊断 / 1 | Kiro · `ab8fd182` | 返回 `retry → code`，处理交接格式 |
| code / 2 | Claude Code · `7c562cb4` | 修正报告格式，保留故障基线，明确两项功能测试失败 |
| review / 1 | Codex · `0b49953a` | 真实测试 0/2 通过，返回 `changes_requested` |
| 诊断 / 2 | Kiro · `a4af4c74` | 返回 `rework → code`，要求移除多余的 `+1` 并重新复审 |
| code / 3 | Claude Code · `1872442c` | 修复实现，保留原测试，实际 2/2 通过 |
| review / 2 | Codex · `fb0bf6ef` | 独立复跑 2/2 通过，源码及测试哈希核对，返回 `pass` |

全过程保留同一个平台 Run、原生父会话 `octopus-run-51e8c99c67714452b59d82519f8e5844`
和 Workspace。5 个业务 Attempt、2 个诊断任务均保留，没有手动修改流程状态以制造完成。
最终独立运行 `node --test tests/add.test.mjs`，退出码 0，2 pass / 0 fail。
API / Worker 更新期间，原生执行仍在原 Gateway 中继续，未重发业务任务。

实验暴露并修复了 findings 兼容问题：现在接受文字项或具有问题说明的对象项，保留 file/line 等证据。
第一轮诊断把格式拒绝解释成“pass 与非空 findings 冲突”，这不是引擎的实际规则；
代码核查确认原因是原先只允许字符串项。已在协调者提示词中写明精确契约，要求区分事实与推测。
这也说明诊断 Agent 的判断不能代替独立复审；本轮最终通过由真实新测试和 Codex 复审确认。

本次没有再次验证 OpenCode，也没有据此宣布大型仓库、多租户部署或所有模型组合已完成验收。

本轮核对了用户所问的 `run-c5b5955e0653468dbabbb8cdab4c65b5`：plan 的原生 Task `f0ddddb0`
已停止但没有原因记录，下游没有开始；该运行另有“新建空项目”与“修改已有项目”目标不一致的问题。
本轮没有替用户猜测仓库或擅自重跑它，也没有删除任何历史运行。

## 上一轮：在 KiroCrew 中打开 Session

先按 Brainstorming 核对旧版入口与新版身份关系，采用“原生认证 + 已绑定父 Session + 新标签页”的迁移方案。
Session 工具栏新增“在 KiroCrew 中打开 ↗”；工作流推荐窗口新增“在 KiroCrew 查看 ↗”。
使用平台已保存的 `native_parent`，经运行所属 Worker 复用 `api.chat_entry_url` 和原生 `/api/token/local`。
打开入口不建立新 Session、Workspace 或 Task，不提交聊天提示词，也不自动恢复已关闭的会话。

新增 `POST /v1/runs/{id}/crew-entry`，需要 `execute` scope；
请求前后检查用户 / Team 权限、执行边界、原生绑定和生命周期。
登录链接只用于即时跳转，不进入草稿、状态快照、事件或导出。
未建立 / 已关闭的会话入口禁用；Worker 故障、旧版本和弹窗拦截都有明确提示。
远程 Worker 可配置 `--crew-url` 指定原生 Gateway 的 HTTPS origin。

验证：40 项现有后端测试、**8 项新增入口测试**与 2 项旧版认证兼容测试通过；
原有 51 项浏览器检查与 **17 项新增入口检查**通过。
浏览器替身覆盖 A/B Session 切换期间的迟到认证响应、推荐会话、错误页、重复点击与窄屏。
真实点击验证打开了 `run-9814f59c8b6c46aab3c8590475ac6136` 对应的原生父会话，
KiroCrew Cookie-only GET 返回 200 且 Session 身份一致；工作台仍停留在原选中项。
脱敏证据见本机 `evidence/mvp-1.0.0/native-crew-entry.json`。
当前原生页面保留已消费的 URL Token 参数，认证完成以原生 Cookie 和实际 Session 读取核对。

Team 原生登录仍以 Worker / Gateway 为隔离边界，不能声称这是单 Session 只读授权，
或平台移除成员会自动撤销已经建立的原生浏览器登录。

### 原线性调度与当前改动

此前新版是 Engine 顺序派发、失败后由用户手动续作；本轮补上了下文描述的 Coordinator 决策和返工闭环。
原生入口本身仍只负责导航和认证，不能把“打开对话”等同于已经恢复自动调度。
旧版由 `poc-coordinator` 接收完成事件并调用 `next / accept / spawn_continue`；旧契约也规定 accept 失败需报告并停止，
因此不能将旧版描述为任何失败都必定自动恢复。

## 上一轮：KiroCrew 推荐与动态提示词

新增真实后台推荐入口：从新增 / 编辑工作流或发起任务进入，由所选 Worker 的 Kiro Agent
读取需求与当前模板 / 任务基线，生成可编辑工作流及各 Task 的具体提示词。
生成结果先校验、再由用户采用和修改，最后保存工作流或提交业务开发。
关闭弹窗后的生成回执可恢复到原草稿；需求或任务变化时禁止采用旧结果。

选择模板时立即载入该模板的提示词，不再依赖“先输入需求”才能看到任务。
模板提示词带入本次目标和步骤位置；手动提示词不会被自动覆盖。
本轮复用旧版 `intent_prompt` / `parse_document`，执行仍经现有 Team API → Engine → Worker → KiroCrew ACP。

验证：**40 项后端测试，原有 17 + 21 项浏览器检查，新增 13 项推荐交互检查通过**。
新增浏览器检查中的生成端点使用模拟响应，真实工作流保存走测试 HTTP / SQLite；
它们不当作真实 Kiro 执行证明。真实推荐的原生结果另存 `evidence/mvp-1.0.0/native-recommendation.json`。

真实验证已完成：平台设计 Run `run-9814f59c8b6c46aab3c8590475ac6136`，
原生 Task `57167967`，实际后端 `kiro`，返回“会议议程生成器:实现与独立审查”，共两个 Task，
分别由 Claude Code 实现、Codex 审查。每个 Task 已生成针对议程输入、时间分配、空主题处理和验证方式的提示词。
推荐 JSON 与交接均通过校验；没有执行推荐中的业务开发任务，也没有自动保存工作流库条目。

实测还暴露了父对话忙碌时的 note 长度限制：原生只接受最多 4,000 字符。
Worker 现在回写最多 3,800 字符的明确摘要，完整原生结果、JSON 草稿和交接保持完整。
回归测试验证长结果不会因父对话摘要截短而丢失。

## 上一轮启动入口修订：需求优先

新版不再要求先保存工作流。Web 先输入需求，匹配已有可运行工作流或 15 个预定义模板，
自动填充 Session 名称并展示任务。可以手动指定流程，或在任务列表编辑工具、模型、Effort 和任务顺序。
任务手动编辑后不会被自动匹配覆盖；草稿按用户 / 空间 / 来源 Session 隔离，迟到响应不覆盖新输入。

统一匹配接口为 `POST /v1/workflows/resolve`，Chat 对应 `octopus_match_workflow`。
预览没有原生副作用；正式提交固定工作流版本或自定义 steps 快照，仍由现有 Engine / Worker / KiroCrew 执行。
当前匹配是明确标注的关键词规则，复用了旧模板库；没有把旧版全局身份的 LLM 生成端点接入 Team API。
Chat 模型可以自行编排 `steps`，经相同服务端校验后提交。

本次入口修订验证：**33 项后端测试、原有 17 项浏览器检查、新增 21 项启动向导检查全部通过**。
浏览器使用临时 SQLite / 测试用户，不派发真实 Coding 任务。覆盖权限过滤、手动优先、版本固定、
无预建工作流提交、草稿恢复、Session 隔离、迟到响应、任务编辑和窄屏。
下文真实 ACP 运行记录属于之前的调度验收，不作为本次新增 LLM 生成能力的证明。

入口与材料：

- 新工作台：`http://127.0.0.1:8918/`
- [配置计划、架构图、交互图及数据关系](MVP-1.0.0-PLAN.md)
- [启动、运行与管理员配置](MVP-1.0.0-STARTUP.md)
- [REST / MCP 接入与调用示例](MVP-1.0.0-API.md)

## 已实现

| 范围 | 本版行为 |
|---|---|
| 用户与 Team | 用户名 / 密码登录，可撤销 Token，Owner / Member，服务端校验成员权限 |
| 工作流共享 | 默认私有，指定用户或 Team 授权 viewer / runner / editor；Owner 单独管理共享 |
| 工作流版本 | 保存形成新版本；运行快照不随工作流库编辑而改变；同 Owner 下拒绝重名 |
| 统一入口 | Web、HTTP API、Python 客户端、stdio MCP；OpenAPI 提供请求 schema |
| 用户需求 | 在 Web 输入需求，或 API 直接提交定制 steps；明确选择新项目 / Git 来源 |
| Task 配置 | 每步先定义任务，再选择 Coding 工具、模型与 Effort；模型以 Worker 公布能力为依据 |
| Session 续作 | 保留平台 ID 与 Workspace，修改 Task 后创建新 Attempt；历史执行及失败证据保留 |
| 调度 | SQLite 事务、单 Engine 进程锁、先记录 Attempt 再派发、丢失回执时核实 |
| KiroCrew 接入 | 复用现有 Gateway / SubagentManager / ProviderRegistry / ACP Client；不直接启动 Coding CLI |
| 实时状态 | 带事件序号的 SSE，断线补读；持续验证 Token / Team 权限 |
| 运维 | 停止、备份后关闭、重新打开、删除父对话；源码与审计保留 |
| Web | 蓝白工作台，个人 / Team 切换，工作流模板与大编辑框，Tasks 进度与历史 Attempt，侧栏可调宽 |

配置、调度和存储已有明确分工；CLI 不再是用户必须经过的调度入口，JSON 文件不再是运行状态的权威来源。
框架没有把 KiroCrew 的 owner 凭据交给普通 Coding 客户端。

## 实际 ACP 验证

平台 Run：

```text
run-61792e17ce544ea7ba431bae1e984203
```

在一个新 Workspace 中实现 Python 加法模块及测试，然后独立审查并交付 HTML：

| Task / Attempt | 实际调用 | 原生 Task ID | 结果 |
|---|---|---|---|
| 规划 / 1 | Kiro CLI | `14d5dab3` | 完成计划和交接；实际模型未报告 |
| 编码 / 1 | Claude Code | `e9326070` | 完成 `calculator.py` 与 unittest |
| 审查 / 1 | Codex | `ec92e70b` | 独立复测，完成 `REVIEW.md` |
| 交付 / 1 | OpenCode / `opencode/big-pickle` | `47dfadd4` | 原生返回 `_No response._`，正确标为阻塞 |
| 交付 / 2 | 请求指定 OpenCode 模型 | 无 | 原生 `model: invalid format` 拒绝，没有伪造执行 ID |
| 交付 / 3 | 改由 Claude Code 执行 | `161fca23` | 完成 HTML、Release 与交接，进度达到 4/4 |

Claude Code 报告模型为 `global.anthropic.claude-sonnet-4-6`。
Codex 报告 `openai.gpt-6-astra[xhigh]`，实际 Effort 为 `xhigh`。
这些是本机原生记录中的观察值，不是框架硬编码的推荐模型清单。

恢复经过 Session revision 1 → 2 → 3；保留同一个 Run、原生父对话与源码目录。
前 3 个 Task 没有重新派发，交付步骤保留 3 个 Attempt。总计 6 个 Attempt，其中一次在原生参数校验阶段被拒绝。
此后用户可正常关闭或继续该 Session；报告中的“完成”指验收时点，不覆盖用户后续操作。

交付文件：

```text
plan.md
calculator.py
test_calculator.py
REVIEW.md
RELEASE.md
index.html
```

框架外独立执行 `python3 -m unittest -v`，3 项测试通过，退出码 0。
正常会话的原生导出也取得有效 gzip JSON bundle。
本轮原生验证使用新建项目；大型现有 Git 项目与独立 Team Worker 的端到端回归还需另外验收。

## 测试结果

| 验证 | 结果 | 说明 |
|---|---|---|
| 新版后端 | 26 项通过 | 权限、共享、撤权、版本、命令幂等、迟到结果、停止竞争、回执丢失、模型证据、备份拒绝 |
| 原框架回归 | 92 项通过 | 不调用真实 Coding 任务的现有测试组 |
| 浏览器 | 17 项检查通过 | 使用临时 SQLite / 测试账户，验证登录、共享、SSE、续作、模型、版本、Token、弹窗和移动端 |
| 真实 Coding 执行 | 5 个原生 Task 实际调用 | 包含 OpenCode 失败；最终交付由 Claude 恢复完成 |
| 交付代码独立测试 | 3 项通过 | 标准库 unittest，独立复跑 |
| 原生运维 | 专用空测试会话通过 | stop → close → reopen → delete；不使用原有会话做删除实验 |
| 旧会话保留 | 57 / 57 | 本轮开始时记录的原有 Slot 全部仍可见 |

浏览器测试与原生执行证据分开统计。Fixture 的模拟任务状态不当作 Coding Agent 调用成功。

本机证据（默认忽略提交，避免把运行内容混进源码仓库）：

```text
evidence/mvp-1.0.0/native-run.json
evidence/mvp-1.0.0/native-lifecycle.json
evidence/mvp-1.0.0/browser-desktop.png
evidence/mvp-1.0.0/browser-mobile.png
```

`native-run.json` 包含实际配置与后端、原生 ID、交接 hash、文件 SHA-256、独立测试输出及旧会话保留核对。
真实运行 Workspace 在新版 Worker 的专属子目录中；日志与执行信息不嵌入 API Token。

## 本次发现并处理的问题

**派发成功但回执丢失**

Worker 先保存 Attempt 的请求 hash 和标记，再调用原生接口。
响应不明确时进入 `dispatch_unknown`，根据同一父会话与 Attempt 标记查找原生任务。
网络恢复时复用原 Attempt；不能让用户用“继续”生成第二个未知执行。

**停止与准备 / 返回结果同时发生**

准备完成后即使已经收到停止命令，也保存原生父会话绑定，以便下一轮准确停止。
迟到的旧结果可以作为历史证据保存，但不能完成新 Attempt 或覆盖停止状态。
Worker 被停用也不跳过已有运行的停止操作。

**空会话无法导出原生 bundle**

只有说明性 note 的原生会话可能返回 `export_bundle_empty`。
此时保存带类型标记的观察记录，保留 notes，再调用原生关闭。
该记录不冒充可导入的 KiroCrew bundle；其他导出错误、损坏 gzip 或隐私拒绝仍阻止关闭 / 删除。

**后端公布模型与派发接口格式不一致**

OpenCode 公布 `provider/model`，当前原生 `SPAWN_RUN_SCHEMA` 的模型格式只允许字母数字、点、下划线与连字符。
本版现在提前过滤不兼容选项，并返回 `model_transport_unsupported`，避免提交之后才产生不可解释的错误。
没有删除失败证据，也没有把未经核实的模型降级包装成执行成功。

## 明确未完成的范围

1. **独立 Team 执行环境的实际部署验收**。API / 数据权限已实现，独立 OS 账号、容器或机器不是声明 `dedicated` 就会自动产生。
2. **OpenCode 完整交付与指定模型兼容**。真实 ACP 调用已发生，但本轮 OpenCode 交付未通过。需要收口原生模型格式与实际账号可用性。
3. **所有模型与 Effort 组合的实际验证**。当前目录依赖原生公布缓存；没有观察值时明确显示未知。
4. **权限批准在新 Web 内的完整代理**。当前显示原生等待状态，批准仍由有权操作相应 Gateway 的人员处理；没有借用别人的原生身份。
5. **SSO、账户完整生命周期、配额、公平排队、分布式高可用、互联网入口运维**。当前是单服务 / SQLite 的本地研发基线。
6. **长期 Team Memory、任意 DAG / 并行分支、大型代码库的完整验收，以及旧 Session 的归属迁移**。
7. **EC2 与 GitHub / GitLab Connector**。保留设计，不在本轮部署。

因此可以开始本机研发与受控验收；在宣布团队生产可用前，需完成对应 Team Worker、认证、备份恢复和原生兼容的验证。

## 下一轮建议顺序

1. 配置一个真正独立的 Team Worker，用两个成员验证同一工作流、同一 Team Run 的完整研发过程。
2. 修复 / 对齐 OpenCode 的原生模型选择契约，再用同一验收任务验证交付。
3. 将原生权限等待、账号就绪、模型能力与运行资源状态完整接入新工作台。
4. 验证已有 Git 项目的 clone、构建、测试、失败恢复与受授权提交。
5. 再决定 EC2 / Connector / SSO 的实施批次。
