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
REPLAN_PREFIX = "replan-"
EXAMPLE = {"intent": "一句话：用户真正要完成什么、属于哪类工作（新功能 / 缺陷修复 / 审查 / 原型 / 其他）",
           "summary": "补全后的需求（Markdown）：目标、范围与不做什么、可验证的验收标准、关键假设与待确认问题",
           "name": "针对本次需求的简短名称",
           "steps": [{"id": "task-1", "name": "任务名称", "tool": "claude", "model": "auto", "effort": "",
                      "prompt": "本任务的具体目标、输入、实施步骤、交付物与验证方法",
                      "kind": "implement", "deliverables": ["本任务交付的文件或产物"],
                      "acceptance": ["可检查的验收项（来自 summary 的验收标准）"], "verify": ["实际执行的验证命令"],
                      "depends_on": [], "paths": ["本任务可改动的路径或 glob"]}]}


def plan_step(models=None, tools=None, limitations=None):
    """The first task of an Agent-planned Session (always Kiro; it plans and does not change business code)."""
    prompt = intent_prompt(dumps(EXAMPLE), "（见上方完整用户需求）", "本任务的工作目录（即本 Session 的源码检出）") + f"""

## 本任务的交付
1. 意图识别：intent 一句话说明用户真正要完成什么、属于哪类工作。
2. 需求补全：summary 写成 Markdown——目标、范围（含不做什么）、可验证的验收标准、关键假设与待确认问题；
   只补全用户没有说清但完成任务必需的内容，不改变用户的明确要求。
3. 任务拆分与 Agent 映射：steps 为 1–7 个顺序任务，每个任务选择最合适的工具并写出具体 prompt；
   后续任务将在同一个 Session、同一份源码检出上依次执行。
4. 任务契约：每个任务必须给出 kind（implement / review / test / deliver / design）、deliverables、
   至少 1 条可检查的 acceptance（审查与测试任务的验收项取自 summary 的验收标准，不另加要求）；
   implement 与 test 任务必须给出至少 1 条 verify（实际可执行的验证命令）；depends_on 只列前序任务的 id；
   paths 写本任务可改动的路径，便于续作时判断哪些任务受影响。
   平台的门禁：test 任务新增的测试须在会话基线（实现之前的源码）上失败；后续任务若要修改此前任务交付的文件，
   须在自己的 paths 中列出这些文件。test 任务只写测试、不改实现，其 verify 不安装依赖（不写 npm install / pip install 等）；
   检查与测试生成的文件（覆盖率、报告、构建产物）应写入 .gitignore。
5. 可运行的产出：交付物是可以运行的应用时，最后的交付任务须在项目根目录写 .octopus/preview.json
   （processes：命令、cwd、端口或 "$PORT"、哪个是入口；install：依赖安装；前后端分开时各写一个进程），
   并把「.octopus/preview.json 能按声明启动应用」列为该任务的验收项；用户据此下载到本机或远程预览。
本任务只做分析与规划，不修改业务代码。

## 此执行环境的 Coding 工具
{dumps(tools or {})}
available 为 false 的工具不要选择（reason 为原因）；没有列出时表示执行环境未报告。
执行环境的已知限制：{dumps(limitations or [])}
按任务性质与这些事实选择工具，并在交接文件中说明选择依据。

## 此执行环境公布的模型
{dumps(models or {})}
不在上面列表中的具体模型一律写 model=auto。

最终回复必须包含且仅包含一个 JSON 代码块（字段：intent、summary、name、steps）；不要加入第二个 JSON/YAML 代码块。
同时按本任务要求写交接文件，说明规划依据与待用户核实的条件。
"""
    return {"id": PLAN_ID, "name": "需求分析与任务规划（KiroCrew）", "tool": "kiro", "model": "auto", "effort": "",
            "prompt": prompt}


def expand(db, rid, text, models=None, replan=False):
    """Validate the plan in ``text`` and append its tasks after the planning task; raises ``Error`` with the reason.
    ``replan``: the Coordinator's replan (D41): at most 6 tasks, recorded under the snapshot's ``replan``."""
    require(isinstance(text, str) and text.strip(), "规划任务没有返回内容")
    plan = import_workflow(text.strip())
    notes = list(plan["notes"])
    # ids of tasks that are, or were (replaced by a replan, their Attempts kept as history), in this Session
    taken = {row["id"] for row in db.execute("SELECT id FROM tasks WHERE run_id=?", (rid,))} | \
        {row[0] for row in db.execute("SELECT DISTINCT task_id FROM attempts WHERE run_id=?", (rid,))}
    limit = 6 if replan else 7
    published = models or {}
    steps = []
    for step in plan["steps"][:limit]:
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
    for step in steps:   # every planned task carries its contract (D34 §6.1)
        missing = [label for label, ok in (("类别（kind）", "kind" in step), ("验收项（acceptance）", step.get("acceptance")),
                                           ("验证命令（verify）", step.get("kind") not in {"implement", "test"} or step.get("verify")))
                   if not ok]
        require(not missing, f"任务「{step['name']}」缺少{'、'.join(missing)}", 409, "plan_invalid")
    if len(plan["steps"]) > limit:
        notes.append(f"规划给出 {len(plan['steps'])} 个任务，只保留前 {limit} 个")
    start = db.execute("SELECT COALESCE(MAX(ordinal), 0) FROM tasks WHERE run_id=?", (rid,)).fetchone()[0] + 1
    for offset, step in enumerate(steps):
        db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, step["id"], start + offset, dumps(step)))
    row = db.execute("SELECT snapshot, version, owner_id FROM runs WHERE id=?", (rid,)).fetchone()
    snapshot = json.loads(row["snapshot"])
    if replan:
        snapshot["replan"] = {**(snapshot.get("replan") or {}), "applied": True, "tasks": [s["id"] for s in steps], "notes": notes,
                              "intent": str(plan.get("intent") or "")[:500]}
        snapshot["steps"] = snapshot.get("steps", []) + steps
        from . import journal
        journal.change(db, "runs", rid, journal.KIROCREW, "plan.applied",
                       event={"tasks": [s["id"] for s in steps], "replan": True, "notes": notes},
                       snapshot=dumps(snapshot), version=row["version"] + 1, updated=time.time())
        from .revisions import record
        record(db, rid, row["version"] + 1, "planned", f"重新规划：追加 {len(steps)} 个任务", row["owner_id"], time.time(),
               planning={"replan": True, "tasks": [s["id"] for s in steps]})
        return snapshot["replan"]
    applied = {"mode": "agent", "applied": True, "intent": str(plan.get("intent") or "")[:500],
               "summary": str(plan.get("summary") or "")[:8000], "name": plan["name"], "tasks": [s["id"] for s in steps],
               "notes": notes}
    snapshot["planning"] = applied
    snapshot["steps"] = snapshot.get("steps", []) + steps
    from . import journal
    journal.change(db, "runs", rid, journal.KIROCREW, "plan.applied",
                   event={"tasks": applied["tasks"], "intent": applied["intent"], "notes": notes},
                   snapshot=dumps(snapshot), version=row["version"] + 1, updated=time.time())
    from .revisions import record
    record(db, rid, row["version"] + 1, "planned", f"规划：追加 {len(steps)} 个任务", row["owner_id"], time.time(),
           planning={"intent": applied["intent"], "tasks": applied["tasks"]})
    return applied


def pending(snapshot, task_id=PLAN_ID):
    """Whether ``task_id``'s plan is still to be applied: the first plan, or the Coordinator's replan (D41)."""
    if task_id != PLAN_ID:
        replan = snapshot.get("replan") or {}
        return replan.get("task") == task_id and not replan.get("applied")
    planning = snapshot.get("planning") or {}
    return planning.get("mode") == "agent" and not planning.get("applied")


def planning_task(task_id):
    """The KiroCrew planning tasks: the first plan and a replan (D41). They change no business code: no candidate, no gates."""
    return task_id == PLAN_ID or str(task_id).startswith(REPLAN_PREFIX)


def replan_step(requirement, planned, accepted, replaced, failure, decision, models=None, tools=None, limitations=None):
    """The Coordinator's replan (D41 §3.2): a Kiro task that plans only the remaining work, knowing what is done, what the
    old plan was and why it failed. Its reply is validated and appended like the first plan."""
    prompt = f"""你是此 Session 的 KiroCrew 规划者。协调者判断原计划有问题，请重新规划剩余的工作。

## 用户需求
{requirement}

## 已补全的需求（首次规划）
意图：{planned.get("intent") or "—"}
{planned.get("summary") or ""}

## 已完成并验收的任务（保留，不要重复）
{dumps(accepted)}

## 被替换的任务（原计划的剩余部分，不再执行）
{dumps(replaced)}

## 失败与协调者的判断
失败：{failure}
协调者的原因：{decision.get("reason")}
协调者的要求：{decision.get("instructions")}

## 本任务的交付
只规划剩余的工作，不重复已完成的任务：steps 为 1–6 个顺序任务，后续任务在同一个 Session、同一份源码检出上依次执行。
每个任务必须给出 kind（implement / review / test / deliver / design）、deliverables、至少 1 条可检查的 acceptance；
implement 与 test 任务必须给出至少 1 条 verify（实际可执行的验证命令）；depends_on 只列本计划中前面的任务；paths 写本任务可改动的路径。
平台的门禁：test 任务新增的测试须在会话基线上失败；后续任务若要修改此前任务交付的文件，须在自己的 paths 中列出这些文件。
本任务只做分析与规划，不修改业务代码。

## 此执行环境的 Coding 工具
{dumps(tools or {})}
available 为 false 的工具不要选择。执行环境的已知限制：{dumps(limitations or [])}

## 此执行环境公布的模型
{dumps(models or {})}
不在上面列表中的具体模型一律写 model=auto。

最终回复必须包含且仅包含一个 JSON 代码块（字段：intent、summary、name、steps）；不要加入第二个 JSON/YAML 代码块。
同时按本任务要求写交接文件，说明重新规划的依据。
"""
    return {"id": REPLAN_PREFIX + "1", "name": "重新规划剩余工作（KiroCrew）", "tool": "kiro", "model": "auto", "effort": "",
            "prompt": prompt}


__all__ = ["PLAN_ID", "REPLAN_PREFIX", "plan_step", "replan_step", "planning_task", "expand", "pending", "Error"]
