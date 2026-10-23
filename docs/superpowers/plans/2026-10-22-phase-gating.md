# 按阶段把关（D69）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：**
- 门禁按任务所在的阶段把关：发散只保留安全底线，设计在阶段末把关，收敛照 D57；
- 审查的结论只在需要把关的地方触发返工。

**Architecture：**
- 新模块 `octopus/phases.py` 负责推断阶段、计算设计阶段末，并判断一条失败能否降级。
- Engine 有两处改动：
  - 发散任务不运行检查命令；
  - 在判定事务里、自动重试决定之后，把可降级的失败改为「已完成（有提示）」。
- 入口（契约、导入、OpenAPI、MCP、控制台）只多一个可选字段 `phase`。

**Tech Stack：** Python 3.12 stdlib（unittest），React + TypeScript + vitest。

**Spec：** `docs/superpowers/specs/2026-10-22-phase-gating-design.md`

## Global Constraints

- `phase` 取值：`diverge`、`design`、`converge`；没有该字段时按类型推断（spec §2）。
- 安全检查（`no_secrets`，以及 D57 T8 之后的安全拦截）在任何阶段都不降级。
- 降级之后：
  - 记录改为 `warn`，`detail` 前缀写「发散阶段不阻断：」或「设计阶段中途不阻断（阶段末再把关）：」，并带 `relaxed: "fail"`；
  - 移除 `evidence["gate"]` 与 `failure_type`。
- 平台任务（规划、重新规划、Evaluator、Knowledge、workflow_design）不受影响。
- 研发会话的任务全部推断为收敛：门禁记录与事件与今天完全相同（以现有测试为准）。
- 规划提示长度：仍受 `tests/test_replan.py` 的上限约束。

## Review Focus

1. 重新规划插入任务后，「设计阶段末」随新顺序改变：旧的阶段末不再是阶段末。
2. 审查依赖多个任务时取最后一个非审查依赖的阶段；没有依赖时取前面最近的非审查任务；审查排在第一个时为收敛。
3. 发散任务的自动重试（T13 平台故障）先于降级：本次是自动重试时不降级。
4. 降级之后，防空转与「同一原因两次」不应把它算作失败。
5. `paused` / `stopped` 的会话（`active` 为假）不降级：原样记录。

---

### Task 1：阶段推断与契约

**Files：**
- 新建：`octopus/phases.py`
- 修改：`octopus/domain.py`（`CONTRACT` 加 `phase`，`steps()` 校验取值）
- 测试：新建 `tests/test_phases.py`

**Interfaces（Produces）：**
- `PHASES = ("diverge", "design", "converge")`
- `phase(spec: dict, specs: list[dict]) -> str`：`specs` 为计划顺序的任务 spec 列表（含 id）；
- `phase_end(task_id: str, specs: list[dict]) -> bool`
- `relaxed(spec: dict, specs: list[dict]) -> str | None`：返回 detail 前缀（发散 / 设计中途），否则返回 `None`；
- `RELAXABLE = {"native_error", "tool", "model"}`，以及 `outcome_pass` 的失败（白名单；其余阻断）；
  `outcome_pass` 的结论类型属于 `recovery.NEEDS_INPUT ∪ {"environment"}` 时不降级；
- `relax(evidence: dict, spec: dict, snapshot: dict, error: str) -> str | None`：
  - 可降级时就地改写 `evidence["gates"]`：fail → warn，加前缀，`relaxed: "fail"`；
  - 同时移除 `gate` 与 `failure_type`，写入 `phase` / `phase_relaxed` / `phase_relaxed_error`；
  - 返回前缀；不可降级时返回 None，不改动任何内容。
  - 安全条件 `guard.blocking` 在 T8 之前内联。

- [ ] **Step 1 写测试**，覆盖以下用例：
  - research → diverge，design → design，implement / test / deliver / write / 未写 → converge；
  - 显式 `phase` 优先；
  - review 依赖 `[research-1, design-1]` → design；依赖为空时取前面最近的非审查任务；排在第一个 → converge；
  - `phase_end`：`[design-a, review-a, implement]` 中 review-a 为阶段末，design-a 不是；只有一个 design 时它是阶段末；
  - `relaxed`：diverge → 「发散阶段不阻断：」；design 中途 → 「设计阶段中途不阻断（阶段末再把关）：」；design 阶段末与 converge → `None`；
  - `relax`：
    - `native_error` 降级；`native_stopped`、`auth`、`lost`（`result_missing`）、`security`、`no_secrets` 与未知类型不降级，证据原样不动；
    - 降级之后 `detail` 保留原文，`phase_relaxed_error` 为原错误；
  - `domain.steps` 接受 `phase: "diverge"`，拒绝 `phase: "later"`（`Error` 400）。
- [ ] **Step 2** 运行 `python -m unittest tests.test_phases`。Expected：失败（模块不存在）。
- [ ] **Step 3** 实现。
- [ ] **Step 4** 运行同一命令，Expected：通过；再运行 `python -m unittest discover -s tests -p 'test_*.py' -q`。
- [ ] **Step 5** 提交。

### Task 2：Engine 的降级与发散不运行检查

**Files：**
- 修改：`octopus/engine.py`
  - `_more_gates` 取 `commands` 处；
  - 判定事务中 `if retry: … elif …` 这条链。
- 测试：新建 `tests/test_phase_gating.py`（沿用 `tests/test_mvp` 的假 Worker：只导入模块，不导入类）。

**Interfaces（Consumes）：** Task 1 的 `phases.relaxed`。安全判定暂用 `evidence.get("gate") == "no_secrets"`，D57 T8 合入后换成它的谓词。

- [ ] **Step 1 写测试**，覆盖以下用例：
  - 发散任务结果为 `blocked`：任务 accepted，`outcome_pass` 为 warn 且带前缀，没有 `evidence["gate"]`，没有 recovery 事件，会话 completed；
  - 发散任务原生失败（非平台故障）：accepted，交接写明「任务未完成」；
  - 发散任务原生失败、属平台故障（never_started）：先自动重试（不降级）；
  - 设计中途的审查给出 `changes_requested`：只记提示；设计阶段末的审查给出 `changes_requested`：照旧 blocked，进入纠错；
  - 发散任务候选里有 `.env`：照旧 blocked，`gate == "no_secrets"`；
  - 发散任务有 verify：不运行，`checks_pass` 为 skip「发散阶段不运行检查命令」；
  - 研发会话（implement → review）的记录与今天相同（现有 test_mvp、test_evaluator、test_recovery 全部通过）。
- [ ] **Step 2** 运行，Expected：失败。
- [ ] **Step 3** 实现：
  - 降级分支：改写记录，`state = "accepted"`；
  - 记录 `evidence["phase"]`、`evidence["phase_relaxed"]`；
  - 原生失败时调用 `generated_handoff` 生成交接，并写明未完成。
- [ ] **Step 4** 运行，Expected：通过；全量 `tests/` 通过。
- [ ] **Step 5** 提交。

### Task 2b：只有调研的会话里必须通过的检查

- 修改 D57 T5 放置继承检查的地方（`octopus/planning.py`，位置已与 D57 会话核对）：
  - **重新规划 `_carry(replaced, steps, notes)`**：
    - 现状：`runnable = [step for step in steps if runs_checks(step)]`，最后有 `require(not missing, …, 409, "plan_invalid")`，这是唯一一处「没有可运行的任务就无效」。
    - 修改：把 `missing` 分成两种情形。
      - 一个可运行的任务都没有，且有候选只因为是发散任务才被排除：记提示「必须通过的检查没有可运行的任务（发散阶段不运行检查命令）」。
      - 有可运行的任务，只是放不下：照旧 409。
  - **首次规划 `_required_checks(db, rid, current, steps, notes, replan)`**：
    - 现状：步骤带有必过标记、却 `not runs_checks(step)` 时（今天是 review / design，D69 之后还有发散任务），这条检查会悄悄不运行。
    - 修改：加同一条提示，让使用者看到。
    - 有可运行的任务时，按 `_carry` 的方式移过去（可选，先做提示）。
- 测试：
  - 只有 research 任务、写了必过检查的会话：计划被接受，并带这条提示；
  - 重新规划把带必过检查的实现任务换成发散任务：带提示，不返回 409；
  - 有可运行的任务但没有空位：照旧 409。

### Task 3：规划规则与 Agent 指令

**Files：** `octopus/prompts.py`（规划规则一行）、`octopus/worker.py` 的 envelope（发散任务结尾一句）；测试 `tests/test_prompts_p1.py`、`tests/test_replan.py`。

- [ ] **Step 1 写测试**：
  - 规划提示含 「phase：diverge」；
  - 发散任务的 envelope 含「发散阶段」；
  - 研发任务的 envelope 不含「发散阶段」；
  - 长度测试照旧。
- [ ] **Step 2–4** RED → 实现 → GREEN。
- [ ] **Step 5** 提交。

### Task 4：导入、OpenAPI、MCP、Skill

**Files：** `octopus/workflow_import.py`、`octopus/schema.py`、`octopus/mcp.py`（说明）、`octopus/skills/kirocrew-on/SKILL.md`；测试 `tests/test_workflow_import.py`、`tests/test_contract.py`。

- [ ] **Step 1 写测试**：
  - 导入时「阶段: 发散」→ `phase: "diverge"`；
  - OpenAPI 的步骤 schema 有 `phase` 枚举。
- [ ] **Step 2–4** RED → 实现 → GREEN。
- [ ] **Step 5** 提交。

### Task 5：控制台

**Files：** `web/src/features/workflows/TaskEditor.tsx`（「阶段」下拉）、会话页任务卡片（阶段标签）、`web/src/lib/labels.ts`、`web/src/api/types.ts`；测试同目录 `*.test.tsx`。

- [ ] **Step 1 写测试**：
  - 编辑器选择「发散」后，保存的步骤带 `phase: "diverge"`；选择「自动」时不带该字段；
  - 会话页任务卡片显示「发散」，推断的显示「自动：收敛」。
- [ ] **Step 2–4** RED → 实现 → GREEN（`npm --prefix web test`、`tsc -b`）。
- [ ] **Step 5** 提交。

### Task 6：文档与看板

- OPERATIONS：写明 D69 的阶段规则，以及 D68 / D69 的提示与 D57 Task 12「有提示就不自动发布」的关系（spec §3a-6）。
- BACKLOG：按阶段的进度视图。
- `docs/progress.json` 记录状态，并执行 `tools/progress.py`。
- 全量回归：`tools/regress.sh`。
- EC2 验收：spec §8。
