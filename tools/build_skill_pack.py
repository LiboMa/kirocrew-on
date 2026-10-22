"""Run from the repository root: SKILL_SOURCES=<claude-plugins-official cache> python3 tools/build_skill_pack.py
Builds octopus/skills/<name>/SKILL.md from the upstream skills (D50): platform head, adaptation note, targeted
replacements of what conflicts with an unattended platform task, and supporting files folded in as appendices."""
from pathlib import Path
import shutil

import os
P = Path(os.environ.get("SKILL_SOURCES", Path.home() / ".claude/plugins/cache/claude-plugins-official"))   # the plugin cache
SP = P / "superpowers/6.4.1/skills"
FD = P / "frontend-design/c2bdb427efe8/skills/frontend-design"
OUT = Path("octopus/skills")

NOTE = ("> 改编说明（KiroCrew ON 平台，D50）：本文改编自 {source}（{license}）。你在平台的一个**无人值守任务**中：不提交、不推送、不开新分支或工作目录、"
        "不写计划文件、不等待用户回答——需要人决定时，在 outcome.json 中写 `decision: \"blocked\"`、`failure_type: \"input\"`，"
        "把问题写进 findings，平台会转给用户；与本任务契约（验收项、检查命令、可改路径）冲突时以契约为准。{extra}\n\n")


def strip_head(text):
    return text.split("---\n", 2)[2] if text.startswith("---\n") else text


def build(name, description, source, licence, body, extra=""):
    head = f"---\nname: {name}\ndescription: {description}\nsource: {source}\nlicense: {licence}\n---\n\n"
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(head + NOTE.format(source=source, license=licence, extra=extra) + body.lstrip())


def replace(text, pairs):
    for old, new in pairs:
        assert old in text, (old[:60],)
        text = text.replace(old, new)
    return text


def appendix(title, path, pairs=()):
    return f"\n\n---\n\n## 附录：{title}\n\n" + replace(strip_head(path.read_text()), pairs).strip() + "\n"


# test-driven-development
tdd = replace(strip_head((SP / "test-driven-development/SKILL.md").read_text()), [
    ("**Exceptions (ask your human partner):**", "**Exceptions (only where the task's contract says so; say it in the handoff):**"),
    ("Write wished-for API. Write assertion first. Ask your human partner.",
     "Write wished-for API. Write assertion first. Still stuck: write BLOCKED with the question in outcome.json."),
    ("No exceptions without your human partner's permission.", "No exceptions unless the task's contract says so."),
    ("When writing or changing any test, read [writing-good-tests.md](writing-good-tests.md) for the rules that keep tests honest:",
     "When writing or changing any test, read the appendix「写出诚实的测试」below for the rules that keep tests honest:"),
])
tdd += appendix("写出诚实的测试（writing-good-tests）", SP / "test-driven-development/writing-good-tests.md", [
    ("agent's behavior (superpowers:writing-skills); prose for humans earns no", "agent's behavior; prose for humans earns no"),
    ('**your human partner\'s correction:** "Are we testing the behavior of a', '**A reviewer\'s correction:** "Are we testing the behavior of a'),
    ("components. **your human partner's question:** \"Do we need to be using a", "components. **A reviewer's question:** \"Do we need to be using a"),
])
build("test-driven-development",
      "实现功能或修复缺陷时先写会失败的测试、看它失败、再写最少的代码让它通过；无法测试时说明原因",
      "superpowers 6.4.1 test-driven-development（Jesse Vincent）", "MIT", tdd)

# systematic-debugging
debug = replace(strip_head((SP / "systematic-debugging/SKILL.md").read_text()), [
    ("   - Use the `superpowers:test-driven-development` skill for writing proper failing tests",
     "   - Follow the test-driven-development method (its skill file, when this task has it) for writing proper failing tests"),
    ("   - Use the `superpowers:verification-before-completion` skill before claiming success",
     "   - Follow the verification-before-completion method before claiming success"),
    ("   **Discuss with your human partner before attempting more fixes**",
     "   **Stop before attempting more fixes:** write `decision: \"blocked\"` (failure_type implementation) in outcome.json with\n"
     "   what you tried and why the design looks wrong; the platform's Coordinator decides the next step"),
    ("## your human partner's Signals You're Doing It Wrong", "## Signals You're Doing It Wrong (in reviews, failing checks and repair instructions)"),
])
for title, file in (("追溯根因（root-cause-tracing）", "root-cause-tracing.md"), ("多层防御（defense-in-depth）", "defense-in-depth.md"),
                    ("按条件等待（condition-based-waiting）", "condition-based-waiting.md")):
    debug += appendix(title, SP / "systematic-debugging" / file)
build("systematic-debugging",
      "遇到缺陷、测试失败或异常行为时，先找根因再修：复现、收集证据、单一假设、一次一个改动；三次修复失败即质疑设计",
      "superpowers 6.4.1 systematic-debugging（Jesse Vincent）", "MIT", debug,
      "文中提到的附录在本文末尾。")

# verification-before-completion
verify = replace(strip_head((SP / "verification-before-completion/SKILL.md").read_text()), [
    ("- About to commit/push/PR without verification", "- About to write `decision: \"pass\"` in outcome.json without verification"),
    ("- Committing, PR creation, task completion", "- Writing outcome.json, the handoff, task completion"),
])
build("verification-before-completion",
      "在 outcome.json 写 pass、在交接中声称完成或修好之前，先运行能证明它的命令并读完输出；证据先于结论",
      "superpowers 6.4.1 verification-before-completion（Jesse Vincent）", "MIT", verify,
      "证据（命令与输出）写进 outcome.json 的 acceptance evidence 与交接。")

# receiving-code-review: the review is the platform's (a review task, the Evaluator, the Coordinator's repair)
review = """# Code Review Reception

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
"""
build("receiving-code-review",
      "返工时按评审意见修改：逐条核实、一次一项并测试；不成立的意见在 findings 中写明理由，不盲从也不默默忽略",
      "superpowers 6.4.1 receiving-code-review（Jesse Vincent）", "MIT", review,
      "原文中与「人类搭档」的对话、GitHub 评论回复等部分已改写为平台的评审来源与结论格式。")

# frontend-design
build("frontend-design", "新建或改造界面时，按需求确定有个性的视觉方向（配色、字体、版式），做出不像模板的完成度",
      "Anthropic claude-plugins-official frontend-design（c2bdb427efe8）", "Apache-2.0",
      strip_head((FD / "SKILL.md").read_text()), "原文未作改动，只加了本说明与头部字段。")

shutil.copy(SP.parent / "LICENSE", OUT / "LICENSE-superpowers")
shutil.copy(P / "frontend-design/c2bdb427efe8/LICENSE", OUT / "LICENSE-frontend-design")
print("built", sorted(p.name for p in OUT.iterdir()))
