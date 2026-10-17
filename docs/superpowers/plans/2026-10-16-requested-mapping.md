# 映射策略：需求中指定的工具与模型（D45） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 规划者把需求中点名的工具 / 模型写进任务的 `requested`；平台照指定执行，不可用时派发前拒绝并说明，不替换、不换工具；来源可见。

**Architecture:** `domain.steps` / `workflow_import.normalize` 接受并校验 `requested`；`planning.expand` 改回指定、保留未公布的指定模型；Engine 派发前核对；
`recovery.schedule` / `switch` 尊重指定；`domain.mapping` / `_attention` 显示；续作改工具时去掉 `requested`。

**Tech Stack:** Python 3 标准库。

**Spec:** `docs/superpowers/specs/2026-10-16-requested-mapping-design.md`

## Global Constraints

- `requested`：对象，键 ⊆ {`tool`, `model`, `quote`}；`tool` ∈ TOOLS；`model`、`quote` 为非空字符串（≤200）；至少一个 `tool` / `model`。
  错误文字「requested 须为 {tool, model, quote}，至少指定 tool 或 model」。
- 说明文字：「任务「{name}」改回需求指定的工具 {tool}」「任务「{name}」：需求指定的模型 {model} 未由执行环境公布」。
- 拒绝文字：「需求指定的 {tool} 在此执行环境不可用：{reason}」「需求指定的模型 {model} 未由执行环境公布」。
- 来源：`{"source": "requested", "label": "需求中指定：「{quote 前 30 字}」"}`。
- `attention`：标题「需求指定的工具或模型不可用」，操作「修复执行环境后续作，或在「配置并续作」中改用可用的工具或模型。」

## Review Focus

1. **不替换**：指定不可用的工具时，没有任何 Attempt 用别的工具运行（`test_an_unavailable_requested_tool_is_refused_not_replaced`）。
2. **不换工具**：协调者不被请来换工具（同上：没有纠错记录）。
3. **模型不改 auto**（`test_an_unpublished_requested_model_is_kept_and_refused`）。
4. **用户续作优先**（`test_a_continue_with_another_tool_supersedes_the_request`）。
5. **旧数据**：没有 `requested` 的任务行为不变（全量回归）。

---

### Task 1: 指定的采用、拒绝与来源

**Files:** `octopus/domain.py`、`octopus/workflow_import.py`、`octopus/planning.py`、`octopus/engine.py`、`octopus/recovery.py`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`；
Test: `tests/test_requested.py`

- [ ] 测试先行：Review Focus 1–4、`test_a_requested_tool_is_used_and_labelled`、`test_a_planner_that_changed_the_tool_is_corrected`、
  `test_requested_is_checked`、`test_the_planner_is_told_how_to_mark_a_request`。
- [ ] 实现、文档、全量测试。
- [ ] Commit `feat(mapping): 需求中指定的工具与模型——规划者标注 requested，平台照用、不可用时派发前拒绝并说明，不替换不换工具；来源「需求中指定」（D45）`
