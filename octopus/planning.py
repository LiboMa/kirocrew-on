"""KiroCrew planning inside a Session: a real Kiro Agent recognises the intent, enriches the requirement and maps it to
tasks; code then validates that plan and appends the tasks to the same Session.

The Agent only proposes. Parsing, the 1–8 task limit, tools, ids and model availability are decided here.
"""
import json
import time

from .db import dumps
from .prompts import intent_prompt
from .security import Error, require
from .workflow_import import import_workflow

PLAN_ID = "plan"
EXAMPLE = {"intent": "一句话：用户真正要完成什么、属于哪类工作（新功能 / 缺陷修复 / 审查 / 原型 / 其他）",
           "summary": "补全后的需求（Markdown）：目标、范围与不做什么、可验证的验收标准、关键假设与待确认问题",
           "name": "针对本次需求的简短名称",
           "steps": [{"id": "task-1", "name": "任务名称", "tool": "claude", "model": "auto", "effort": "",
                      "prompt": "本任务的具体目标、输入、实施步骤、交付物与验证方法"}]}


def plan_step(models=None):
    """The first task of an Agent-planned Session (always Kiro; it plans and does not change business code)."""
    prompt = intent_prompt(dumps(EXAMPLE), "（见上方完整用户需求）", "本任务的工作目录（即本 Session 的源码检出）") + f"""

## 本任务的交付
1. 意图识别：intent 一句话说明用户真正要完成什么、属于哪类工作。
2. 需求补全：summary 写成 Markdown——目标、范围（含不做什么）、可验证的验收标准、关键假设与待确认问题；
   只补全用户没有说清但完成任务必需的内容，不改变用户的明确要求。
3. 任务拆分与 Agent 映射：steps 为 1–7 个顺序任务，每个任务选择最合适的工具并写出具体 prompt；
   后续任务将在同一个 Session、同一份源码检出上依次执行。
本任务只做分析与规划，不修改业务代码。

## 此执行环境公布的模型
{dumps(models or {})}
不在上面列表中的具体模型一律写 model=auto。

最终回复必须包含且仅包含一个 JSON 代码块（字段：intent、summary、name、steps）；不要加入第二个 JSON/YAML 代码块。
同时按本任务要求写交接文件，说明规划依据与待用户核实的条件。
"""
    return {"id": PLAN_ID, "name": "需求分析与任务规划（KiroCrew）", "tool": "kiro", "model": "auto", "effort": "",
            "prompt": prompt}


def expand(db, rid, text, models=None):
    """Validate the plan in ``text`` and append its tasks after the planning task; raises ``Error`` with the reason."""
    require(isinstance(text, str) and text.strip(), "规划任务没有返回内容")
    plan = import_workflow(text.strip())
    notes = list(plan["notes"])
    taken = {row["id"] for row in db.execute("SELECT id FROM tasks WHERE run_id=?", (rid,))}
    published = models or {}
    steps = []
    for step in plan["steps"][:7]:
        step = dict(step)
        base, number = step["id"], 2
        while step["id"] in taken:
            step["id"], number = f"{base}-{number}", number + 1
        if step["id"] != base:
            notes.append(f"任务 ID {base} 与已有任务重复，改为 {step['id']}")
        taken.add(step["id"])
        if step["model"] not in {"auto", "default"} and step["model"] not in (published.get(step["tool"]) or []):
            notes.append(f"{step['name']}：模型 {step['model']} 不在执行环境公布的列表中，改为 auto")
            step["model"] = "auto"
        steps.append(step)
    if len(plan["steps"]) > 7:
        notes.append(f"规划给出 {len(plan['steps'])} 个任务，只保留前 7 个")
    start = db.execute("SELECT COALESCE(MAX(ordinal), 0) FROM tasks WHERE run_id=?", (rid,)).fetchone()[0] + 1
    for offset, step in enumerate(steps):
        db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, step["id"], start + offset, dumps(step)))
    row = db.execute("SELECT snapshot, version FROM runs WHERE id=?", (rid,)).fetchone()
    snapshot = json.loads(row["snapshot"])
    applied = {"mode": "agent", "applied": True, "intent": str(plan.get("intent") or "")[:500],
               "summary": str(plan.get("summary") or "")[:8000], "name": plan["name"], "tasks": [s["id"] for s in steps],
               "notes": notes}
    snapshot["planning"] = applied
    snapshot["steps"] = snapshot.get("steps", []) + steps
    db.execute("UPDATE runs SET snapshot=?,version=?,updated=? WHERE id=?", (dumps(snapshot), row["version"] + 1, time.time(), rid))
    return applied


def pending(snapshot):
    planning = snapshot.get("planning") or {}
    return planning.get("mode") == "agent" and not planning.get("applied")


__all__ = ["PLAN_ID", "plan_step", "expand", "pending", "Error"]
