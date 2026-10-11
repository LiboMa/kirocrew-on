# M1 本地端到端验收记录（macOS，真实 ACP）

日期：2026-10-11　·　分支：`feat/m1-runtime-foundation`　·　决策：D12（先本地跑通再部署远端）、D14（无人值守边界使用 auto 批准）

本文是脱敏摘要：只记录 Run ID、阶段、实际后端与模型、门禁结论和观察到的现象，不含 token、凭据或需求以外的内容。
原始数据在本机 `state/local-e2e/`（git 忽略）。

## 环境

| 项 | 值 |
|---|---|
| 主机 | macOS（Apple Silicon），本机用户 |
| KiroCrew | 0.8.0（`/Applications/KiroCrew.app` 自带解释器运行 `composition.gateway`） |
| ACP 适配器 | claude-agent-acp 0.84.0、codex-acp 1.11.0（codex-cli 0.153.4），由本仓库 `npm ci` 安装 |
| 工具 | kiro-cli 2.29.0、Claude Code 2.1.296、OpenCode 1.18.30（版本为本机观测值） |
| 隔离 | 独立端口 Gateway 6476 / Worker 8938 / API 8948；独立 `KIROCREW_HOME`、`KIRO_HOME` 在 `state/local-e2e/`；`~/.kiro/agents` 与 `~/.kiro/crew/config.json` 前后校验和不变 |
| 执行边界 | `trusted-local`，`approval_mode = auto`（D14：仅平台派发的 spawn；全局工具批准保持 interactive） |
| 平台 | `engine_workers = 4`，`retry_unavailable = 2.0`，`shutdown_grace = 30` |

## 验收结果

| # | 场景 | Run | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 组合检查 | — | 通过 | `--check` 五个模板分别路由到 kiro / kiro / claude / codex / opencode（`AcpProvider`） |
| 2 | 三服务无桌面启动与就绪 | — | 通过 | `wait-ready` 三项通过；factory 标记由提供服务的进程在启动后 1.9 s 写入 |
| 3 | Kiro 冒烟 | `run-fdcddf23…` | 通过 | kiro，原生 `19e232bc…`，outcome pass，Workspace 中生成 HELLO.md |
| 4 | Claude 编码 → Codex 审查 | `run-f9da7efa…` | 通过 | code：claude（`global.anthropic.claude-sonnet-4-6`）pass；review：codex（`openai.gpt-6-astra[xhigh]`）pass |
| 5 | 审查退回 → Coordinator 返工 → 复审 | `run-2d3377a7…` | 通过 | review#1 `changes_requested`（缺类型校验与负数 / 小数 / TypeError 覆盖）→ `recovery.applied` 目标 code → code#2 → review#2 pass；同一 Workspace 与父会话，Session 版本 2 |
| 6 | Kiro 规划 → Claude 编码 → Codex 审查 | `run-0b4a73ce…` | 通过（经一次修复） | 首次：审查发现 `.js` / `.mjs` 契约差异，Coordinator 正确诊断但对上游 code 给出 `retry` 标签被 Engine 阻断；修复后（`retry` 上游 → `rework`，记录 `normalized_from`）手动发起诊断，code#2 改为 `.js`，review#2 pass |
| 7 | OpenCode 单独核实 | `run-93a3e5fa…` | **未通过** | `opencode/big-pickle`：Attempt 1、2 用 shell 生成了页面但未写 `handoff.md`（契约失败），Attempt 3 返回 `_No response._`；两轮纠错后预算耗尽 blocked。复现 K01 |
| 7a | OpenCode 替换配置 | `run-79831882…` | 通过 | 同一交付任务改由 claude 执行，一次 accepted |
| 8 | 编码中重启 API 与 Worker | `run-2ca88f36…` | 通过 | 两次重启后继续推进并完成；每个 Attempt 在原生侧恰好 1 个带标记的任务（无重复派发） |
| 9 | 编码中重启 Gateway | `run-84677cb5…` | 通过（自动恢复） | 原生将运行中任务标为 `Orphaned: cancelled` → code#1 blocked → Coordinator 自动诊断 → code#2 accepted → review accepted → completed |
| 10 | 并发 | `run-0b4a73ce…` × `run-93a3e5fa…` | 通过 | 两个 Session 的 Attempt 有 3 对时间区间重叠，互不阻塞 |
| 11 | 备份与独立目录恢复 | — | 通过 | 数据库备份 integrity ok（11 Run、24 Attempt）；Worker 数据归档 258 个文件；`restore-verify` 在新目录恢复，行数一致；文件 0600 |
| 12 | 聚焦审查修复后复验：全局 interactive + 平台派发 auto + `poc-diagnose` 诊断 | `run-a9715ef6…` | 通过 | review#1 `changes_requested` → `poc-diagnose` 诊断 → code#2 → review#2 被原生取消（Codex 运行了无脚本的 `node` REPL）→ 自动诊断重试 → review#3 pass |
| 13 | 最终配置冒烟 | `run-bb5f374f…` | 通过 | kiro，outcome pass |

## 本地端到端发现并修复的问题

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| L1 | 首次启动 `wait-ready --worker` 崩溃 | Worker 启动时才生成 token 文件 | `wait-ready` 在超时内等待 token 文件 |
| L2 | `init --password-env OCTOPUS_ADMIN_PASSWORD` 失败 | 环境变量层把所有 `OCTOPUS_*` 当配置项 | 只读取已知配置项 |
| L3 | 每个 spawn 被原生拒绝（无 dashboard 连接） | 原生 interactive 模式在无人值守时没有批准界面 | `approval_mode` 配置项，Gateway 启动核对一致性（D14） |
| L4 | `agent.approval_mode=auto` 后 spawn 仍被拒 | 该项只管工具调用，不放行 spawn | 平台派发的 spawn 请求原生 `approval_mode=auto` |
| L5 | 用内部密钥派发被拒（`member_identity_unavailable`） | 0.8.0 要求内部调用方带已验证的执行身份 | 改为 owner 认证的 `/api/spawn`（契约风险 K19） |
| L6 | Coordinator 正确诊断却被阻断 | `retry` 标签指向上游 Task | 规范化为 `rework` 并记录 `normalized_from` |
| L7 | 自动批准可能扩散到诊断 Agent 的嵌套 spawn（聚焦审查 I1） | `poc-coordinator` 模板带 kirocrew-core | 诊断改用仅 read / write / shell 的 `poc-diagnose` |
| L8 | 长期活跃的老 Session 饿死新 Session（聚焦审查 C1） | 每轮总从最早创建的 Session 开始提交 | 按最久未推进优先轮转 |

同期修复的全分支审查问题：I1 reconcile 与在途推进交错、I2 停机不受限、I3 Gateway 不可用被判 blocked、I4 re-exec 无法检测、I5 安装失败不可重试。

## 观察（未处理）

- Claude 编码任务的输出中出现提示：组织限制某一默认模型，适配器改用了另一模型；而原生状态中的实际模型为 `global.anthropic.claude-sonnet-4-6`。请求为 `auto`，未触发模型门禁。指定具体模型时需复核“请求 / 实际”是否一致（M2）。
- Kiro 不报告实际模型（K05），证据中显示为空。
- 本地 macOS 不提供 cgroup v2 资源上限（原生日志 SECURITY 警告）；EC2 Linux 上复核。

## 未覆盖

- EC2 / Linux（Task 15–20）。
- 已有远程仓库的 clone 与 Feature 分支（M2）。
- 原生权限批准代理、看门狗、L0–L4 完整恢复阶梯（M2）。
