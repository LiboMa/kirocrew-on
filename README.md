# KiroCrew ON

以 **KiroCrew 原生机制与 ACP Harness 为执行底座**的业务研发平台。
开发者在自己的 AI Coding 工具（Claude Code、Kiro CLI）或 Web UI 中用自然语言提交开发任务；
后端登记请求，自动分析拆分任务并映射到合适的 Coding Agent，在独立 Feature 分支上完成编码、
确定性测试门禁、独立审查与纠错，持续监控直至完成，并通过 Feature 报告和远端分支跟踪进度。

> **当前状态：设计阶段（2026-10-10）。**
> 本仓库目前只有规划与设计文档，**没有可运行代码**。下文所有功能都是目标，尚未实现、尚未验证。
> 现有可运行实现位于 `kirocrew-octopus`（新版 Team MVP，8918）和 `experiments/kirocrew-app-poc`
> （旧版 Workflow Studio，8917），本仓库将以「先等价迁入、后受测重构」的方式在其基础上继续建设。

## 日常主线

```text
开发者在 Coding 工具里说：「为 X 功能实现 Y，用 Codex 审查」
  → Skill 调用确定性 CLI，自动识别仓库 remote / 分支 / HEAD，提交结构化请求
  → 后端登记 Request（幂等、可追踪）
  → Kiro 分析需求并拆分任务；代码按映射策略和用户指定把任务分配给 Agent
  → 用户确认计划（或按已授权的自动准入策略直接运行）
  → 在 Feature 分支上：编码 → 确定性测试门禁 → 独立审查 → 诊断与返工 → 复审
  → 监控：心跳、超时、卡住检测，异常给出具体原因和可执行操作
  → 审查通过后推送远端 Feature 分支，生成 Feature 报告
  → 开发者在 Coding 工具 / Web UI 查询状态与报告，在原 Session 上继续迭代
```

## 核心功能（目标）

| # | 功能 | 说明 |
|---|---|---|
| 1 | Session 共享 | Session 可共享给个人、Team、公司（组织）；共享 Session 不等于共享凭据或原生入口 |
| 2 | Skills 对接 | 用户安装 Skill 后即可从 Coding 工具提交任务、查询进度，不需要自己配置 MCP 或写平台 JSON |
| 3 | 自动拆分与 Agent 映射 | 后端自动分析、拆分任务并映射到 Kiro / Claude Code / Codex / OpenCode；也可在 Prompt 中指定 |
| 4 | Feature 跟踪 | 每个 Feature 一个分支和一份按模板生成的报告；通过报告和远端分支跟踪 |
| 5 | 代码优先原则 | 可以用代码实现的功能，不依赖 Agent 的通用性 |
| 6 | 请求全程托管 | 后端接收、记录、分析处理并监控请求，保证开发任务顺利完成或给出明确阻塞原因 |

工具范围：Kiro CLI、Claude Code、Codex、OpenCode。**明确排除 Gemini CLI。**

## 设计原则

### 代码与 Agent 的分工

| 由代码负责（确定性） | 由 Agent 负责（需要理解或判断） |
|---|---|
| 请求登记、幂等、状态机、版本 | 需求理解与任务拆分（Kiro） |
| 从 cwd 识别仓库 remote / 分支 / HEAD | 编码（Claude Code / Codex / …） |
| 任务 → Agent 映射：策略表 + 显式指定覆盖 | 审查判断（Codex 等） |
| 模型与能力校验、派发、核对 | 失败诊断（Kiro Coordinator） |
| clone、建分支、commit、push | 报告中的叙述性摘要（标注为 Agent 生成） |
| 确定性门禁：运行仓库声明的测试 / 构建命令，记录真实退出码 | |
| 报告生成：用固定模板拼装结构化证据 | |
| 监控：心跳、超时、卡住检测、通知 | |
| 分享与权限 | |

用户在 Prompt 中的自然语言指定（如「用 Codex 审查」）由**用户自己的 Coding Agent** 在 Skill 中转成
结构化字段提交；后端用代码校验，并记录每个映射的来源（`user_prompt` / `policy`）。

### 继承的约束

- Engine 是唯一业务派发者；KiroCrew 决定原生 Agent 会话如何运行。
- 不绕过 KiroCrew 直接启动 Coding CLI，不重新实现 ACP，不以换框架或重写前端为默认方案。
- 技能、权限、认证使用各工具和 KiroCrew 的原生机制；Prompt 不能冒充权限控制。
- 共享工作流、共享 Session、共享凭据是三件事，互不隐式授权。
- 用户 Token、Worker Token、Gateway 原生凭据分开；不整体复制个人 HOME。
- 推荐不暗中执行；直接运行必须显式配置并留存策略快照。
- 状态、模型、测试与部署结论如实报告；未知显示未知，不把目标写成已上线。

## 架构概览（M1 目标形态，未部署）

```text
开发者浏览器 / Claude Code·Kiro（Skill → octopus CLI / stdio MCP → HTTP API）
   │  M1：SSM 端口转发      团队阶段：HTTPS 反向代理
   ▼
[OS 用户 octopus]  octopus-api.service
   Web UI + /v1 API + Engine ── SQLite  /var/lib/octopus/platform/
   │  Worker Token（回环 HTTP，与用户 Token 分离）
   ▼
[OS 用户 crew-<边界>]  octopus-worker@<边界>.service
   源码准备 · 派发 · 门禁 · 核对 ── /var/lib/octopus/boundaries/<边界>/worker/
   │  同用户读 .local_secret → 换取原生 token（回环）
   ▼
[同一执行用户]  octopus-gateway@<边界>.service
   KiroCrew Gateway + composition（PocProviderRegistry）
   KIROCREW_HOME / KIRO_HOME 指向 /var/lib/octopus/boundaries/<边界>/
   ▼
ACP：kiro-cli · claude-agent-acp → Claude Code · codex-acp → Codex · opencode acp
```

平台身份与执行身份从第一天起分属不同 OS 用户。一个执行边界 = 一个 Worker + 一个 Gateway +
独立 HOME + 该边界下各工具自己的认证。M1 只有一个个人边界；同一宿主账号上的多个 Team
不构成多租户隔离，Team 隔离在 M3 以容器或独立主机实现后再验收。

## 仓库结构（目标，M1 重构完成后）

```text
kirocrew-on/
├── octopus/              业务服务（迁自 kirocrew-octopus/octopus 的 15 个模块）
│   ├── config.py         统一运行时配置（配置文件 + 环境变量，单一来源）
│   ├── migrations/       schema 版本迁移
│   ├── sources.py        ← workflow_workspace.py（源码准备）
│   ├── documents.py      ← workflow_files.py（JSON / YAML / Markdown 安全解析）
│   ├── prompts.py        ← development_prompts.py
│   ├── gateway_client.py ← api.py（原生 Gateway HTTP 与 token）
│   ├── templates/        ← ui/workflow-templates.json
│   └── ui/               原生 JS，无构建步骤
├── composition/          ← poc.py：registry、poc-* 模板生成、Gateway 入口、--check
├── skills/               Claude Code / Kiro CLI Skill 包（M2）
├── deploy/               AL2023 安装（固定版本）、systemd unit、env 样例、备份 / 恢复
├── tests/                基线回归 + 原生契约 + 浏览器 + Linux / EC2 验收
└── docs/                 PROVENANCE、功能对照表、已知故障、运维手册、specs / plans
```

Python 包名保持 `octopus`，以兼容 `python -m octopus`、`/v1` API 和 13 个 `octopus_*` MCP 工具。

## 路线图

| 里程碑 | 范围 | 完成条件 | 状态 |
|---|---|---|---|
| **M1 运行底座** | 迁入两版资产并建立回归基线；运行时配置解耦；无桌面启动；systemd 服务；schema 迁移；EC2 单边界部署 | EC2（AL2023 x86_64）上真实跑通 Kiro、Claude Code、Codex 的最小研发与返工；重启可恢复；OpenCode 单独核实并如实记录 | 设计中 |
| **M2 核心日常闭环** | Request 一等对象；自动拆分与 Agent 映射策略；Skill 客户端；Feature 分支与确定性门禁；监控；Feature 报告；审查通过后推送远端分支 | 在 EC2 上对一个获授权的真实仓库，从 Coding 工具提交需求到远端 Feature 分支与报告形成完整链路；Web 与 Coding 客户端查询同一 Session | 未开始 |
| **M3 共享与组织** | 组织层；Session 共享给个人 / Team / 公司；权限变化对查询、SSE、导出、运行的影响；Team 执行隔离；排队与配额 | 无权用户无法访问他人 Session 与认证；授权协作路径完整通过；隔离在真实独立身份 / 容器 / 主机上验收 | 未开始 |
| **M4 仓库闭环** | Draft PR / MR；Webhook / Issue 输入；写回去重与冲突；检查状态同步 | 需求进入 → 自动研发 → 质量复审 → 获授权写回 → 状态可追踪；Webhook 重放不产生重复 Run 或 PR / MR | 未开始 |

详细范围、决策与验收见 [MVP Release 计划与状态](docs/MVP-RELEASE.md)。

## 与现有项目的关系

| 项目 | 位置 | 在本仓库中的角色 |
|---|---|---|
| 新版 Team MVP（8918 API / 8928 Worker） | `../../kirocrew-octopus/octopus/` | **主干来源**：业务服务、SQLite、Engine、Worker、Recovery、MCP、UI |
| 旧版共享模块 | `../../kirocrew-octopus/` 根目录（与 `../kirocrew-app-poc/` 一致，`api.py` 以 octopus 版为准） | `poc.py`、`api.py`、`workflow_workspace.py`、`workflow_files.py`、`development_prompts.py`、模板 JSON |
| 旧版 Workflow Studio（8917） | `../kirocrew-app-poc/` | **不迁入**；继续作为旧版入口运行，旧历史与运行数据保持不动 |

迁入来源以 `kirocrew-octopus` 工作树为准（分支 `codex/coordinator-recovery`，HEAD `b1ba98f`，
`octopus/` 等新版文件尚未提交）。迁入时逐文件记录来源路径与 sha256；
不拷贝任何运行数据、Session、Memory、认证文件或 `state/` 内容。

## 数据和发布范围

仓库只提交源码、模板、部署脚本、图表与文档。运行产物、Sessions、Memory、认证、
模型能力缓存、依赖目录和客户原始证据留在部署环境中，不进入仓库。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/MVP-RELEASE.md](docs/MVP-RELEASE.md) | MVP 范围、已确定决策、默认值、各里程碑验收、继承的已知问题、发布记录 |
