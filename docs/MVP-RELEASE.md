# MVP Release 计划与状态

更新日期：2026-10-11　·　当前阶段：**M1 待验收（实现与本地 / EC2 验收完成，待负责人确认）**　·　本仓库可运行代码：**M1 运行底座（分支 `feat/m1-runtime-foundation`，未合并）**　·　已发布版本：**无**

本文记录 KiroCrew ON 首个可用 MVP 的范围、已确定决策、各里程碑交付与验收条件。
“已确定”“默认值（待确认）”“未开始 / 待验收”分别标注；不要把本文中的目标当作已上线功能。
引用的历史验证结果来自现有项目的报告，**不是在本仓库重新执行的结果**。
总体设计见 [docs/superpowers/specs/2026-10-10-kirocrew-on-design.md](superpowers/specs/2026-10-10-kirocrew-on-design.md)。

---

## 1．MVP 定义

**首个可用 MVP = M1（运行底座）+ M2（核心闭环）+ M3（客户端直连与控制台）在 EC2 上完成验收。**
M4（共享与组织）、M5（仓库闭环）在 MVP 之后分别立项，各自经过 spec → plan → 实施 → 验收。

优先级：① 每个任务能够完成 → ② 完成的质量更好 → ③ 使用体验。

状态标记：`未开始` · `设计中` · `实现中` · `待验收` · `已验收`（附证据位置）· `未通过`（附原因与恢复条件）

| 里程碑 | 状态 |
|---|---|
| M1 运行底座 | 待验收（负责人确认）：本地（macOS）与 EC2 验收条件全部完成；OpenCode 核实结论为未通过（K01），替换配置可用。证据见 `docs/handover/M1-LOCAL-EVIDENCE.md`、`docs/handover/M1-EC2-EVIDENCE.md` |
| M2 核心闭环 | 未开始 |
| M3 客户端直连与控制台 | 未开始 |
| M4 共享与组织 | 未开始 |
| M5 仓库闭环 | 未开始 |

## 2．已确定决策

以下决策已由项目负责人在 2026-10-10 的设计讨论中确认。

| ID | 决策 |
|---|---|
| D01 | 在 `kirocrew-on/` 新建仓库，迁入新旧两版资产；原目录与运行数据不动 |
| D02 | 交付一份覆盖全部阶段的总体 Design；详细实施计划先只写 M1，后续里程碑在前一阶段验收后再分别出 spec 与 plan |
| D03 | ~~M1 的 Linux 开发与验收直接在 EC2 上进行~~（2026-10-11 由 D12 取代；EC2 实例创建仍需单独授权） |
| D04 | EC2 使用 Amazon Linux 2023 x86_64 |
| D05 | 实施路线：先等价迁入并建立回归基线，再在测试保护下分步重构 |
| D06 | 核心功能：Session 可共享给个人 / Team / 公司；Skills 对接后端提交任务；后端自动分析、拆分并映射 Agent（可在 Prompt 中指定）；通过报告与 Branch 跟踪 Feature；可用代码实现的功能不依赖 Agent 的通用性；后端接收、记录、分析处理并监控请求 |
| D07 | 首个可用版本在审查通过后**只推送远端 Feature 分支**；Draft PR / MR 创建后置到 M5 |
| D08 | 旧版的“根据需求自动生成 / 编排 Pipeline”是核心功能，与新版业务服务融合；不单纯以新版 `octopus/` 为主干 |
| D09 | Session 监控与管理以灵活、易用为目的，以查看和搜索为主，不以编辑为主 |
| D10 | 稳定性、自主编排、自动重试是核心；先保证每个任务完成，其次提升完成质量 |
| D11 | 与 Claude Code / Codex / Kiro CLI / Kiro IDE 直连，本地与远端智能联动，纳入 MVP |
| D12 | 先在本地（macOS）用本仓库代码以真实 ACP 跑通完整链路，再部署到远端 EC2（2026-10-11） |
| D13 | 自动生成任务（Pipeline 生成）参考旧版 `kirocrew-app-poc` 的实现：意图生成、Pipeline 规划阶段、确定性门禁与模板（2026-10-11） |
| D14 | 无人值守的执行边界使用 KiroCrew 原生 auto 批准，**仅限平台派发的 spawn**：边界配置 `approval_mode = "auto"` 时 Worker 以 owner 认证请求原生 `approval_mode=auto`；边界的全局工具批准（`KIROCREW_HOME/config.json`）保持 `interactive`，owner 与父会话的原生聊天仍需人工批准；诊断任务使用不能派生子任务的 `poc-diagnose` 模板；约束来自执行边界（独立 OS 用户 / HOME、原生沙箱、cwd 限定 Worker 数据目录）（2026-10-11，经本地真实运行收窄） |
| D15 | EC2 部署使用 Shell + CloudFormation，IaC 代码留存在 `deploy/aws/`（`kirocrew-on-ec2.yaml`、`deploy.sh`、`stack.env`）与 `deploy/install/al2023.sh`（2026-10-11） |
| D16 | 对外入口为 CloudFront → 安全组 → EC2：IaC 新建专用 VPC 公有子网，EC2 经 EIP 提供稳定源站名，安全组只允许 CloudFront origin-facing 托管前缀列表访问 API 端口；平台只服务携带 CloudFront 注入的 `X-Origin-Verify` 头的请求；使用默认 `*.cloudfront.net` 证书；不加 WAF；原生 KiroCrew 入口不经 CloudFront，只经 SSM 端口转发（2026-10-11） |
| D17 | 部署 Region 为 us-east-1（2026-10-11） |
| D18 | KiroCrew 使用 `https://github.com/kirodotdev/KiroCrew.git` 最新源码安装：部署时锁定到具体 commit（`deploy/versions.env`），已验证集合之外的版本须原生契约检查通过后才在边界配置 `kirocrew_versions` 中接受（2026-10-11；当前锁定 0.10.0 `57df464…`，本机已验证） |

## 3．设计默认值（已提出，待确认）

以下是设计中采用的默认值，尚未得到明确确认；如有异议，修改后同步更新本表、README 与总体设计。

| ID | 默认值 | 理由 |
|---|---|---|
| P01 | Python 包名保持 `octopus` | 兼容 `python -m octopus`、`/v1` API 与现有 `octopus_*` MCP 工具 |
| P02 | 旧版代码随基线整体迁入以保持回归测试；8917 / 8916 服务不从本仓库部署，继续从原目录运行；旧代码能力被新模块吸收后逐项移除 | 融合旧版 Pipeline 能力（D08），同时不影响现有旧版入口与数据 |
| P03 | 新应用使用独立数据根，不改动现有 `octopus.sqlite3` | 保留历史数据；如需导入旧数据另做显式只读导入工具 |
| P04 | 前端继续使用无构建的原生 JS | 复用现有 UI，不以重写前端为默认方案 |
| P05 | 平台（API / Engine）与执行（Worker / Gateway）分属不同 OS 用户 | 平台身份与执行身份从第一天起分离 |
| ~~P06~~ | ~~M1–M3 远程访问使用 SSM 端口转发，不开放公网端口~~ | 2026-10-11 由 D16 取代：控制台 / API 经 CloudFront 对外；原生入口仍只经 SSM |
| P07 | 一个部署对应一个组织；“共享给公司”= 组织内全部在册用户 | 首个云端实现为单公司部署 |
| P08 | Session 共享角色：`viewer`（查看）与 `collaborator`（可续作、提交修订；执行仍使用 Session 所属执行边界的身份与凭据） | 共享 Session 不等于共享凭据 |
| P09 | 共享 Session 不开放原生 KiroCrew 入口 | 原生浏览器登录覆盖整个 Gateway，平台撤权不会使其失效 |
| P10 | 提交后自动生成 Pipeline 与映射；默认在 Coding 工具中展示计划，由用户确认后运行；个人 / Team 可为指定仓库开启“自动准入”跳过确认，并留存策略快照 | 同时满足“自动”与“直接运行必须显式配置” |
| P11 | Feature 报告使用固定章节模板，可按 Team / Workflow 指定；由代码从结构化证据生成，Agent 只写叙述摘要并标注来源 | 代码优先原则 |
| P12 | 四个客户端共用一份 `SKILL.md`，底层统一调用确定性的 `octopus` CLI，MCP 作为并行通道；M2 先用 Claude Code 完成闭环验收，M3 覆盖全部四个客户端 | 本机已核实四个客户端使用相同的 SKILL.md 格式 |
| P13 | 默认映射：plan → Kiro CLI；code → Claude Code；test / review → Codex；deliver → OpenCode（回退 Claude Code）；review 实际工具必须与 code 不同 | 延续四工具默认组合，可按个人 / Team 修改 |
| P14 | 恢复预算默认：L0 每阶段 2 次、L1 每 Session 3 轮、L2 每阶段 1 次、L3 每 Session 1 次；M2 实测后定稿 | 完成优先，但不无限重试 |

## 4．MVP 范围

### 包含

- 新旧两版资产的等价迁入、来源记录与回归基线。
- 可配置运行时、无桌面启动、systemd 服务、schema 迁移、EC2 单执行边界部署。
- Request 一等对象：登记、幂等、状态、与 Session 的关联。
- Pipeline 自动生成与编排：规则匹配、Kiro 生成、schema 2 规范化、确定性门禁目录、重规划。
- Agent 映射策略、Prompt 显式指定、回退链；确认模式与显式配置的自动准入模式。
- 完成优先的恢复阶梯（瞬时重试 → 修复返工 → 工具回退 → 重规划 → 等待并自动恢复）、看门狗、重启核对、批准代理。
- 候选 commit、Feature 分支、审查通过后推送远端分支、模板化 Feature 报告。
- 四个客户端（Claude Code、Codex、Kiro CLI、Kiro IDE）的 Skill + MCP 接入与本地 / 远端联动。
- Session 监控控制台：列表、过滤、全文搜索、只读详情与少量管理操作。

### 不包含（MVP 之后）

- Session 共享给个人 / Team / 公司与组织层、Team 执行隔离、排队与配额、HTTPS 入口（M4）。
- Draft PR / MR、Webhook / Issue 输入、远端检查状态同步（M5）。
- 自动合并、生产发布（独立策略，不是自动研发的隐含默认）。
- 远程 HTTP MCP transport、SSO、高可用、任意 DAG / 并行阶段、长期 Team Memory。

## 5．M1 运行底座

### 交付

1. **迁入与基线**：按原相对路径原样迁入两版代码与测试，`docs/PROVENANCE.md` 逐文件记录来源路径、sha256 与来源处 git 状态；现有回归测试与 UI 检查在新仓库原样通过。
2. **接手材料**：新旧功能对照表、当前事实清单、主要调用链、已知故障清单。
3. **运行时配置**：解释器、KiroCrew 安装、工具路径、端口、数据根、HOME、公开 URL 与允许 Host、日志目录统一配置；保留 macOS 路径兼容。
4. **依赖解耦**：平台侧代码不再依赖 Worker 侧模块与旧 UI 文件。
5. **组合启动**：`composition` 包加载 `PocProviderRegistry`，提供 `--check`；健康检查报告组合特性。
6. **Engine 可靠性底座**：按 Session 并发推进、同一 Session 串行（同一 Workspace 写入互斥）、Worker / Gateway 不可用时退避重连而不阻断；派发回执不明仍显示 `dispatch_unknown`。L0–L4 恢复阶梯在 M2 实现。
7. **无桌面启动与服务生命周期**：API、Worker、Gateway 独立服务入口；systemd unit、就绪检查、优雅重启、日志、重启恢复。
8. **持久数据**：代码、平台数据、Worker 数据、原生 Crew 数据分离；schema 迁移且迁移前自动备份。（Worker Token 移出数据库放到 M4：平台调用 Worker 必须持有可用的 Token，不能只存哈希。）
9. **凭据与环境**：各工具在执行用户下初始化认证；平台只保存必要引用。
10. **网络入口**：Host 白名单与 Origin 校验支持 SSM 隧道；验证 SSE 与原生跳转。
11. **版本与回滚**：固定 KiroCrew、Coding 工具、ACP 适配器、Node / Python 版本；记录迁移与回退步骤。
12. **EC2 部署材料**：CloudFormation 模板（参数由目标环境给出）、AL2023 安装脚本；只在获得授权后创建资源。
13. **备份恢复**：在独立目录实际恢复平台数据库、Workspace 与必要原生数据，列明未覆盖内容。
14. **运维手册**：首次启动、升级、重启、日志、端口、认证过期、停止失败、磁盘与队列异常。

### 验收条件

- [x] 新仓库内迁入的回归测试与 UI 检查全部通过：旧版 92 项、新版 66 项（42 + 16 + 8）、迁入工具 3 项，Python 3.12.13 / 3.13（2026-10-11，`tools/regress.sh`，提交 `3a84485`）。
- [x] 本机 macOS 以本仓库代码、独立端口与数据目录拉起三服务，以真实 ACP 跑通 Kiro、Claude Code、Codex 的最小研发、审查返工、三阶段流水线、API / Worker / Gateway 中途重启恢复、并发与备份恢复（2026-10-11，见 `docs/handover/M1-LOCAL-EVIDENCE.md`）。OpenCode 本地核实**未通过**（K01 复现），替换配置 deliver→claude 通过。
- [x] EC2（AL2023 x86_64）上 KiroCrew Gateway（0.10.0，官方源码）以组合方式无桌面启动，`--check` 6 条路由正确，原生契约 6/6（2026-10-11）。
- [x] Kiro、Claude Code、Codex 的真实最小研发与返工在 EC2 上跑通，证据包含实际后端、原生 ID、源码基线、diff 与测试结果（2026-10-11，经 CloudFront：Kiro 冒烟、Claude→Codex、Kiro 规划三阶段、确定性审查返工 `run-317da209…`；见 `docs/handover/M1-EC2-EVIDENCE.md` #5、#9–#11）。
- [x] OpenCode 单独核实；失败如实记录，并提供用户可见的替换配置。核实结论：**未通过**（本地与 EC2 均复现 K01；EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝），替换配置 deliver→claude 通过。恢复条件：M2 回退链，并查明命中的门禁层。
- [x] API / Worker / Gateway 分别重启后状态自动恢复，原生执行身份与持久化记录一致（本地与 EC2，2026-10-11；EC2 另测整机重启：约 30 s 恢复服务，Session 自动完成；10 个 Attempt 在原生侧各恰好 1 个子 Agent，无重复派发）。
- [x] 一个慢 Session 不阻塞其他 Session 推进（`tests/test_engine_concurrency.py`；本地两个 Session 3 对 Attempt 区间重叠；EC2 两个 Session 并行执行）。
- [x] 备份在独立目录恢复成功，源数据保留（本地与 EC2，2026-10-11；EC2 备份另存 S3）。

## 6．M2 核心闭环

### 交付

1. **Request**：数据表与 API（提交者、空间、来源客户端、目标、仓库与 ref、显式指定、检查命令、执行策略、时间戳、关联 Session）。
2. **Pipeline 生成**：规则匹配 → Kiro 生成（只读源码检查）→ schema 2 规范化；兼容 schema 1。
3. **门禁目录**：`native_completed`、`identity_match`、`handoff_present`、`outcome_valid`、`tests_fail_on_baseline`、`artifacts_locked`、`diff_present`、`checks_pass`、`review_bound`、`candidate_unchanged`；结果入库并在报告中展示。
4. **映射策略**：显式指定 → Workflow → 个人 / Team 策略 → 平台默认；能力校验；回退链；记录映射来源。
5. **准入**：确认模式（默认）与按仓库范围显式授权的自动准入，留存策略快照。
6. **恢复阶梯**：L0–L4 全部级别、预算、看门狗、条件满足后自动恢复；`fallback` 与 `replan` 决定的代码校验。
7. **批准代理**：原生权限批准请求在控制台、CLI 与 MCP 中可见并可处理。
8. **候选与分支**：每个改代码的 Attempt 由 Worker 提交候选 commit；平台创建 Feature 分支并保证唯一。
9. **远端分支推送**：只推送已验证候选；写凭据只在执行边界内；不推默认分支、不 force push；推送前后核对远端 ref。
10. **Feature 报告**：模板化 Markdown / HTML 报告。
11. **Claude Code 基础 Skill**：提交、查看计划、确认、查询状态与报告，用于 M2 闭环验收。

### 验收条件

- [ ] 在 Claude Code 中用自然语言提交需求，后端登记 Request 并返回自动生成的 Pipeline、映射与门禁。
- [ ] Prompt 中指定的映射被采用并标注来源；不可用的指定给出原因，不静默替换。
- [ ] 确认后在 EC2 上对一个获授权的已有小型仓库完成：clone → Feature 分支 → 编码 → 候选 commit → 门禁 → 独立审查 → 返工 → 复审。
- [ ] 故障注入（测试失败、工具空结果、Gateway 重启、派发回执丢失、Attempt 卡住）在预算内自动恢复并完成；预算耗尽时给出原因与操作。
- [ ] 换工具与重规划在记录和报告中可见。
- [ ] 审查通过后远端出现对应 Feature 分支，commit 与已验证候选一致；回执丢失与远端变化不产生重复或覆盖。

## 7．M3 客户端直连与控制台

### 交付

1. **`octopus connect <client>`**：为 Claude Code、Codex、Kiro CLI、Kiro IDE 合并式写入 Skill 与 MCP 配置；写前备份，支持 `--dry-run`。
2. **CLI**：`login`、`submit`、`plan`、`confirm`、`status`、`watch`、`report`、`pull`、`sync`、`continue`、`approvals`、`search`。
3. **MCP 工具**：现有工具 + 请求、计划确认、搜索、报告、批准、关联。
4. **本地 / 远端联动**：未推送提交检测与 `--push-base`；`pull` 拉回分支与上下文到 `.octopus/sessions/<id>/`；`sync` 让远端快进到本地推送的提交并重新门禁与审查；`.octopus/session.json` 会话关联。
5. **监控控制台**：列表、过滤（状态 / 仓库 / 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间）、全文搜索、服务端分页；只读详情；少量操作（停止、续作、重试、批准、关闭 / 重开、导出、复制 CLI 命令）。

### 验收条件

- [ ] 四个客户端各自完成：connect → submit → status → pull → 本地修改并推送 → sync → 远端复审通过。
- [ ] connect 重复执行结果不变，用户原有 Skills 与 MCP 配置保留。
- [ ] 控制台可按全部过滤条件与全文检索找到 Session，详情覆盖 Pipeline、门禁、Attempt、恢复记录、事件、diff、报告。

## 8．MVP 验收矩阵

| 场景 | 必须观察到的结果 |
|---|---|
| Skill 提交自然语言需求 | Request 被登记；仓库信息由代码识别；重复提交返回原回执 |
| 自动生成 Pipeline | 阶段、提示词、验收条件、门禁与映射可见；来源可追溯 |
| Prompt 指定 Agent | 指定优先于策略；能力不支持时明确拒绝并说明 |
| 使用已有代码库 | 实际源码就绪后才派发编码，记录 ref / SHA / cwd |
| Claude 编码、Codex 审查 | 路由证据与原生 ID 对应正确工具；审查绑定候选 commit 与真实检查结果 |
| 审查要求修改 | Coordinator 诊断，原 Workspace 返工，下游重新复审 |
| 工具空结果 / 模型不可用 | 按回退链换工具并记录；不宣称原工具完成 |
| 阶段拆分不可行 | 重规划生成新修订，已验收且仍有效的阶段保留 |
| 缺源码、认证或批准 | 进入等待并说明原因；条件满足后自动恢复 |
| 派发响应丢失 / Attempt 卡住 | 核对原生状态后再重派，不重复启动编码 |
| 服务或浏览器重启 | 状态可恢复，原生执行身份与持久化记录一致 |
| 远端分支推送 | 只推送已验证候选；回执丢失不重复，远端变化显示冲突 |
| 本地 / 远端联动 | pull 拉回分支与上下文；sync 后远端重新门禁与审查 |
| 控制台搜索 | 过滤与全文检索结果正确，分页稳定 |
| 备份恢复 | 在独立环境恢复成功，源数据与旧 Session 保留 |

## 9．继承的已知问题

以下问题来自现有项目文档（`kirocrew-octopus/docs/MVP-1.0.0-RELEASE.md`、`MVP-1.0.0-STARTUP.md`、
`COORDINATOR-RECOVERY.md`、`architecture/OCTOPUS-V2-ARCHITECTURE.md`、`WORKFLOW-MVP.md`、`PIPELINE.md`），
属于历史报告记录，迁入后需要复现与处理。

| # | 问题 | 影响的里程碑 |
|---|---|---|
| K01 | OpenCode 执行曾返回空结果或不完整交付，需原生续接补齐（M1 本地与 EC2 均复现；EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝） | M2 |
| K02 | 含 `/`、`:`、`[]` 的模型 ID 被原生 spawn 格式拒绝（影响 OpenCode `provider/model` 与部分 Codex 模型 ID） | M1 |
| K03 | 原生浏览器登录覆盖整个 Gateway，不是单 Session 票据；平台撤权不会使其失效 | M1 / M4 |
| K04 | 新版 Web 未代理原生权限批准；存在待批准项时停止可能挂起 | M2 |
| K05 | Kiro 不报告实际模型；模型目录依赖 ACP 缓存，公布的模型不代表有配额 | M1 |
| K06 | 父会话 note 上限约 4000 字符，Worker 截断到 3800；截断不能当作完整交接 | M2 |
| K07 | 原生 continue 不读取 `reasoning_effort`；原生 spawn 没有 HTTP 幂等键 | M1 |
| K08 | Coordinator “不派生任务”的限制来自提示词，不是工具级隔离；其诊断可能有误 | M2 |
| K09 | Engine 单线程串行推进，慢 Run 会阻塞其他 Run | M1 |
| K10 | SQLite 没有 schema 迁移机制（M1）；Worker Token 以明文存放在数据库 `workers.config` 中（M4 移出数据库） | M1 / M4 |
| K11 | 组合逻辑只存在于单个进程内；任何 re-exec 都会丢失 `PocProviderRegistry` | M1 |
| K12 | Linux 沙箱依赖非特权 user namespace；不经官方 launcher 启动时需单独核实（AL2023 已实测：`unshare -Ur true` 成功） | M1 |
| K13 | 现有启动器与路径依赖 macOS：`/Applications/...`、App 内置 Python、zsh、Electron、`lsof`、Homebrew 路径 | M1 |
| K14 | 旧版 Host 校验固定为 `127.0.0.1:<port>`，隧道或反向代理下会失败 | M1 |
| K15 | Gateway 重启后不再列出已结束的内存任务；`/context` 背景在重启后丢失（本地与 EC2 实测：运行中任务被原生取消，经自动诊断重试后完成，含整机重启） | M1 / M2 |
| K19 | 原生 `/api/spawn` 的 `approval_mode` 在 0.8.0 中对 owner 认证请求生效，但原生文档字符串称其只对内部调用方生效；升级 KiroCrew 时必须复核，失效时改用 `hooks.auto_approve_subagent_spawn` | 每次升级 |
| K20 | 父会话（原生聊天）在子任务进行时会被唤醒并尝试执行工具（额外 token 消耗）；全局 interactive 下这些请求 600 s 后被原生拒绝，不影响平台任务 | M2 排查 note / 完成事件触发机制 |
| K21 | kiro-cli 没有非交互登录；`deploy.sh kiro-login` 以伪终端运行设备码登录（EC2 实测通过） | 上游提供非交互参数后移除 |

## 10．历史验证基线（来源记录，非本仓库复跑）

据 `kirocrew-octopus/docs/MVP-1.0.0-RELEASE.md` 与 `PIPELINE.md` 记录：

- 新版最近一次 Coordinator 功能验证：66 项后端回归通过（42 项 Team / Worker、8 项原生入口、16 项纠错）；
  85 项浏览器检查通过（模拟响应与真实原生实验分开记录）；6 项本机原生接入契约检查通过（KiroCrew 0.8.0）。
- 一个真实受控故障实验：Claude 编码 → Codex 测试拒绝 → Kiro 决定返工 → Claude 修复 → Codex 独立复测，最终两项测试通过。
- 旧版四阶段 Pipeline 本机验收（2026-10-08，KiroCrew 0.7.2）：四阶段均通过，OpenCode 交付经两次原生续接补齐。
- 新版更早的四阶段实验实际调用过 OpenCode，但 OpenCode 交付未通过。

这些结果均在 macOS 本机获得。小型实验不代表大型仓库、Team 隔离、EC2 或所有模型组合已通过。
迁入后的第一项工作是在本仓库重新运行回归，并以实际输出替换这里的引用。

## 11．验证分层与证据规则

- 分层记录：单元 / 集成替身、浏览器、原生契约、真实 ACP、客户端联动、Linux / EC2、Team 隔离、Connector；各层不得互相替代。
- 每项验收记录：环境、版本、测试方法、真实结果、证据位置和未通过项。
- 真实 ACP 实验使用单独明确的测试项目，不在用户历史 Session 上做删除或故障注入。
- 原始证据留在部署环境，不提交公开仓库；仓库只提交脱敏摘要。
- 未完成的关键目标保留为未通过项，不合并成“全部通过”。

## 12．需要的外部输入

不阻塞设计和不依赖这些参数的开发工作；在对应步骤开始前集中确认。

| 输入 | 用于 |
|---|---|
| ~~执行 `deploy.sh up` 创建云资源的明确授权；确认候选规格~~（2026-10-11 已授权，按 m7i.xlarge、100 GiB 创建；告警邮箱未设置） | M1 部署 |
| ~~Kiro CLI 在 EC2 执行用户下的一次性设备码登录~~（2026-10-11 已完成，IAM Identity Center，Pro） | M1 真实研发 |
| ~~KiroCrew 固定版本~~（已由 D18 确定：官方最新源码锁定 commit） | — |
| 获授权的测试仓库（GitHub 或 GitLab）及其写凭据形式 | M2 闭环与分支推送 |
| 报告模板的具体章节要求（如有指定格式） | M2 Feature 报告 |

## 13．发布记录

尚无发布。
