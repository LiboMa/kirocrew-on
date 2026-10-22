---
name: receiving-code-review
description: 返工时按评审意见修改：逐条核实、一次一项并测试；不成立的意见在 findings 中写明理由，不盲从也不默默忽略
source: superpowers 6.4.1 receiving-code-review（Jesse Vincent）
license: MIT
---

> 改编说明（KiroCrew ON 平台，D50）：本文改编自 superpowers 6.4.1 receiving-code-review（Jesse Vincent）（MIT）。你在平台的一个**无人值守任务**中：不提交、不推送、不开新分支或工作目录、不写计划文件、不等待用户回答——需要人决定时，在 outcome.json 中写 `decision: "blocked"`、`failure_type: "input"`，把问题写进 findings，平台会转给用户；与本任务契约（验收项、检查命令、可改路径）冲突时以契约为准。原文中与「人类搭档」的对话、GitHub 评论回复等部分已改写为平台的评审来源与结论格式。

# Code Review Reception

## Overview

A rework on the platform comes with review feedback: a review or test task's findings, the Evaluator's verdicts, or the
Coordinator's repair instructions (all in this task's instructions.md). Feedback requires technical evaluation, not
performative agreement or blind implementation.

**Core principle:** Verify before implementing. Technical correctness over social comfort.

## The Response Pattern

```
WHEN this task carries review feedback:

1. READ: All the feedback (instructions.md: repair, failure, findings) without reacting
2. UNDERSTAND: Restate each item as a technical requirement
3. VERIFY: Check it against the code as it is now
4. EVALUATE: Technically sound for THIS codebase and this task's contract?
5. DECIDE: Fix it, or record a reasoned disagreement (below)
6. IMPLEMENT: One item at a time, test each
```

## Forbidden Responses

**NEVER** in the handoff or outcome.json:
- "You're absolutely right!" / "Great point!" / "Thanks for catching that!" (performative)
- "Fixed" without the command and output that show it

**INSTEAD:** state each item, what you changed, and the evidence.

## Unclear Items

```
IF an item is unclear:
  Do not guess. Items may be related: partial understanding = wrong implementation.
  If the code and the requirement do not settle it, write decision "blocked" (failure_type "input") in outcome.json
  with the question in findings; the platform asks the user.
```

## Checks Before Implementing a Suggestion

```
1. Technically correct for THIS codebase?
2. Breaks existing functionality or tests?
3. Is there a reason for the current implementation (git log, comments, tests)?
4. Works on every platform / version the project supports?
5. Did the reviewer have the full context (this task's contract, upstream handoffs)?

IF a suggestion seems wrong:
  Do not implement it silently, do not ignore it silently: record it in outcome.json findings as
  "not applied: <item> — <technical reason, with evidence>". The Evaluator and the Coordinator weigh it.

IF it conflicts with the requirement or this task's contract:
  The contract wins; record the conflict the same way.
```

## YAGNI Check for "Professional" Features

```
IF feedback asks to "implement properly" something:
  grep the codebase for actual usage
  IF unused: record "not applied (unused, YAGNI)" with the evidence
  IF used: implement it properly
```

## Implementation Order

```
FOR multi-item feedback:
  1. Settle anything unclear FIRST (above)
  2. Then:
     - Blocking issues (breaks, security)
     - Simple fixes (typos, imports)
     - Complex fixes (refactoring, logic)
  3. Test each fix individually
  4. Verify no regressions (run the task's verify commands)
```

## When To Disagree

Disagree (with evidence, in findings) when the suggestion:
- breaks existing functionality;
- comes from a reviewer without the full context;
- violates YAGNI (an unused feature);
- is technically incorrect for this stack;
- ignores legacy or compatibility reasons;
- conflicts with the requirement or the contract.

Use technical reasoning, not defensiveness: reference the working tests and code.

## Acknowledging Correct Feedback

When feedback IS correct, just fix it and say what changed:
```
✅ "Fixed: <specific issue>, in <file:line>; <command> now passes."
❌ "You're absolutely right!" / any gratitude expression
```

## If Your Disagreement Was Wrong

```
✅ "Checked <X>: it does <Y>. Applied the fix."
❌ Long apology, defending the earlier position, over-explaining
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Performative agreement | State the requirement, then act |
| Blind implementation | Verify against the codebase first |
| Batch without testing | One item at a time, test each |
| Assuming the reviewer is right | Check whether it breaks things |
| Silently skipping an item | Record "not applied" with the reason |
| Partial implementation | Settle every unclear item first |
