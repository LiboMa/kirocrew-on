# 一般任务：任务类型不与 Git 强绑定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话可以不带代码仓库（来源 `none`），任务可以是调研（research）与撰写（write）；规划、Agent 指令与入口都兼顾一般任务；研发会话不变。

**Architecture:** `none` 来源在平台记作 `none`、发给 Worker 时按 `new` 准备（旧 Worker 可用）；`KINDS` 加 `research`、`write`，门禁与 `deliver`
同档；`intent_prompt` / `planning.rules` / `worker.envelope` 按任务性质与来源给出措辞；控制台、MCP、CLI、Skill 增加入口。

**Tech Stack:** Python 3.12 标准库；React + TanStack Query。

**Spec:** `docs/superpowers/specs/2026-10-22-general-tasks-design.md`（D65）

## Global Constraints

- 研发会话（来源 `git` / `new`、kind 为 implement / review / test / deliver / design）的门禁、事件、Evaluator、Knowledge、发布与提示词中的研发规则不变。
- `none` 来源：`set(source) == {"type"}`；发布 409 `no_repository`「此会话没有代码仓库：产出在会话页下载」。
- 新 kind：`research`（调研）、`write`（撰写）。`no_secrets` 对它们照旧阻断。
- 首次规划提示 ≤ 7400 字、带已保留任务时 ≤ 7800 字（`tests/test_replan.py`）。
- 文案：一般任务的 Agent 指令标题「本次任务」、目录「工作目录」；控制台「下载产出」「文件改动」「调研」「撰写」。

## Review Focus

- **研发会话无回归**：git / new 来源的 implement 任务的门禁与事件序列与改动前相同（Task 1 测试对照）。
- **旧 Worker**：`none` 来源发给 Worker 的是 `{"type": "new"}`（Task 1 测试）。
- **混合会话**：同一会话里 research 后接 implement，implement 仍按研发门禁、Evaluator 照旧追加（Task 1 测试）。
- **提示长度**：加了一般任务的规则后首次规划提示仍 ≤ 7400（Task 2 测试）。
- **D57 的 review 撤回**：审查 write 任务的文档时，被审查文件的改动照旧撤回、审查意见作为新文件保留（Task 1 测试）。

---

### Task 1: `none` 来源与 research / write 任务类型

**Files:** Modify `octopus/domain.py`（`Service.start` 来源校验；`KINDS`；发布拒绝 `none`；准备时发给 Worker 的来源）、`octopus/schema.py`（`Source`）、
`octopus/engine.py`（若 `_more_gates` 按 kind 名单判断 deliver 档，加入 research / write）、`octopus/roles.py`（如需）、`octopus/report.py`（「工作目录」措辞）；
Test `tests/test_general_tasks.py`

**Interfaces — Produces:** `domain.GENERAL_KINDS = ("research", "write")`；`domain.general(source) -> bool`（来源为 `none`）；`domain.worker_source(source) -> dict`（`none` → `{"type": "new"}`）。

- [ ] 测试：`none` 来源被接受、带 url 被拒；准备请求里来源为 `new`；research / write 被接受；write 任务没写文件时 `diff_present` 为 warn、写了秘密时阻断、没有 verify 不运行检查；
  `none` 会话发布 409 `no_repository`；混合会话中 implement 仍追加 Evaluator，只有 research / write 的会话不追加；研发会话门禁对照不变；审查 write 的文档时改动被撤回、新文件保留。
- [ ] 失败；实现；通过（含 `tests.test_engine_gates tests.test_gate_relief tests.test_evaluator tests.test_publish` 等相关模块）；提交。

### Task 2: 规划与 Agent 指令

**Files:** Modify `octopus/prompts.py`（`intent_prompt`、`HANDOFF`）、`octopus/planning.py`（`rules`、`EXAMPLE` 视需要）、`octopus/worker.py`（`TAILS`、`envelope` 标题与目录措辞）；
Test `tests/test_general_tasks.py`、`tests/test_replan.py`（长度）

**Interfaces — Consumes:** Task 1 的 `GENERAL_KINDS`、`general(source)`。

- [ ] 测试：规划提示含一般任务的 kind 与「deliverables 写产出文件名、不必写 paths 与 verify」，研发规则写明只对改代码的任务；`none` 来源时提示「本会话没有代码仓库」；
  research / write 的 Agent 指令标题「本次任务」、「工作目录：」、对应结尾与交接要求；implement 的指令与今天逐字相同；首次规划提示 ≤ 7400。
- [ ] 失败；实现；通过（含 `tests.test_replan tests.test_planning tests.test_worker*`）；提交。

### Task 3: 控制台、MCP、命令行与 Skill

**Files:** Modify `web/src/features/launch/{steps.tsx,state.ts}`、`web/src/lib/policies.ts`（TASK_KIND_LABELS）、`web/src/features/sessions/{SessionView.tsx,AttemptDiff.tsx,PublishControl.tsx}`、
`octopus/mcp.py`（`octopus_start`）、`octopus/cli.py`（`submit --no-repo`）、`skills/kirocrew-on/SKILL.md`；Test vitest 与 `tests/test_general_tasks.py`

- [ ] 测试：向导可选「不需要代码仓库」并提交 `{"type": "none"}`；`none` 会话页无「发布到仓库」、按钮「下载产出」；kind 标签；MCP schema 与说明；`octopus submit --no-repo`。
- [ ] 失败；实现；通过（`npx vitest run`、`npx tsc --noEmit -p .`）；提交。

### Task 4: 文档、回归、终审、部署、EC2

- [ ] OPERATIONS（一般任务一节）、MVP-RELEASE、progress；`tools/regress.sh` 全量；终审；部署（先与 D57 会话协调）；EC2：`none` 来源「调研并写报告」会话一次完成且产出可下载，另一个研发会话无回归。
