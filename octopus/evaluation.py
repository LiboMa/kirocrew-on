"""Evaluator gate (D46 P3): a Session that changes code passes on its Evaluator's verdict on each acceptance item — pass,
fail or pending, each with its evidence (the closed set KiroCrew's accept_eval uses) — not on the implementer's own pass.
A pending item waits for a person (「验收待确认」). Applies to Sessions of execution contract 4 on a Worker with role cards
(an Evaluator without its card is not asked for the table)."""
import json
import time

RESULTS = ("pass", "fail", "pending")


def applies(snapshot, spec, config):
    return (snapshot.get("execution_contract", 1) >= 4 and spec.get("role") == "evaluator"
            and bool((config.get("capabilities") or {}).get("role_cards")))


def verdicts(spec, report):
    """(error, pending) of the Evaluator's verdicts against its own acceptance items: each item takes the verdict naming it,
    and items it paraphrased take the remaining verdicts in order — so a verdict is never moved off the item it named. Any
    failure it reports, on an item of its own or not, fails."""
    items = [str(i).strip() for i in spec.get("acceptance") or []]
    given = [a for a in (report or {}).get("acceptance") or [] if isinstance(a, dict)]
    result = lambda verdict: str(verdict.get("result") or "").strip().lower()
    failures = [str(a.get("item") or "").strip() for a in given if result(a) == "fail"]
    if failures:
        return "Evaluator 判定未通过：" + "；".join(failures[:5]), []
    remaining, matched = list(given), []
    for item in items:
        hit = next((a for a in remaining if str(a.get("item") or "").strip() == item), None)
        matched.append(hit)
        if hit is not None:
            remaining.remove(hit)
    rest = iter(remaining)   # paraphrased items, in the order the Evaluator gave them
    matched = [hit if hit is not None else next(rest, None) for hit in matched]
    missing = [item for item, verdict in zip(items, matched) if verdict is None]
    if missing:
        return "Evaluator 未判定验收项：" + "；".join(missing[:5]), []
    bad = [item for item, verdict in zip(items, matched)
           if result(verdict) not in RESULTS or not str(verdict.get("evidence") or "").strip()]
    if bad:
        return "Evaluator 的判定须为 pass / fail / pending 并附证据：" + "；".join(bad[:5]), []
    return None, [{"item": item, "evidence": str(verdict["evidence"])[:500],
                   **({"stated": str(verdict.get("item"))[:300]} if str(verdict.get("item") or "").strip() != item else {})}
                  for item, verdict in zip(items, matched) if result(verdict) == "pending"]


DEFAULT_ITEM = "本次需求的目标已实现并经验证"
WRITERS = ("implement", "deliver")


def _items(tasks):
    """What an Evaluator judges: the acceptance items of the tasks that changed code or tested it, in order, once each."""
    found = list(dict.fromkeys(i for t in tasks if t.get("kind") in (*WRITERS, "test") for i in t.get("acceptance") or []))
    return found[:20] or [DEFAULT_ITEM]


def refresh(db, rid, task, snapshot, config):
    """Before it runs, an Evaluator judges the Session's items as they are now: the one the platform added (a continue may
    have changed them), and one that was given none. Returns whether its spec changed."""
    from . import journal
    from .db import dumps
    spec = json.loads(task["spec"])
    if not applies(snapshot, spec, config):
        return False
    if task["id"] != (snapshot.get("evaluator_added") or {}).get("task") and spec.get("acceptance"):
        return False
    before = [json.loads(r["spec"]) for r in db.execute("SELECT spec FROM tasks WHERE run_id=? AND ordinal<? ORDER BY ordinal",
                                                         (rid, task["ordinal"]))]
    items = _items(before)
    if items == spec.get("acceptance"):
        return False
    journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "task.spec", "Evaluator 按当前验收项判定",
                   spec=dumps({**spec, "acceptance": items}))
    return True


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
    return (available[0] if available else next(t.get("tool") for t in tasks if t.get("kind") in WRITERS)), False


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
    items = _items(tasks)
    tool, independent = _tool(tasks, config)
    spec = {"id": tid, "name": "Evaluator 验收", "tool": tool, "model": "auto", "effort": "",
            "prompt": "逐条核对本次会话的验收项，给出 pass / fail / pending 与证据；对上游审查、测试与挑战的发现给出成立或不成立与依据。"
                      "不修改文件。",
            "kind": "review", "role": "evaluator", "acceptance": items}
    db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, tid, rows[-1]["ordinal"] + 1, dumps(spec)))
    fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
    fresh["evaluator_added"] = {"task": tid, "tool": tool, "independent": independent}
    journal.change(db, "runs", rid, journal.ENGINE, "run.evaluator", quiet=("snapshot",), snapshot=dumps(fresh), updated=time.time())
    journal.record(db, rid, "run.evaluator", journal.ENGINE, "平台追加 Evaluator：改代码的会话须经 Evaluator 验收"
                   + ("" if independent else "（审查不独立：没有作者以外的可用工具）"), task=tid, tool=tool)
    return tid
