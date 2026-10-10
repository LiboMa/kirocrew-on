# KiroCrew ON

以 **KiroCrew 原生机制与 ACP Harness 为执行底座**的业务研发平台。
开发者在自己的 AI Coding 工具（Claude Code、Codex、Kiro CLI、Kiro IDE）或 Web 中用自然语言提交开发任务；
后端登记请求，**根据需求自动生成并编排研发 Pipeline**，把每个阶段映射到合适的 Coding Agent，
在 Feature 分支上完成编码、确定性门禁、独立审查与纠错，以**完成优先**的自动重试机制持续推进直至完成；
本地与远端双向联动：远端结果可拉回本地继续，本地修改可交给远端复审和续作。

> **当前状态：设计阶段（2026-10-10）。**
> 本仓库目前只有规划与设计文档，**没有可运行代码**。下文所有功能都是目标，尚未实现、尚未验证。
> 现有可运行实现位于 `kirocrew-octopus`（新版 Team MVP，8918）和 `experiments/kirocrew-app-poc`
> （旧版 Workflow Studio / Pipeline，8917 / 8916），本仓库以「先等价迁入、后受测重构」的方式融合两版。

## 日常主线

```text
开发者在 Coding 工具里说：「为 X 功能实现 Y，用 Codex 审查」
  → Skill 调用确定性 CLI，自动识别仓库 remote / 分支 / HEAD，提交结构化请求
  → 后端登记 Request（幂等、可追踪）
  → 自动生成 Pipeline：模板规则匹配 / Kiro 生成阶段与提示词 → 代码规范化、插入门禁、映射 Agent
  → 用户确认计划（或按已授权的自动准入策略直接运行）
  → 在 Feature 分支上逐阶段执行：编码 → 候选 commit → 确定性门禁 → 独立审查
  → 失败时按恢复阶梯自动处理：瞬时重试 → 修复返工 → 工具回退 → 重规划 → 等待并自动恢复
  → 审查通过后推送远端 Feature 分支，生成 Feature 报告
  → 开发者在 Coding 工具里 status / watch / pull，或在 Web 控制台搜索查看；在原 Session 上继续迭代
```

## 核心功能（目标）

| # | 功能 | 说明 |
|---|---|---|
| 1 | Pipeline 自动生成与编排 | 根据需求自动生成或编排 Pipeline（融合旧版实现）；阶段、提示词、验收条件由 Kiro 生成，门禁与映射由代码规范化 |
| 2 | 自动拆分与 Agent 映射 | 阶段按策略映射到 Kiro / Claude Code / Codex / OpenCode；可在 Prompt 中指定，指定优先并记录来源 |
| 3 | 完成优先的稳定性 | 自主编排与自动重试为核心：先保证每个任务完成，再提升质量；预算内自动恢复，耗尽时给出原因与操作 |
| 4 | 客户端直连与本地 / 远端联动 | Claude Code、Codex、Kiro CLI、Kiro IDE 共用一份 Skill + MCP；提交、查询、拉回、同步、续作 |
| 5 | Skills 对接 | 安装 Skill 即可从 Coding 工具提交任务，不需要自己配置 MCP 或写平台 JSON |
| 6 | Feature 跟踪 | 每个 Feature 一个分支和一份按模板生成的报告；审查通过后推送远端分支 |
| 7 | 请求全程托管 | 后端接收、记录、分析处理并监控请求（看门狗、超时、卡住检测） |
| 8 | Session 监控控制台 | 灵活易用，以查看和搜索为主，不以编辑为主 |
| 9 | Session 共享 | Session 可共享给个人、Team、公司（组织）；共享 Session 不等于共享凭据或原生入口 |
| 10 | 代码优先原则 | 可以用代码实现的功能，不依赖 Agent 的通用性 |

工具范围：Kiro CLI、Claude Code、Codex、OpenCode。**明确排除 Gemini CLI。**

## 设计原则

### 代码与 Agent 的分工

| 由代码负责（确定性） | 由 Agent 负责（需要理解或判断） |
|---|---|
| 请求登记、幂等、状态机、版本 | 需求理解，Pipeline 阶段拆分与提示词（Kiro） |
| 从 cwd 识别仓库 remote / 分支 / HEAD / 未推送提交 | 编码（Claude Code / Codex / OpenCode / Kiro） |
| Pipeline 规范化：schema 校验、阶段上限、插入门禁 | 审查判断（Codex 等） |
| 阶段 → Agent 映射：策略表 + 显式指定 + 回退链 | 失败诊断与重规划建议（Kiro Coordinator） |
| 确定性门禁：测试、diff、哈希锁定、审查绑定候选 | 报告中的叙述性摘要（标注为 Agent 生成） |
| 候选 commit、Feature 分支、push、远端 ref 核对 | |
| 自动重试阶梯、预算、看门狗、重启核对 | |
| 报告生成、搜索过滤、权限分享、客户端接入配置 | |

用户在 Prompt 中的自然语言指定（如「用 Codex 审查」）由**用户自己的 Coding Agent** 在 Skill 中转成
结构化字段提交；后端用代码校验，并记录每个映射的来源（`user_prompt` / `workflow` / `policy` / `default`）。

### 继承的约束

- Engine 是唯一业务派发者；KiroCrew 决定原生 Agent 会话如何运行。
- 不绕过 KiroCrew 直接启动 Coding CLI，不重新实现 ACP，不以换框架或重写前端为默认方案。
- 技能、权限、认证使用各工具和 KiroCrew 的原生机制；Prompt 不能冒充权限控制。
- 共享工作流、共享 Session、共享凭据是三件事，互不隐式授权。
- 用户 Token、Worker Token、Gateway 原生凭据、仓库写凭据分开；不整体复制个人 HOME。
- 推荐不暗中执行；直接运行必须显式配置并留存策略快照。
- 换工具、重规划必须显式记录并在报告中可见；不通过隐藏失败宣告成功。
- 状态、模型、测试与部署结论如实报告；未知显示未知，不把目标写成已上线。

## 架构概览（目标形态，未部署）

```text
Claude Code · Codex · Kiro CLI · Kiro IDE · 浏览器
   │  Skill → octopus CLI / stdio MCP → HTTP API（用户 Token）
   │  M1–M3：SSM 端口转发      M4 起：HTTPS 入口
   ▼
[OS 用户 octopus]  octopus-api.service
   Web 控制台 + /v1 API + Request / Pipeline 服务 + Engine ── SQLite（WAL + FTS）
   │  Worker Token（回环 HTTP）
   ▼
[OS 用户 crew-<边界>]  octopus-worker@<边界>.service
   源码准备 · 派发 · 门禁执行 · 候选 commit · push · 核对
   │  同用户读 .local_secret → 换取原生 token（回环）
   ▼
[同一执行用户]  octopus-gateway@<边界>.service
   KiroCrew Gateway + composition（PocProviderRegistry）
   ▼
ACP：kiro-cli · claude-agent-acp → Claude Code · codex-acp → Codex · opencode acp
```

平台身份与执行身份分属不同 OS 用户。一个执行边界 = 一个 Worker + 一个 Gateway + 独立 HOME +
该边界下各工具自己的认证。同一宿主账号上的多个 Team 不构成多租户隔离，Team 隔离在 M4 验收。

## 仓库结构（目标）

```text
kirocrew-on/
├── octopus/              业务服务（新版 15 个模块 + 旧版 Pipeline 能力融合）
│   ├── config.py         统一运行时配置
│   ├── migrations/       schema 版本迁移
│   ├── requests.py       Request 登记与准入
│   ├── pipelines.py      Pipeline schema 2、规范化、映射、回退链
│   ├── gates.py          确定性门禁（吸收旧版 pipeline checkpoint）
│   ├── reports.py        Feature 报告
│   ├── sources.py        源码准备与 Feature 分支（← workflow_workspace.py）
│   ├── connect/          四个客户端的 Skill 与 MCP 配置写入
│   └── ui/               监控控制台（原生 JS，无构建）
├── composition/          ← poc.py：registry、poc-* 模板、Gateway 入口、--check
├── skills/octopus/       四个客户端共用的 SKILL.md
├── deploy/               AL2023 安装、systemd、CloudFormation、备份恢复
├── tests/                基线回归 + 原生契约 + 浏览器 + 客户端联动 + EC2 验收
└── docs/                 设计、计划、PROVENANCE、功能对照表、已知故障、运维手册
```

Python 包名保持 `octopus`，以兼容 `python -m octopus`、`/v1` API 和现有 `octopus_*` MCP 工具。

## 路线图

**MVP = M1 + M2 + M3 在 EC2 上完成验收。**

| 里程碑 | 范围 | 状态 |
|---|---|---|
| **M1 运行底座** | 先本地 macOS 跑通，再部署 EC2；两版资产迁入与回归基线；运行时配置；composition；schema 迁移；按 Session 并发的 Engine 与不可用退避；systemd；EC2 单边界部署；备份恢复 | 实现中 |
| **M2 核心闭环** | Request；Pipeline 生成、规范化与门禁；映射与回退链；准入；完整恢复阶梯与看门狗；批准代理；Feature 分支推送与报告 | 未开始 |
| **M3 客户端直连与控制台** | Claude Code / Codex / Kiro CLI / Kiro IDE 接入；pull / sync / watch / 会话关联；控制台搜索与详情 | 未开始 |
| **M4 共享与组织** | 组织层；Session 共享给个人 / Team / 公司；Team 执行隔离；排队与配额；HTTPS | 未开始 |
| **M5 仓库闭环** | Draft PR / MR；Webhook / Issue 输入；写回去重与冲突；检查状态同步 | 未开始 |

## 与现有项目的关系

| 项目 | 位置 | 在本仓库中的角色 |
|---|---|---|
| 新版 Team MVP（8918 / 8928） | `../../kirocrew-octopus/octopus/` | 业务服务、SQLite、Engine、Worker、Recovery、MCP、UI |
| 旧版 Workflow Studio / Pipeline（8917 / 8916） | `../../kirocrew-octopus/` 根目录（与 `../kirocrew-app-poc/` 一致，`api.py` 以 octopus 版为准） | Pipeline 生成与编排、确定性门禁、模板、源码准备、组合、提示词、导入导出 |
| 旧版运行目录 | `../kirocrew-app-poc/` | 8917 入口继续从这里运行；旧历史与运行数据保持不动 |

迁入来源以 `kirocrew-octopus` 工作树为准（分支 `codex/coordinator-recovery`，HEAD `b1ba98f`，
`octopus/` 等新版文件尚未提交）。旧版代码随基线迁入以保持回归测试，但不从本仓库部署；
其能力被新模块吸收、对应功能可用后逐项移除。迁入时逐文件记录来源路径与 sha256；
不拷贝任何运行数据、Session、Memory、认证文件或 `state/` 内容。

## 数据和发布范围

仓库只提交源码、模板、部署脚本、图表与文档。运行产物、Sessions、Memory、认证、
模型能力缓存、依赖目录和客户原始证据留在部署环境中，不进入仓库。

## 文档

| 文档 | 内容 |
|---|---|
| [总体设计](docs/superpowers/specs/2026-10-10-kirocrew-on-design.md) | 领域模型、组件、主流程、Pipeline 生成与门禁、映射、恢复阶梯、客户端联动、控制台、部署、安全、迁移、测试、里程碑 |
| [MVP Release 计划与状态](docs/MVP-RELEASE.md) | MVP 范围、已确定决策、默认值、各里程碑验收、继承的已知问题、发布记录 |
| [M1 实施计划](docs/superpowers/plans/2026-10-10-m1-runtime-foundation.md) | M1 运行底座：本机代码、本地端到端、EC2 部署的分任务计划 |
| [运维手册](docs/OPERATIONS.md) | 目录与端口、启动、访问、升级回滚、日志、认证、备份恢复、本地端到端 |
| [接手材料](docs/handover/) | 功能对照、事实清单、调用链、已知故障、旧版生成参考 |
| [迁入来源](docs/PROVENANCE.md) | 迁入文件的来源路径、sha256 与来源 git 状态 |
