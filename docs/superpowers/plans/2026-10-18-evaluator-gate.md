# Evaluator 门禁（D46 P3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改代码的新会话以 Evaluator 的逐项判定为准：缺少 Evaluator 时平台追加；Evaluator 每项 pass / fail / pending 带证据；fail 进入返工；pending 停在「验收待确认」，由人确认通过。

**Architecture:** 新模块 `octopus/evaluation.py`（适用条件、逐项判定、追加 Evaluator）；引擎在 `_more_gates` 之后判定 `evaluated` 门禁，pending 时执行与任务进入 `awaiting_acceptance`、会话停下；会话的任务全部通过时，引擎先检查是否需要追加 Evaluator；`Service.operate("accept")` 记确认者并继续。适用条件：执行契约 ≥4 且 Worker 公布 `role_cards`。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-18-evaluator-gate-design.md`

## Global Constraints

- 适用：`execution_contract >= 4`（新会话）且 Worker 能力 `role_cards` 为真；契约 ≤3 或旧 Worker 不变。
- 判定集合：`pass` / `fail` / `pending`，每项须有非空 `evidence`；按验收项原文匹配，原文不全但条数相同时按顺序匹配。
- 追加的 Evaluator：id `evaluator`（冲突时 `evaluator-2`…），`kind: review`、`role: evaluator`、`model: auto`；验收项取此前 implement / deliver / test 的验收项（去重、≤20，空时「本次需求的目标已实现并经验证」）；
  工具：计划中审查用过且非作者、可用的工具 → 第一个非作者的可用工具（环境报告顺序）→ 第一个可用工具并记「审查不独立」。
- 等待确认不计入预算（确认时 `restart_budget`）。确认者：创建者、Team owner、管理员（与 `confirm` 相同）。
- 规划提示在最大输入下 ≤8000；角色卡片正文 ≤600。

## Review Focus

- 续作返工后 Evaluator 复评又全 pass：会话完成，旧的 pending 不残留（Task 1 `test_a_rework_after_pending_reevaluates`）。
- 重新规划替换了 Evaluator 或在其后加了实现任务：完成前再次检查并追加（Task 2 `test_an_implementation_after_the_evaluator_gets_another`）。
- Evaluator 以 changes_requested 结束（决定本身不通过）：照现有 `outcome_pass` 失败流程，不进入 `evaluated`（Task 1 `test_a_changes_requested_evaluator_goes_to_recovery`）。
- 停在「验收待确认」时停止、关闭、续作：照常可用（Task 1 `test_awaiting_acceptance_can_be_stopped_or_continued`）。
- 已归档 Team 的会话不能确认验收（Task 1 `test_an_archived_teams_session_cannot_accept` 以 `active_team` 检查覆盖）。

---

### Task 1: 逐项判定、「验收待确认」与确认

**Files:**
- Create: `octopus/evaluation.py`、`tests/test_evaluator.py`
- Modify: `octopus/engine.py`（判定后）、`octopus/domain.py`（`SETTLED`、start 的契约版本、`operate` 的 `accept`、`_attention`）、`web/src/lib/labels.ts`（事件标题）、`tests/test_engine_gates.py:82,280`（契约版本 3 → 4，Ruling）

**Interfaces:**
- Produces: `evaluation.applies(snapshot, spec, config) -> bool`、`evaluation.verdicts(spec, report) -> (error | None, pending: list[{item, evidence}])`、运行 / 任务 / 执行状态 `awaiting_acceptance`、`Service.operate(actor, rid, "accept", {request_id, expected_version})`、事件 `run.awaiting_acceptance`、`run.accepted`、`command.accept`。

- [ ] **Step 1: 写失败的测试 `tests/test_evaluator.py`**

```python
"""D46 P3: a Session that changes code passes on its Evaluator's verdict on each acceptance item — a fail goes to rework,
a pending one waits for a person (「验收待确认」); the implementer's own pass is not the Session's."""
import json
import unittest

from octopus.db import dumps
from octopus.security import Error
from tests.test_confirmation import ConfirmationCase

TEAM = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "role": "sde",
         "acceptance": ["登录成功", "错误密码提示"], "verify": ["true"]},
        {"id": "check", "name": "评估", "tool": "codex", "prompt": "评估", "kind": "review", "role": "evaluator",
         "acceptance": ["登录成功", "错误密码提示"]}]


class EvaluatorCase(ConfirmationCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:   # a Worker with role cards: the Evaluator gate applies
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": {"role_cards": True,
                               "tools": {"kiro": {"available": True}, "claude": {"available": True}, "codex": {"available": True}}}}),))
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pass", "evidence": "截图"}]]
        self.decision = "pass"

    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        if path == "/inspect" and (self.bodies[body["attempt_id"]].get("contract") or {}).get("role") == "evaluator":
            outcome = json.loads(result["outcome"])
            outcome.update(decision=self.decision, acceptance=self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0])
            result["outcome"] = dumps(outcome)
        return result

    def run_(self, steps=TEAM):
        rid = self.start(steps=steps, recovery={"enabled": False, "max_cycles": 2})
        return rid, self.tick(rid)


class VerdictTests(EvaluatorCase):
    def test_every_item_passing_completes_the_session(self):
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")

    def test_a_failed_item_goes_to_rework(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "fail", "evidence": "没有提示"}]]
        _, run = self.run_()
        self.assertEqual(run["status"], "blocked")
        self.assertIn("错误密码提示", run["error"])

    def test_a_missing_item_or_evidence_is_refused(self):
        for verdicts in ([[{"item": "登录成功", "result": "pass", "evidence": "curl 200"}]],
                         [[{"item": "登录成功", "result": "pass", "evidence": ""}, {"item": "错误密码提示", "result": "pass", "evidence": "x"}]],
                         [[{"item": "登录成功", "result": "maybe", "evidence": "x"}, {"item": "错误密码提示", "result": "pass", "evidence": "x"}]]):
            with self.subTest(verdicts):
                self.verdicts = verdicts
                _, run = self.run_()
                self.assertEqual(run["status"], "blocked")

    def test_paraphrased_items_in_order_are_matched(self):
        self.verdicts = [[{"item": "能登录", "result": "pass", "evidence": "curl 200"},
                          {"item": "密码错误时有提示", "result": "pass", "evidence": "截图"}]]
        self.assertEqual(self.run_()[1]["status"], "completed")

    def test_a_changes_requested_evaluator_goes_to_recovery(self):
        self.decision = "changes_requested"
        _, run = self.run_()
        self.assertEqual(run["status"], "blocked")


class AcceptanceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_a_pending_item_waits_for_a_person_then_completes(self):
        rid, run = self.run_()
        self.assertEqual(run["status"], "awaiting_acceptance")
        self.assertEqual(run["attention"]["title"], "验收待确认")
        self.assertIn("错误密码提示", run["attention"]["detail"])
        self.service.operate(self.actor, rid, "accept", {"request_id": "accept-fixture-1", "expected_version": run["version"]})
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        check = next(t for t in run["tasks"] if t["id"] == "check")
        self.assertEqual(check["attempts"][-1]["evidence"]["evaluation"]["accepted_by"]["username"], "owner")

    def test_only_a_waiting_session_can_be_accepted(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pass", "evidence": "b"}]]
        rid, run = self.run_()
        with self.assertRaises(Error):
            self.service.operate(self.actor, rid, "accept", {"request_id": "accept-fixture-2", "expected_version": run["version"]})

    def test_awaiting_acceptance_can_be_stopped_or_continued(self):
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "continue", {"request_id": "accept-fixture-3", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补上错误密码提示的文案"})
        self.assertNotEqual(self.service.run(self.actor, rid)["status"], "awaiting_acceptance")

    def test_a_rework_after_pending_reevaluates(self):
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pending", "evidence": "b"}],
                         [{"item": "登录成功", "result": "pass", "evidence": "a"}, {"item": "错误密码提示", "result": "pass", "evidence": "c"}]]
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "continue", {"request_id": "accept-fixture-4", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补文案"})
        self.assertEqual(self.tick(rid)["status"], "completed")


class ScopeTests(ConfirmationCase):
    def test_without_role_cards_nothing_changes(self):
        rid = self.start(steps=TEAM, recovery={"enabled": False, "max_cycles": 2})   # the fixture's Worker has no role_cards
        self.assertEqual(self.tick(rid)["status"], "completed")

    def test_new_sessions_use_contract_4(self):
        rid = self.start(steps=TEAM)
        with self.db.read() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
        self.assertEqual(snapshot["execution_contract"], 4)
```
（已归档 Team 的确认：`operate` 中把 `accept` 加入 `active_team` 检查的集合，测试照 `tests/test_confirmation.py` 中「已归档 Team 的会话不能确认」的写法复制为 accept 版本，Ruling 记 ledger。）

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_evaluator -v`
Expected: 多数 FAIL / ERROR（契约为 3、没有 `awaiting_acceptance`、`accept` 不支持）；`test_without_role_cards_nothing_changes` PASS。

- [ ] **Step 3: 实现**

`octopus/evaluation.py`：
```python
"""Evaluator gate (D46 P3): a Session that changes code passes on its Evaluator's verdict on each acceptance item — pass,
fail or pending, each with its evidence (the closed set KiroCrew's accept_eval uses) — not on the implementer's own pass.
A pending item waits for a person (「验收待确认」). Applies to Sessions of execution contract 4 on a Worker with role cards
(an Evaluator without its card is not asked for the table)."""
RESULTS = ("pass", "fail", "pending")


def applies(snapshot, spec, config):
    return (snapshot.get("execution_contract", 1) >= 4 and spec.get("role") == "evaluator"
            and bool((config.get("capabilities") or {}).get("role_cards")))


def verdicts(spec, report):
    """(error, pending) of the Evaluator's verdicts against its own acceptance items: matched by their text, or in order
    when it paraphrased them all."""
    items = [str(i).strip() for i in spec.get("acceptance") or []]
    given = [a for a in (report or {}).get("acceptance") or [] if isinstance(a, dict)]
    by_text = {str(a.get("item") or "").strip(): a for a in given}
    matched = [by_text.get(item) for item in items]
    if None in matched and len(given) == len(items):
        matched = given
    missing = [item for item, verdict in zip(items, matched) if verdict is None]
    if missing:
        return "Evaluator 未判定验收项：" + "；".join(missing[:5]), []
    shown = lambda verdict: str(verdict.get("result") or "").strip().lower()
    bad = [item for item, verdict in zip(items, matched) if shown(verdict) not in RESULTS or not str(verdict.get("evidence") or "").strip()]
    if bad:
        return "Evaluator 的判定须为 pass / fail / pending 并附证据：" + "；".join(bad[:5]), []
    failed = [item for item, verdict in zip(items, matched) if shown(verdict) == "fail"]
    if failed:
        return "Evaluator 判定未通过：" + "；".join(failed[:5]), []
    return None, [{"item": item, "evidence": str(verdict["evidence"])[:500]} for item, verdict in zip(items, matched)
                  if shown(verdict) == "pending"]
```
`octopus/engine.py`：在 `_more_gates` 调用块之后（`if lost and not error:` 之前）：
```python
            if not error and gated and evaluation.applies(snapshot, spec, config):   # D46 P3
                error, pending = evaluation.verdicts(spec, report)
                evidence["gates"].append(gates.record("evaluated", "fail" if error else "pass",
                                                      error or (f"{len(pending)} 项待人确认" if pending else None)))
                if error:
                    failure_type, evidence["gate"] = "gate", "evaluated"
                elif pending:
                    evidence["evaluation"] = {"pending": pending}
```
`state = "blocked" if error else "accepted"` 之后：
```python
            if state == "accepted" and (evidence.get("evaluation") or {}).get("pending"):
                state = "awaiting_acceptance"   # D46 P3: what the Evaluator left to a person
```
在写 attempt 状态的事务中，`state == "awaiting_acceptance"` 且是当前 Attempt 时加：
`journal.record(db, rid, "run.awaiting_acceptance", journal.ENGINE, "验收待确认", pending=len(evidence["evaluation"]["pending"]))`。
（`spec`、`snapshot` 在该函数中的变量名以现有代码为准；import `from . import evaluation`。）

`octopus/domain.py`：
- `SETTLED` 加 `"awaiting_acceptance"`。
- start 中 `execution_contract=3 if design is None else 1` → `4 if design is None else 1`。
- `operate`：动作集合加 `"accept"`；`active_team` 检查集合加 `"accept"`；在 `elif action == "confirm":` 之后加：
```python
                elif action == "accept":   # D46 P3: the person's word on what the Evaluator left pending
                    require(run["status"] == "awaiting_acceptance" and run["lifecycle"] == "open",
                            "此 Session 不在等待验收确认", 409, "invalid_state")
                    task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status='awaiting_acceptance' ORDER BY ordinal LIMIT 1",
                                      (rid,)).fetchone()
                    require(task is not None and task["current_attempt"], "没有待确认的验收", 409, "invalid_state")
                    attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
                    evidence = json.loads(attempt["evidence"] or "{}")
                    evidence["evaluation"] = {**(evidence.get("evaluation") or {}), "accepted_at": time.time(),
                                              "accepted_by": {"id": actor["id"], "username": actor.get("username")}}
                    journal.change(db, "attempts", attempt["id"], who, "run.accepted", status="accepted", evidence=dumps(evidence),
                                   updated=time.time())
                    journal.change(db, "tasks", (rid, task["id"]), who, "task.state", status="accepted")
                    restart_budget(db, rid, who)   # the person's word allows the automatic work from here (as confirm)
                    fields.update(status="ready", error=None)
```
- `_attention`：在 `awaiting_confirmation` 分支之后：
```python
        if run["status"] == "awaiting_acceptance":   # D46 P3
            task = next((t for t in run["tasks"] if t["status"] == "awaiting_acceptance"), None)
            pending = ((((task or {}).get("attempts") or [{}])[-1].get("evidence") or {}).get("evaluation") or {}).get("pending") or []
            return {"title": "验收待确认", "detail": "Evaluator 判为需要人判断：" + "；".join(f"{p['item']}（{p['evidence']}）" for p in pending[:5]),
                    "action": "核对后在会话页点「确认通过」，或续作返工；Coding Agent 经用户同意后用 octopus_accept。",
                    "task_id": task["id"] if task else None}
```
`web/src/lib/labels.ts` `EVENT_LABELS` 加：`"run.awaiting_acceptance": "验收待确认", "run.accepted": "已确认验收", "command.accept": "收到验收确认",`；`ADMIN_VERBS` 加 `accept: "确认验收"`。
`tests/test_engine_gates.py:82,280`：断言 3 → 4（Ruling：新会话契约版本按 spec R1 提升）。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_evaluator tests.test_engine_gates tests.test_confirmation tests.test_event_labels tests.test_budget -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add octopus/evaluation.py octopus/engine.py octopus/domain.py web/src/lib/labels.ts tests/test_evaluator.py tests/test_engine_gates.py
git commit -m "feat(evaluation): Evaluator 门禁（D46 P3）——改代码的新会话（契约 4、Worker 支持角色卡片）以 Evaluator 对每条验收项的 pass / fail / pending 与证据为准：缺项或缺证据交给协调者，fail 返工，pending 停在「验收待确认」，经确认（记确认者，不计预算）后继续"
```

---

### Task 2: 平台追加 Evaluator、规划提示与卡片

**Files:**
- Modify: `octopus/evaluation.py`（`needed`、`append`）、`octopus/engine.py`（完成前）、`octopus/domain.py`（`mapping`）、`octopus/planning.py`（规则文字）、`octopus/roles/evaluator/SKILL.md`、`web/src/lib/labels.ts`（`run.evaluator`）
- Test: `tests/test_evaluator.py`（追加）

**Interfaces:**
- Produces: `evaluation.needed(tasks, snapshot, config) -> bool`（`tasks`：按 ordinal 的任务 spec 列表）、`evaluation.append(db, rid, snapshot, config) -> str | None`（新任务 id）、`snapshot["evaluator_added"] = {"task", "tool", "independent"}`、事件 `run.evaluator`、映射来源 `{"source": "platform", "label": "平台追加（改代码的会话须经 Evaluator）"}`。

- [ ] **Step 1: 写失败的测试（追加）**

```python
CODE_ONLY = [TEAM[0]]


class AppendTests(EvaluatorCase):
    def test_a_session_without_an_evaluator_gets_one_on_another_tool(self):
        rid, run = self.run_(CODE_ONLY)
        added = next(t for t in run["tasks"] if t["spec"].get("role") == "evaluator")
        self.assertNotEqual(added["spec"]["tool"], "claude")
        self.assertEqual(added["spec"]["acceptance"], ["登录成功", "错误密码提示"])
        self.assertEqual(added["mapping"]["label"], "平台追加（改代码的会话须经 Evaluator）")
        self.assertEqual(run["status"], "completed")

    def test_a_planned_reviewer_tool_is_preferred(self):
        steps = [TEAM[0], {"id": "look", "name": "审查", "tool": "kiro", "prompt": "审查", "kind": "review", "acceptance": ["无阻断"]}]
        run = self.run_(steps)[1]
        self.assertEqual(next(t for t in run["tasks"] if t["spec"].get("role") == "evaluator")["spec"]["tool"], "kiro")

    def test_with_an_evaluator_none_is_added(self):
        run = self.run_()[1]
        self.assertEqual([t["id"] for t in run["tasks"]], ["code", "check"])

    def test_an_implementation_after_the_evaluator_gets_another(self):
        steps = [*TEAM, {"id": "more", "name": "再实现", "tool": "claude", "prompt": "再实现", "kind": "implement",
                         "acceptance": ["导出可用"], "verify": ["true"]}]
        run = self.run_(steps)[1]
        self.assertEqual(sum(t["spec"].get("role") == "evaluator" for t in run["tasks"]), 2)

    def test_a_session_without_code_changes_gets_none(self):
        steps = [{"id": "plan-doc", "name": "设计", "tool": "kiro", "prompt": "设计", "kind": "design", "acceptance": ["有规格"]}]
        self.assertEqual([t["id"] for t in self.run_(steps)[1]["tasks"]], ["plan-doc"])


class PromptTests(unittest.TestCase):
    def test_the_planner_is_told_to_plan_an_evaluator(self):
        from octopus import planning
        self.assertIn("改代码的计划最后安排一个 evaluator 任务", planning.plan_step()["prompt"])

    def test_the_evaluator_card_answers_the_challenges(self):
        from octopus import roles
        card = roles.card("evaluator")
        self.assertIn("pending", card)
        self.assertIn("Challenge", card)
        self.assertLessEqual(len(card), 600)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_evaluator -v`
Expected: `AppendTests` 多数 FAIL（不追加）；`PromptTests` FAIL。

- [ ] **Step 3: 实现**

`octopus/evaluation.py` 追加：
```python
import json
import time

DEFAULT_ITEM = "本次需求的目标已实现并经验证"
WRITERS = ("implement", "deliver")


def needed(tasks, snapshot, config):
    """A Session of contract 4 on a Worker with role cards that changes code and has no Evaluator after its last change."""
    if snapshot.get("execution_contract", 1) < 4 or not (config.get("capabilities") or {}).get("role_cards"):
        return False
    last = max((i for i, t in enumerate(tasks) if t.get("kind") in WRITERS), default=None)
    return last is not None and not any(t.get("role") == "evaluator" for t in tasks[last + 1:])


def _tool(tasks, config):
    """The Evaluator's tool from the environment's facts: one a review in the plan used that did not write the change, else
    the first available one that did not; with none, the first available one (noted as not independent)."""
    facts = (config.get("capabilities") or {}).get("tools") or {}
    available = [tool for tool, state in facts.items() if isinstance(state, dict) and state.get("available")]
    authors = {t.get("tool") for t in tasks if t.get("kind") in WRITERS}
    reviewers = [t.get("tool") for t in tasks if t.get("kind") == "review"]
    for tool in [*reviewers, *available]:
        if tool in available and tool not in authors:
            return tool, True
    return (available[0] if available else next(iter(authors))), False


def append(db, rid, snapshot, config):
    """Add the Evaluator a code-changing Session lacks (D46 P3 R2); returns its id, or None when none is needed."""
    from . import journal
    from .db import dumps
    rows = [dict(r) for r in db.execute("SELECT id, ordinal, spec FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
    tasks = [json.loads(r["spec"]) for r in rows]
    if not needed(tasks, snapshot, config):
        return None
    ids = {r["id"] for r in rows}
    tid = next(name for name in ["evaluator", *(f"evaluator-{n}" for n in range(2, 100))] if name not in ids)
    items = list(dict.fromkeys(i for t in tasks if t.get("kind") in (*WRITERS, "test") for i in t.get("acceptance") or []))[:20]
    tool, independent = _tool(tasks, config)
    spec = {"id": tid, "name": "Evaluator 验收", "tool": tool, "model": "auto", "effort": "",
            "prompt": "逐条核对本次会话的验收项，给出 pass / fail / pending 与证据；对上游审查、测试与挑战的发现给出成立或不成立与依据。不修改文件。",
            "kind": "review", "role": "evaluator", "acceptance": items or [DEFAULT_ITEM]}
    db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, tid, rows[-1]["ordinal"] + 1, dumps(spec)))
    fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
    fresh["evaluator_added"] = {"task": tid, "tool": tool, "independent": independent}
    journal.change(db, "runs", rid, journal.ENGINE, "run.evaluator", quiet=("snapshot",), snapshot=dumps(fresh), updated=time.time())
    journal.record(db, rid, "run.evaluator", journal.ENGINE, "平台追加 Evaluator：改代码的会话须经 Evaluator 验收"
                   + ("" if independent else "（审查不独立：没有作者以外的可用工具）"), task=tid, tool=tool)
    return tid
```
`octopus/engine.py` 完成前（`if task is None:` 内、`journal.change(... "run.completed" ...)` 之前）：
```python
                if evaluation.append(db, rid, json.loads(fresh["snapshot"]), config):   # D46 P3: verdict before completion
                    return
```
（`config` 为该函数中 Worker 配置的变量名，以现有代码为准。）
`octopus/domain.py` `mapping`：`switched` 分支之后加：
```python
    if task["id"] == (snapshot.get("evaluator_added") or {}).get("task"):   # D46 P3
        return {"source": "platform", "label": "平台追加（改代码的会话须经 Evaluator）"}
```
`octopus/planning.py` 规则文字（审查独立性一行之后）加：
`   改代码的计划最后安排一个 evaluator 任务（与作者不同的工具），其验收项为 summary 的验收标准；没有时平台会追加。`
`octopus/roles/evaluator/SKILL.md` 正文改为：
```
你担任 Evaluator。逐条对照本任务的验收项给出 pass / fail / pending 与证据（命令输出、文件:行）：自己核对，不以实现者的自述为准；需要人判断的（如观感、文案）给 pending 并写明已核对的部分。对上游 Challenge 与安全测试的每条发现写明成立或不成立及依据。任一项 fail 即 changes_requested。不修改文件。
```
`web/src/lib/labels.ts` 加 `"run.evaluator": "平台追加 Evaluator",`。

- [ ] **Step 4: 运行，确认通过；提示长度**

Run: `.venv/bin/python -m unittest tests.test_evaluator tests.test_roles tests.test_prompts_p1 tests.test_event_labels -v`
Expected: 全部 PASS（规划提示 ≤8000；卡片 ≤600）。

- [ ] **Step 5: Commit**

```bash
git add octopus/evaluation.py octopus/engine.py octopus/domain.py octopus/planning.py octopus/roles/evaluator/SKILL.md web/src/lib/labels.ts tests/test_evaluator.py
git commit -m "feat(evaluation): 改代码的会话缺少 Evaluator 时平台在完成前追加（验收项取此前任务、工具按环境事实与审查独立性），映射来源与时间线可见；规划提示要求安排 Evaluator；Evaluator 卡片要求 pending 与对挑战的逐条回应（D46 P3）"
```

---

### Task 3: 控制台、MCP、报告与文档

**Files:**
- Modify: `web/src/api/types.ts`（`RunStatus` 加 `awaiting_acceptance`）、`web/src/lib/labels.ts`（`STATES`、tone）、`web/src/features/sessions/SessionView.tsx`（「验收待确认」提示与「确认通过」）、`web/src/features/sessions/useSessionActions.ts`（`accept`）
- Modify: `octopus/mcp.py`（`octopus_accept`、精简状态的 `pending`）、`octopus/report.py`（「验收（以 Evaluator 为准）」）、`skills/kirocrew-on/SKILL.md`、`docs/OPERATIONS.md`
- Test: `tests/test_evaluator.py`（追加）、`web/src/features/sessions/SessionView.test.tsx`（追加）

- [ ] **Step 1: 写失败的测试**

`tests/test_evaluator.py` 追加：
```python
class SurfaceTests(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.verdicts = [[{"item": "登录成功", "result": "pass", "evidence": "curl 200"},
                          {"item": "错误密码提示", "result": "pending", "evidence": "文案需产品确认"}]]

    def test_the_tools_accept_and_show_what_waits(self):
        from octopus import mcp
        rid, run = self.run_()
        view = mcp.compact_status(run, [])["session"]
        check = next(t for t in view["tasks"] if t["id"] == "check")
        self.assertEqual(check["latest"]["pending"][0]["item"], "错误密码提示")
        seen = []
        class Client:
            def request(self, method, path, body):
                seen.append((method, path))
                return {}
        mcp.call(Client(), "octopus_accept", {"run_id": rid, "request_id": "accept-mcp-1", "expected_version": run["version"]})
        self.assertEqual(seen, [("POST", f"/v1/runs/{rid}/accept")])

    def test_the_report_judges_by_the_evaluator(self):
        from octopus import report
        rid, run = self.run_()
        self.service.operate(self.actor, rid, "accept", {"request_id": "accept-report-1", "expected_version": run["version"]})
        md = report.markdown(self.tick(rid))
        self.assertIn("## 验收（以 Evaluator 为准）", md)
        self.assertIn("错误密码提示 | pending（owner 确认通过）", md)
```
`SessionView.test.tsx` 追加（照该文件已有的 `awaiting_confirmation` 用例的写法）：渲染 `status: "awaiting_acceptance"`、`attention: {title: "验收待确认", …}` 的会话，断言出现「验收待确认」与按钮「确认通过」，点击后 `POST /runs/run-a/accept` 带 `expected_version`。

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m unittest tests.test_evaluator -v`；`npx --prefix web vitest run --root web src/features/sessions/SessionView.test.tsx`
Expected: FAIL（没有 `octopus_accept`、`pending`、报告一节、按钮）。

- [ ] **Step 3: 实现**

- `mcp.py`：工具 `octopus_accept`（描述：「确认一个「验收待确认」（awaiting_acceptance）的 Session：Evaluator 判为需要人判断的验收项。调用前必须把这些项与证据展示给用户并得到明确同意；不同意时用 octopus_continue 返工。」，参数同 `octopus_confirm`，`destructiveHint`）；调用分支与 `octopus_confirm` 相同，路径 `/accept`。`compact_status` 的 `latest()` 加 `**({"pending": evidence["evaluation"]["pending"]} if (evidence.get("evaluation") or {}).get("pending") else {})`。
- `report.py`：`model()` 增加 `"evaluation"`：最后一个 `role == "evaluator"` 任务的最后一次 Attempt 的 `outcome.acceptance` 与 `evaluation.accepted_by`；`markdown()` 在验收相关一节后输出：
```
## 验收（以 Evaluator 为准）

| 验收项 | 判定 |
|---|---|
| 登录成功 | pass |
| 错误密码提示 | pending（owner 确认通过） |
```
  `html()` 同样一节（照该文件的表格写法）。
- 控制台：`RunStatus` 加 `"awaiting_acceptance"`；`STATES.awaiting_acceptance = "验收待确认"`，tone 与 `awaiting_confirmation` 相同；`useSessionActions` 加 `accept`（`POST /runs/<id>/accept`，`expected_version`）；`SessionView` 在 `awaiting_confirmation` 提示旁加同样结构的 `awaiting_acceptance` 提示（id `accept-notice`，按钮 id `run-accept`「确认通过」），并把 `run.status !== "awaiting_acceptance"` 加入下方 `run-attention` 的排除条件。
- `SKILL.md`：会话页 / MCP 一节加：「改代码的会话由 Evaluator 逐项验收；停在 `awaiting_acceptance` 时把待判定项与证据给用户看，同意后 `octopus_accept`，否则 `octopus_continue` 返工。」
- `OPERATIONS.md`：新增「Evaluator 门禁（D46 P3）」：适用条件、判定集合、追加规则、验收待确认与确认、预算、报告。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m unittest tests.test_evaluator tests.test_report tests.test_mvp -v`；`npx --prefix web vitest run --root web`；`npm --prefix web run build`
Expected: 全部 PASS，构建成功。

- [ ] **Step 5: Commit**

```bash
git add octopus/mcp.py octopus/report.py skills/kirocrew-on/SKILL.md docs/OPERATIONS.md tests/test_evaluator.py web/src
git commit -m "feat(console): 「验收待确认」提示与「确认通过」、MCP octopus_accept 与精简状态的待判定项、报告「验收（以 Evaluator 为准）」；Skill 与 OPERATIONS（D46 P3）"
```

---

### Task 4: 回归、终审与 EC2 验收

- [ ] **Step 1:** `PYTHON=<venv python> bash tools/regress.sh` → `regress: OK`。
- [ ] **Step 2:** 终审（opus 子代理），Critical / Important 一次修复（各 RED→GREEN），Minor 记 ledger 与 BACKLOG。
- [ ] **Step 3:** 部署后刷新 Worker 能力，`/tmp/kc_accept_p3.py`：公开测试仓库（只读、不发布）上 (a) 只有实现任务的会话 → 平台追加 Evaluator（作者以外的工具）→ 逐项判定 → 完成；
  (b) 验收项含「README 新增一节的语气符合品牌（需人判断）」→ 停在「验收待确认」→ API 确认 → 完成，报告有确认者。
