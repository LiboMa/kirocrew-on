# 角色卡片（D46 P2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 任务可带 `role`；平台把对应的角色卡片（KiroCrew SKILL.md 格式）内联到任务说明开头，规划者按角色列表与审查独立性规划，控制台 / MCP / 报告显示角色。

**Architecture:** 新模块 `octopus/roles.py` 读 `octopus/roles/<role>/SKILL.md`；`role` 加入任务契约（`domain.CONTRACT`），随契约到达 Worker，`worker.envelope` 内联卡片；`domain.steps` 校验角色、类别相容与（提交时的）审查独立性；`planning` 的规则文字列出角色，`expand` 对规划的违反记说明。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-18-role-cards-design.md`

## Global Constraints

- 角色：`spec`（design）、`sde`（implement / deliver）、`integration_test`（test）、`security_test`、`challenge`、`evaluator`（review）。
- 卡片：`octopus/roles/<role>/SKILL.md`，frontmatter 只有 `name`（= 目录名）与 `description`；正文 ≤600 字符。
- 省略 `role` 时不发卡片、不推断；给 `role` 省略 `kind` 时取该角色首个类别；不相容拒绝。
- 审查类角色（security_test / challenge / evaluator）的 `tool` 不得在此前 implement / deliver 任务的 `tool` 中：提交的拒绝（400），规划的记说明。
- 规划提示在最大输入下 ≤8000 字符（`tests/test_prompts_p1.py` 两条用例保持通过）。
- 旧版工作流运行时（`workflow.py`）丢弃 `role`，与其他契约字段相同。

## Review Focus

- 续作覆盖（`execution` override）改了 `role` 或 `kind`：仍按相容规则校验（Task 1 `test_a_continue_that_changes_the_role_is_checked`）。
- 规划提示在最大输入下超过 8000：卡片列表必须精简到仍能通过（Task 2 运行 `tests.test_prompts_p1`）。
- 旧版工作流（`workflow.py` 校验）收到带 `role` 的步骤：丢弃而不报错（Task 1 `test_the_legacy_runtime_drops_the_role`）。
- 重新规划（replan）的任务带 `role`：同样内联卡片、同样记独立性说明（Task 2 `test_a_replan_notes_a_reviewer_on_the_authors_tool`）。
- 修订历史对比两版：`role` 的变化列出「角色」（Task 1 `test_a_role_change_shows_in_the_revisions`）。

---

### Task 1: 角色卡片、`role` 字段与任务说明

**Files:**
- Create: `octopus/roles.py`、`octopus/roles/{spec,sde,integration_test,security_test,challenge,evaluator}/SKILL.md`
- Modify: `octopus/domain.py:42`（CONTRACT）与 `steps`；`octopus/worker.py`（`envelope`、`briefing`）；`octopus/revisions.py:9-11`；`workflow.py:120`
- Create: `tests/test_roles.py`

**Interfaces:**
- Produces: `roles.KINDS: dict[str, tuple[str, ...]]`、`roles.REVIEWERS: frozenset`、`roles.LABELS: dict[str, str]`、`roles.card(role) -> str`、`roles.description(role) -> str`、`roles.catalogue() -> str`、`roles.unindependent(steps) -> list[(step, author_tool)]`。

- [ ] **Step 1: 写失败的测试 `tests/test_roles.py`**

```python
"""D46 P2: a task's role chooses the card its Agent works by (inlined at the top of its instructions); kind keeps deciding
the gates. Cards are KiroCrew-style SKILL.md files (owner 2026-10-18: a KiroCrew skill cannot be chosen per spawn)."""
import json
import re
import unittest
from pathlib import Path

from octopus import revisions, roles
from octopus.domain import steps
from octopus.security import Error
from octopus.worker import envelope

ROOT = Path(__file__).resolve().parents[1]


def step(**extra):
    return {"id": extra.pop("id", "task-1"), "name": extra.pop("name", "任务"), "tool": extra.pop("tool", "claude"),
            "prompt": "做", **extra}


class CardTests(unittest.TestCase):
    def test_every_role_has_a_kirocrew_style_card(self):
        self.assertEqual(set(roles.KINDS), {"spec", "sde", "integration_test", "security_test", "challenge", "evaluator"})
        for role in roles.KINDS:
            with self.subTest(role):
                text = (ROOT / "octopus/roles" / role / "SKILL.md").read_text()
                front = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
                fields = dict(line.split(": ", 1) for line in front.splitlines())
                self.assertEqual(set(fields), {"name", "description"})
                self.assertEqual(fields["name"], role)
                self.assertTrue(0 < len(roles.card(role)) <= 600)

    def test_the_catalogue_names_every_role_and_its_kind(self):
        text = roles.catalogue()
        for role, kinds in roles.KINDS.items():
            self.assertIn(f"{role}（{roles.LABELS[role]}，kind {'/'.join(kinds)}）", text)


class RoleFieldTests(unittest.TestCase):
    def test_a_role_is_kept_and_infers_its_kind(self):
        self.assertEqual(steps([step(role="security_test", tool="codex")])[0]["kind"], "review")
        self.assertEqual(steps([step(role="sde", kind="deliver")])[0]["role"], "sde")

    def test_an_unknown_role_or_an_incompatible_kind_is_refused(self):
        for bad in (step(role="tester"), step(role="sde", kind="review"), step(role="spec", kind="implement")):
            with self.subTest(bad), self.assertRaises(Error):
                steps([bad])

    def test_a_submitted_reviewer_on_the_authors_tool_is_refused(self):
        plan = [step(id="code", role="sde", tool="claude"), step(id="check", role="evaluator", tool="claude")]
        with self.assertRaises(Error) as caught:
            steps(plan)
        self.assertIn("审查不独立", str(caught.exception))
        steps([plan[0], {**plan[1], "tool": "codex"}])   # another tool is fine
        self.assertEqual(len(roles.unindependent(steps(plan[:1]) + [{**plan[1], "kind": "review"}])), 1)

    def test_without_a_role_nothing_changes(self):
        self.assertNotIn("role", steps([step(kind="review")])[0])

    def test_the_legacy_runtime_drops_the_role(self):
        import workflow
        self.assertNotIn("role", workflow.validate({"steps": [step(role="sde", kind="implement")]})["steps"][0]
                         if isinstance(workflow.validate({"steps": [step(role="sde", kind="implement")]}), dict) else {})

    def test_a_role_change_shows_in_the_revisions(self):
        self.assertIn("角色", revisions.LABELS.values())
        self.assertIn("role", revisions.CONTRACT)


class InstructionsTests(unittest.TestCase):
    def render(self, contract):
        return envelope("需求", None, contract, "任务说明", [], "/w", "/w/handoff.md")

    def test_a_task_with_a_role_starts_with_its_card(self):
        text = self.render({"kind": "review", "role": "security_test"})
        self.assertTrue(text.startswith("# 你的角色：安全测试\n" + roles.card("security_test")))
        self.assertIn("角色：安全测试", text.split("## 本任务契约", 1)[1])

    def test_a_task_without_a_role_is_unchanged(self):
        self.assertTrue(self.render({"kind": "review"}).startswith("# 本次研发任务"))
```
（`test_the_legacy_runtime_drops_the_role` 以 `workflow.validate` 的真实返回结构为准：先 `grep -n "def validate" -A20 workflow.py` 确认返回值，再把断言写成直接检查返回的步骤不含 `role`，并在 ledger 记 Ruling。）

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_roles -v`
Expected: ERROR `ImportError: cannot import name 'roles' from 'octopus'`。

- [ ] **Step 3: 实现**

`octopus/roles.py`：
```python
"""Role cards (D46 P2): how a task's Agent works — what it does and delivers, and when it says changes_requested or
BLOCKED — chosen by the task's ``role``, while ``kind`` keeps deciding the gates. Each card is a KiroCrew-style SKILL.md
under octopus/roles/<role>/, inlined at the top of the task's instructions (owner 2026-10-18: a KiroCrew skill is fixed
per agent spec and cannot be chosen per spawn, so the card travels with the task)."""
from pathlib import Path
import re

ROOT = Path(__file__).with_name("roles")
KINDS = {"spec": ("design",), "sde": ("implement", "deliver"), "integration_test": ("test",),
         "security_test": ("review",), "challenge": ("review",), "evaluator": ("review",)}
LABELS = {"spec": "Spec 分析", "sde": "SDE 开发", "integration_test": "集成测试", "security_test": "安全测试",
          "challenge": "Challenge", "evaluator": "Evaluator"}
REVIEWERS = frozenset({"security_test", "challenge", "evaluator"})   # never the tool that wrote the change they look at


def _read(role):
    match = re.match(r"---\n(.*?)\n---\n(.*)", (ROOT / role / "SKILL.md").read_text(), re.S)
    return dict(line.split(": ", 1) for line in match.group(1).splitlines()), match.group(2).strip()


def card(role):
    """The card's text, as the task's instructions carry it."""
    return _read(role)[1]


def description(role):
    return _read(role)[0]["description"]


def catalogue():
    """One line per role for the planner: its id, label, kind and what it is for."""
    return "\n".join(f"   - {role}（{LABELS[role]}，kind {'/'.join(kinds)}）：{description(role)}" for role, kinds in KINDS.items())


def unindependent(planned):
    """Reviewing tasks that use the tool of a task before them that wrote the change: [(task, author tool)]."""
    found, authors = [], set()
    for item in planned:
        if item.get("role") in REVIEWERS and item.get("tool") in authors:
            found.append((item, item["tool"]))
        if item.get("kind") in ("implement", "deliver"):
            authors.add(item.get("tool"))
    return found
```
六张卡片（正文按此，各 ≤600 字符）：

`octopus/roles/spec/SKILL.md`
```
---
name: spec
description: 读需求与源码写规格（目标、范围、验收标准、风险、待确认问题），不改业务代码
---
你担任 Spec 分析。先读需求与相关源码，写出规格：目标、范围与不做什么、可检查的验收标准、主要风险、待确认问题，写入本任务的交付物（如 spec.md）。不修改业务代码。需求含糊时把疑点列为待确认问题并给出你采用的假设；缺少无法替代的输入时 BLOCKED 并写明需要什么。
```
`octopus/roles/sde/SKILL.md`
```
---
name: sde
description: 按规格与契约实现并自测，改动限定在可改动路径
---
你担任 SDE 开发。按规格与本任务契约实现，改动限定在可改动路径；补充或更新测试并实际运行检查命令，不降低测试强度、不跳过失败。交接写明改了什么、为什么、怎样验证、有哪些限制。需求不清时按最合理的理解实现，并在交接中写明假设。
```
`octopus/roles/integration_test/SKILL.md`
```
---
name: integration_test
description: 从用户视角跨模块验证真实接口与流程，只写测试与报告
---
你担任集成测试。从用户视角跨模块验证：按需启动服务，跑真实的接口与流程，不只跑单元测试。只写测试与报告，不修改实现。发现缺陷用 changes_requested，附复现步骤、实际与期望结果；检查生成的文件写入 .gitignore。
```
`octopus/roles/security_test/SKILL.md`
```
---
name: security_test
description: 只读审查改动的秘密、依赖与配置、输入校验、权限，按严重度给出发现
---
你担任安全测试。只读审查本会话的改动：秘密与凭证、依赖与配置、输入校验与注入、权限与越权、日志与错误信息泄露。每条发现写明严重度（高 / 中 / 低）、文件:行与修复建议；有高危即 changes_requested。不修改文件。
```
`octopus/roles/challenge/SKILL.md`
```
---
name: challenge
description: 对候选改动做对抗性检查，找反例、边界与失败场景
---
你担任 Challenge。对候选改动做对抗性检查：找反例、边界、并发与失败场景、与需求不符之处，能复现的写出复现步骤。按影响排序给出挑战清单；有成立的问题即 changes_requested。只读，不修改文件。
```
`octopus/roles/evaluator/SKILL.md`
```
---
name: evaluator
description: 逐条核对验收项并给出证据，以自己的核对而非实现者的自述做判定
---
你担任 Evaluator。逐条对照本任务的验收项给出 pass / fail 与证据（命令输出、文件:行），综合实现、测试与挑战的结论做出判定；不以实现者的自述为准，自己核对。任一验收项 fail 即 changes_requested。不修改文件。
```
`octopus/domain.py`：`CONTRACT = ("kind", "role", "deliverables", "acceptance", "verify", "depends_on", "paths")`；`steps()` 中 `kind` 处理之后加入：
```python
        if "role" in step:   # D46 P2: how the Agent works; kind keeps deciding the gates
            from . import roles
            require(step["role"] in roles.KINDS, "角色只能是 " + "、".join(roles.KINDS))
            allowed = roles.KINDS[step["role"]]
            if "kind" in item:
                require(item["kind"] in allowed, f"角色 {step['role']} 的任务类别须为 {' 或 '.join(allowed)}")
            else:
                item["kind"] = allowed[0]
            item["role"] = step["role"]
```
循环之后、`return result` 之前：
```python
    if not planned:   # D46 P2: a submitted reviewer never uses the author's tool; a planned one is noted (planning.expand)
        from . import roles
        bad = roles.unindependent(result)
        require(not bad, "审查不独立：" + "；".join(f"任务「{t['name']}」（{roles.LABELS[t['role']]}）与被审查改动的作者使用同一工具 {tool}"
                                               for t, tool in bad))
```
`octopus/worker.py` `envelope`：返回值前加卡片：
```python
    from . import roles
    role = (contract or {}).get("role")
    card = f"# 你的角色：{roles.LABELS[role]}\n{roles.card(role)}\n\n" if role in roles.KINDS else ""
    return (card + "# 本次研发任务\n\n" + ...)
```
`briefing`：`类别：` 一行之后加 `(f"角色：{roles.LABELS[contract['role']]}\n" if contract.get("role") in roles.KINDS else "")`。
`octopus/revisions.py`：`CONTRACT` 加 `"role"`（在 `"kind"` 之后），`LABELS` 加 `"role": "角色"`。
`workflow.py:120`：`CONTRACT_FIELDS` 加 `"role"`。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_roles tests.test_prompts_p1 tests.test_workflow_import tests.test_revisions -v`（不存在的模块以 `ls tests` 为准删去）
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add octopus/roles.py octopus/roles octopus/domain.py octopus/worker.py octopus/revisions.py workflow.py tests/test_roles.py
git commit -m "feat(roles): 角色卡片（D46 P2）——任务可带 role（6 个角色，KiroCrew SKILL.md 格式），任务说明开头内联对应卡片、契约写明角色；角色与类别须相容、省略类别时推断；提交的审查类任务不得使用被审查改动的作者工具"
```

---

### Task 2: 规划者与独立性说明

**Files:**
- Modify: `octopus/planning.py`（`EXAMPLE` 加 `"role": "sde"`；`rules()` 加角色列表与独立性；`expand` 记说明）
- Test: `tests/test_roles.py`（追加）

**Interfaces:**
- Consumes: `roles.catalogue()`、`roles.unindependent()`、`roles.LABELS`。

- [ ] **Step 1: 写失败的测试（追加）**

```python
class PlannerTests(unittest.TestCase):
    def test_the_planner_is_told_the_roles_and_independence(self):
        from octopus import planning
        for prompt in (planning.plan_step()["prompt"],
                       planning.replan_step("replan-1", [], "失败", {"reason": "r", "instructions": "i"})["prompt"]):
            self.assertIn("security_test（安全测试，kind review）", prompt)
            self.assertIn("不得使用被审查改动的作者工具", prompt)
            self.assertIn('"role"', prompt)


PLANNED = """```json
{"intent": "实现并审查", "summary": "需求", "name": "计划", "steps": [
 {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "role": "sde", "acceptance": ["完成"], "verify": ["true"]},
 {"id": "check", "name": "评估", "tool": "claude", "prompt": "评估", "kind": "review", "role": "evaluator", "acceptance": ["完成"]}]}
```"""


from tests.test_confirmation import ConfirmationCase


class PlannedRoleTests(ConfirmationCase):
    def test_a_planned_reviewer_on_the_authors_tool_is_applied_with_a_note(self):
        import tests.test_confirmation as confirmation
        original, confirmation.PLAN = confirmation.PLAN, PLANNED
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertEqual([t["spec"].get("role") for t in run["tasks"] if t["id"] != "plan"], ["sde", "evaluator"])
        self.assertTrue(any("审查不独立" in note and "评估" in note for note in run["planning"]["notes"]))
```
并追加 `test_a_replan_notes_a_reviewer_on_the_authors_tool`：沿用 `tests/test_replan.py` 的 `ReplanCase`，让协调者返回 `replan`、重新规划返回同样违反独立性的两个任务，断言 `run["replan"]["notes"]` 含「审查不独立」（写法照该文件中已有的 replan 用例；以其 fixture 名称为准，Ruling 记入 ledger）。

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_roles -v`
Expected: `PlannerTests` FAIL（提示中没有角色列表）；`PlannedRoleTests` FAIL（没有说明）。

- [ ] **Step 3: 实现**

`planning.py`：`EXAMPLE["steps"][0]` 在 `"kind": "implement"` 后加 `"role": "sde"`。`rules()` 的第 4 条末尾（「需求要求新建仓库时…」之后）加入：
```python
   每个任务可给出 role（担任的角色，决定 Agent 拿到的角色卡片；kind 仍决定门禁），可选：
{roles.catalogue()}
   审查类角色（security_test / challenge / evaluator）不得使用被审查改动的作者工具（此前 implement / deliver 任务的 tool）。
```
（`rules` 是 f-string：在函数开头 `from . import roles`；花括号不需转义，因为是表达式。）`expand` 中 D47 步骤说明的循环之后加入：
```python
    from . import roles
    for task, tool in roles.unindependent(steps):   # D46 P2: applied as planned; P3's Evaluator gate judges on it
        notes.append(f"审查不独立：任务「{task['name']}」（{roles.LABELS[task['role']]}）与被审查改动的作者使用同一工具 {tool}")
```
（`expand` 里规划的任务变量名以现有代码为准——`steps` 为已校验的任务列表。）

- [ ] **Step 4: 运行，确认通过；提示长度不超限**

Run: `.venv/bin/python -m unittest tests.test_roles tests.test_prompts_p1 tests.test_replan tests.test_confirmation tests.test_auto_publish -v`
Expected: 全部 PASS。若 `test_the_first_plan_fits_with_many_models` / `test_the_replan_fits_with_every_input_at_its_limit` 失败，把 `catalogue()` 每行的用途截短（description 只取前 24 个字符），ledger 记 Ruling。

- [ ] **Step 5: Commit**

```bash
git add octopus/planning.py tests/test_roles.py
git commit -m "feat(planning): 规划提示介绍 6 个角色（id、类别、用途）与审查独立性，示例带 role；规划的审查类任务与作者同工具时照常应用并记「审查不独立」（D46 P2）"
```

---

### Task 3: 控制台、MCP、报告与文档

**Files:**
- Modify: `web/src/api/types.ts`（`Step.role`）、`web/src/lib/labels.ts`（`ROLE_LABELS`）、`web/src/features/sessions/TaskList.tsx:34`
- Modify: `octopus/mcp.py`（精简状态的任务带 `role`）、`octopus/report.py:97`（类别列）
- Modify: `skills/kirocrew-on/SKILL.md`、`docs/OPERATIONS.md`
- Test: `tests/test_roles.py`（追加）、`web/src/lib/labels.test.ts`（追加）

- [ ] **Step 1: 写失败的测试**

`tests/test_roles.py` 追加：
```python
class VisibleTests(unittest.TestCase):
    def test_the_console_has_a_title_for_every_role(self):
        text = (ROOT / "web/src/lib/labels.ts").read_text()
        block = re.search(r"ROLE_LABELS[^=]*=\s*\{(.*?)\}", text, re.S).group(1)
        self.assertEqual(dict(re.findall(r'(\w+):\s*"([^"]+)"', block)), roles.LABELS)

    def test_the_compact_status_and_the_report_carry_the_role(self):
        from octopus import mcp
        task = {"id": "check", "status": "accepted", "spec": {"name": "安全审查", "tool": "codex", "kind": "review",
                                                              "role": "security_test"}, "attempts": []}
        view = mcp.compact_status({"id": "run-1", "status": "completed", "tasks": [task]}, [])["session"]
        self.assertEqual(view["tasks"][0]["role"], "security_test")
        from octopus import report
        self.assertIn("review（安全测试）", report.markdown({"id": "run-1", "name": "n", "status": "completed", "input": "x",
                                                         "tasks": [{**task, "ordinal": 1}]}))
```
（`report.markdown` 的真实函数名与最小输入以 `grep -n "^def " octopus/report.py` 为准；若需要更多字段，用 `tests/test_report.py` 已有的 fixture 构造，Ruling 记入 ledger。）

`web/src/lib/labels.test.ts` 追加：
```ts
  test("a task's role has a title (D46 P2)", () => {
    expect(ROLE_LABELS.security_test).toBe("安全测试");
  });
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_roles -v`；`npx --prefix web vitest run --root web src/lib/labels.test.ts`
Expected: FAIL（`ROLE_LABELS` 不存在；精简状态无 `role`；报告无角色）。

- [ ] **Step 3: 实现**

- `types.ts` `Step`：`kind?` 之后加 `/** D46 P2: the role the task's Agent works in (its card); kind decides the gates. */ role?: "spec" | "sde" | "integration_test" | "security_test" | "challenge" | "evaluator";`
- `labels.ts`：
```ts
/** D46 P2: the roles a task's Agent works in (octopus/roles.py LABELS; kept equal by tests/test_roles.py). */
export const ROLE_LABELS: Record<string, string> = {
  spec: "Spec 分析", sde: "SDE 开发", integration_test: "集成测试", security_test: "安全测试", challenge: "Challenge", evaluator: "Evaluator",
};
```
- `TaskList.tsx`：在 `Effort {…}` 之后加 `{spec.role && <>{" · "}<span>角色：{ROLE_LABELS[spec.role] ?? spec.role}</span></>}`（import `ROLE_LABELS`）。
- `mcp.py` 精简状态的任务字典加 `**({"role": task["spec"]["role"]} if task["spec"].get("role") else {})`。
- `report.py:97`：`"kind": (spec.get("kind") or "—") + (f"（{roles.LABELS[spec['role']]}）" if spec.get("role") in roles.LABELS else "")`（import `from . import roles`）。
- `SKILL.md`（Skill）在任务字段说明处加：「`role` 可选：`spec`、`sde`、`integration_test`、`security_test`、`challenge`、`evaluator`——Agent 按对应角色卡片工作；审查类角色不要用实现该改动的工具。」
- `OPERATIONS.md` 加一节「角色卡片（D46 P2）」：角色与类别、卡片位置与内联方式、省略时不变、独立性（提交拒绝、规划记说明）。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_roles tests.test_auto_publish -v`；`npx --prefix web vitest run --root web`；`npm --prefix web run build`
Expected: 全部 PASS，构建成功。

- [ ] **Step 5: Commit**

```bash
git add web/src/api/types.ts web/src/lib/labels.ts web/src/lib/labels.test.ts web/src/features/sessions/TaskList.tsx octopus/mcp.py octopus/report.py skills/kirocrew-on/SKILL.md docs/OPERATIONS.md tests/test_roles.py
git commit -m "feat(console): 任务卡片、MCP 精简状态与报告显示角色（D46 P2）；Skill 与 OPERATIONS 写明 role 与审查独立性"
```

---

### Task 4: 回归、终审与 EC2 验收

- [ ] **Step 1:** `PYTHON=<venv python> bash tools/regress.sh` → `regress: OK`。
- [ ] **Step 2:** 终审（opus 子代理），Critical / Important 一次修复（各 RED→GREEN），Minor 记 ledger 与 BACKLOG。
- [ ] **Step 3:** 部署（先确认 EC2 空闲），运行 `/tmp/kc_accept_p2.py`：公开测试仓库 `LiboMa/bedrock-claude-call`（只读、不发布）上的 KiroCrew 规划会话，需求「为 README 补一节用法说明，并做一次安全审查（检查密钥泄露与敏感信息）」；
  核对规划的审查任务 `role = security_test`、其 `instructions.md`（Worker 上的 Attempt 目录）以「# 你的角色：安全测试」开头、结论含严重度；会话完成或审查给出 changes_requested 后按恢复完成。
