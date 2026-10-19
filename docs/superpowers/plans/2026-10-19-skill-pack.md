# 编码技能包（D50 第 1 期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台维护的方法技能包随任务交给三个后端：规划者按任务推荐，Worker 把技能文件放在 Attempt 目录并在说明中列出，证据与报告记录用了哪些。

**Architecture:** `octopus/skills/<名称>/SKILL.md` + `octopus/skills.py`（目录、规划提示的清单、路径与 sha256）；任务契约 `skills`（domain.steps 校验、CONTRACT、schema、workflow_import 规范化）；规划提示列出技能；引擎对没有 `skills` 能力的 Worker 去掉该字段；Worker 复制文件、写「## 方法」、证据 `skills`、健康信息；控制台 / 报告 / MCP 显示。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-19-skill-pack-design.md`

## Global Constraints

- 技能名：`test-driven-development`、`systematic-debugging`、`verification-before-completion`、`receiving-code-review`、`frontend-design`。
- SKILL.md 头部：`name`、`description`、`source`（来源与版本）、`license`（MIT / Apache-2.0）；正文开头一行「改编说明」。
- 改编后的正文不含：提交 / 推送（`git commit`、`git push`）、`worktree`、询问用户（`your human partner`、「问用户」）、写计划文件（`docs/superpowers`）。
- 契约 `skills`：0–4 个，取自技能包，去重保序；规划结果中的未知名称去掉并记说明。
- Attempt 目录 `skills/<名称>.md`；「## 方法」一节列名称、用途与绝对路径。

## Review Focus

- 旧 Worker：平台不发 `skills`，任务照常（Task 2 `test_an_older_worker_gets_no_skills`）。
- 规划推荐了技能包里没有的名称：去掉并说明，计划仍然应用（Task 2 `test_an_unknown_planned_skill_is_dropped_with_a_note`）。
- 续接的返工：同样复制所选技能（Task 2 `test_a_continued_rework_has_its_skills`）。
- 技能文件在 Attempt 目录中不可被 Agent 改坏影响下一次：每个 Attempt 各自复制（Task 2 `test_each_attempt_has_its_own_copy`）。
- 技能与契约冲突：说明写明以契约为准（Task 1 `test_the_method_section_puts_the_contract_first`）。

---

### Task 1: 技能包与目录

**Files:** Create `octopus/skills/<五个>/SKILL.md`、`octopus/skills/LICENSE-superpowers`、`octopus/skills/LICENSE-frontend-design`、`octopus/skills.py`（`CATALOG`、`description(name)`、`path(name)`、`sha256(name)`、`catalogue()`、`method_section(names, folder)`）；Create `tests/test_skills.py`。

- [ ] 测试：`test_every_skill_has_a_head_and_its_license`、`test_no_skill_tells_a_task_to_commit_open_worktrees_or_ask`、`test_the_catalogue_names_every_skill`、`test_the_method_section_puts_the_contract_first`。
- [ ] 失败 → 写技能包（逐个改编）→ 通过。
- [ ] Commit：`feat(skills): 平台维护的编码技能包——测试先行、系统化调试、完成前验证、按评审返工、前端设计（改编自 superpowers 与 Anthropic frontend-design，附许可证）（D50）`

### Task 2: 契约、规划与派发

**Files:** Modify `octopus/domain.py`（`CONTRACT`、`steps` 校验 `skills`）、`octopus/schema.py`、`octopus/workflow_import.py`、`octopus/planning.py`（提示列出技能）、`octopus/engine.py`（没有能力时去掉）、`octopus/worker.py`（复制、「## 方法」、证据、健康信息）；Create `tests/test_skill_tasks.py`。

- [ ] 测试：`test_a_task_may_name_up_to_four_skills_of_the_pack`、`test_the_planner_is_told_the_skills`、`test_an_unknown_planned_skill_is_dropped_with_a_note`、`test_the_worker_copies_the_skills_and_lists_them`、`test_each_attempt_has_its_own_copy`、`test_a_continued_rework_has_its_skills`、`test_an_older_worker_gets_no_skills`、`test_the_evidence_records_name_and_hash`。
- [ ] 失败 → 实现 → 通过（加 `tests.test_roles tests.test_contract tests.test_prompts_p1 tests.test_kirocrew_contract`）。
- [ ] Commit：`feat(skills): 任务契约 skills——规划者按任务推荐，Worker 随 Attempt 交付并记录（D50）`

### Task 3: 显示、文档、回归、终审与 EC2

- 控制台任务卡片「方法：…」、报告、MCP `skills`；OPERATIONS「编码技能包（D50）」、Skill 说明；`tools/regress.sh`；opus 终审一次修复；EC2：界面类需求的规划与执行。
