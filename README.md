# KiroCrew ON

**一个 7×24 在线的研发协作空间。** 在你习惯的 Coding Agent（Claude Code、Kiro CLI 等）里用一句话交代任务，
后台由多个 Agent 分工规划、实现、互审并自动纠错；个人、Team 与组织在同一空间里共享会话、工作流与成果。
不绑定任何工具或模型，几乎没有学习成本。

> **MVP 0.1.0**（2026-10-12）：下文“能做什么”已在 AWS us-east-1 的部署上实测；Roadmap 为规划。
> 图文介绍：[`docs/KiroCrew-ON-MVP-0.1.0.html`](docs/KiroCrew-ON-MVP-0.1.0.html)　·　详细说明：[`docs/MVP-0.1.0.md`](docs/MVP-0.1.0.md)　·　0.1.1 版本说明：[`docs/MVP-0.1.1.md`](docs/MVP-0.1.1.md)（[HTML](docs/KiroCrew-ON-MVP-0.1.1.html)）

## 它是什么，不是什么

| 它是 | 它不是 |
|---|---|
| 后台协作空间：任务在服务器上持续执行，随时回来查看与续作 | 新的 IDE 或 Coding Agent |
| 多 Agent 编排：KiroCrew Agent 规划，Kiro / Claude Code / Codex 分工执行与互审 | 模型或模型网关 |
| 共享空间：个人与 Team 共享会话、产出与工作流 | 绑定某个工具或模型的系统 |
| 质量由平台把关：交接与结论经校验，失败自动诊断返工 | CI/CD 或自动发布 |
| 自托管：部署在你自己的 AWS 账号，按个人 / Team 隔离执行环境 | 托管 SaaS 或聊天机器人 |

## 怎么用

1. **部署**（管理员，一次）：`deploy/aws/deploy.sh up && deploy/aws/deploy.sh push` —— 见 [`docs/OPERATIONS.md`](docs/OPERATIONS.md)。
2. **连接你的 Coding Agent**（每人一次）：
   ```bash
   .venv/bin/python -m octopus connect claude --url https://<控制台域名>
   .venv/bin/python -m octopus connect kiro --url https://<控制台域名> --project <项目目录>
   ```
   其他客户端见 [`docs/CONNECT.md`](docs/CONNECT.md)。
3. **在 Agent 里直接说**：「用 KiroCrew ON 做一个番茄钟，Codex 审查」→「现在做到哪了？」→「给我预览」→「把小时改成补零」。

## 0.1.0 能做什么

- 自然语言提交，KiroCrew Agent 识别意图、补全需求、拆分任务并为每个任务选 Agent
- Kiro、Claude Code、Codex 分工执行与互审；交接与结论不通过时自动诊断并返工
- 在 Coding Agent 或控制台查看进度、观察协调者与子 Agent 的对话、打开 KiroCrew 原生会话
- 隔离预览产出；在同一会话与工作区上续作
- 工作流库：模板、KiroCrew 推荐、导入、版本、共享给个人或 Team
- Team 与执行环境：共享环境、Team 专属的独立 Worker 主机（`deploy.sh worker-host add <名称>`）
- 会话的每次变更可审计（谁、从哪个客户端、改了什么）；续作只重跑受影响的任务；任务带验收项与验证命令，结论逐项对照
- 下载会话源码（含 `.git`）到本机运行：控制台「下载源码」或 Coding Agent 的 `octopus_download`
- 浏览器内远程预览：在执行环境中运行会话的应用（开发服务器、前后端两个进程），经独立的预览域名打开：控制台「远程预览」或 `octopus_app`

已知限制（OpenCode、私有仓库、成果出库、用户管理等）见 [`docs/MVP-0.1.0.md`](docs/MVP-0.1.0.md) §8。

## 架构

```text
你的 Coding Agent（Skill + MCP） · Web 控制台
        │ HTTPS · 用户 Token
        ▼
KiroCrew ON 平台：身份与权限 · 个人 / Team 空间 · 会话 · 工作流 · 质量判定 · 自动纠错
        │ Worker Token（单向驱动）
        ▼
执行环境：共享环境 · Team 专属 Worker 主机 ── KiroCrew 协调者 → Kiro / Claude Code / Codex ── 模型（Bedrock 等）
```

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## Roadmap

| 版本 | 主题 |
|---|---|
| **0.1.0** | 跑通协作闭环（当前） |
| 0.2.0 | 可管理：用户、Team、权限与 MCP 按角色调用；私有仓库只读 |
| 0.3.0 | 成果出库：Feature 分支、diff、报告 |
| 0.4.0 | 完成优先：重试、工具回退、预算、看门狗、批准可见 |
| 0.5.0 | 处处可用：更多客户端一键接入、服务端检索 |
| 1.0.0 | Always-On 组织协作空间：组织层、跨 Team 共享、PR / MR、Issue 与定时触发 |

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/MVP-0.1.0.md`](docs/MVP-0.1.0.md) | 产品定义、工作流程、能力清单、架构、原则、限制、Roadmap |
| [`docs/MVP-0.1.1.md`](docs/MVP-0.1.1.md) | 0.1.1 版本说明：前端优化、Token 重新生成、聊天协作与门禁补充、列表瘦身、升级须知 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 当前架构：拓扑、KiroCrew 与平台的逐层交互、时序、信任边界 |
| [`docs/WORKERS.md`](docs/WORKERS.md) | 开发空间、Worker 与执行边界 |
| [`docs/CONNECT.md`](docs/CONNECT.md) · [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | 客户端接入 · 部署与运维 |
| [`docs/MVP-RELEASE.md`](docs/MVP-RELEASE.md) · [`docs/BACKLOG.md`](docs/BACKLOG.md) | 里程碑、决策与进度 · 后续排期 |

## 开发

```bash
PYTHON=.venv/bin/python tools/regress.sh   # Python、控制台单测与构建、旧版回归
```

开发分支：`feat/m1-runtime-foundation`。
