"""Session revisions (D32): a full snapshot of the requirement and every task at each change, and the line-level
differences between any two revisions, for the console and for Coding Agents (MCP)."""
import difflib
import json
import time

from .db import dumps
from .domain import CONTRACT   # one list of contract fields: a new one is recorded without being added here

FIELDS = ("name", "tool", "model", "effort")
LABELS = {"name": "名称", "tool": "工具", "model": "模型", "effort": "Effort", "kind": "类别", "role": "角色", "deliverables": "交付物",
          "acceptance": "验收项", "verify": "验证命令", "depends_on": "依赖", "paths": "可改动路径", "skills": "方法"}


def _task(spec):
    return {"id": spec.get("id"), **{key: spec.get(key) or "" for key in FIELDS}, "prompt": spec.get("prompt") or "",
            **{key: spec[key] for key in CONTRACT if key in spec}}


def snapshot(db, rid):
    """The requirement and every task of a run as they are now."""
    run = db.execute("SELECT input FROM runs WHERE id=?", (rid,)).fetchone()
    tasks = [json.loads(row["spec"]) for row in db.execute("SELECT spec FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
    return {"input": run["input"], "tasks": [_task(spec) for spec in tasks]}


def record(db, rid, version, kind, summary, actor, at, **legacy):
    """Write a revision with its snapshot; ``legacy`` keeps the keys older readers expect (input, steps, task …)."""
    db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
               (rid, version, dumps({**legacy, "kind": kind, "summary": summary, "snapshot": snapshot(db, rid)}), actor, at))


def appended(db, rid, task, summary):
    """The version a task the platform appended takes (the Evaluator, D46 P3; Knowledge, P4), written as a revision of its
    own so that the next continue's differences are the user's alone; the caller sets the run's version to it."""
    row = db.execute("SELECT version, owner_id FROM runs WHERE id=?", (rid,)).fetchone()
    record(db, rid, row["version"] + 1, "platform", summary, row["owner_id"], time.time(), platform={"task": task})
    return row["version"] + 1


def changes(before, after):
    """What a continue changed in one task, as「工具 claude → codex、提示词」."""
    parts = [f"{LABELS[key]} {before.get(key) or '默认'} → {after.get(key) or '默认'}" for key in FIELDS
             if key != "name" and (before.get(key) or "") != (after.get(key) or "")]
    if (before.get("prompt") or "") != (after.get("prompt") or ""):
        parts.append("提示词")
    parts += [LABELS[key] for key in CONTRACT if before.get(key) != after.get(key)]
    return "、".join(parts)


def _legacy(content):
    if "steps" in content:
        return "created", f"创建：{len(content['steps'])} 个任务"
    if "task" in content:
        return "continued", f"续作「{content['task'].get('name', content['task'].get('id'))}」"
    if "decision" in content:
        reason = (content["decision"] or {}).get("reason")
        return "recovered", "自动纠错" + (f"：{reason}" if reason else "")
    return "changed", "修订"


def history(db, rid):
    """Every revision, oldest first, each with its snapshot. Revisions written before snapshots existed are rebuilt
    from what they recorded (reconstructed=True); tasks a plan appended back then may be missing from them."""
    users = dict(db.execute("SELECT id,username FROM users").fetchall())
    state, result = None, []
    for row in db.execute("SELECT * FROM revisions WHERE run_id=? ORDER BY version", (rid,)):
        content = json.loads(row["content"])
        reconstructed = "snapshot" not in content
        if not reconstructed:
            state = content["snapshot"]
        elif "steps" in content:
            state = {"input": content.get("input", ""), "tasks": [_task(step) for step in content["steps"]]}
        elif "task" in content and state is not None:
            task = _task(content["task"])
            tasks = [task if t["id"] == task["id"] else t for t in state["tasks"]]
            if task["id"] not in {t["id"] for t in state["tasks"]}:
                tasks.append(task)
            state = {"input": content.get("input", state["input"]), "tasks": tasks}
        kind, summary = (content["kind"], content["summary"]) if "kind" in content else _legacy(content)
        result.append({"version": row["version"], "kind": kind, "summary": summary, "created": row["created"],
                       "actor": {"id": row["actor"], "username": users.get(row["actor"])}, "reconstructed": reconstructed,
                       "snapshot": json.loads(dumps(state)) if state is not None else None,
                       "content": {key: value for key, value in content.items() if key != "snapshot"}})
    return result


def lines(before, after):
    """Line operations turning ``before`` into ``after``: " " kept, "-" removed, "+" added."""
    a, b = (before or "").splitlines(), (after or "").splitlines()
    result = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            result += [{"op": " ", "text": text} for text in a[i1:i2]]
        else:
            result += [{"op": "-", "text": text} for text in a[i1:i2]] + [{"op": "+", "text": text} for text in b[j1:j2]]
    return result


def diff(old, new):
    """The requirement and every task between two snapshots: status added / removed / changed / same, changed fields
    as [before, after], and the prompt's line operations."""
    before = {task["id"]: task for task in old["tasks"]}
    after = {task["id"]: task for task in new["tasks"]}
    order = [task["id"] for task in new["tasks"]] + [tid for tid in before if tid not in after]
    tasks = []
    for tid in order:
        a, b = before.get(tid), after.get(tid)
        status = "added" if a is None else "removed" if b is None else "changed" if a != b else "same"
        fields = {key: [(a or {}).get(key), (b or {}).get(key)] for key in FIELDS + CONTRACT
                  if (a or {}).get(key) != (b or {}).get(key)}
        if status in {"added", "removed"}:
            fields = {}
        tasks.append({"id": tid, "name": (b or a)["name"], "status": status, "fields": fields,
                      "prompt": lines((a or {}).get("prompt"), (b or {}).get("prompt"))})
    return {"input": lines(old["input"], new["input"]), "tasks": tasks}
