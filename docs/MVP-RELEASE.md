# MVP Release 计划与状态

更新日期：2026-10-19　·　当前阶段：**M1 已验收（负责人 2026-10-14 确认）；M2 核心闭环的主体已在 EC2 验收（命名门禁、候选、报告、发布 Draft PR、恢复阶梯、计划确认、映射、故障注入、已有仓库的完整闭环）；D46 角色与 Agent 体系 P0–P3 已在 EC2 验收，P4–P6a、D49、D50 本机完成、待 EC2 验收（§14.33–14.38）**　·　本仓库可运行代码：**分支 `feat/m11-gates`（PR #4；4724b9a 之后的提交尚未推送，待 EC2 验收与负责人确认）；早期分支 `feat/m1-runtime-foundation`（origin a57b30e）**　·　已发布版本：**无**

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
| M1 运行底座 | 已验收（负责人 2026-10-14 确认）：本地（macOS）与 EC2 验收条件全部完成；OpenCode 核实结论为未通过（K01），替换配置可用。证据见 `docs/handover/M1-LOCAL-EVIDENCE.md`、`docs/handover/M1-EC2-EVIDENCE.md` |
| M2 核心闭环 | 实现中，主体已在 EC2 验收：KiroCrew 规划、工作流文档规范化、命名门禁（含 `tests_fail_on_baseline`、`artifacts_locked`）、候选提交、Feature 报告、发布 Draft PR / MR（含新建仓库与自动发布）、换工具、恢复阶梯（L0 重试、看门狗、重新规划、预算、等待与自动恢复）、计划确认、映射来源与需求中指定、故障注入、已有仓库的完整闭环。剩余：Request 表、Pipeline schema 2、按仓库自动准入、个人 / Team 映射偏好、批准代理的真实批准验收与 CLI。明细见 §14.2 |
| M3 客户端直连与控制台 | 实现中：Claude Code / Kiro CLI 的 connect + Skill + MCP（35 个工具）与 React 控制台（含门禁、报告、修订对比、待批准、发布、用量）已上线；Codex / Kiro IDE、用户 CLI、pull / sync、服务端分页与全文搜索未开始。明细见 §14.3 |
| M4 共享与组织 | 部分提前（D28–D30、D49）：Team 执行环境与 Team 会话共享已在 EC2 验收；每人一个边界第 1 期（D49）本机完成并已建 owner 边界、待 EC2 验收，第 2 期（调度）待讨论；Worker Token 移出数据库（D59）已在 EC2 核对，排队可见（M4-4 第 1 期，D60）本机完成；组织层、单 Session 票据（K03）、配额未开始。明细见 §14.4、§14.37 |
| Agent 体系（D46、D50，MVP 之后的质量提升） | P0–P3 已在 EC2 验收（§14.25、14.26、14.30、14.31）；P4 Knowledge、P5a 保留上下文的返工、P5b 运行中补充说明、P6a 用量、D50 编码技能包本机完成，待 EC2 验收（§14.33–14.36、14.38）；P6b 工具时间线等待 KiroCrew 为子任务记录工具调用 |
| M5 仓库闭环 | 未开始：GitHub Connector 设计草案（`docs/superpowers/specs/2026-10-11-github-connector-design.md`） |

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
| D19 | 控制台用 React 框架性重写，Python 后端与 `/v1` API 不变；后端只调整静态文件服务（2026-10-11） |
| D20 | 前端技术栈：Vite + React + TypeScript，Tailwind CSS 4 + shadcn/ui（Radix），TanStack Query；源码在 `web/`（2026-10-11） |
| D21 | 新版控制台在 `/`，旧版原生 JS 控制台暂留 `/classic/`，功能对齐并经负责人确认后再移除（2026-10-11） |
| D22 | 前端在主机安装时构建（`npm ci && npm run build` → `octopus/ui/dist`，不入库）；离线回归包含前端单测与构建（2026-10-11） |
| D23 | 发起开发采用向导：需求 → 编排方式 → 任务与 Agent → 确认；编排方式提供参考模板、KiroCrew Agent 推荐（匹配每个 Task 的 Prompt）、直接提交 Agent 预先生成的工作流（UI 导入或经 API / MCP `document` 提交，由代码解析与规范化）；不删除既有能力（2026-10-11） |
| D24 | 服务端原生 KiroCrew 对话经第二个 CloudFront 分发 → 主机 nginx（校验源站密钥、WebSocket）→ 回环 Gateway；入口登录 1 h（K03）；代码与 IaC 已完成，部署前需负责人确认（2026-10-11） |
| D25 | 最小 MVP = Coding Agent 连接后台 → 提交任务 → 查看任务 → 更新优化应用；分支开发相关的 M2 / M3 内容后排（`docs/BACKLOG.md`）（2026-10-11） |
| D26 | 意图识别与需求补全必须由 KiroCrew Agent 生成：未指定任务时 Session 第 1 个任务由 Kiro Agent 规划（intent、summary、1–7 个任务与 Agent 映射），代码校验后追加到同一 Session；规则匹配仅在 `plan=match` 时使用；K01 修复前 OpenCode 任务由代码改派 Claude Code（2026-10-11） |
| D27 | Coding Agent 接入：`octopus connect claude|kiro` 写入 MCP 服务器 `kirocrew-on` 与同一份 Skill（由 `skills/kirocrew-on/SKILL.md` 渲染；其他客户端见 `docs/CONNECT.md`）；产出经 `/preview` 在隔离来源中预览，开发服务器类项目用 `deploy.sh space <run-id>` 转发到本机（2026-10-11） |
| D28 | Team 执行环境：创建 Team 时必须选择执行环境——**共享环境**（管理员指定的共享 Worker，服务 Team 与用户的个人空间，每个用户不必有自己的 Worker；环境内会话的 Agent 以同一 OS 用户运行、互相可读，适用于同一信任范围；MVP 默认）或**专属环境**（独立执行边界：同一主机上的独立 OS 用户或第二台主机；适用于有保密要求的 Team；MVP 之后实现）；会话共享属于平台授权，不共享执行环境（`docs/WORKERS.md`）（2026-10-11；同日修订：由仅严格隔离改为两种并存，MVP 先实现共享环境；专属环境随后提前实现，见 D29） |
| D29 | 专属环境先以**独立 Worker 主机**实现（2026-10-11）：每台一个 EC2 与 CloudFormation 栈，与平台同 VPC，只接受平台主机访问；平台↔Worker 使用平台私有 CA 签发的 TLS；新主机登记为空闲执行环境，由管理员在控制台分配给 Team；先 1 台（m7i.xlarge）验证，确认后扩到 3 台。同主机新 OS 用户的形态按需再做 |
| D30 | Worker 主机的原生 KiroCrew 入口统一经平台主机中转（2026-10-12，方案 B）：每台 Worker 主机一个 CloudFront 分发，源站为平台主机的中转端口，平台 nginx 校验源站密钥后经私网 TLS 转发到该主机的 Gateway；管理侧收口为控制台「执行环境」对话框与管理员 API `GET /v1/admin/environments`，运维为 `deploy.sh worker-host add <名称>` |
| D31 | 访问管理（2026-10-12，`docs/superpowers/specs/2026-10-12-access-management-design.md`）：新增 `admin` scope，平台管理需管理员账号且 Token 含 `admin`；看不到返回 404、看得到不能做返回 403 并说明原因；平台管理员可查看并操作全部会话（原生入口仍按 K03），越权与用户管理写入审计；管理类操作经 MCP 开放但只接受 admin Token；不删除用户与 Team，停用即可 |
| D35 | 代码仓库凭证（2026-10-13，`docs/superpowers/specs/2026-10-13-repo-credentials-design.md`）：个人 / Team / 平台三级 GitHub 与 GitLab 凭证（参照 Jenkins Credentials），秘密只写、加密存储（openssl AES-256-CBC + HMAC，主密钥只在平台主机）；工作流可带默认凭证，提交时按明确指定 → 工作流默认 → 按仓库自动匹配解析并固定在会话中，会话显示创建者与凭证；个人凭证可用于本人提交的 Team 会话；第 1 期克隆私有仓库，第 2 期平台代发 PR / MR，第 3 期 GitHub App 与专属环境注入 |
| D36 | 开发成果快速预览（2026-10-13，`docs/superpowers/specs/2026-10-13-session-preview-design.md`）：第 1 期下载会话源码（含 `.git`、排除依赖与 `.env`，10 分钟签名链接、每次复核权限、流式不落盘），MCP `octopus_download` 解压到本机并给出启动步骤；交付任务以 `.octopus/preview.json` 声明启动方式（下载与远程预览共用）；第 2 期浏览器内远程预览（独立预览分发 + 签名 Cookie） |
| D34 | 会话变更记录与运行优化（2026-10-12，`docs/superpowers/specs/2026-10-12-session-changes-and-runtime-design.md`）：会话的每次写入（人与系统）记录操作者、来源、字段前后值与原因，可查询、筛选、导出；KiroCrew 规划产出结构化任务（类别、交付物、验收项、验证命令、依赖、可改动路径），规划补全的需求与任务契约传给每个任务与协调者，结论逐项对照验收项并单列建议；续作可只重跑受影响的任务（依赖、审查 / 测试与未验收规则补全），其余沿用上一版结果；运行时取消与结果丢失自动重试一次、不占纠错轮次，kiro-cli 登录过期时派发等待而不失败；部署时 Gateway 依赖未变不重启 |
| D33 | 访问管理第 2 期 Team 与 Worker 管理（2026-10-12，`docs/superpowers/plans/2026-10-12-access-phase2-teams-workers.md`）：Team 改名、归档 / 恢复（归档后拒绝新会话与新成员，旧会话可查看与停止）、管理员托管成员、成员退出、管理员更换执行环境；Worker 改名、退役（迁移 4）、控制台更新连接，服务的全部用户可刷新模型；Engine 派发前核对会话的 Worker 仍服务其空间，否则进入等待并可重新分配；Team 的专属主机更换后改为空闲，只能回到原 Team，重新部署（新的执行边界）后才可给其他 Team；Team 与执行环境的管理操作写入审计 |
| D32 | 会话修订历史（2026-10-12）：每次创建 / KiroCrew 规划 / 续作 / 自动纠错都记录需求与全部任务的完整快照和摘要；任意两版可对比需求与各任务的字段前后值和 Prompt 逐行增删（服务端计算，控制台与 MCP `octopus_revisions` 共用）；此前的修订按记录还原并标注 |
| D53 | 后台管理与实际使用分开设计（负责人 2026-10-20）：企业中管理者与使用者可能是两类人。管理操作（部署、开通执行边界、kiro 登录、执行环境与 Team 分配、用户与权限）与使用流程（连接、提交、查看、续作、预览、共享）在控制台、MCP / Skill、提示与文档中分开设计；使用者不需要了解或执行管理步骤，需要管理员处理时提示写明原因并指向管理员；需要两类人配合的步骤写明各自做什么（总体设计 §2 原则 8） |

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

- [x] 在 Claude Code 中用自然语言提交需求，后端登记 Request 并返回自动生成的 Pipeline、映射与门禁。（D55，EC2 80bc0c4，2026-10-21：经 MCP 不带 `admission` 提交 run-2a681323…，回执带 `request_id`，停在「计划待确认」并列出任务、工具、映射来源与检查，依据「平台默认」；`octopus_requests` 列出该 Request。个人准入规则命中后 run-0cf3db03… 未经确认直接执行至完成，依据「按准入规则」，client 为 `claude`。验收会话已关闭，规则与临时 Token 已删除）
- [x] Prompt 中指定的映射被采用并标注来源；不可用的指定给出原因，不静默替换。（D45，§14.23）
- [x] 确认后在 EC2 上对一个获授权的已有小型仓库完成：clone → Feature 分支 → 编码 → 候选 commit → 门禁 → 独立审查 → 返工 → 复审。（§14.24）
- [ ] 故障注入（测试失败、工具空结果、Gateway 重启、派发回执丢失、Attempt 卡住）在预算内自动恢复并完成；预算耗尽时给出原因与操作。（除「工具空结果」外均有 EC2 证据，§14.21）
- [x] 换工具与重规划在记录和报告中可见。（D38 §14.14、D41 §14.18；映射来源 §14.17）
- [ ] 审查通过后远端出现对应 Feature 分支，commit 与已验证候选一致；回执丢失与远端变化不产生重复或覆盖。

## 7．M3 客户端直连与控制台

### 交付

1. **`octopus connect <client>`**：为 Claude Code、Codex、Kiro CLI、Kiro IDE 合并式写入 Skill 与 MCP 配置；写前备份，支持 `--dry-run`。
2. **CLI**：`login`、`submit`、`plan`、`confirm`、`status`、`watch`、`report`、`pull`、`sync`、`continue`、`approvals`、`search`。
3. **MCP 工具**：现有工具 + 请求、计划确认、搜索、报告、批准、关联。
4. **本地 / 远端联动**：未推送提交检测与 `--push-base`；`pull` 拉回分支与上下文到 `.octopus/sessions/<id>/`；`sync` 让远端快进到本地推送的提交并重新门禁与审查；`.octopus/session.json` 会话关联。
5. **监控控制台**：列表、过滤（状态 / 仓库 / 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间）、全文搜索、服务端分页；只读详情；少量操作（停止、续作、重试、批准、关闭 / 重开、导出、复制 CLI 命令）。

### 验收条件

- [ ] 四个客户端各自完成：connect → submit → status → pull → 本地修改并推送 → sync → 远端复审通过。（D58，EC2 c4469aa，2026-10-22：Claude Code 的 MCP 工具与 `octopus` 命令行各走通——SSH 克隆的本地仓库提交（按 HTTPS），远端实现（claude）、审查（codex）、Evaluator 与 Knowledge 通过；pull 增量拉回分支与上下文；本地提交 sync 后写代码任务的「本地同步」Attempt 运行检查、审查与 Evaluator 重新进行，Evaluator 指出本地改动使末行验收不成立，自动纠错返工后完成；返工后 pull 快进；带 `.env` 的 sync 被拒（sync_secret_found）。待：Codex、Kiro IDE 由负责人在各自客户端核对，run-45aa7b87…）
- [ ] connect 重复执行结果不变，用户原有 Skills 与 MCP 配置保留。
- [x] 控制台可按全部过滤条件与全文检索找到 Session，详情覆盖 Pipeline、门禁、Attempt、恢复记录、事件、diff、报告。（D56，EC2 8cfef39，2026-10-22：经控制台所用的 API 实测，状态 / 空间 / 仓库（短写与 https 地址）/ 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间 / 全文及组合均命中，94 个会话 14 页无重复无遗漏、与总数一致；候选的代码改动可读；控制台界面由测试覆盖，负责人可在控制台复核）

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
| K01 | OpenCode 执行曾返回空结果或不完整交付，需原生续接补齐（M1 本地与 EC2 均复现；EC2 上 shell 写文件被 KiroCrew 工具门禁拒绝；2026-10-16 YOLO 常驻下复测仍只返回 `_No response._`）。**负责人 2026-10-16：先停用**——Worker `disabled_tools` 默认 `["opencode"]` | M2 |
| K02 | 含 `/`、`:`、`[]` 的模型 ID 被原生 spawn 格式拒绝（影响 OpenCode `provider/model` 与部分 Codex 模型 ID） | M1 |
| K03 | 原生浏览器登录覆盖整个 Gateway，不是单 Session 票据；平台撤权不会使其失效 | M1 / M4 |
| K04 | 新版 Web 未代理原生权限批准；存在待批准项时停止可能挂起 | M2（D38 第 2 期：控制台与 MCP 代理批准，见 §14.14） |
| K05 | Kiro 不报告实际模型；模型目录依赖 ACP 缓存，公布的模型不代表有配额 | M1 |
| K06 | 父会话 note 上限约 4000 字符，Worker 截断到 3800；截断不能当作完整交接 | M2 |
| K07 | 原生 continue 不读取 `reasoning_effort`；原生 spawn 没有 HTTP 幂等键 | M1 |
| K08 | Coordinator “不派生任务”的限制来自提示词，不是工具级隔离；其诊断可能有误 | M2 |
| K09 | Engine 单线程串行推进，慢 Run 会阻塞其他 Run | M1 |
| K10 | ~~SQLite 没有 schema 迁移机制（M1）~~（已有迁移）；~~Worker Token 以明文存放在数据库 `workers.config` 中~~（D59：以 D35 主密钥密封为 `token_sealed`，只在调用时解开，启动时密封旧数据；EC2 30ca5b7 已核对） | M1 / M4 |
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

尚无发布。首个发布计划为 **MVP 0.1.0**：范围、流程与架构见 [`docs/MVP-0.1.0.md`](MVP-0.1.0.md)，图文介绍见 [`docs/KiroCrew-ON-MVP-0.1.0.html`](KiroCrew-ON-MVP-0.1.0.html)；平台与 Worker 上报版本 `0.1.0`。

## 14．进度与未完成事项（2026-10-12，2026-10-19 更新）

状态：✅ 完成 · ◐ 部分 · ○ 未开始。“EC2”表示已在 EC2 实测。

### 14.1 M1 运行底座

14 项交付与 §5 验收条件全部完成（本地与 EC2）。✅ 负责人 2026-10-14 确认 M1 验收，并要求向 `main` 发起合并请求、继续 M2 / M3 / M4（下一项：会话独立预览域名（通配证书），其后为 M2 候选与分支、命名门禁与 Feature 报告）。OpenCode（K01）按 M2 回退链与门禁层排查处理。

### 14.2 M2 核心闭环

| # | 交付 | 状态 | 已有 / 剩余 |
|---|---|---|---|
| 1 | Request | ✅ EC2 | D55（2026-10-21，见 OPERATIONS「Request、准入规则与映射偏好」）：每次提交登记 Request（来源客户端、需求、源码、显式选择、策略快照），会话带 `request`；`GET /v1/requests[/<id>]`、MCP `octopus_requests`、`octopus_start` 回执带 `request_id`、会话页「来自请求…」。先一对一，「重新发起」以后做 |
| 2 | Pipeline 生成 | ◐ EC2 | 已有：Kiro Agent 规划（D26：意图、补全、1–7 个任务与 Agent 映射，追加到同一 Session）、`plan=match` 规则匹配、工作流文档（JSON / YAML / Markdown）解析与规范化。剩余：schema 2、规划阶段的只读源码检查 |
| 3 | 门禁目录 | ◐ EC2 | D37 第 1 期（分支 `feat/m11-gates`，✅ EC2 2026-10-14，见 §14.13）：命名门禁 `native_completed`、`identity_match`、`handoff_present`、`outcome_valid`、`outcome_pass`、`diff_present`、`no_secrets`、`checks_pass`（平台运行 `verify`）、`review_bound`，逐条入库并在控制台与 MCP 展示；发布只推送已验证候选（`candidate_unchanged`）。第 3 期（✅ EC2 2026-10-15，见 §14.13）：`artifacts_locked`、`tests_fail_on_baseline` |
| 4 | 映射策略 | ◐ | 已有：Prompt / 工作流显式指定；D38 第 1 期（分支 `feat/m11-gates`，见 §14.14）：工具层失败由 KiroCrew 协调者推荐换用的工具与模型（`switch_tool`），平台按执行环境核对并记录，规划时 Kiro 看到各工具是否可用；不内置回退链（原 OpenCode→Claude 的代码改派已去掉）。映射来源（本机完成）：每个任务写明工具与模型来自提交的 steps、工作流（名称与版本）、模板 / 规则匹配的模板、导入的文档、KiroCrew 规划、续作时的用户修改或协调者换用，控制台、MCP 与报告可见。需求中指定（D45，✅ EC2 2026-10-16，见 §14.23）：规划者标注、平台照用、不可用时如实拒绝不替换。个人 / Team 偏好（D55，本机）：按类别或角色对应工具 / 模型，进入规划提示供规划者参考，采纳时映射来源写「按个人 / Team 偏好」；不做平台默认链。剩余：模板 / 工作流的 `tool: auto`（现有契约没有 auto 工具） |
| 5 | 准入 | ✅ EC2 | 计划确认：D44（✅ EC2 2026-10-16，见 §14.22：`admission: confirm` 停在「计划待确认」，确认或续作后执行）。D55（本机）：平台默认改为确认；个人 / Team 按仓库范围的规则（`auto` / `confirm`），判定依据写进会话与 Request 的策略快照，会话页可见；控制台维护、MCP 只读 |
| 6 | 恢复阶梯 | ◐ EC2 | 已有：Worker / Gateway 不可用退避（M1）、协调者诊断（重试、退回上游返工、复审、换工具（D38））与轮数上限。D39（本机完成，见 §14.15）：看门狗停止没有进展或运行超时的原生任务、自动重试一次、再交给诊断。重规划：D41（✅ EC2 2026-10-16，见 §14.18）。预算：D42（✅ EC2 2026-10-16，见 §14.19：轮数、自动重试次数与时长，从续作起重新计算）。等待与自动恢复：D43（✅ EC2 2026-10-16，见 §14.20）。剩余：预算按个人 / Team 配置（设计 §8.1，随 `policies` 表） |
| 7 | 批准代理 | ◐ | D38 第 2 期（本机完成，见 §14.14）：会话父 slot 的原生工具请求与状态级批准在控制台「待批准」卡片与 MCP（`octopus_approvals`、`octopus_approve`，答复前须用户同意）中可见并可答复；创建者、Team owner、管理员可答复，写审计。剩余：EC2 验收、CLI |
| 8 | 候选与分支 | ◐ EC2 | D37 第 1 期（✅ EC2，§14.13）：每个改代码的任务在 `octopus/<run>` 上留下候选提交（检出秘密时分支不动），审查绑定候选；D35 的发布推送已验证候选并创建 Draft PR / MR |
| 9 | 远端分支推送 | ◐ EC2 | D35 第 2 期：发布把已验证候选推到 `octopus/<run>` 并建 Draft PR / MR（✅ EC2，§14.11）；D47 自动发布、D48 新建仓库（✅ EC2，§14.27、§14.29）；不推默认分支、不 force push。剩余：回执丢失与远端变化的 EC2 核对；平台级 GitHub App（M5） |
| 10 | Feature 报告 | ✅ EC2 | D37 第 2 期（2026-10-14，2aa3941，见 §14.13）：Markdown / HTML 报告（需求与计划、门禁、候选、检查、审查、纠错、PR），控制台「报告」、`GET /v1/runs/<id>/report`、MCP `octopus_report` |
| 11 | Claude Code 基础 Skill | ✅ EC2 | D27：同一份 Skill 也安装到 Kiro CLI |

§6 验收条件（2026-10-19）：2、3、5 已勾选；4 除「工具空结果」外均有 EC2 证据（§14.21）；1 依赖 Request 表；6 的推送与 Draft PR 已在 EC2 验收（§14.11、14.27、14.29），尚缺回执丢失与远端变化不重复、不覆盖的 EC2 核对。

### 14.3 M3 客户端直连与控制台

| # | 交付 | 状态 | 已有 / 剩余 |
|---|---|---|---|
| 1 | `octopus connect` | ◐ EC2 | 已有：`claude`、`kiro`（Kiro CLI），合并写入 MCP 与 Skill、`--dry-run`。剩余：Codex、Kiro IDE（现为 `docs/CONNECT.md` 手动说明）、写前备份核对 |
| 2 | 用户 CLI | ✅ EC2 | D58：`octopus login / submit / plan / confirm / status / watch / report / pull / sync / continue / approvals / search`（中文摘要或 `--json`，默认用本仓库关联的会话）；运维命令不变 |
| 3 | MCP 工具 | ◐ | 已有 37 个：除工作流、teams、runs、status、preview、start、continue、recover、control 外，计划确认、验收、报告、批准、修订、补充说明、Request（`octopus_requests`，D55）、规则（`octopus_policies` 只读）；`octopus_runs` 按仓库、阶段、工具、客户端、提交者、时间过滤与全文搜索（D56，本机）。会话关联（`octopus_link`，D58，本机） |
| 4 | 本地 / 远端联动 | ✅ EC2 | D58：git bundle 交换提交；`.octopus/session.json` 会话关联；pull 拉回分支与上下文；sync 只接受快进，最后一个写代码的任务重新检查、下游重新审查；未推送提交检测与 `--push-base`；MCP `octopus_link / octopus_pull / octopus_sync` |
| 5 | 监控控制台 | ◐ EC2 | 已有（React，D19–D23）：列表、空间切换、状态筛选与关键字搜索（客户端）、详情（任务、Attempt 证据、恢复记录、事件轨迹）、续作 / 诊断 / 停止 / 关闭 / 重开 / 删除 / 导出 / 分配 Worker / 原生入口 / 预览、发起向导、工作流库、设置。门禁、报告、修订对比、待批准、发布、用量（D37–D47）。D56（本机）：服务端分页，按状态 / 仓库 / 分支 / 阶段 / 工具 / 客户端 / 提交者 / 时间过滤与全文搜索（筛选写进地址栏），每次 Attempt 的代码改动（diff）。剩余：复制 CLI 命令（随 M3-2）；`/classic/` 待对齐后移除 |

§7 验收条件均未勾选（依赖 pull / sync 与服务端搜索）。

### 14.4 提前实施：Team 执行环境（D28–D30，原 M4 范围）

| 阶段 | 状态 | 说明 |
|---|---|---|
| A 平台执行环境（共享 / 专属 / 空闲，Team 必须带环境，服务端自动选择） | ✅ EC2 | personal 设为共享，服务管理员与 dev1 的个人空间；`run-1eef4428…` 在共享环境完成 |
| B 独立 Worker 主机（私有 CA TLS、空闲登记、控制台分配） | ✅ EC2 | `w1`（m7i.xlarge）分配给「Web应用软件研发组」；`run-e0dff5b1…` 经 MCP 自动选择 w1 完成；隔离检查通过（外部访问 w1:8943 不通、w1 访问平台 API 不通、w1 上无 CA 私钥 / 管理员密码 / 源站密钥）；`run-b74e9f0d…` 在 w1 上 8 个任务全部通过 |
| C 原生入口经平台主机中转、`worker-host add`、管理员 API 与统一入口（D30） | ✅ EC2 | 2026-10-12：平台 `up`（规则 8918–8929，原地更新）与 `push`；`worker-host add w1` 全部步骤（对已有主机重复执行无副作用，已登录时跳过设备码）；`run-e0dff5b1…` 的原生入口经 w1 分发 → 平台中转 → 私网 TLS → w1 Gateway 打开，API 200、WebSocket `/api/ws` 101 保持 136 s，两跳访问日志不含 `token=`；`GET /v1/admin/environments` 实时列出两个环境与健康。验收中修复：安全组规则描述字符集、共享环境上 Team 会话的原生入口（边界所有者被误拒）、已设为共享的 Worker 主机重新登记、中转实测等待端口监听 |

剩余：`w2`、`w3` 扩容（负责人确认后）；同主机新 OS 用户边界（设计 §4.1–4.3，按需）；共享环境的记忆分隔（T7，等待 KiroCrew 提供成员创建与继承接口）；组织层、单 Session 原生票据（K03）、配额（M4-4 第 2 期）。D59 Worker Token 密封已在 EC2 核对；D60 排队可见本机完成，待 EC2。

### 14.6 会话修订历史与 Prompt 差异（D32）

✅ EC2（2026-10-12，`436233b`）：线上已有会话的旧修订按记录还原（`run-690b49c1…` 9 条：创建、续作、自动纠错；`run-63b33288…`
v4 → v6 显示规划 Prompt 新增的行）；控制台「修订历史」在改动处保留 2 行上下文、其余折叠。离线：Python 322、控制台 91、旧版回归 92。

### 14.15 恢复阶梯第 1 期：看门狗（D39，M2）

本机完成：Worker 报告运行中 Attempt 的进展（`state.json` 的 `updated_at`、轮数、最后工具）并可停止单个原生任务；
平台在没有进展超过 `idle_minutes`（默认 30）或总时长超过 `attempt_minutes`（默认 170，低于 KiroCrew 自己的 180 分钟期限）时停止、当场判定、自动重试一次，
重试仍卡住时交给协调者诊断；不能停止时绝不重试（避免两个 Agent 同时写同一工作区）。记录在控制台、MCP 与报告中可见。
- 最终审查（opus）：4 项重要问题已修复（停止超时或重启后仍按看门狗判定、等待批准的时间不算没有进展、没有进展信号时只按总时长、
  协调者区分不会结束的命令与工具挂起），另修 5 项次要；5 项次要记入 BACKLOG。

✅ EC2（2026-10-15，任务 `sleep 900`、`idle_minutes: 5`，两次运行）：
- `run-f09ff1ba…`（9462dc0）：第 2 次 Attempt 前台运行 `sleep` 5 分钟没有进展，看门狗停止（313 秒）并自动重试；重试同样卡住，
  看门狗再次停止，未运行第三次而交给诊断（该会话的 1 轮纠错已用完，会话 `blocked`）。
- `run-b736ad3e…`（b545803，审查修复后）：第 1 次 Attempt 在 5 分钟时被停止（原因写明最后工具，取自 KiroCrew 隐去后的值）并自动重试，
  不消耗纠错轮次；停止后主机上始终至多一个 `sleep 900`（旧进程已被 KiroCrew 收回，没有两个 Agent 同时运行）。
- 说明：Claude Code 的 shell 工具对前台命令有时限，Agent 有时不等 `sleep 900` 结束就返回（判为缺少交接，进入诊断），与看门狗无关。

### 14.14 换工具第 1 期（D38，M2）

本机完成（分支 `feat/m11-gates`）：
- Worker `/health` 的 `tools` 报告每个 Coding 工具能否启动（配置路径或 Gateway 的搜索路径；Kiro 另报登录），平台登记或「刷新模型」时保存。
- 工具层失败（平台记录中不可用的工具不派发、`model_unavailable` / `model_transport_unsupported`、实际后端不符）记为
  `failure_type: tool` 并进入诊断；协调者可给出 `switch_tool`。
- 平台按诊断时的执行环境核对：工具可用、模型已发布、与失败的配置不同、审查与被审查代码的工具不同、会话未关闭换工具（`recovery.switch_tools: false`）。
- 新 Attempt 的证据带 `switched_from`，控制台、MCP 状态与报告写明「由协调者从 X 换为 Y」；修订历史与变更记录（`task.tool_switched`）同步。
- 规划提示列出工具可用性与已知限制（K01），不再由代码把 OpenCode 改派 Claude。

✅ EC2（2026-10-14，567e331，测试仓库 `LiboMa/bedrock-claude-call`）16/16：
- 刷新能力后，平台主机与 w1 的 Worker 都报告了四个工具（均可用，Kiro 已登录）。
- **换工具**（`run-c5ebe889…`，实现 claude + 未发布的模型 `claude-not-published`，审查 codex）：
  - 实现的第 1 次派发被拒（`model_unavailable`，`failure_type: tool`）；协调者推荐 `switch_tool` claude / auto，
    理由中写明不选 codex（其后审查所用）与 opencode（K01）；实现以 claude / auto 完成。
  - 审查（codex）两次被运行时取消（自动重试后再现）；第 2 轮协调者按「native_error 且有 transient_retry_of」推荐换为 kiro
    （与被审查代码的 claude 不同），审查通过，会话完成。
  - 控制台 Attempt、MCP `latest.switched_from`、报告的「换工具」行、修订历史与变更记录 `task.tool_switched` 一致。
- **关闭换工具**（`run-ec6135df…`，`recovery.switch_tools: false`）：协调者被告知不可换工具，给出 `wait_for_user` 与恢复办法，
  `attention.title` 为「需要换用工具或模型」，没有应用换工具。
- **规划**：规划任务的提示列出各工具是否可用与已知限制（K01），不再有代码改派。
- 最终审查（opus）：1 项重要（换用后的实现与其后的审查同一工具；模板中未标 kind 的审查）与 5 项升级为重要的问题已修复
  （重试后仍标注换用来源、协调者提示如实写明原生错误重试后再现、过期能力的提示、未核实的后端仍等待用户、控制台纠错记录）。

第 2 期「批准代理」（K04）本机完成：
- Worker 只读本会话父 slot 的两类待批准项：会话内工具请求（`pending_approval_info`）与状态级批准（`/api/approvals` 中属于该 slot 的记录）。
  答复前重新核对标识（`request_mid` / `instance`），他人会话、已答复或已被替换的请求一律 `approval_not_pending`。
- 平台 `GET /v1/runs/<id>/approvals`、`POST /v1/runs/<id>/approvals/answer`：创建者、Team owner、管理员可答复（与发布相同），
  审计 `approval.answer`，变更记录 `approval.answered`；旧 Worker 说明不支持；会话为 `waiting_approval` 时 `attention` 写明。
- 控制台「待批准」卡片（确认前展开完整内容）；MCP `octopus_approvals` / `octopus_approve`（工具说明与 Skill 要求先得到用户同意）。

EC2（2026-10-15，a402caa）部分验收 6/6：
- 列表经 Worker 读到真实 Gateway（`supported: true`）；答复已不在等待中的请求得到 409 `approval_not_pending`；缺少标识时 400 写明缺少的字段；
  MCP `octopus_approvals` / `octopus_approve` 经平台调用；会话视图对管理员 `permissions.publish` 为真。
- **未能实测真实的批准与拒绝**：此执行边界的 KiroCrew 处于 YOLO（`config.json` 的 `yolo_duration: until_shutdown`，在 Dashboard 中开启，
  Gateway 重启前一直有效）。父会话自己运行的 shell 命令（`run-287fe39b…` 写出 `kc-approval-1.txt`）与父会话自行派生的子 Agent 都直接执行，
  没有任何待批准项。关闭 YOLO 影响该边界上的所有会话，留待负责人决定；关闭后按计划末尾的步骤补测批准与拒绝。
- 最终审查（opus）：1 项重要（控制台的确认可能答复被替换的同 id 请求）与 9 项升级为重要的问题已修复；4 项次要记入 BACKLOG。
- **YOLO 常驻**（负责人 2026-10-15「务必自动保持 YOLO 模式开启」）：执行边界配置 `crew_yolo` 默认开启，Gateway 启动时写入
  `agent.dangerously_skip_permissions`（eb28bc0）。部署后平台主机与 w1 的 KiroCrew `yolo-status` 均为
  `active: true, source: config, permanent: true`，不再依赖 Dashboard 中手动开启、也不随重启失效；`crew_yolo = false` 恢复逐项批准。

### 14.13 候选提交与命名门禁（D37 第 1 期，M2）

✅ EC2（2026-10-14，c9881d3，测试仓库 `LiboMa/bedrock-claude-call`）：
- **成功路径**（`run-6de37646…`，实现 + 审查）：
  - 实现任务全部九个门禁中的八个通过（`review_bound` 属审查任务）；平台在 Worker 上运行 `verify`（退出码 0）。
  - 留下候选提交；审查任务写明 `reviewed_commit`，`review_bound` 通过，会话显示该候选为已验证候选。
  - 发布推送的正是它（Draft PR #3）；之后在工作区改动已跟踪文件时，发布 409 `publish_unverified`。
- **失败路径**（`run-3662576a…`）：
  - 一条在 Agent 看来通过、在平台提交候选后失败的检查（依赖文件尚未提交）：Agent 报告 pass，平台运行的检查退出码 1，`checks_pass` 失败。
  - 失败按门禁失败进入自动纠错，协调者如实诊断出检查与提交行为的矛盾；没有已验证候选的会话发布 409。
- **另见**（`run-56a21673…`）：任务的检查命令与 Prompt 互相矛盾时，Agent 报告 BLOCKED，协调者请用户二选一，不擅自决定。
- **最终审查**（opus）：1 项严重（检查命令留下后台进程时会话挂起）、5 项重要问题已修复：
  - Worker 拒绝生成候选不再挂起，按门禁失败进入纠错；
  - 检查留下的未跟踪文件不算改动；
  - 审查后的交付改动仍可发布；
  - 按时间取会话分支所在的候选；
  - 新会话没有已验证候选时不发布。
  
  另把检查命令不带 Worker 的 `PYTHONPATH`、无法启动时失败而不挂起列为重要并修复。

第 3 期「`artifacts_locked` 与 `tests_fail_on_baseline`」本机完成（2026-10-15）：
- 候选记录改动的文件名；声明了 `paths` 的任务修改此前已验收任务的交付物时须被 `paths` 覆盖，否则门禁失败并进入纠错。
- `test` 任务在 `checks_pass` 之后，在项目目录中原地切到会话基线并叠加测试文件再运行一次 `verify`：在改动之前也通过的测试不被接受。
  运行结束后必定恢复到候选；Worker 中途重启留下的基线在下一次操作前恢复；运行中的基线不被预览或下载请求打断。
- Agent 的任务说明与规划提示写明这两条门禁。
- 最终审查（opus）：2 项严重（为已有代码补测试时必然失败；重试按上一次 Attempt 计算）与 5 项重要问题已修复：任务从起点计算、
  基线为三方合并（冲突跳过）、不会丢失工作区状态时才切换（否则跳过）、`paths` 支持 `**/`、检查命令留下的文件不算交付物、
  基线运行期间预览 / 下载 / 发布返回 409；3 项次要记入 BACKLOG。

✅ EC2（2026-10-15，e7db020，测试仓库 `LiboMa/bedrock-claude-call`）：
- **真实的测试**（`run-1d7a7483…`，实现 `kc_mod.py` + 测试）：`tests_fail_on_baseline` 通过（基线上缺实现而失败）、
  `artifacts_locked` 通过，会话完成，工作区回到候选。
- **恒真的测试**（`run-18178af5…`）：第 1 次 Attempt 的 Agent 读到门禁说明后自行报告 BLOCKED（「两项要求冲突，需要协调者决定」）；
  协调者按契约重试，第 2 次 Attempt `tests_fail_on_baseline` 失败（「新增的测试在改动之前（基线 90f1d09）也通过」）。
- **越过 `paths` 修改实现文件**（`run-457982d8…`）：第 1 次 Attempt 的 Agent 同样自行拒绝；重试后 `artifacts_locked` 失败
  （「改动了已验收任务「实现 kc_mod」的交付物：kc_mod.py」）。
- **为已有代码补测试**（`run-a82d2bcd…`）：`tests_fail_on_baseline` 跳过（会话此前没有源码改动），会话完成。
- 三个会话结束后工作区都回到候选（`git status` 干净、HEAD 为候选、没有遗留标记）。

第 2 期「Feature 报告」✅ EC2（2026-10-14，2aa3941）12/12：
- `run-6de37646…` 的 Markdown 报告含计划、`checks_pass` / `review_bound`、候选、审查绑定、已验证候选与 PR #3 链接。
- HTML 报告带 `Content-Security-Policy`，无脚本，唯一的链接是 PR。
- 失败会话的报告显示未通过的门禁与纠错轮次；D37 之前的会话照常生成（「门禁：无记录」）；MCP `octopus_report` 返回 Markdown。
- 浏览器中「报告」对话框的 iframe 为 `sandbox="allow-popups allow-popups-to-escape-sandbox"`，控制台读不到其内容。
- 最终审查（opus）的 2 项重要问题已修复：Markdown 中会话文字不再能改变报告结构或伪造链接；显示任务契约并标出未报告的验收项。

### 14.12 会话独立预览域名（D36 第 3 期）

✅ EC2（2026-10-14，be34901）：负责人选定 `*.preview.tinyboat.blog`（通配证书）。
- **部署**：`deploy.sh up` 申请 `*.preview.tinyboat.blog` 证书并在 Route 53 完成 DNS 验证（1 分钟内签发），预览分发原地更新为通配别名并写入
  A / AAAA 记录；`push` 写入 `preview_domain`，并完成全部步骤。
- **打开链接**：直接指向会话自己的域名（`https://run-e769393d….preview.tinyboat.blog/…`），证书为 `CN=*.preview.tinyboat.blog`。
- **浏览器（Playwright）**：同一浏览器的两个标签页同时打开两个会话的 Vite 预览，B 读不到 A 的 `localStorage`；打开 B 之后 A 仍可访问，
  存储仍在（共用域名时打开 B 会替换 A 的 Cookie）。
- **拒绝**：A 的链接用在 B 的域名上 403、无 Cookie 403、未知会话的域名 403、非会话的名字 404。
- **平台主机实测**：新的 nginx 配置通过 `nginx -t`；应用为 `tinyboat.blog` / `.preview.tinyboat.blog` 设置的 Cookie 被改回会话自己的主机名。
- **审查与取舍**：最终审查（opus）的 2 项重要问题已修复（旧版本可回滚、证书等待与沿用待验证的证书），另把主机检查的回退加固为不退回共用模式。
  负责人知悉的取舍：应用的 JavaScript 仍可为 `.tinyboat.blog` 写 Cookie；预览与 `*.tinyboat.blog` 的站点同站，SameSite 不再隔开它们；
  专用域名只需改 `PREVIEW_SESSION_DOMAIN`。

### 14.11 代码仓库凭证第 1 期（D35）

离线完成（2026-10-13，分支 `feat/m8-repo-credentials`）：
- 凭证加密存储；个人 / Team / 平台作用域与授权；托管方测试。
- 提交时解析与会话绑定、改绑。
- Engine 在准备的那一刻解密，Worker 以 `GIT_ASKPASS` 克隆私有仓库（push 地址不可用、错误去除凭证、使用写审计）；源码失败的原因如实显示（原为「Worker 操作未完成」）。
- 控制台设置页、向导选择与会话页显示；MCP `octopus_credentials`。

本机测试用真实的需认证 HTTPS Git 仓库（自签证书 + git-http-backend）。最终审查（opus）的 5 项重要问题已修复：使用时复核、托管方检查的 SSRF 与跳转、
带凭证的 git 不读全局配置、会话页 / MCP 更换凭证、撤下留痕。

✅ EC2（2026-10-14，b894363）：负责人提供的 GitHub 细粒度 Token 以「kirocrew-on-admin」加密存入平台（本机临时文件随即删除），
托管方检查得到账号 LiboMa 与到期时间；`LiboMa/bedrock-claude-call` 的会话（`run-a11cccd9…`）以该凭证在平台主机准备好工作区，
会话页显示创建者与凭证，`credential.use` 审计与 `run.credentials` 变更记录写入；停用的凭证不能指定（403）、范围外的仓库被拒（409）；
所有 API 回答中都没有 Token。10/10。

第 2 期「平台代发布」离线完成（分支 `feat/m9-repo-publish`）：Worker 导出待发布的提交（自动提交、排除 .env 与依赖、拒绝疑似凭证），
平台只快进推送 `octopus/<run>` 并创建或沿用 Draft PR / MR，会话页「发布到仓库」与 MCP `octopus_publish`。本机以需认证的 HTTPS 仓库与假 GitHub / GitLab API 测试。
最终审查（opus）的 1 项严重、4 项重要问题已修复：检查覆盖 bundle 携带的全部历史（拒绝后分支不动，Agent 提交的 `.env` 与已删除的凭证
也被发现）、按原始内容检查任何文件、平台先限大小、推送后 PR / MR 失败也记录与审计、排除的目录不读入 Git。
✅ EC2（2026-10-14，937ba64，负责人同意在公开测试仓库实测）：只读凭证不能发布（409 `credential_read_only`）、默认分支被拒（400）；
会话 `run-26056305…` 执行一个小任务（新建 `KIROCREW_ON_PUBLISH_TEST.md`，未提交），发布后 GitHub 上出现分支 `octopus/run-26056305…` 与
Draft PR #1（目标 `main`，只含该文件，一个作者为 KiroCrew ON 的提交）；再次发布沿用 PR #1；变更记录两条 `run.published`，
每次使用写凭证都审计（`result`）；会话页显示 PR 链接。实测中发现令牌起初只有读权限：GitHub 以 403 拒绝推送，平台未记为已发布、
两次使用均审计为失败；随后把这种情况改为说明缺少的权限（409 `credential_permission`，`937ba64`），负责人给令牌补上 Contents 与
Pull requests 读写后发布成功。远端分支被他人改动时拒绝、`.env` 与疑似凭证不被推送由本机测试覆盖。

### 14.10 开发成果快速预览第 1 期「下载」（D36）

✅ EC2（2026-10-13）：启动声明解析与自动识别、Worker 流式打包、平台签名下载与转发、MCP `octopus_download`、控制台「下载源码」、
规划要求交付任务写启动声明。最终审查后的修复：打包出错时 zip 不写结尾、平台严格校验结尾记录（不再把残缺包记为已下载）；
目录项（含空目录）；`.git/config` 只保留格式 / remote / branch、不打包 hooks；说明文件含依赖安装；签发前在 Worker 试算
（`bytes_estimate`，超限与离线直接报错）；MCP 解压保留可执行位。负责人确认后新建项目也 `git init`（`octopus/<run>` 与首个 commit）。
验收（不执行任务）：平台主机与 `w1` 各下载一个会话（经 CloudFront，带试算），不含依赖与 `.env`，变更记录有 `run.downloaded`；
篡改链接 403、外人 404、无源码 409、10 分钟后 403 `archive_expired`；MCP 解压与非空目录拒绝；Git 来源的 `run-bca9c588…`
解压后 refs 目录在、hooks 已去掉、config 已清理、`git log` / `git status` 可用；两台主机以执行用户新建项目得到 Git 仓库。
同日控制台加上自定义域名 `https://kirocrew-on.tinyboat.blog`（`*.tinyboat.blog` 证书、Route 53 别名记录；cloudfront.net 域名保留）。
第 2 期（浏览器内远程预览）：计划 `docs/superpowers/plans/2026-10-13-preview-remote.md`。✅ EC2（2026-10-14，b894363）21/21：
平台主机的 Vite 项目 7 s 启动，打开页清除预览域的站点数据、一次性链接换取 `__Host-` Cookie 后进入应用，应用经预览分发回答且不缓存，
Vite 客户端模块可用；打开链接第二次使用被拒、无 Cookie 时为拒绝页、Service Worker 不能注册；同一执行环境第 4 个预览 409 `app_limit`，
腾出位置后静态站点运行、点文件不提供；`w1` 的应用经平台主机（私网 TLS + 校验头）回答，停止后为拒绝页；外人申请链接或启动均 404；
变更记录有 `app.started` / `app.stopped`；MCP 状态、打开与停止。浏览器（Playwright）中修改 `src/styles.css` 后样式即时更新、页面未重载
（Vite HMR 经 CloudFront 与 nginx 的 WebSocket），改回后同样更新。无需登录的检查：伪造或缺少校验头的 `w1` 预览端口 403、
本机未带门禁头的 `preview-auth` 404。

### 14.9 访问管理第 3 期「MCP 与角色调用」（D31）

离线完成（2026-10-13）：`octopus connect` 以本人账号签发 30 天 Token（`read, execute, manage`），`--admin` 另含 admin、7 天，
完成后登出临时登录；MCP 新增 `octopus_me`、`octopus_environments`、`octopus_assign_worker`、`octopus_changes` 与三个管理员工具
（用户启停与升降、Team 管理、执行环境管理；不创建用户、不设密码），按 Token scope 列出；提交不再在客户端预选 Worker；
错误以 `{status, code, error}` 返回。
✅ EC2（2026-10-13，客户端改动，平台无需部署；不执行任务）15 项：测试用户经 `issue_token` 得到唯一一枚 `claude MCP`
（read, execute, manage），临时登录已登出；普通账号 `--admin` 被拒且同样登出；管理员 `--admin` 得到含 admin 的 7 天 Token。
本机 MCP 服务以普通 Token 列出角色工具、不列管理员工具，`octopus_me` 显示 e2e-member · 未提权 · mcp，环境与会话可查，调用
管理员工具得结构化 403；以 admin Token 列出三个管理员工具，用户与执行环境（personal、w1）可列出，变更记录按人筛选，
不存在的用户名得结构化 404。验收后两枚 Token 已吊销、`e2e-member` 停用。独立审查 3 个 Important 与 4 个上调项修复后重跑 15/15；
离线 Python 443、控制台 108、旧版回归 92。

### 14.39 模型列表与自动发现（D51）

本机完成，未部署：任务的模型从下拉中选择该执行环境公布的模型（「自动」时也列出全部；此前输入框按当前值过滤建议，「自动」时只剩 auto），
模型取自将要执行的环境；Worker 在后台对尚无模型的可用工具各做一轮固定探测，KiroCrew 记下公布的模型，平台 10 分钟内带上——无须人为操作
（负责人 2026-10-20）；codex 报告其配置的默认模型。待 EC2 验收：部署后不做任何操作，owner 边界的 claude 在 25 分钟内出现模型列表。

### 14.38 编码技能包（D50）

本机完成，已部署 EC2（30a377f，各 Worker 报告 5 个技能）：平台维护五个方法类技能——测试先行、系统化调试、完成前验证、按评审返工、前端设计（改编自 superpowers 6.4.1 与
Anthropic frontend-design，附许可证，去掉询问用户、提交推送、开 worktree、写计划文件）。任务契约 `skills`（至多 4 个）由 KiroCrew 规划者按任务
推荐，Worker 复制到每个 Attempt 目录并在任务说明加「## 方法」（与契约冲突时以契约为准），三个后端一致；证据记名称与 sha256；控制台、报告、MCP
显示。流程类（brainstorming、计划、执行）由平台的规划、计划确认、门禁与 Evaluator 承担。终审 3 项已修（规划者的畸形 `skills` 不拒整份计划、
修订历史记录「方法」、只发 Worker 技能包里有的）。待 EC2 验收：界面类需求的规划推荐 `frontend-design`，实现任务按测试先行交付。

### 14.37 每人一个边界第 1 期（D49）

本机完成，已部署 EC2：一台主机可放多人的 KiroCrew 边界（OS 用户 `crew-<名称>`、各自端口与 systemd 资源上限）；`deploy.sh boundary add`、
`boundaries`、`kiro-login --boundary`、`tunnel`；个人会话未指定执行环境时在此人的专属边界执行。2026-10-19 在平台主机为 admin 建 owner 边界
（Gateway 5481、Worker 9001），隔离检查通过，两个边界的同时升级已验证；kiro 待登录。待：owner 边界上的会话验收；第 2 期调度（Team 会话在
提交者的边界执行）：负责人 2026-10-19 选定无边界时回退到 Team 的环境并提示，本机完成（spec `2026-10-19-session-placement-design.md`；`GET /v1/placement`、控制台向导默认交给平台选择、原生入口只给边界主人；R10 只有边界主人在自己的边界上下达指令），已部署 EC2（d9f57c3；主机装好无头浏览器依赖，Chromium 实测可启动），待 EC2 验收。

### 14.36 用量（D46 P6a）

本机完成，已部署 EC2（8b7c0ed）：每个 Attempt 的用量（kiro 的 credits 与时长，Claude Code / Codex 的 token、费用与时长）进入证据并按会话合计；
任务卡片、报告「## 用量」与 MCP 精简状态显示。待 EC2 验收：报告与合计的数值与 KiroCrew 一致。工具时间线（P6b）见 BACKLOG。

### 14.35 运行中的补充说明与释放对话（D46 P5b）

本机完成，已部署 EC2（30a377f，各 Worker 报告 `steer`）：创建者、Team owner、管理员可给运行中的 Kiro 任务补充说明（控制台、MCP `octopus_steer`、`POST /v1/runs/<id>/steer`，经
KiroCrew steer 中断后送达，写审计与时间线）；codex 与 Claude Code 的任务如实拒绝（KiroCrew 0.10.0 只支持 kiro 后端）；删除会话时释放其可续接的
原生对话。待 EC2 验收。

### 14.34 保留上下文的返工（D46 P5a）

本机完成，已部署 EC2（8b7c0ed）：平台安排的返工（同一工具、模型与 Effort）续接任务原来的 KiroCrew 对话（`/api/spawn/{对话}/continue`），
被拒时重新派发并记原因；用户续作、换工具与自动重试不续接。2026-10-19 EC2 发现：续接的运行都以「实际 ACP 后端未核实」被 `identity_match` 拦下
（KiroCrew 按对话的 key 写 routing 记录，Worker 按本次运行查找；5 次，kiro / codex / claude 都有），已修复（8a7c3f6，2026-10-20 随 30a377f 部署）。待 EC2 验收：返工的 Agent 引用上一轮自己的工作，门禁通过。

### 14.33 Knowledge（D46 P4）

本机完成，已部署 EC2（8b7c0ed）：专属执行环境上源码来自仓库的会话完成前，平台追加只读的「Knowledge 沉淀」任务，把至多 5 条项目约定与坑写成
KiroCrew lessons（`repo_scope` 为仓库标记），之后同一空间同一仓库的会话自动注入；共享环境不沉淀、不注入（负责人 2026-10-18）；会话页可删除，
台账与审计可查（OPERATIONS「Knowledge（D46 P4）」）。待 EC2 验收：两次会话的写入与注入、另一空间看不到、删除。

### 14.32 BACKLOG 防线项（负责人 2026-10-18：先清防线项）

- **部署维护期**（fc75650，复盘 #8）：Gateway 需要重启时先排空——Worker 的 `.maintenance` 标记下不接受新的准备与派发（平台自动退避重试，会话显示
  「执行环境维护中（部署），稍后自动继续」），等运行中的原生任务结束（最多 30 分钟）后重启。
  ✅ EC2（02f44e3，`run-49b58fad…`）：标记下新会话停在准备并写明维护中，移除后自动继续到「计划待确认」。
  ✅ EC2 端到端（9f820ac，会改动 Gateway 的部署，`run-136595fa…`）：任务运行中部署，安装日志「等待 1 个原生任务结束后重启 Gateway」，任务结束后才重启；
  该任务只执行 1 次（无 runtime_exited 重试），会话完成。
- **KiroCrew HTTP 字段契约**（02f44e3）：平台发给 KiroCrew 的 10 个请求体只许用其处理函数读取的键（AST 扫描），部署时原生测试核对已安装 KiroCrew
  的处理模块仍读取这些键。✅ 平台主机与 w1 原生测试 11/11。
- **延后项第一批**（6e853db）：会话页开启自动发布按创建者核对凭证；新建源码启动时要求自动发布须给仓库（✅ EC2：409 `publish_target_missing`）；
  发布仓库比对不区分大小写；已有空仓库在时间线为「发布到已有的空仓库」、`run.published` 带新仓库地址；停止后仍等待验收的会话不送诊断。
- **KiroCrew 契约后续**（9f820ac）：现有边界的 config.json 去掉平台以前写入、KiroCrew 从未读取的键，原生测试核对现有边界（✅ 平台主机与 w1 12/12）；
  协调者另以 `managedToolPolicy.exclude` 屏蔽 monitor 工具。
- **延后项第二批**（87332a1）：新仓库的凭证下拉按会话空间过滤并显示范围；Evaluator 缺项或缺证据记为 contract、判定不通过为 implementation；
  追加的 Evaluator 不复用历史 id；报告的 Evaluator 一节只在门禁生效时出现（✅ EC2：P3 的两个会话有该节、P2 的会话没有）。

### 14.31 Evaluator 门禁（D46 P3）

本机完成：改代码的新会话（执行契约 4、Worker 支持角色卡片）以 Evaluator 对每条验收项的 pass / fail / pending 与证据为准（负责人：以 Evaluator 为准、
改代码的会话都要经过）：缺项或缺证据交给协调者，任何 fail 返工，pending 停在「验收待确认」，由创建者、Team owner 或管理员在会话页或
MCP `octopus_accept` 确认（记确认者，不计预算）；缺少 Evaluator 时平台在完成前追加（工具按环境事实与审查独立性），运行前按当前验收项判定；
报告「验收（以 Evaluator 为准）」。离线：Python 1088、控制台 163；独立终审（opus）无严重问题，9 项重要问题已修复，其余记入 BACKLOG。
✅ EC2（2026-10-18，公开测试仓库，只读、未发布）：
(a) 7540427，`run-8165de8e…`：只有实现任务（claude）的会话，平台追加 Evaluator（codex，与作者不同的工具，来源「平台追加」），`evaluated` 通过，会话完成。
(b) 649d30a，`run-b819da61…`：验收项含「欢迎语的语气符合品牌风格（需人工判断）」，Evaluator 判 pending 并附核对到的原文与位置，会话停在「验收待确认」，
API 确认后完成，报告写明「admin 确认通过」。首次运行（`run-346afc30…`，7540427）Evaluator 把只有 pending 的结论写成 blocked、会话按缺输入停下——
说明与卡片补写「有 pending 而无 fail 时 decision 写 pass」，平台对只有 pending 的 blocked 结论按逐项判定处理（649d30a）。

### 14.30 角色卡片（D46 P2）

本机完成：任务可带 `role`（Spec 分析、SDE 开发、集成测试、安全测试、Challenge、Evaluator）；角色卡片按 KiroCrew 的 SKILL.md 格式存于
`octopus/roles/`，按任务内联到任务说明开头（负责人 2026-10-18：KiroCrew 的 skill 不能按任务选择）；`kind` 仍决定门禁；角色与类别须相容；
审查类角色不得使用作者工具（提交与续作拒绝、规划记说明）；规划提示列出角色；Worker 公布 `role_cards` 后才发送角色（未升级的 Worker 照常运行）；
控制台、MCP、报告、OpenAPI、修订对比显示角色。离线：Python 1052、控制台 161；独立终审（opus）无严重问题，5 项重要问题已修复，其余记入 BACKLOG。
✅ EC2（2026-10-18，15a2ca2，`run-f897a462…`，公开测试仓库，只读、未发布）：KiroCrew 规划把实现标为 `sde`（claude）、安全审查标为
`security_test`（codex，与作者不同的工具）；两个任务的 `instructions.md` 均以对应角色卡片开头；安全审查按严重度给出发现；会话完成。
同一会话发现：需求写明「不推送、不提交 PR」，规划说明仍记「要求推送或创建 PR」——推送判定未排除否定说法，已修正（否定的不算推送）。

### 14.29 发布到新仓库（D48，EC2 run-f7670bfb… 的需求）

本机完成：新建源码的会话发布时给出 `repository: {owner, name, private?}`（默认私有）与写凭证，平台在 Worker 的发布包通过全部检查后建仓库
（GitHub 用户 / 组织、GitLab 命名空间；或用已有的空仓库），会话基线推到 `main`、工作推到 `octopus/<run>` 并建 Draft PR；只建一次并记
`publish_target`，再次发布与自动发布沿用；非空的同名仓库、范围不覆盖的凭证在任何托管方调用前拒绝。规划者识别到建仓库只记建议，完成后
「需要处理」为「发布到新仓库待确认」；会话页「发布到新仓库」表单、向导「完成后发布到新仓库」；MCP / OpenAPI / Skill 同步。
离线：Python 1028、控制台 160；独立终审（opus）无严重问题，5 项重要问题已修复（含先建仓库后检查、基线推送不幂等、建议可取消「私有」），
其余记入 BACKLOG。
✅ EC2（2026-10-18，a044fb8，`run-4be521cf…`，负责人同意在 LiboMa 下建私有仓库、用凭证 full-access-repo）：`publish: {auto: true, repository,
credential}` 的新建源码会话（新建 README + 独立审查）完成后，平台自动新建私有仓库 `LiboMa/kirocrew-d48-acceptance`，`main` 为会话基线，
Draft PR #1 从 `octopus/run-4be521cf…` 指向 `main`、只含 README.md；会话页显示仓库与 PR 链接，时间线「已新建仓库」「已发布到仓库」「自动发布」。

### 14.28 协调者的可选目标（EC2 run-40a1…）

本机完成：协调者诊断时收到 `allowed_targets`（失败任务及其之前的任务）与此前各轮决定及其被拒原因（`previous_decisions[].refused`）；
指向失败任务之后的任务时，拒绝信息写明可选目标并说明「问题出在上游产出的代码时用 rework 指向该上游任务，其后的任务会自动重跑」；
规划者不再在审查之后另设「按审查修复」的任务。
✅ EC2（2026-10-17，ed78aa6，`run-6d7c132b…`，公开测试仓库，只读、未推送）：复现 run-40a1 的结构（实现 → 审查 → 按审查修复），
审查以日期不符 `changes_requested` → 协调者收到可选目标 `code`、`review`，依据 `allowed_targets` 返工上游的 `code`（未指向 `fix`，无被拒决定）
→ 复审通过 → `fix` 执行 → 会话完成。同一会话中，返工后的实现任务答复完成后 KiroCrew 未能清理残留进程组（`kill failed: … process group …
still has members`），被当成原生错误交给协调者，用掉一轮纠错；已改为与「ACP 进程退出」相同的 L0 自动重试（`runtime_cleanup`，不消耗纠错轮次）。

### 14.27 发布：需求要求或会话页开启时自动发布 Draft PR（D47，EC2 run-361cd491…）

本机完成：会话的发布策略 `publish: {auto}`——启动时（API / MCP / 控制台向导）或会话页随时开启；需求中明确要求提 PR / MR 时由 KiroCrew
规划者标注，平台据此开启（用户启动时明确设置的优先）；默认仍由人确认发布。会话完成后平台以创建者身份自动发布（与「发布到仓库」同样的核对），
同一版本只试一次；做不到时会话仍为完成，「需要处理」写明「自动发布未完成」与原因。规划中的推送任务记说明；任务因尝试推送失败时提示
「Agent 不能推送或创建 PR」，不再误导为权限问题。
✅ EC2（2026-10-17，ed78aa6，`run-d098543e…`，公开测试仓库 `LiboMa/bedrock-claude-call`，负责人同意建 Draft PR）：`publish: {auto: true}`
的会话（实现 + 独立审查）完成后，平台推送 `octopus/run-d098543e…` 并自动建出 Draft PR #5（只改 README.md，指向 main）；会话页显示
「已发布 · PR #5」与已勾选的「完成后自动发布 Draft PR」，时间线有「自动发布」。时间线中 `run.published` 等 15 种事件没有标题，已补齐，
并加契约测试：后端写入的每种事件在控制台都有标题（d417ee2 部署后时间线显示「已发布到仓库」）。
`run-f7670bfb…`（负责人：在 LiboMa 下新建 Repo 并推 README，新建源码）：发布只能到会话启动时的仓库，此前的提示仍指向「发布到仓库」——
已改为说明没有远端仓库、可「下载源码」或以已有仓库新建会话，规划者标注的「要求提交 PR」也只对来自仓库的会话开启自动发布。平台尚不能新建仓库。
同一会话首次执行时部署重启了运行时（`AcpProcessDied: Runtime process died during prompt`），此前未按 L0 重试，已与「ACP process exited」同样处理。

### 14.26 KiroCrew 配置与隔离（D46 P0）

本机完成：按 KiroCrew 0.10.0 的真实配置项修正——不再写 KiroCrew 不认识的 `memory.enabled`；
协调者只能派生平台的工具 Agent（`toolsSettings.subagent.availableAgents`），并排除 monitor 工具；工具 Agent 用固定并发；不再传 KiroCrew 忽略的
`solo_*` 字段；已有配置迁移时移除旧的允许列表。
✅ EC2（2026-10-17，ed78aa6，`run-d098543e…`）：新的允许列表下实现与审查任务均正常派发并完成。
❌→✅ 回归（3ec713b 引入，6077f92 修复）：P0 曾以 `memory_mode: incognito` 建立父会话，而 KiroCrew 只导出 persistent 的会话（`session_export.py:380`），
07:00 部署后新建的会话无法备份、不能关闭或删除（`run-6d7c132b…` 复现「原生备份未成功」）；incognito 也仍读记忆。父会话改回 persistent，
记忆按执行环境分隔（负责人 2026-10-17：每人独立 Worker，只有 Team 共用）；测试改用按记忆模式拒绝导出的严格假 KiroCrew。
✅ EC2（2026-10-17，d417ee2，`run-30eb53ef…`）：新会话（`admission: confirm`，未派发任务）关闭时导出通过记忆模式检查（只有说明性 note，
记为 `empty-session-observation`）并关闭；卡在关闭中的 `run-6d7c…` 已停止。
期间以 incognito 建立的会话：负责人决定（2026-10-17）KiroCrew 不导出 incognito / temporary 会话时记标明的观察记录、照常关闭与删除（7d0d733）；
incognito 的 slot 在 Gateway 重启后不恢复，关闭 / 删除反复 404，KiroCrew 已没有该 slot 时同样处理（ed5b0a6）。
✅ EC2（2026-10-17，2bde009）：负责人删除的 `run-6d7c…` 完成删除（`missing-session-observation.json`，`slot_not_found`，无伪造备份）；
部署时原生测试 9/9（含配置契约）在平台主机与 w1 通过。

### 14.25 Agent 提示 P1：一致性与正确性（D46）

KiroCrew 的通用 Agent 提示经盘点（25 处问题）后修正：统一规划者称呼、任务数与规则（首次与重新规划共用、环境事实截断，最大输入下不超 8000 字符）；
去掉默认工具组合；诊断与父会话各有提示（Owner 观察员）；Agent 不自行推送；任务说明按任务性质结尾（规划、设计、诊断不被要求开发，实现与交付须产生改动，
审查只读）；交接清单与检查命令说明写进任务说明；纠错只给被安排修复的任务且摘要化，复审只收到复验说明；内置模板补 `kind` 与验收项、不再用 OpenCode。
旧版工作流运行时保留原有提示（CI 回归 `tools/regress.sh` 恢复全绿，含此前因 YOLO 默认开启而失败的一项）。
✅ EC2（2026-10-16，7e48c7f）：`run-25ed73bb…`（KiroCrew 规划）规划任务的 `instructions.md` 为「只做分析与规划」与规划结论契约、实现任务写明须产生改动与交接清单；
`run-2ad9c5b3…`（实现 + 审查，审查第一次要求修改）返工的实现收到决定与失败摘要（不含整份上下文），复审收到「本轮返工后的复验要求」。

### 14.24 M2 验收 3：已有仓库的完整闭环

✅ EC2（2026-10-16，0668bc8，`run-b35a39c4…`，公开测试仓库 `LiboMa/bedrock-claude-call`，只读、未推送）：`admission: "confirm"` →
克隆后停在「计划待确认」→ API 确认 → 实现任务在会话分支留下候选 `1d465ca` → 独立审查（kiro）以缺少日期为由 `changes_requested`
（`outcome_pass` 门禁失败）→ 协调者安排返工 → 新候选 `56512a6` → 复审通过且绑定 `56512a6`（`review_bound`）→ 会话的已验证候选为 `56512a6`，完成。

### 14.23 映射策略：需求中指定的工具与模型（D45，M2 验收 2）

本机完成：KiroCrew 规划标注需求中点名的工具 / 模型（`requested`），平台照用（规划者改了会改回，未公布的指定模型不改 `auto`）；
不可用时派发前拒绝并写明「需求指定的 …」，不诊断、不换工具，由用户决定；映射来源「需求中指定：「…」」。
✅ EC2（2026-10-16，0668bc8）：`run-7c110c8a…`「…审查用 codex」——规划者把审查任务标为需求中指定 codex，由 codex 执行，来源
「需求中指定：「审查用 codex」」，会话完成；`run-fc90a6ca…`「用 claude 的 opus-9-preview 模型…」——模型保留为 opus-9-preview（未改 auto），
规划说明预先写明未公布，派发前停下并写明「需求指定的模型 opus-9-preview 未由执行环境公布」，未换模型、未请协调者。
离线：Python 921；独立审查 3 个 Important 与 3 个上调项已修复，其余记入 BACKLOG。

### 14.22 准入第 1 期：计划确认（D44，M2）

本机完成：会话可设 `admission: "confirm"`——规划照常完成，第一个执行任务派发前停在「计划待确认」，经控制台「确认并开始」、API 或 MCP
`octopus_confirm`（客户端须先向用户展示计划并得到同意）确认，或修改后续作；只确认一次；平台默认 `default_admission = auto`，不改变现有行为。
只有 KiroCrew 规划会话的规划任务在确认前运行；等待不计入预算；已归档 Team 的会话不能确认；视图、报告与时间线写明确认者。
✅ EC2（2026-10-16，68ad2f3，`run-e758afac…`）：`admission: "confirm"` 的 KiroCrew 规划会话——`plan` 完成后停在「计划待确认」，
列出规划的 `task-1`（claude，来源「KiroCrew 规划」），未派发；经 API 确认后执行完成，时间线有「计划待确认」与确认记录。
离线：Python 905、控制台 151；独立审查 4 个 Important 与 3 个上调项已修复，其余记入 BACKLOG。第 2 期：按仓库范围的自动准入与策略快照。

### 14.21 M2 故障注入（验收 4）

✅ EC2（2026-10-16，eaba137，`run-bccbfa36…`）：一个会话先后遇到 Gateway 重启（任务运行中 `systemctl restart octopus-gateway@personal`）
与注入的一次性失败测试：ACP 进程被 SIGTERM → L0 立即重试（`runtime_exited`，不经诊断；首次运行发现此前按普通原生错误交给协调者，已修复）→
重试后平台检查失败 → 协调者诊断为一次性 flaky 测试、重试 → 通过，审查通过，会话完成；预算消耗纠错 1/2 轮、自动重试 1/4 次；
每个 Attempt 对应唯一的原生任务，无重复派发。其他故障的 EC2 证据：Attempt 卡住（D39，§14.15）、派发回执丢失与 API / Worker / Gateway 重启（M1，§13）、
预算耗尽给出原因与操作（D42，§14.19）。尚未在 EC2 上注入：工具空结果（`result_missing`，本机测试覆盖）。

### 14.20 恢复阶梯第 4 期：等待与条件满足后自动恢复（D43，M2）

本机完成：平台定期读取 Worker 健康并保存执行环境能力（不必再手动刷新）；Kiro 任务因登录过期失败时，登录恢复后自动重跑（失败后须先见过过期，
登录一直正常的认证失败不重跑）；Worker 重新启用、执行环境重新服务该空间、仓库凭证重新可用后自动继续；「需要处理」写明在等什么、谁处理、
满足后自动继续；不计入预算（等待的时长从时长预算中扣除）。只认原生错误中的登录过期；失败后见到登录正常即交给人处理；
健康检查在边界、宿主账号或隔离方式变化时不保存。
✅ EC2（2026-10-16，a0e3a27，`run-8d2b1456…`）：两台 Worker 的健康由平台读取并保存（10 分钟一轮，04:19 与 04:29 两次）；
运行中停用执行环境 → 会话 `blocked`「Worker 已停用」、「需要处理」为「执行环境已停用」；约一分钟后重新启用 → 下一周期回到 `running`、
继续查看同一 Attempt 并完成，时间线「执行环境已重新启用，自动继续」。离线：Python 884、控制台 148；独立审查 3 个 Important 与 1 个上调项已修复，
其余记入 BACKLOG（含部署后重新登记会暂时清掉「最近检查」）。

### 14.19 恢复阶梯第 3 期：会话预算（D42，M2）

本机完成：自动纠错的预算——协调者轮数、自动重试次数（D34 瞬时与 D39 看门狗合计）与时长——从会话开始或用户最近一次续作 / 请求诊断起计算
（此前轮数从不重置，续作后同一会话不再自动纠错）；被预算拒绝时会话的「需要处理」写明是哪一项（记在失败 Attempt 的 `budget_refused`）；
会话停下后时长不再累计；分配 Worker 也重新计算；控制台、MCP 与报告显示消耗。
✅ EC2（2026-10-16，ffc2969，`run-0f51d4d3…`，`max_cycles: 1`、检查命令必定失败）：协调者判断检查命令属计划问题选 `replan`，`replan-1` 的规划结果
JSON 有误未通过校验，轮数 1/1 用完 → `blocked`，「需要处理」末尾为「自动纠错已达到 1 轮上限（自会话开始）；续作会重新计算预算。」；续作 `replan-1`
后预算显示「自上次续作 / 请求诊断起」、轮数从 0 计，重新规划成功、会话完成，报告的纠错一节为同一行。离线：Python 866、控制台 146；
独立审查 3 个 Important 与 1 个上调项已修复，其余记入 BACKLOG。

### 14.18 恢复阶梯第 2 期：重新规划（D41，M2）

协调者的 `replan` 决定——保留已验收任务，替换失败任务及其后的任务为 KiroCrew 重新规划的结果（`replan-1` 的提示列出被替换的任务、
失败原因与协调者的要求，需求与已验收任务的交接引用派发时的上下文），新任务 ID 避开历史、`depends_on` 随改名改写；每个会话一次；
修订历史、映射来源与控制台纠错记录可见。Worker 上报检查命令可用的常见命令（`commands`），规划者与协调者据此写 `verify`。
✅ EC2（2026-10-16，f65187e，`run-acffb322…`）：只写文档的任务 + 要求实现的审查——审查失败后协调者判断「计划缺少实现任务」选 `replan`，
`docs` 保留，`replan-1` 一次通过校验，追加的实现任务 `verify` 为 `python3 -m unittest -v`（据上报的命令，主机没有 `python`），
实现与审查各一次通过，会话完成，只用一轮纠错；修订记录「自动纠错：重新规划…」与「重新规划：追加 2 个任务」。
此前两次：修复前的版本 5/5；终审修复后的首次（c08c333）4/5——提示丢了任务字段示例致首次规划缺少 `prompt`，`verify` 用 `python`
退出码 127 而纠错轮次用完，均已修复。离线：Python 847；独立审查 1 个 Critical、4 个 Important 与 2 个上调项已修复，其余记入 BACKLOG。

### 14.17 任务的映射来源（M2 验收 2）

✅ EC2（2026-10-15，2439571）：已有会话即按新规则显示——`run-c5ebe889…` 的两个任务为「协调者从 claude / claude-not-published 换来…」
与「协调者从 codex / auto 换来…」，`run-9c1532df…` 为「平台：需求分析与规划（Kiro）」与「KiroCrew 规划」，`run-c19c1e15…` 为「提交时指定」；
MCP 状态与报告的计划表「来源」列一致。

### 14.16 交接精简与自适应轮询（D40，D34 后续）

本机完成：前序交接改为结构化摘要（结论、发现、候选、检查与完整交接文件的路径），不再附原始结果与证据；
运行中的 Attempt 按运行时长降低查看频率（2 / 5 / 10 / 20 秒），命令不受影响。
✅ EC2（2026-10-15，43035d0，`run-c19c1e15…`，实现 + 审查）：审查任务收到的前序交接为 485 字符（同类会话此前约 4.7K）——
结论、摘要、候选 `3385ac2` 与改动的文件、检查命令及退出码、完整交接文件的路径；审查照常完成。

### 14.8 会话变更记录与运行优化（D34）

✅ EC2（2026-10-13，`2ea7dd0`，平台与 `w1`）：不执行任务的验收 15 项通过——两台主机可达且 kiro-cli 登录为 ok；现有会话的变更记录
可查询，按人 / 系统筛选不重叠，导出包含全部变更；测试用户的结构化会话停在等待（无执行环境），`rerun` 列出前序任务得 400，
`rerun=[]` 时规则补全所有未验收的后续任务，修订对比显示验收项变化，每次写入记录操作者 `e2e-member` 与来源「控制台」，关闭记下
生命周期 open → closed，他人读取变更记录得 404；第二次无代码变化的部署日志显示「Gateway 未变化」、Gateway 进程不变。
会执行任务的验收（续作沿用已验收任务、规划后的派发携带需求与契约、结论逐项验收）待负责人同意后进行。
离线：Python 415、控制台 108、旧版回归 92；独立审查 3 个 Important 与 7 个上调项已修复，其余记入 BACKLOG。

### 14.7 访问管理第 2 期「Team 与 Worker 管理」（D33）

✅ EC2（2026-10-12，`b57cba1`）：迁移 4；平台与个人 Worker 0.1.0（`w1` 无 Worker 端改动，未重新部署）。验收 28/28，不执行任务：
管理员创建测试 Team「e2e-组」（共享环境，owner `e2e-member`）、owner 改名；管理员不是成员也能增删成员与设为 owner，唯一 owner 退出得 409，
另有 owner 后可退出；归档后提交与添加新成员得 409 `team_archived`、已有成员仍可改角色，恢复后正常；提交测试会话后立即把 Team 移出共享环境，
会话进入等待（0 个 Attempt）并说明「执行环境已更换或已移出该空间」，重新给环境后可分配，随后关闭；`personal`、`w1` 改名再改回；
退役 `w1` 得 409 `in_use`；普通成员可刷新共享环境的模型；全部写入审计。验收后测试 Team 归档、`e2e-member` 停用。
离线：Python 365、控制台 102、旧版回归 92；独立审查 2 个 Important 与 4 个上调项已修复，7 个次要项记入 BACKLOG。下一步：第 3 期 MCP 角色调用。

### 14.5 访问管理第 1 期「权限底座」（D31）

✅ EC2（2026-10-12，`3976dcd`）：迁移 3（迁移前备份 `octopus-v2-20261012T113115Z.sqlite3`）；平台上报版本 0.1.0。验收 18/18：
管理员登录带 `admin`；现有 `claude MCP` Token 仍能查看本人会话、调管理接口得 403；测试用户 `e2e-member` 的会话停在等待且不执行，
调管理接口得 403；管理员默认列表不含、`?all=1` 包含该会话，关闭成功并写入审计，原生入口得 403 并说明原因；改密码吊销其他 Token；
重置与停用立即吊销 Token、停用后不能登录。验收后 `e2e-member` 保持停用。离线：Python 316、控制台 88、旧版回归 92；独立审查的
6 个 Important 与 5 个上调项已修复（含 10 × 5 权限矩阵测试）。下一步：第 2 期 Team 与 Worker 管理、第 3 期 MCP 角色调用。

