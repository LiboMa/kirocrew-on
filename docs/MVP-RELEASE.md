# MVP Release 计划与状态

更新日期：2026-10-14　·　当前阶段：**M1 已验收（负责人 2026-10-14 确认）；最小 MVP 已在 EC2 跑通；Team 执行环境（D28：共享 / 专属 / 空闲，独立 Worker 主机）已在 EC2 验收**　·　本仓库可运行代码：**分支 `feat/m1-runtime-foundation`（origin 在 a57b30e：D36 第 1 期、新建项目 `git init`、控制台域名，经负责人同意以 `--no-verify` 推送）；远程预览（D36 第 2 期）与代码仓库凭证第 1 / 2 期（D35）在 `feat/m7`–`feat/m9`，已在 EC2 验收（937ba64）：远程预览、凭证第 1 期与第 2 期发布（公开测试仓库的 Draft PR #1）**　·　已发布版本：**无**

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
| M2 核心闭环 | 实现中（按 D25 只做最小 MVP 路径）：Kiro Agent 规划、工作流文档规范化、交接与 outcome 校验、协调者诊断返工已上线；Request、门禁目录、回退链、准入、完整恢复阶梯、批准代理、候选 commit 与分支推送、报告未开始。明细见 §14.2 |
| M3 客户端直连与控制台 | 实现中：Claude Code / Kiro CLI 的 connect + Skill + MCP 与 React 控制台已上线；Codex / Kiro IDE、用户 CLI、pull / sync、服务端分页与全文搜索未开始。明细见 §14.3 |
| M4 共享与组织 | 部分提前（D28–D30）：Team 执行环境与 Team 会话共享已在 EC2 验收；组织层、Worker Token 移出数据库、单 Session 票据（K03）、排队与配额未开始。明细见 §14.4 |
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

尚无发布。首个发布计划为 **MVP 0.1.0**：范围、流程与架构见 [`docs/MVP-0.1.0.md`](MVP-0.1.0.md)，图文介绍见 [`docs/KiroCrew-ON-MVP-0.1.0.html`](KiroCrew-ON-MVP-0.1.0.html)；平台与 Worker 上报版本 `0.1.0`。

## 14．进度与未完成事项（2026-10-12）

状态：✅ 完成 · ◐ 部分 · ○ 未开始。“EC2”表示已在 EC2 实测。

### 14.1 M1 运行底座

14 项交付与 §5 验收条件全部完成（本地与 EC2）。✅ 负责人 2026-10-14 确认 M1 验收，并要求向 `main` 发起合并请求、继续 M2 / M3 / M4（下一项：会话独立预览域名（通配证书），其后为 M2 候选与分支、命名门禁与 Feature 报告）。OpenCode（K01）按 M2 回退链与门禁层排查处理。

### 14.2 M2 核心闭环

| # | 交付 | 状态 | 已有 / 剩余 |
|---|---|---|---|
| 1 | Request | ○ | Session 的 `request_id` 提供幂等登记；Request 表、来源客户端与仓库字段、与 Session 的关联未建 |
| 2 | Pipeline 生成 | ◐ EC2 | 已有：Kiro Agent 规划（D26：意图、补全、1–7 个任务与 Agent 映射，追加到同一 Session）、`plan=match` 规则匹配、工作流文档（JSON / YAML / Markdown）解析与规范化。剩余：schema 2、规划阶段的只读源码检查 |
| 3 | 门禁目录 | ◐ EC2 | D37 第 1 期（分支 `feat/m11-gates`，✅ EC2 2026-10-14，见 §14.13）：命名门禁 `native_completed`、`identity_match`、`handoff_present`、`outcome_valid`、`outcome_pass`、`diff_present`、`no_secrets`、`checks_pass`（平台运行 `verify`）、`review_bound`，逐条入库并在控制台与 MCP 展示；发布只推送已验证候选（`candidate_unchanged`）。剩余：`tests_fail_on_baseline`、`artifacts_locked`（第 3 期）、Feature 报告（第 2 期） |
| 4 | 映射策略 | ◐ | 已有：Prompt / 工作流显式指定、K01 期间 OpenCode 由代码改派 Claude Code。剩余：个人 / Team 策略、能力校验后的通用回退链、映射来源记录 |
| 5 | 准入 | ○ | 确认模式与按仓库范围的自动准入 |
| 6 | 恢复阶梯 | ◐ EC2 | 已有：Worker / Gateway 不可用退避（M1）、协调者诊断（重试、退回上游返工、复审）与轮数上限。剩余：L0 瞬时重试、工具回退、重规划、预算、看门狗、条件满足后自动恢复 |
| 7 | 批准代理 | ○ | 现以 D14（仅平台派发的 spawn 原生 auto）运行；交互式批准请求在控制台 / MCP 中不可见 |
| 8 | 候选与分支 | ◐ | D37 第 1 期：每个改代码的任务在 `octopus/<run>` 上留下候选提交（检出秘密时分支不动），审查绑定候选；D35 的发布推送已验证候选并创建 Draft PR / MR |
| 9 | 远端分支推送 | ○ | 依赖仓库写凭据（GitHub Connector 写权限，M5 设计草案） |
| 10 | Feature 报告 | ✅ EC2 | D37 第 2 期（2026-10-14，2aa3941，见 §14.13）：Markdown / HTML 报告（需求与计划、门禁、候选、检查、审查、纠错、PR），控制台「报告」、`GET /v1/runs/<id>/report`、MCP `octopus_report` |
| 11 | Claude Code 基础 Skill | ✅ EC2 | D27：同一份 Skill 也安装到 Kiro CLI |

§6 验收条件均未勾选：其中 1、2、4 项依赖 Request、门禁与回退链，3、6 项依赖候选 commit 与分支推送。

### 14.3 M3 客户端直连与控制台

| # | 交付 | 状态 | 已有 / 剩余 |
|---|---|---|---|
| 1 | `octopus connect` | ◐ EC2 | 已有：`claude`、`kiro`（Kiro CLI），合并写入 MCP 与 Skill、`--dry-run`。剩余：Codex、Kiro IDE（现为 `docs/CONNECT.md` 手动说明）、写前备份核对 |
| 2 | 用户 CLI | ○ | 现有命令为运维类（init、serve、worker、connect、skill、wait-ready、bootstrap-worker、backup、archive、restore-verify）；login / submit / status / watch / pull / sync 等未做，用户经 MCP 与控制台操作 |
| 3 | MCP 工具 | ◐ EC2 | 已有 15 个：工作流列表 / 匹配 / 推荐 / 解析 / 保存 / 共享、teams、runs、status（精简与完整）、preview、start、continue、recover、control。剩余：Request、计划确认、搜索、报告、批准、会话关联 |
| 4 | 本地 / 远端联动 | ○ | 替代：`deploy.sh space <run-id>` 在主机上运行产出并转发到本机、`/preview`。pull / sync / 会话关联后排（BACKLOG） |
| 5 | 监控控制台 | ◐ EC2 | 已有（React，D19–D23）：列表、空间切换、状态筛选与关键字搜索（客户端）、详情（任务、Attempt 证据、恢复记录、事件轨迹）、续作 / 诊断 / 停止 / 关闭 / 重开 / 删除 / 导出 / 分配 Worker / 原生入口 / 预览、发起向导、工作流库、设置。剩余：服务端分页，按仓库 / 分支 / 工具 / 客户端 / 提交者 / 时间过滤，全文搜索，diff、门禁与报告视图；`/classic/` 待对齐后移除 |

§7 验收条件均未勾选（依赖 pull / sync 与服务端搜索）。

### 14.4 提前实施：Team 执行环境（D28–D30，原 M4 范围）

| 阶段 | 状态 | 说明 |
|---|---|---|
| A 平台执行环境（共享 / 专属 / 空闲，Team 必须带环境，服务端自动选择） | ✅ EC2 | personal 设为共享，服务管理员与 dev1 的个人空间；`run-1eef4428…` 在共享环境完成 |
| B 独立 Worker 主机（私有 CA TLS、空闲登记、控制台分配） | ✅ EC2 | `w1`（m7i.xlarge）分配给「Web应用软件研发组」；`run-e0dff5b1…` 经 MCP 自动选择 w1 完成；隔离检查通过（外部访问 w1:8943 不通、w1 访问平台 API 不通、w1 上无 CA 私钥 / 管理员密码 / 源站密钥）；`run-b74e9f0d…` 在 w1 上 8 个任务全部通过 |
| C 原生入口经平台主机中转、`worker-host add`、管理员 API 与统一入口（D30） | ✅ EC2 | 2026-10-12：平台 `up`（规则 8918–8929，原地更新）与 `push`；`worker-host add w1` 全部步骤（对已有主机重复执行无副作用，已登录时跳过设备码）；`run-e0dff5b1…` 的原生入口经 w1 分发 → 平台中转 → 私网 TLS → w1 Gateway 打开，API 200、WebSocket `/api/ws` 101 保持 136 s，两跳访问日志不含 `token=`；`GET /v1/admin/environments` 实时列出两个环境与健康。验收中修复：安全组规则描述字符集、共享环境上 Team 会话的原生入口（边界所有者被误拒）、已设为共享的 Worker 主机重新登记、中转实测等待端口监听 |

剩余：`w2`、`w3` 扩容（负责人确认后）；同主机新 OS 用户边界（设计 §4.1–4.3，按需）；共享环境的记忆分隔（T7，等待 KiroCrew 提供成员创建与继承接口）；组织层、Worker Token 移出数据库（K10）、单 Session 原生票据（K03）、排队与配额。

### 14.6 会话修订历史与 Prompt 差异（D32）

✅ EC2（2026-10-12，`436233b`）：线上已有会话的旧修订按记录还原（`run-690b49c1…` 9 条：创建、续作、自动纠错；`run-63b33288…`
v4 → v6 显示规划 Prompt 新增的行）；控制台「修订历史」在改动处保留 2 行上下文、其余折叠。离线：Python 322、控制台 91、旧版回归 92。

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

