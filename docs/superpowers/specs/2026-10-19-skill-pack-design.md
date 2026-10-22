# 编码技能包：三个后端一致的方法（D50 第 1 期）

## 1．背景

负责人 2026-10-19 问：调用 Coding 工具时，是否给它们配备最标准的技能（Brainstorming、frontend-design、plan、execute）？

EC2 核对（2026-10-19）：
- Claude Code：personal 边界装有 `superpowers` 插件（10-13 安装，来源不明），但没有启用它的 `settings.json`，近 3 天 47 个会话无一使用；新边界 owner 没有任何插件。
- Codex：只有模型配置，没有任何技能或 AGENTS.md。
- Kiro（`poc-*` agent）：`resources: []`，未挂任何技能；KiroCrew 自带 30 多个 builtin skill，平台任务未用。

平台在流程层已有自己的一套：KiroCrew 规划（需求补全与拆分，相当于 brainstorming + plan）、计划确认、角色卡片、任务契约与门禁、
Evaluator（相当于 execute + review）。缺的是任务内部的方法：测试先行、系统化调试、完成前的验证、按评审返工、前端设计。

## 2．已确定（负责人 2026-10-19 未提异议，按推荐方案）

| 编号 | 决定 |
|---|---|
| R1 | 两类分开：**流程类**（brainstorming、writing-plans、executing-plans、subagent-driven、worktrees、finishing-a-branch）不交给执行任务——它们由平台的规划、确认、派发、门禁与 Evaluator 承担；原样交给无人值守的任务会与平台冲突（自行开 worktree、写计划文件、提交、等待用户回答）。**方法类**交给执行任务。 |
| R2 | 技能包：平台维护、随发布固定版本，`octopus/skills/<名称>/SKILL.md`（`name`、`description`、`source`、`license`）。第 1 期五个：`test-driven-development`、`systematic-debugging`、`verification-before-completion`、`receiving-code-review`（改编自 superpowers 6.4.1，MIT，Jesse Vincent）、`frontend-design`（改编自 Anthropic claude-plugins-official，Apache-2.0）。改编只去掉与平台冲突的部分（询问用户、提交 / 推送、开 worktree、写计划文件），改为平台的说法（交接、outcome.json、BLOCKED）；原许可证随包附上，每个文件注明改动。 |
| R3 | 选择：任务契约新增 `skills`（至多 4 个，取自技能包）。KiroCrew 规划者按任务推荐（规划提示列出技能与用途，如界面类任务推荐 `frontend-design`、实现与测试推荐 `test-driven-development`）；提交者也可在任务中写；平台不内置「角色 → 技能」对照表（负责人：不内置默认链路）。 |
| R4 | 交付：Worker 把所选技能的 SKILL.md 复制到 Attempt 目录（`skills/<名称>.md`，与 instructions.md 同处，三个后端都能读），instructions.md 增「## 方法」一节：每个技能的名称、用途与路径，「开始前完整阅读并遵循；与本任务契约冲突时以契约为准」。 |
| R5 | 能力与兼容：Worker 健康信息 `skills: [名称…]`；规划只推荐该 Worker 有的；对没有该能力的 Worker 平台不发 `skills`（与 `role` 相同）。 |
| R6 | 可审计：Attempt 证据 `skills: [{name, sha256}]`；任务卡片「方法：…」、报告的任务一节、MCP 精简状态 `skills`。 |

## 3．以后

- KiroCrew builtin skill（`frontend-design-workflow`、`web-verify`、`security-conductor` 等）：多依赖 KiroCrew 自身工具，按任务后端逐个评估后纳入。
- 返工自动附 `systematic-debugging` / `receiving-code-review`（属平台规则，需负责人认可）。
- 技能使用的证据（Agent 是否读了文件）：可要求交接中写明。

## 4．测试与验收

- **本机**：技能包文件格式与许可证；契约 `skills` 的校验（名称、数量、与角色无关）；规划提示列出技能、规划结果保留；Worker 复制文件与「方法」一节、证据 sha256；
  旧 Worker 不收到 `skills`；控制台、报告、MCP。
- **EC2**：一个界面类需求，规划为前端任务推荐 `frontend-design`、实现任务推荐 `test-driven-development`；三个后端的任务都在交接中按技能行事（如先写失败的测试）。
