"""Shared use cases for HTTP, Web, CLI and MCP. Authorization lives here."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
import unicodedata

from . import journal, policies, quotas, remote_preview, requests as submissions
from .db import dumps, uid
from .security import Error, check_password, console_login, elevated, expiry, password_hash, require

TOOLS = ("kiro", "claude", "codex", "opencode")
ROLES = {"viewer": 1, "runner": 2, "editor": 3, "owner": 4}
ROLE_LABELS = {"viewer": "查看", "runner": "运行", "editor": "编辑", "owner": "所有者"}
# Statuses in which an open Session is not running on its Worker (it can be continued).
SETTLED = ("failed", "blocked", "completed", "stopped", "waiting_worker", "awaiting_confirmation", "awaiting_acceptance")
RUN_STATES = SETTLED + ("preparing", "ready", "running", "waiting_approval", "dispatch_unknown", "stopping", "reopening", "diagnosing")


LISTED_INPUT = 200   # characters of the requirement a list row keeps: the title of a Session without a name


def listed(view):
    """A Session as the console's list and the Coding Agents' list need it (M3-5): its tasks by name and state, the plan
    without its prose — the tasks' prompts alone were two thirds of the list on EC2 (101 Sessions, ~550 000 characters) —
    and the requirement's beginning only (2026-10-24: 28 % of 50 rows, 223 KB; the session page reads it whole)."""
    keep = ("id", "name", "tool", "model", "kind", "role")
    tasks = [{"id": t["id"], "status": t["status"], "spec": {k: t["spec"][k] for k in keep if k in t["spec"]}}
             for t in view.get("tasks") or []]
    prose = {"summary", "notes", "replaced", "instructions", "reason", "intent"}
    plans = {key: {k: v for k, v in view[key].items() if k not in prose}
             for key in ("planning", "replan") if isinstance(view.get(key), dict)}
    text = view.get("input") or ""
    shown = text if len(text) <= LISTED_INPUT else text[:LISTED_INPUT].rstrip() + "…"
    return {**view, "input": shown, "tasks": tasks, **plans}
# What the admin environment listing shows of a live /health (no boundary or principal hashes).
HEALTH_FIELDS = ("isolation", "crew_entry", "crew_origin", "version", "tool_auth")


def text(value, field, maximum=12000, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum
            and not any(ord(c) < 32 and c not in "\n\r\t" for c in value),
            f"{field} 须为 {minimum}–{maximum} 个字符")
    return value.strip()


def name(value):
    value = " ".join(unicodedata.normalize("NFKC", text(value, "名称", 80)).split())
    require(not any(unicodedata.category(c) in {"Cc", "Cf"} for c in value), "名称包含不可见字符")
    return value


# Optional task structure (D34 §6.1): what a task delivers and how it is checked, kept in its spec only when given.
# verify_required (D57 §5): those of the checks that must pass, named by their text; the others only report.
CONTRACT = ("kind", "role", "phase", "deliverables", "acceptance", "verify", "verify_required", "depends_on", "paths", "skills")
MAX_TASKS = 12   # D66 (owner 2026-10-22): a workflow or a Session; was 8 — a 9-task recommendation was refused
KINDS = ("implement", "review", "test", "deliver", "design", "research", "write")
# D65: general work, not bound to Git — research (调研) and write (撰写), judged like a delivery; never an Evaluator's or
# Knowledge's (they follow code changes and a repository).
GENERAL_KINDS = ("research", "write")
NO_REPOSITORY = "此会话没有代码仓库：产出在会话页「下载产出」取得"


def general(source):
    """A Session without a code repository (D65): its files are only its working directory's."""
    return (source or {}).get("type") == "none"


def worker_source(source):
    """What a Worker prepares: a Session without a repository is an empty project to it (an older Worker knows no none)."""
    return {"type": "new"} if general(source) else source
LIMITS = {"deliverables": (20, 500), "acceptance": (20, 500), "verify": (10, 500), "verify_required": (10, 500),
          "paths": (20, 500), "depends_on": (8, 48)}


# Kinds whose checks the platform never runs (a design changes no code; a review is bound to the candidate instead): the
# Engine starts a task's checks only when runs_checks says so, and the Agent is told which (worker.briefing).
UNCHECKED = ("review", "design")


def runs_checks(spec):
    """Whether the platform runs ``spec``'s verify commands when the task ends (D37 §5): never a diverge task's (D69)."""
    from .phases import own
    return spec.get("kind") not in UNCHECKED and own(spec) != "diverge"


def strings(value, field, items, chars):
    require(isinstance(value, list) and len(value) <= items and all(isinstance(x, str) for x in value),
            f"{field} 须为不超过 {items} 项的字符串列表")
    result = list(dict.fromkeys(x.strip() for x in value if x.strip()))
    require(all(len(x) <= chars for x in result), f"{field} 每项不超过 {chars} 个字符")
    return result


def requested(value, tool):
    """A task's tool / model as the requirement names it (D45): the planner marks it, the platform runs it as asked — so it
    names the task's own tool."""
    ok = isinstance(value, dict) and value and set(value) <= {"tool", "model", "quote"} and ("tool" in value or "model" in value) \
        and value.get("tool", tool) == tool and all(isinstance(value.get(k, "x"), str) and value.get(k, "x").strip()
                                                    and len(value.get(k, "x")) <= 200 for k in ("model", "quote"))
    require(ok, "requested 须为 {tool, model, quote}，至少指定 tool 或 model，且 tool 与任务的工具相同")
    return {k: value[k].strip() for k in ("tool", "model", "quote") if k in value}


def basis(value, required):
    """The requirement's words a required check stands on (D57 §13.2), ``{command: quote}`` as the planner quoted them;
    kept for the checks still required."""
    require(isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) and v.strip() and len(v) <= 200
                                            for k, v in value.items()), "verify_basis 须为 {检查命令: 需求原文片段}")
    return {k.strip(): v.strip() for k, v in value.items() if k.strip() in required}


def required_checks(run, spec):
    """The checks of ``spec`` that must pass (D57 §5): its ``verify_required``; without one (a task from before D57) none of
    a planner's — an agent-planned Session's or a replan's task — and all of a person's (steps given, a saved workflow).
    ``run`` is a Session's row (its snapshot as JSON) or its view."""
    if "verify_required" in spec:
        return list(spec["verify_required"] or [])
    run = dict(run)
    snapshot = run.get("snapshot")
    snapshot = json.loads(snapshot or "{}") if isinstance(snapshot, str) else snapshot if isinstance(snapshot, dict) else run
    from .planning import replans
    planner = spec.get("origin") == "planner" or snapshot.get("match_mode") == "agent" or \
        any(spec.get("id") in (r.get("tasks") or []) for r in replans(snapshot))
    return [] if planner else list(spec.get("verify") or [])


def steps(value, known=(), planned=False):
    """Validated tasks; ``known`` are the ids of tasks before ``value`` (a continued task depends on them). ``planned``: the
    KiroCrew planner's tasks (or a continue of one), the only ones that may carry ``requested`` (D45), ``origin`` and
    ``verify_basis`` (D57) — anywhere else they are dropped, so a saved workflow or a given plan never claims an old
    requirement's choice. A person's checks (not ``planned``) must all pass unless ``verify_required`` names some (D57 §5.3)."""
    require(isinstance(value, list) and 1 <= len(value) <= MAX_TASKS, f"流程须包含 1–{MAX_TASKS} 个顺序任务")
    seen, earlier, result = set(), set(known), []
    for step in value:
        require(isinstance(step, dict) and set(step) <= {"id", "name", "prompt", "tool", "model", "effort", "requested",
                                                         "origin", "verify_basis", *CONTRACT},
                "不支持的 Task 字段")
        sid = step.get("id")
        require(isinstance(sid, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", sid)
                and sid not in seen, "Task ID 无效或重复")
        seen.add(sid)
        tool = step.get("tool")
        require(tool in TOOLS, "不支持的 Coding 工具")
        effort = step.get("effort", "")
        require(effort in ("", "low", "medium", "high", "xhigh", "max"), "Effort 无效")
        require(tool != "opencode" or not effort, "当前 OpenCode 接入未验证 Effort 覆盖")
        item = {"id": sid, "name": name(step.get("name")), "tool": tool, "model": text(step.get("model", "auto"), "Model", 200),
                "effort": effort, "prompt": text(step.get("prompt"), "任务说明", 8000)}
        if "kind" in step:
            require(step["kind"] in KINDS, "任务类别只能是 implement、review、test、deliver、design、research（调研）或 write（撰写）")
            item["kind"] = step["kind"]
        if "phase" in step:   # D69: diverge / design / converge decides which failures stop the Session
            from .phases import PHASES
            require(step["phase"] in PHASES, "阶段（phase）只能是 diverge（发散）、design（设计）或 converge（收敛）")
            item["phase"] = step["phase"]
        if "role" in step:   # D46 P2: how the Agent works; kind keeps deciding the gates
            from . import roles
            require(step["role"] in roles.KINDS, "角色只能是 " + "、".join(roles.KINDS))
            allowed = roles.KINDS[step["role"]]
            if "kind" in item:
                require(item["kind"] in allowed, f"角色 {step['role']} 的任务类别须为 {' 或 '.join(allowed)}")
            else:
                item["kind"] = allowed[0]
            item["role"] = step["role"]
        for field, (items, chars) in LIMITS.items():
            if field in step:
                item[field] = strings(step[field], field, items, chars)
        if "verify_required" in item:   # D57 §5: by their text, wherever they stand among the checks
            require(set(item["verify_required"]) <= set(item.get("verify", [])), "verify_required 的每条检查须在 verify 中")
        elif not planned and item.get("verify"):   # a person's own checks must pass (D57 §5.3)
            item["verify_required"] = list(item["verify"])
        if "origin" in step and planned:   # D57: the planner wrote this task (and its checks)
            require(step["origin"] == "planner", "origin 只能是 planner")
            item["origin"] = "planner"
        if "verify_basis" in step and planned:
            kept = basis(step["verify_basis"], item.get("verify_required", []))
            if kept:
                item["verify_basis"] = kept
        if "skills" in step:   # D50: methods from the platform's skill pack the task's Agent works by
            from . import skills as pack
            chosen = step["skills"]
            require(isinstance(chosen, list) and all(isinstance(x, str) for x in chosen), "skills 须为技能名列表")
            unknown = [x for x in chosen if x not in pack.CATALOG]
            require(not unknown, "未知的技能：" + "、".join(unknown[:4]) + "；可选：" + "、".join(pack.CATALOG))
            chosen = list(dict.fromkeys(chosen))
            require(len(chosen) <= pack.LIMIT, f"每个任务至多 {pack.LIMIT} 个技能")
            if chosen:
                item["skills"] = chosen
        if "requested" in step and planned:   # the tool / model the requirement names for this task (D45)
            item["requested"] = requested(step["requested"], tool)
        # Only earlier tasks: dependencies follow the order the Engine runs them in.
        require(all(dep in (seen | earlier) - {sid} for dep in item.get("depends_on", [])),
                f"任务「{item['name']}」的 depends_on 只能指向前序任务")
        result.append(item)
    if not planned:   # D46 P2: a submitted reviewer never uses the author's tool; a planned one is noted (planning.expand)
        from . import roles
        bad = roles.unindependent(result)
        require(not bad, "审查不独立：" + "；".join(f"任务「{task['name']}」（{roles.LABELS[task['role']]}）与被审查改动的作者使用同一工具 {tool}"
                                               for task, tool in bad))
    return result


def rerun_plan(ordered, index, rerun=None):
    """Which tasks a continue from ``ordered[index]`` reruns (D34 §7): all later ones when ``rerun`` is None; otherwise
    the chosen task, those listed, and those the rules require — not yet accepted, depending on a task that reruns, or a
    review / test after one. Returns (rerunning, kept, reasons) with the reason for each task the rules added."""
    chosen = None if rerun is None else set(rerun)
    rerunning, kept, reasons = [ordered[index]["id"]], [], {}
    for row in ordered[index + 1:]:
        spec = json.loads(row["spec"])
        why = None
        if chosen is not None and row["id"] not in chosen:
            why = ("pending" if row["status"] != "accepted" else
                   "depends" if any(dep in rerunning for dep in spec.get("depends_on", [])) else
                   "review" if spec.get("kind") in {"review", "test"} else
                   "knowledge" if spec.get("role") == "knowledge" else None)   # D46 P4: gathered again after a change
        if chosen is None or row["id"] in chosen or why:
            rerunning.append(row["id"])
            if why:
                reasons[row["id"]] = why
        else:
            kept.append(row["id"])
    return rerunning, kept, reasons


def member(db, user, team, owner=False):
    """The user's role in the Team: 404 to a non-member (the Team stays unknown to them), 403 to a member who is not
    the owner that the action needs."""
    row = db.execute("SELECT role FROM members WHERE user_id=? AND team_id=?", (user, team)).fetchone()
    require(row is not None, "Team 不存在或无权限", 404, "not_found")
    require(not owner or row["role"] == "owner", "只有 Team owner 可以执行此操作", 403, "forbidden")
    return row["role"]


def workflow_access(db, user, wid, role="viewer"):
    flow = db.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone()
    require(flow is not None, "Workflow 不存在或无权限", 404, "not_found")
    allowed = 4 if flow["owner_id"] == user else 0
    for grant in db.execute("SELECT * FROM grants WHERE workflow_id=?", (wid,)):
        match = grant["kind"] == "user" and grant["principal"] == user
        if grant["kind"] == "team":
            match = db.execute("SELECT 1 FROM members WHERE team_id=? AND user_id=?",
                               (grant["principal"], user)).fetchone()
        if match:
            allowed = max(allowed, ROLES[grant["role"]])
    require(allowed > 0, "Workflow 不存在或无权限", 404, "not_found")
    require(allowed >= ROLES[role], f"需要该工作流的 {role} 权限（{ROLE_LABELS[role]}）", 403, "forbidden")
    return dict(flow), next(k for k, v in ROLES.items() if v == allowed)


def space_workers(db, user, team):
    """Enabled Workers usable in a space: dedicated by owner/team, shared through worker_spaces; spare never (D28). In a
    Team's space also the member's own boundary (D49 phase 2: a Team Session runs on its submitter's boundary)."""
    if team:
        query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.team_id=?) OR "
                 "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)) OR "
                 "(w.scope='dedicated' AND w.team_id IS NULL AND w.owner_id=? AND "
                 "EXISTS(SELECT 1 FROM members m WHERE m.team_id=? AND m.user_id=?)))")
        return db.execute(query + " ORDER BY w.name", (team, team, user, team, user)).fetchall()
    query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.owner_id=?) OR "
             "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.user_id=?)))")
    return db.execute(query + " ORDER BY w.name", (user, user)).fetchall()


HINT = ("你还没有自己的执行边界：本次在 Team 的环境执行，与其他成员共用并发与 kiro 额度；"
        "可请平台管理员为你建立一个")
DISABLED_HINT = ("你的执行边界目前已停用：本次在 Team 的环境执行，与其他成员共用并发与 kiro 额度；"
                 "请平台管理员重新启用（不要再建第二个：有两个时会话会等待选择）")


def disabled_own(db, user):
    """Whether the person has an own boundary that is disabled (not retired): the hints say so instead of "none" (D49)."""
    return db.execute("SELECT 1 FROM workers WHERE owner_id=? AND team_id IS NULL AND scope='dedicated' AND enabled=0 "
                      "AND retired=0", (user,)).fetchone() is not None


def own(row, user):
    """The person's own boundary: a dedicated Worker of theirs, not a Team's (D49)."""
    return row["scope"] == "dedicated" and not row["team_id"] and row["owner_id"] == user


def boundary_owner(db, run):
    """The person whose own boundary runs the Session (D49 R10); None on a Team's or a shared environment."""
    row = db.execute("SELECT scope,team_id,owner_id FROM workers WHERE id=?", (run["worker_id"],)).fetchone() \
        if run["worker_id"] else None
    return row["owner_id"] if row is not None and own(row, row["owner_id"]) else None


def directs(db, actor, run):
    """Why the actor may not give the work its instructions where it runs (D49 R10), or None: on a person's own boundary
    the Agent runs as them — their OS user, kiro login and memory — so only they continue, diagnose, confirm, add a note
    or answer an approval there (as only they open its native entry, R7)."""
    holder = boundary_owner(db, run)
    if holder is None or holder == actor["id"]:
        return None
    name = db.execute("SELECT username FROM users WHERE id=?", (holder,)).fetchone()
    return (f"此会话在 {name['username'] if name else '创建者'} 的执行边界上运行：只有边界主人能在上面下达指令（续作、诊断、确认计划、"
            "补充说明、批准工具）。")


def moving(run):
    """The takeover the Engine is carrying out for this Session (D74), or None."""
    takeover = json.loads(run["snapshot"]).get("takeover") or {}
    return takeover if takeover.get("state") == "moving" else None


def takeover_target(db, actor, run):
    """Where the actor would take the Session over to (D74) and why not: ``(worker, reason, code)`` — their one own
    boundary, while the Session runs on someone else's own boundary, has stopped and nothing else moves it."""
    if moving(run):
        return None, "正在接手到新的执行环境，请稍候", "takeover_in_progress"
    holder = boundary_owner(db, run)
    if holder is None:
        return None, "会话在 Team 或共享的执行环境上，能续作的人都可以直接续作，不需要接手", "not_needed"
    if holder == actor["id"]:
        return None, "会话已经在你的执行环境上", "already_yours"
    mine = [w for w in space_workers(db, actor["id"], run["team_id"]) if own(w, actor["id"])]
    if len(mine) != 1:
        return None, "你还没有自己的执行环境：请联系平台管理员开通后再接手", "no_own_environment"
    if run["lifecycle"] != "open" or run["status"] not in SETTLED or run["status"] == "waiting_worker":
        return None, "会话正在执行：请先等待结束或停止，再接手", "busy"
    return mine[0], None, None


def placement(db, user, team):
    """Where a new Session of this person in this space runs when no Worker is named (D49 R2): their one own boundary;
    else the space's only environment (a Team's with a hint to get a boundary, owner 2026-10-19); else a choice waits."""
    options = space_workers(db, user, team)
    mine = [w for w in options if own(w, user)]
    if len(mine) == 1:
        return {"worker": mine[0], "placement": "own", "hint": None, "options": options}
    if len(options) == 1:
        hint = (DISABLED_HINT if disabled_own(db, user) else HINT) if team else None
        return {"worker": options[0], "placement": "team" if team else "only", "hint": hint, "options": options}
    return {"worker": None, "placement": None, "hint": None, "options": options}


def publish_policy(value):
    """A Session's publish policy (D47): ``auto`` publishes a Draft PR / MR on its own once the Session completes and passes
    the same checks as the 「发布到仓库」 button; off, a person approves the publish. A new-source Session names the new
    repository and the write credential to publish with (D48)."""
    require(isinstance(value, dict) and set(value) <= {"auto", "branch", "title", "repository", "credential"}
            and isinstance(value.get("auto", False), bool), "publish 须为 {auto: true|false, branch?, title?, repository?, credential?}")
    if value.get("branch") is not None:
        from .publish import check_branch
        check_branch(value["branch"])
    if value.get("title") is not None:
        require(isinstance(value["title"], str) and 0 < len(value["title"].strip()) <= 200, "publish.title 须为 1–200 个字符")
    extra = {}
    if value.get("repository") is not None or value.get("credential") is not None:
        from .publish import check_repository
        require(value.get("repository") is not None and isinstance(value.get("credential"), str) and value["credential"],
                "publish.repository 与 publish.credential 须一起给出", 400)
        extra = {"repository": check_repository(value["repository"]), "credential": value["credential"]}
    return {"auto": bool(value.get("auto", False)), **{k: value[k] for k in ("branch", "title") if value.get(k)}, **extra}


PUSHING = re.compile(r"git push|push(ed)? (failed|rejected)|推送|pull request|merge request|创建 PR|提交 PR|Authentication failed for",
                     re.I)
# What a task is told not to do is not what it asks for (EC2 run-f897a462…: 「不推送、不提交 PR」 was read as pushing).
NOT_PUSHING = re.compile(r"(不要|不必|不需要|无需|不得|禁止|切勿|勿|别|不|\b(?:do not|don't|never|no)\b)\s*(也不要|也不)?\s*"
                         r"(提交|创建|发起|开)?\s*(git push|push|推送|pull request|merge request|PR|MR)"
                         r"(\s*(or|和|或|、|/)\s*(提交|创建|发起|open|create)?\s*(a )?(pull request|merge request|PR|MR))?", re.I)


def pushing(text):
    """Whether ``text`` asks for, or reports, a push or a pull / merge request (D47) — not one it says not to make."""
    return bool(PUSHING.search(NOT_PUSHING.sub(" ", text or "")))


def confirm_plan(db, rid, who, actor, via):
    """The user confirmed the Session's plan (D44): it runs from here, and does not wait again."""
    snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
    snapshot["confirmed"] = {"by": actor["username"], "at": time.time(), "via": via}
    journal.change(db, "runs", rid, who, "run.confirmed", "已确认计划", quiet=("snapshot",), snapshot=dumps(snapshot))
    journal.record(db, rid, "run.confirmed", who, "已确认计划", via=via, by=actor["username"])


def release_confirmations(db, skip=()):
    """No Session waits for a person any more (owner 2026-10-22). One an earlier version left in 「计划待确认」 starts as if
    confirmed — by the platform (D70); one left in 「验收待确认」 has what its Evaluator left pending accepted as a warning
    (D71). Once, here."""
    rows = db.execute("SELECT id,status FROM runs WHERE status IN ('awaiting_confirmation','awaiting_acceptance') "
                      "AND lifecycle='open'").fetchall()
    for row in rows:
        if row["id"] in skip:
            continue
        if row["status"] == "awaiting_confirmation":
            confirm_plan(db, row["id"], journal.ENGINE, {"username": "platform"}, "platform")
            reason = "计划确认已取消，直接开始（D70）"
        else:
            for task in db.execute("SELECT * FROM tasks WHERE run_id=? AND status='awaiting_acceptance'", (row["id"],)).fetchall():
                attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
                if attempt is not None:
                    evidence = json.loads(attempt["evidence"] or "{}")
                    evidence["evaluation"] = {**(evidence.get("evaluation") or {}), "automatic": True, "accepted_at": time.time()}
                    journal.change(db, "attempts", attempt["id"], journal.ENGINE, "attempt.state", "验收确认已取消，按提示记录（D71）",
                                   status="accepted", evidence=dumps(evidence), updated=time.time())
                journal.change(db, "tasks", (row["id"], task["id"]), journal.ENGINE, "task.state", status="accepted")
            reason = "验收确认已取消，待人判断的项记为提示（D71）"
        restart_budget(db, row["id"], journal.ENGINE)
        journal.change(db, "runs", row["id"], journal.ENGINE, "run.state", reason,
                       desired="active", status="ready", error=None, updated=time.time())


def restart_budget(db, rid, who):
    """The user's continue, requested diagnosis or Worker assignment authorises more automatic work: the recovery budget
    counts from now (D42 B2). Written quietly — it is not a change of the Session's settings."""
    snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
    snapshot = {key: value for key, value in snapshot.items() if key != "budget_waited"}   # a new window has waited for nothing
    journal.change(db, "runs", rid, who, "run.budget", quiet=("snapshot",), snapshot=dumps({**snapshot, "budget_since": time.time()}))


def executor_of(run):
    """Who the Session runs as (D74): its creator, until someone took it over to their own boundary."""
    try:
        value = run["executor_id"]
    except (KeyError, IndexError):
        value = None
    return value or run["owner_id"]


def unserved(db, run):
    """Why the run's Worker no longer runs it (spec §6.2): None while it is enabled and still serves the run's space."""
    row = db.execute("SELECT enabled,retired FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
    if row is None or row["retired"]:
        return "执行环境已退役"
    if not row["enabled"]:
        return "执行环境已停用"
    person = executor_of(run)   # D74: the one it runs as — its creator unless taken over
    if all(w["id"] != run["worker_id"] for w in space_workers(db, person, run["team_id"])):
        mine = db.execute("SELECT scope,team_id,owner_id FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        if run["team_id"] and own(mine, person):   # D49 R3: the creator's boundary served the Team while they were in it
            return "会话创建者已不在该 Team：请 Team owner 或管理员把会话分配到 Team 的环境"
        return "执行环境已更换或已移出该空间"
    return None


def assign_spare(db, worker, team):
    """A spare execution environment becomes the Team's dedicated one (D28, T4; P2 for a host that served a Team)."""
    changed = db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL, last_team=NULL "
                         "WHERE id=? AND scope='spare' AND enabled=1 AND retired=0", (team, worker)).rowcount
    require(changed == 1, "该执行环境不可用或已分配", 409, "worker_unavailable")


def team_environment(db, team):
    """The Team's execution environment, enabled or not: its dedicated Worker or the shared one serving it (T6)."""
    return db.execute("SELECT w.* FROM workers w WHERE (w.scope='dedicated' AND w.team_id=?) OR (w.scope='shared' AND "
                      "EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)) ORDER BY w.name",
                      (team, team)).fetchone()


def active_team(db, team):
    """A Team that takes new Sessions and members: 409 once archived (spec §5)."""
    row = db.execute("SELECT * FROM teams WHERE id=?", (team,)).fetchone()
    require(row is not None, "Team 不存在或无权限", 404, "not_found")
    require(not row["archived"], f"Team「{row['name']}」已归档：会话可查看与停止，不能提交新会话或添加成员",
            409, "team_archived")
    return row


def give_environment(db, worker, team):
    """Give a Team an execution environment: a shared Worker serves it, a spare one becomes its own."""
    row = db.execute("SELECT scope,last_team FROM workers WHERE id=? AND enabled=1 AND retired=0", (worker,)).fetchone()
    require(row is not None and row["scope"] in {"shared", "spare"}, "该执行环境不可用或已分配", 409, "worker_unavailable")
    require(team_environment(db, team) is None, "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
    if row["scope"] == "shared":
        db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (worker, team))
        return
    if row["last_team"] not in (None, team):
        served = db.execute("SELECT name FROM teams WHERE id=?", (row["last_team"],)).fetchone()
        raise Error(409, "environment_reuse", f"该空闲环境曾服务 Team「{served['name'] if served else row['last_team']}」，"
                    "主机上留有其源码、工作区与记忆；请重新部署该主机（新的执行边界）后再分配给其他 Team")
    assign_spare(db, worker, team)


def run_access(db, actor, rid, owner=False, admin=True, read=False, shares=True):
    """The run if the actor may see it (404 otherwise, never revealing that it exists). owner=True also requires the
    right to delete it (403 with the reason). An elevated admin sees and operates every run unless admin=False (A2).
    D64: a Session shared with the actor lets them in too — for a read (``read=True``) whatever the role, otherwise only
    as a collaborator (what an ordinary Team member is); never to delete it, and not at all with ``shares=False`` (the
    native entry, D55's Requests). Every caller that only reads says so: one that does not is refused to a viewer."""
    row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    require(row is not None, "Session 不存在或无权限", 404, "not_found")
    if admin and elevated(actor):
        return dict(row)
    if row["team_id"]:
        role = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?", (row["team_id"], actor["id"])).fetchone()
        if role is not None:
            require(not owner or role["role"] == "owner" or row["owner_id"] == actor["id"],
                    "只有 Team owner 可以删除他人创建的会话", 403, "forbidden")
            return dict(row)
    elif row["owner_id"] == actor["id"]:
        return dict(row)
    from .shares import best
    share = best(db, actor, row)
    require(share is not None, "Session 不存在或无权限", 404, "not_found")
    require(not owner, "共享的会话不能删除：只有创建者、Team owner 或平台管理员可以删除", 403, "forbidden")
    require(shares, "此操作不对共享开放：只属于会话的创建者与所在 Team", 403, "forbidden")
    require(read or share["role"] == "collaborator", "你以查看身份访问此会话，不能操作", 403, "forbidden")
    return dict(row)


def access_of(db, actor, run):
    """How the actor reaches ``run`` (D64): its creator, a member of its Team, a share (with its role and who shared
    it), or an administrator's oversight."""
    if run["team_id"]:   # as run_access judges it: a Team Session through its Team, even for its creator
        role = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?", (run["team_id"], actor["id"])).fetchone()
        if role is not None:
            return {"via": "owner"} if run["owner_id"] == actor["id"] else {"via": "team", "role": role["role"]}
    elif run["owner_id"] == actor["id"]:
        return {"via": "owner"}
    from .shares import best
    share = best(db, actor, run)
    if share is not None:
        return {"via": "share", "role": share["role"], "by": share["by_name"]}
    return {"via": "admin"}


def audit(db, actor, action, kind, target, detail=None):
    """Who did what to which object: administration and admin oversight of others' Sessions (spec §3.4)."""
    db.execute("INSERT INTO audit(at,actor_id,action,kind,target,detail) VALUES(?,?,?,?,?,?)",
               (time.time(), actor["id"], action, kind, target, dumps(detail or {})))


_password_attempts, _password_lock = {}, threading.Lock()   # per user: failed current-password checks in the last minute


def revoke_tokens(db, user, keep=None):
    db.execute("DELETE FROM tokens WHERE user_id=? AND (? IS NULL OR id != ?)", (user, keep, keep))


def _active_admins(db):
    return db.execute("SELECT count(*) FROM users WHERE admin=1 AND active=1").fetchone()[0]


def _followed(snapshot, spec):
    """「（按个人偏好）」 when the planner's tool is the one the Session's preferences name for the task's role — or, without
    one for the role, its kind (D55)."""
    preferences = snapshot.get("preferences") or []
    chosen = next((p for p in preferences if spec.get("role") and p.get("role") == spec["role"]), None) or \
        next((p for p in preferences if p.get("kind") and p["kind"] == spec.get("kind")), None)
    if chosen is None or chosen.get("tool") != spec.get("tool"):
        return ""
    return "（按个人偏好）" if chosen.get("scope") == "personal" else "（按 Team 偏好）"


def mapping(snapshot, task, evidence=None):
    """Where a task's tool and model came from (M2 §6 acceptance 2): the Coordinator's switch, the user's continue (the
    configuration differs from the one it was created or planned with), the KiroCrew planner, a saved workflow, a template,
    an imported document, or the steps submitted with the request."""
    switched = (evidence or {}).get("switched_from")
    if switched:
        reason = str(switched.get("reason") or "")
        reason = reason if len(reason) <= 40 else reason[:40] + "…"   # the full reason is on the Attempt (switched_from)
        return {"source": "coordinator", "label": f"协调者从 {switched.get('tool')} / {switched.get('model') or 'auto'} 换来"
                                                  + (f"：{reason}" if reason else "")}
    if task["id"] == (snapshot.get("evaluator_added") or {}).get("task"):   # D46 P3
        return {"source": "platform", "label": "平台追加（改代码的会话须经 Evaluator）"}
    if task["id"] in ((snapshot.get("knowledge_added") or {}).get("tasks") or []):   # D46 P4
        return {"source": "platform", "label": "平台追加（沉淀项目知识）"}
    spec, key = task["spec"], lambda s: (s.get("tool"), s.get("model") or "auto", s.get("effort") or "")
    original = next((s for s in snapshot.get("steps") or [] if s.get("id") == task["id"]), None)
    if original and key(original) != key(spec):
        return {"source": "continue", "label": "续作时由用户修改"}
    if spec.get("requested"):   # D45: what the requirement named, as the planner marked it
        quote = spec["requested"].get("quote") or ""
        return {"source": "requested", "label": f"需求中指定：「{quote if len(quote) <= 30 else quote[:30] + '…'}」" if quote
                else "需求中指定"}
    from .planning import replans
    planned, replanned = snapshot.get("planning") or {}, replans(snapshot)   # D57 §6.1: there may be more than one
    if any(task["id"] == r.get("task") for r in replanned):
        return {"source": "platform", "label": "平台：重新规划（Kiro）"}
    if any(task["id"] in (r.get("tasks") or []) for r in replanned):
        return {"source": "planner", "label": "KiroCrew 重新规划" + _followed(snapshot, spec)}
    if task["id"] == "plan" and planned.get("mode") == "agent":
        return {"source": "platform", "label": "平台：需求分析与规划（Kiro）"}
    if task["id"] in (planned.get("tasks") or []):
        return {"source": "planner", "label": "KiroCrew 规划" + _followed(snapshot, spec)}
    if snapshot.get("origin") == "document":
        return {"source": "document", "label": "导入的工作流文档"}
    selection = snapshot.get("selection") or {}
    if selection.get("workflow_id"):
        return {"source": "workflow", "label": f"工作流「{snapshot.get('workflow_name')}」v{snapshot.get('workflow_version')}"}
    if selection.get("template_id"):
        kind = "规则匹配的模板" if snapshot.get("match_mode") == "rules" else "模板"
        return {"source": "template", "label": f"{kind}「{snapshot.get('workflow_name')}」"}
    return {"source": "steps", "label": "提交时指定"}


def may_publish(db, actor, run):
    """The creator, a Team owner or an admin (D35 §5.2): who controls a Session — shares it, deletes its knowledge."""
    team_owner = bool(run["team_id"]) and db.execute(
        "SELECT 1 FROM members WHERE team_id=? AND user_id=? AND role='owner'", (run["team_id"], actor["id"])).fetchone()
    return run["owner_id"] == actor["id"] or bool(team_owner) or elevated(actor)


def may_direct(db, actor, run):
    """Who may publish a Session, add a note to its running turn and answer its approvals (D38 A3): those who control it,
    and the one it runs as (D74) — a turn they took over runs on their boundary with their credentials."""
    return may_publish(db, actor, run) or executor_of(run) == actor["id"]


def run_permissions(db, actor, run):
    """What the console may offer on a run it shows (the server enforces the same rules). native: whether the
    actor's own access (never admin oversight, K03) lets them open its native KiroCrew entry."""
    def allowed(**kwargs):
        try:
            run_access(db, actor, run["id"], **kwargs)
            return True
        except Error:
            return False
    # The native entry never opens through a share (K03), except to the one the Session runs as on their own boundary (D74)
    native, reason = allowed(admin=False, shares=executor_of(run) == actor["id"]), None
    if native and run["worker_id"]:
        worker = db.execute("SELECT scope,owner_id,team_id FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        native = worker is None or worker["scope"] != "shared" or worker["owner_id"] == actor["id"]
        if native and worker is not None and own(worker, worker["owner_id"]) and worker["owner_id"] != actor["id"]:
            # D49 R7: a member's own boundary carries their kiro login and memory; the server refuses the same
            holder = db.execute("SELECT username FROM users WHERE id=?", (worker["owner_id"],)).fetchone()
            native, reason = False, (f"此会话在 {holder['username'] if holder else '创建者'} 的执行边界上运行：原生 KiroCrew 入口只开放给"
                                     "边界主人；可在本页「KiroCrew 会话」查看。")
    # An archived Team's Sessions can be read and stopped, not continued, diagnosed, reopened or reassigned (P3).
    archived = bool(run["team_id"]) and bool(
        (db.execute("SELECT archived FROM teams WHERE id=?", (run["team_id"],)).fetchone() or {"archived": 0})["archived"])
    instruct = directs(db, actor, run)   # D49 R10
    # D74: take a Session on someone else's own boundary over to one's own — who may continue it, not through oversight
    reach = allowed(admin=False)
    _, refused, _ = takeover_target(db, actor, run) if reach else (None, "管理员不因管理员身份接手会话；查看者不能接手", None)
    if archived and not refused:
        refused = "Team 已归档，不能接手"
    return {"operate": allowed(), "delete": allowed(owner=True), "native": native, "resume": not archived,
            "takeover": refused is None, **({"takeover_reason": refused} if refused else {}),
            "publish": may_direct(db, actor, run), "share": may_publish(db, actor, run),   # D64: a viewer only reads
            "assign": allowed(shares=False), **({"native_reason": reason} if reason else {}),
            "direct": instruct is None, **({"direct_reason": instruct} if instruct else {})}


def _probe(config):
    """One Worker's live health for the admin listing; whatever it answers (or fails with) stays in its own entry."""
    from .transport import worker_request
    try:
        health = worker_request(config, "/health", timeout=4)
    except Error as exc:
        return {"reachable": False, "error": str(exc)}
    except Exception as exc:  # a non-HTTP banner, a truncated body, a config without a token
        return {"reachable": False, "error": f"Worker 健康检查失败（{type(exc).__name__}）"}
    if not isinstance(health, dict):
        return {"reachable": False, "error": "Worker 健康检查返回的不是对象"}
    return {"reachable": True, **{key: health[key] for key in HEALTH_FIELDS if key in health}}


class Service:
    def __init__(self, database):
        self.db = database
        self._opened, self._opened_lock = {}, threading.Lock()   # remote preview opening links already followed
        self._moving = set()   # D74: the Sessions whose takeover is being carried out here
        from .public_shares import Tries
        self._share_tries = Tries()   # D75: wrong access codes per visitor (the share's own count is in the database)
        self._share_views = {}   # D75: share → (views not yet written, when last written)
        from .credentials import CredentialStore
        self.credentials = CredentialStore(database)   # D35; the server sets its key file
        self.provisioning = None   # MVP 0.2.0 P2 Task 6: octopus.provision.Provisions where the root service is installed

    def teams(self, actor):
        """The actor's Teams; an admin sees every Team (role None where not a member). Each carries its environment."""
        with self.db.read() as db:
            if elevated(actor):
                rows = db.execute("SELECT t.*,m.role FROM teams t LEFT JOIN members m ON m.team_id=t.id AND m.user_id=? "
                                  "ORDER BY t.created", (actor["id"],)).fetchall()
            else:
                rows = db.execute("SELECT t.*,m.role FROM teams t JOIN members m ON t.id=m.team_id "
                                  "WHERE m.user_id=? ORDER BY t.created", (actor["id"],)).fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                entry["members"] = [dict(m) for m in db.execute(
                    "SELECT u.id,u.username,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.team_id=?",
                    (row["id"],))]
                entry["environment"] = self._environment(db, row["id"])
                result.append(entry)
            return result

    @staticmethod
    def _environment(db, team):
        row = team_environment(db, team)
        return {"worker_id": row["id"], "name": row["name"], "scope": row["scope"],
                "enabled": bool(row["enabled"])} if row else None

    def workers(self, actor):
        """Workers the actor may see: an admin all; otherwise dedicated ones of their spaces and shared ones serving them."""
        with self.db.read() as db:
            teams = {r["team_id"] for r in db.execute("SELECT team_id FROM members WHERE user_id=?", (actor["id"],))}
            result = []
            for row in db.execute("SELECT * FROM workers WHERE retired=0").fetchall():
                spaces = [{"team_id": s["team_id"]} if s["team_id"] else {"user_id": s["user_id"]} for s in db.execute(
                    "SELECT team_id,user_id FROM worker_spaces WHERE worker_id=? ORDER BY rowid", (row["id"],))]
                if not elevated(actor):
                    spaces = [x for x in spaces if x.get("user_id") == actor["id"] or x.get("team_id") in teams]
                visible = elevated(actor) or (row["scope"] == "shared" and spaces) or (
                    row["scope"] == "dedicated" and (row["owner_id"] == actor["id"] or row["team_id"] in teams))
                if visible:
                    value = {k: row[k] for k in ("id", "name", "team_id", "owner_id", "enabled", "scope")}
                    value["spaces"] = spaces
                    value["capabilities"] = json.loads(row["config"]).get("capabilities", {})
                    result.append(value)
            return result

    def environments(self, actor):
        """Admin view of every execution environment (D28): bindings, Sessions running on it and live health. Health
        checks run concurrently with a short timeout: an unreachable Worker neither fails nor holds up the listing."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.read() as db:
            users = dict(db.execute("SELECT id,username FROM users").fetchall())
            teams = dict(db.execute("SELECT id,name FROM teams").fetchall())
            active = dict(db.execute(
                "SELECT worker_id,COUNT(*) FROM runs WHERE worker_id IS NOT NULL AND lifecycle='open' AND status NOT IN "
                f"({','.join('?' * len(SETTLED))}) GROUP BY worker_id", SETTLED).fetchall())
            spaces = {}
            for row in db.execute("SELECT worker_id,team_id,user_id FROM worker_spaces ORDER BY rowid"):
                spaces.setdefault(row["worker_id"], []).append(
                    {"team_id": row["team_id"], "name": teams.get(row["team_id"])} if row["team_id"]
                    else {"user_id": row["user_id"], "username": users.get(row["user_id"])})
            rows = db.execute("SELECT * FROM workers WHERE retired=0 ORDER BY rowid").fetchall()
        configs = [json.loads(row["config"]) for row in rows]
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(rows)))) as pool:
            health = list(pool.map(_probe, configs))
        return [{"id": row["id"], "name": row["name"], "scope": row["scope"], "enabled": bool(row["enabled"]),
                 "owner": {"id": row["owner_id"], "username": users.get(row["owner_id"])} if row["owner_id"] else None,
                 "team": {"id": row["team_id"], "name": teams.get(row["team_id"])} if row["team_id"] else None,
                 "spaces": spaces.get(row["id"], []), "url": config.get("url"),
                 "active_runs": active.get(row["id"], 0), "health": probe}
                for row, config, probe in zip(rows, configs, health)]

    @staticmethod
    def _live_worker(db, wid):
        row = db.execute("SELECT * FROM workers WHERE id=?", (wid,)).fetchone()
        require(row is not None, "Worker 不存在", 404, "not_found")
        require(not row["retired"], "执行环境已退役", 409, "retired")
        return row

    def rename_worker(self, actor, wid, body):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            title = name(body.get("name"))
            db.execute("UPDATE workers SET name=? WHERE id=?", (title, wid))
            audit(db, actor, "worker.rename", "worker", wid, {"from": row["name"], "to": title})
        return {"id": wid, "name": title}

    def retire_worker(self, actor, wid):
        """Retire an execution environment (spec §6.1, P6): no Team may still use it and nothing may be in progress on
        it; the personal spaces it served lose it; historical Sessions keep their reference."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            team = row["team_id"] or (db.execute("SELECT team_id FROM worker_spaces WHERE worker_id=? AND team_id IS NOT NULL",
                                                 (wid,)).fetchone() or [None])[0]
            if team:
                title = db.execute("SELECT name FROM teams WHERE id=?", (team,)).fetchone()
                raise Error(409, "in_use", f"仍是 Team「{title['name'] if title else team}」的执行环境：先为该 Team 更换执行环境")
            # Whatever the Engine still drives on it, a closed Session being reopened included.
            busy = db.execute("SELECT count(*) FROM runs WHERE worker_id=? AND lifecycle!='deleted' AND status NOT IN "
                              f"({','.join('?' * len(SETTLED))})", (wid, *SETTLED)).fetchone()[0]
            require(busy == 0, f"还有 {busy} 个进行中的会话：先等待结束或停止", 409, "busy")
            db.execute("DELETE FROM worker_spaces WHERE worker_id=?", (wid,))
            db.execute("UPDATE workers SET retired=1, enabled=0 WHERE id=?", (wid,))
            audit(db, actor, "worker.retire", "worker", wid)
        return {"id": wid, "retired": True}

    def dedicate_worker(self, actor, wid):
        """A shared Worker host serving exactly one Team becomes that Team's dedicated environment again (D30): every
        member then opens its native KiroCrew entry, as in a personal space."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            spaces = db.execute("SELECT team_id,user_id FROM worker_spaces WHERE worker_id=?", (wid,)).fetchall()
            require(row["scope"] == "shared" and len(spaces) == 1 and spaces[0]["team_id"],
                    "只有只服务一个 Team 的共享环境可以改回专属环境", 409, "invalid_state")
            # A Team's own Worker must be a dedicated boundary: every member's native login covers its Gateway.
            require(json.loads(row["config"]).get("capabilities", {}).get("isolation") == "dedicated",
                    "该 Worker 运行在平台主机上（trusted-local），不能成为 Team 的专属环境；请使用独立的 Worker 主机",
                    409, "isolation_required")
            team = spaces[0]["team_id"]
            db.execute("DELETE FROM worker_spaces WHERE worker_id=?", (wid,))
            db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL WHERE id=?", (team, wid))
            audit(db, actor, "worker.dedicate", "worker", wid, {"team_id": team})
        return {"id": wid, "scope": "dedicated", "team_id": team}

    def share_worker(self, actor, wid, body):
        """Turn a dedicated Worker into a shared execution environment; its current space stays served (D28, T3)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(body.get("confirm") is True, "设为共享环境需要确认：环境内所有会话的 Agent 以同一系统账号运行，"
                "能互相读取源码、工具登录与记忆", 400, "confirmation_required")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            require(row["scope"] == "dedicated", "只有专属 Worker 可以设为共享环境", 409, "invalid_state")
            # Teams and other users would run as the platform's own account, which reads the database and Worker tokens.
            local = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
            require(json.loads(row["config"]).get("capabilities", {}).get("host_principal") != local,
                    "平台宿主账号上的 Worker 不能设为共享环境；请使用独立执行账号、容器或主机", 409, "isolation_required")
            if row["team_id"]:
                db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (wid, row["team_id"]))
            else:
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, row["owner_id"]))
            # The boundary owner is kept: it opens the native entry and re-registers the Worker on reinstall.
            db.execute("UPDATE workers SET scope='shared', team_id=NULL, owner_id=? WHERE id=?",
                       (row["owner_id"] or actor["id"], wid))
            audit(db, actor, "worker.share", "worker", wid)
        return {"id": wid, "scope": "shared"}

    def worker_space(self, actor, wid, body, remove=False):
        """Add a Team or a user's personal space to a shared execution environment, or take it away."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        team, user = body.get("team_id"), body.get("user_id")
        require(isinstance(team or user, str) and bool(team) != bool(user), "需要且只能指定 team_id 或 user_id")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            # A spare environment can only be given to a Team, where it becomes that Team's own (T4).
            require(row["scope"] == "shared" or (row["scope"] == "spare" and team and not remove),
                    "只有共享环境可以调整服务空间；空闲环境只能分配给 Team", 409, "invalid_state")
            column, value = ("team_id", team) if team else ("user_id", user)
            if remove:
                changed = db.execute(f"DELETE FROM worker_spaces WHERE worker_id=? AND {column}=?", (wid, value)).rowcount
                require(changed == 1, "该空间不在此共享环境中", 404, "not_found")
            elif team:
                require(db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone(), "Team 不存在", 404, "not_found")
                give_environment(db, wid, team)
            else:
                require(db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (user,)).fetchone(),
                        "用户不存在", 404, "not_found")
                require(not db.execute("SELECT 1 FROM worker_spaces WHERE worker_id=? AND user_id=?", (wid, user)).fetchone(),
                        "该用户已在此共享环境中", 409, "conflict")
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, user))
            audit(db, actor, "worker.space.remove" if remove else "worker.space.add", "worker", wid, {column: value})
        return {"ok": True}

    def create_team(self, actor, body):
        """Admin only; the Team gets its owner and its execution environment in the same transaction (D28)."""
        require(elevated(actor), "只有平台管理员可以创建 Team", 403, "forbidden")
        title = name(body.get("name"))
        require(isinstance(body.get("worker_id"), str) and body["worker_id"],
                "创建 Team 必须选择执行环境", 400, "worker_required")
        with self.db.transaction() as db:
            require(not db.execute("SELECT 1 FROM teams WHERE name=?", (title,)).fetchone(),
                    "Team 名称已存在", 409, "conflict")
            owner = db.execute("SELECT id FROM users WHERE username=? AND active=1",
                               (text(body.get("owner"), "Team owner", 64).lower(),)).fetchone()
            require(owner is not None, "Team owner 不存在", 404, "not_found")
            tid = uid("team")
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,?)", (tid, title, time.time()))
            db.execute("INSERT INTO members VALUES(?,?,?)", (tid, owner["id"], "owner"))
            give_environment(db, body["worker_id"], tid)
            audit(db, actor, "team.create", "team", tid, {"owner": owner["id"], "worker_id": body["worker_id"]})
            role = "owner" if owner["id"] == actor["id"] else None
            return {"id": tid, "name": title, "role": role, "environment": self._environment(db, tid)}

    @staticmethod
    def _team(db, actor, tid, owner=False):
        """The Team an admin (any Team) or its members (owner=True: its owners) may manage; 404 / 403 as member()."""
        row = db.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
        if elevated(actor):
            require(row is not None, "Team 不存在", 404, "not_found")
        else:
            member(db, actor["id"], tid, owner=owner)
        return dict(row)

    def rename_team(self, actor, tid, body):
        with self.db.transaction() as db:
            team = self._team(db, actor, tid, owner=True)
            title = name(body.get("name"))
            require(not db.execute("SELECT 1 FROM teams WHERE name=? AND id!=?", (title, tid)).fetchone(),
                    "Team 名称已存在", 409, "conflict")
            db.execute("UPDATE teams SET name=? WHERE id=?", (title, tid))
            audit(db, actor, "team.rename", "team", tid, {"from": team["name"], "to": title})
        return {"id": tid, "name": title}

    def archive_team(self, actor, tid, body):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(isinstance(body.get("archived"), bool), "archived 必须为布尔值")
        with self.db.transaction() as db:
            self._team(db, actor, tid)
            db.execute("UPDATE teams SET archived=? WHERE id=?", (int(body["archived"]), tid))
            audit(db, actor, "team.archive" if body["archived"] else "team.restore", "team", tid)
        return {"id": tid, "archived": body["archived"]}

    def change_environment(self, actor, tid, body):
        """Move a Team to another execution environment (spec §5). Old Sessions are not moved (T6): their next dispatch
        finds the Worker no longer serves the Team and waits to be reassigned (§6.2)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        wid = body.get("worker_id")
        require(isinstance(wid, str) and wid, "需要 worker_id")
        with self.db.transaction() as db:
            self._team(db, actor, tid)
            require(db.execute("SELECT 1 FROM workers WHERE id=? AND retired=0", (wid,)).fetchone(),
                    "Worker 不存在", 404, "not_found")
            old = team_environment(db, tid)
            require(old is None or old["id"] != wid, "该 Team 已在使用这个执行环境", 409, "conflict")
            if old is not None and old["scope"] == "shared":
                db.execute("DELETE FROM worker_spaces WHERE worker_id=? AND team_id=?", (old["id"], tid))
            elif old is not None:
                # The Team's own host waits as a spare that remembers it: its code and memories are still there (P2).
                db.execute("UPDATE workers SET scope='spare', team_id=NULL, owner_id=?, last_team=? WHERE id=?",
                           (actor["id"], tid, old["id"]))
            give_environment(db, wid, tid)
            audit(db, actor, "team.environment", "team", tid, {"from": old["id"] if old else None, "to": wid})
            return {"id": tid, "environment": self._environment(db, tid)}

    def membership(self, actor, tid, body, remove=False):
        """Owners and admins (elevated, members or not) add, remove and re-role members; a member may remove only
        themselves (leave). The last active owner stays (B4)."""
        with self.db.transaction() as db:
            username = text(body.get("username"), "用户名", 64).lower()
            leaving = remove and username == actor["username"]
            self._team(db, actor, tid, owner=not leaving)
            # A deactivated member can still be removed; only active users join or change role.
            target = db.execute("SELECT id FROM users WHERE username=?" + ("" if remove else " AND active=1"),
                                (username,)).fetchone()
            require(target is not None, "用户不存在", 404, "not_found")
            role = body.get("role", "member")
            require(role in {"owner", "member"}, "角色无效")
            current = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?",
                                 (tid, target["id"])).fetchone()
            if not remove and current is None:
                active_team(db, tid)   # an archived Team takes no new members; roles may still change
            if current and current["role"] == "owner" and (remove or role != "owner"):
                # Another *active* owner must remain: a deactivated owner cannot manage the Team (B4).
                require(db.execute("SELECT count(*) FROM members m JOIN users u ON u.id=m.user_id WHERE m.team_id=? "
                                   "AND m.role='owner' AND u.active=1 AND m.user_id!=?", (tid, target["id"])).fetchone()[0] > 0,
                        "不能移除最后一位有效的 Team Owner", 409, "conflict")
            if remove:
                require(current is not None, "该用户不是 Team 成员", 404, "not_found")
                db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, target["id"]))
                audit(db, actor, "team.member.leave" if leaving else "team.member.remove", "team", tid,
                      None if leaving else {"user": target["id"]})
            else:
                db.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(team_id,user_id) DO UPDATE SET role=excluded.role",
                           (tid, target["id"], role))
                audit(db, actor, "team.member.set", "team", tid, {"user": target["id"], "role": role})
        return {"ok": True}

    def workflows(self, actor):
        result = []
        with self.db.read() as db:
            for row in db.execute("SELECT id FROM workflows WHERE archived=0 ORDER BY created DESC"):
                try:
                    flow, role = workflow_access(db, actor["id"], row["id"])
                except Error:
                    continue
                version = db.execute("SELECT spec FROM workflow_versions WHERE workflow_id=? AND version=?",
                                     (flow["id"], flow["version"])).fetchone()
                flow.update(role=role, steps=json.loads(version["spec"]),
                            grants=[dict(g) for g in db.execute("SELECT kind,principal,role FROM grants WHERE workflow_id=?",
                                                               (flow["id"],))] if role == "owner" else [])
                result.append(flow)
        return result

    def save_workflow(self, actor, body, wid=None):
        title, spec = name(body.get("name")), steps(body.get("steps"))
        named = body.get("credentials") or {}
        require(isinstance(named, dict) and set(named) <= {"source"} and isinstance(named.get("source", ""), str),
                "credentials 只能是 {\"source\": \"<凭证 ID>\"}")
        with self.db.transaction() as db:
            if named.get("source"):
                # Only a reference: whoever runs the workflow later uses it only if they may (spec §4).
                from . import credentials as repo_credentials
                row = db.execute("SELECT * FROM credentials WHERE id=?", (named["source"],)).fetchone()
                require(row is not None and repo_credentials.usable(db, actor["id"], row, "*"),
                        "无权使用此凭证作为工作流默认值", 403, "credential_forbidden")
            if wid:
                flow, _ = workflow_access(db, actor["id"], wid, "editor")
                require(body.get("expected_version") == flow["version"], "Workflow 版本已更新", 409, "version_conflict")
                owner, version = flow["owner_id"], flow["version"] + 1
            else:
                owner, version, wid = actor["id"], 1, uid("flow")
            conflict = db.execute("SELECT id FROM workflows WHERE owner_id=? AND name_key=? AND id!=?",
                                  (owner, title.casefold(), wid)).fetchone()
            require(not conflict, "同一 Owner 下已有同名 Workflow", 409, "conflict")
            if version == 1:
                db.execute("INSERT INTO workflows(id,owner_id,name,name_key,version,created) VALUES(?,?,?,?,?,?)",
                           (wid, owner, title, title.casefold(), version, time.time()))
            else:
                db.execute("UPDATE workflows SET name=?,name_key=?,version=? WHERE id=?",
                           (title, title.casefold(), version, wid))
            db.execute("INSERT INTO workflow_versions(workflow_id,version,spec,author,created,credentials) VALUES(?,?,?,?,?,?)",
                       (wid, version, dumps(spec), actor["id"], time.time(), dumps(named)))
        return {"id": wid, "name": title, "version": version, "steps": spec, "credentials": named}

    def resolve_workflow(self, actor, body):
        """Preview only: no Session, source checkout or Coding task is created."""
        from .intent import catalog, preset_steps, ranked, selection, title_from
        goal = text(body.get("input"), "开发需求", minimum=3)
        choices = [key for key in ("workflow_id", "template_id", "steps", "document") if body.get(key) is not None]
        require(len(choices) <= 1, "只指定 workflow_id、template_id、steps 或 document 中的一项")
        if choices == ["document"]:
            # A workflow generated ahead of time by an Agent runs as submitted, after code normalization.
            from .workflow_import import import_workflow
            document = body["document"]
            require(isinstance(document, dict) and set(document) <= {"content", "format"}, "document 需要 content 与可选 format")
            body, choices = {**body, "steps": import_workflow(document.get("content"), document.get("format"))["steps"]}, ["steps"]
            del body["document"]
        require("workflow_version" not in body or "workflow_id" in choices, "版本必须对应 workflow_id")
        with self.db.read() as db:
            if body.get("team_id"):
                member(db, actor["id"], body["team_id"])
            if "workflow_id" in choices:
                flow, _ = workflow_access(db, actor["id"], body["workflow_id"], "runner")
                require(not flow["archived"], "工作流已归档", 409, "archived")
                version = body.get("workflow_version", flow["version"])
                require(isinstance(version, int) and not isinstance(version, bool) and version > 0, "版本无效")
                saved = db.execute("SELECT spec FROM workflow_versions WHERE workflow_id=? AND version=?",
                                   (flow["id"], version)).fetchone()
                require(saved is not None, "工作流版本不存在", 404, "not_found")
                row = {"kind": "workflow", "id": flow["id"], "version": version,
                       "name": flow["name"], "steps": json.loads(saved["spec"]), "reason": "手动指定工作流"}
            elif "steps" in choices:
                row = {"kind": "custom", "name": "自定义任务", "steps": steps(body["steps"]),
                       "reason": "采用用户或 Chat 编排的任务，不重新匹配"}
            elif "template_id" in choices:
                row = next((t for t in catalog() if t["id"] == body["template_id"]), None)
                require(row is not None, "模板不存在", 404, "not_found")
                row = {**row, "kind": "template", "reason": "手动指定模板"}
        recommendations = []
        if not choices:
            recommendations = ranked(goal, self.workflows(actor))
            named = [r for r in recommendations if r["kind"] == "workflow" and r["score"] >= 1000]
            require(len(named) <= 1, "多个可用工作流名称匹配，请通过 workflow_id 指定", 409, "ambiguous_workflow")
            row = recommendations[0]
        picked = {"steps": row["steps"]} if row["kind"] == "custom" else selection(row)
        return {"input": goal, "name": name(body["name"]) if "name" in body else title_from(goal),
                "workflow_name": row["name"], "steps": steps(preset_steps(row, goal) if row["kind"] == "template" else row["steps"]), "selection": picked,
                "mode": "manual" if choices else "rules", "reason": row["reason"],
                "recommendations": [{"name": r["name"], "reason": r["reason"], "selection": selection(r),
                                     "tasks": len(r["steps"])} for r in recommendations[:5]],
                "notice": "即时匹配基于模板与可访问工作流的关键词，不是 LLM 生成。提交后才准备源码并调用 ACP。"}

    def share(self, actor, wid, body, revoke=False):
        with self.db.transaction() as db:
            workflow_access(db, actor["id"], wid, "owner")
            kind, principal = body.get("kind"), body.get("principal")
            require(kind in {"user", "team"} and isinstance(principal, str), "共享对象无效")
            if kind == "team":
                member(db, actor["id"], principal)
            else:
                require(db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (principal,)).fetchone(),
                        "共享用户不存在", 404, "not_found")
            if revoke:
                db.execute("DELETE FROM grants WHERE workflow_id=? AND kind=? AND principal=?", (wid, kind, principal))
            else:
                role = body.get("role", "viewer")
                require(role in {"viewer", "runner", "editor"}, "共享角色无效")
                db.execute("INSERT INTO grants VALUES(?,?,?,?) ON CONFLICT(workflow_id,kind,principal) DO UPDATE SET role=excluded.role",
                           (wid, kind, principal, role))
        return {"ok": True}

    def archive_workflow(self, actor, wid, body):
        with self.db.transaction() as db:
            flow, _ = workflow_access(db, actor["id"], wid, "owner")
            require(body.get("expected_version") == flow["version"], "Workflow 版本已更新", 409, "version_conflict")
            archived = body.get("archived", True)
            require(isinstance(archived, bool), "archived 必须为布尔值")
            db.execute("UPDATE workflows SET archived=? WHERE id=?", (int(archived), wid))
        return {"id": wid, "archived": archived}

    def _command(self, db, actor, action, target, body, execute):
        key = body.get("request_id")
        require(isinstance(key, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{8,100}", key), "需要 8–100 字符 request_id")
        digest = hashlib.sha256(dumps([action, target, body]).encode()).hexdigest()
        old = db.execute("SELECT * FROM commands WHERE actor=? AND key=?", (actor["id"], key)).fetchone()
        if old:
            require(old["hash"] == digest, "request_id 对应不同请求", 409, "idempotency_conflict")
            return json.loads(old["response"])
        result = execute()
        db.execute("INSERT INTO commands VALUES(?,?,?,?,?)",
                   (actor["id"], key, digest, dumps(result), time.time()))
        return result

    def start(self, actor, body, *, design=None):
        from .recovery import policy
        chosen_explicitly = submissions.explicit(body)   # D55: what the submitter chose, before planning replaces it
        selected = any(body.get(key) is not None for key in ("workflow_id", "template_id", "steps", "document"))
        require(body.get("plan") in (None, "agent", "match"), "plan 只能是 agent 或 match")
        require(not (selected and body.get("plan") == "agent"), "plan=agent 时不要同时指定工作流、模板、steps 或 document")
        # D70: no Session waits for its plan's confirmation; an older client's admission is checked, then not used
        require(body.get("admission") in (None, "confirm", "auto"), "admission 须为 confirm 或 auto")
        if "publish" in body:
            publish_policy(body["publish"])
        placed = {"kind": "requested"} if body.get("worker_id") else None   # D49 R5: recorded with the Session
        if not body.get("worker_id"):
            # Where it runs (D49 R2); a Team with neither an environment nor the submitter's boundary takes none (D28).
            team = body.get("team_id") or None
            with self.db.read() as db:
                if team:
                    member(db, actor["id"], team)   # a non-member learns nothing about the Team
                    active_team(db, team)
                chosen = placement(db, actor["id"], team)
                if team and not chosen["options"]:
                    row = db.execute("SELECT name FROM teams WHERE id=?", (team,)).fetchone()
                    if row is not None and team_environment(db, team) is not None:
                        raise Error(409, "environment_disabled", f"Team「{row['name']}」的执行环境已停用，请联系平台管理员")
                    if row is not None:
                        mine = "你的执行边界也已停用" if disabled_own(db, actor["id"]) else "你也还没有自己的执行边界"
                        raise Error(409, "team_without_worker", f"Team「{row['name']}」尚未分配执行环境，{mine}："
                                                                "请联系平台管理员")
            if chosen["worker"] is not None:   # D49: one's own boundary first, in a Team's space too
                body = {**body, "worker_id": chosen["worker"]["id"]}
                placed = {"kind": chosen["placement"], **({"hint": chosen["hint"]} if chosen["hint"] else {})}
        planning = design is None and (body.get("plan") == "agent" or (not selected and body.get("plan") != "match"))
        with self.db.read() as db:   # D55: the submitter's and the Team's mapping preferences, for the planner to weigh
            preferences = policies.preferences(db, actor["id"], body.get("team_id") or None) if design is None else []
        if planning:
            # A real KiroCrew Agent recognises the intent, enriches the requirement and plans the tasks (planning.py).
            from .planning import plan_step
            capabilities = {}
            if body.get("worker_id"):
                with self.db.read() as db:
                    row = db.execute("SELECT config FROM workers WHERE id=?", (body["worker_id"],)).fetchone()
                capabilities = json.loads(row["config"]).get("capabilities", {}) if row else {}
            # The planner sees which tools this environment can start and its known limits, and chooses (D38 A5).
            body = {**{k: v for k, v in body.items() if k != "plan"},
                    "steps": [plan_step(capabilities.get("models", {}), capabilities.get("tools"), capabilities.get("limitations"),
                                       capabilities.get("commands"), preferences, general=general(body.get("source")))]}
        resolved = self.resolve_workflow(actor, {k: v for k, v in body.items() if k != "plan"})
        with self.db.transaction() as db:
            wid = resolved["selection"].get("workflow_id")
            team = body.get("team_id") or None
            if team:
                member(db, actor["id"], team)
                active_team(db, team)
            if wid:
                flow, _ = workflow_access(db, actor["id"], wid, "runner")
                require(not flow["archived"], "工作流已归档，历史 Session 可继续", 409, "archived")
                version = resolved["selection"]["workflow_version"]
                saved = db.execute("SELECT spec,credentials FROM workflow_versions WHERE workflow_id=? AND version=?",
                                   (wid, version)).fetchone()
                require(saved is not None, "工作流版本不存在", 404, "not_found")
                spec, default = json.loads(saved["spec"]), json.loads(saved["credentials"]).get("source")
            else:
                spec, version, default = resolved["steps"], None, None
            goal = text(body.get("input"), "开发需求", minimum=3)
            source = body.get("source")
            require(isinstance(source, dict) and set(source) <= {"type", "url", "ref"}
                    and source.get("type") in {"new", "git", "none"},
                    "来源须为 git（已有仓库）、new（新建项目）或 none（不需要代码仓库，D65）")
            if source["type"] == "git":
                from .sources import normalize_source
                try:
                    url, kind = normalize_source(text(source.get("url"), "仓库地址", 2000))
                except ValueError as exc:
                    raise Error(400, "invalid_request", str(exc)) from None
                require(kind == "git", "Team API 只接受远程 Git 来源，不接受宿主机任意路径")
                source = {**source, "url": url}
                if "ref" in source:
                    source["ref"] = text(source["ref"], "Git ref", 200)
                    require(not source["ref"].startswith("-"), "Git ref 无效")
            else:
                require(set(source) == {"type"}, "新建项目与不需要仓库的会话不接受 url/ref")
            worker = body.get("worker_id")
            if worker:
                self._worker(db, actor["id"], worker, team)
            named = body.get("credentials") or {}
            require(isinstance(named, dict) and set(named) <= {"source"} and isinstance(named.get("source", ""), str),
                    "credentials 只能是 {\"source\": \"<凭证 ID>\"}")
            from . import credentials as repo_credentials
            bound = repo_credentials.resolve(db, actor, source, team, named.get("source"), default)
            if "publish" in body:   # D48: a new repository is checked with the credential named for it
                require(not general(source), NO_REPOSITORY + "，不能设置发布")
                wanted = publish_policy(body["publish"])
                self._new_repository_policy(db, actor, team, source, wanted)
                require(not wanted["auto"] or source.get("type") == "git" or wanted.get("repository"),
                        "新建源码的会话自动发布需要新仓库（repository）与有写权限的凭证（credential）", 409, "publish_target_missing")

            def execute():
                rid, now = uid("run"), time.time()
                snapshot = {"workflow_id": wid, "workflow_version": version, "steps": spec,
                            **({"origin": "document"} if body.get("document") is not None else {}),
                            "name": resolved["name"], "workflow_name": resolved["workflow_name"],
                            "selection": {k: v for k, v in resolved["selection"].items() if k != "steps"},
                            "match_mode": resolved["mode"], "match_reason": resolved["reason"]}
                snapshot.update(recovery=policy(body.get("recovery")) if design is None else policy({"enabled": False}),
                                execution_contract=4 if design is None else 1)   # D46 P3: the Evaluator gate
                if design is None:   # D70: every Session starts once planned
                    snapshot.update(admission="auto", admission_basis={"mode": "auto", "basis": "default"})
                if "publish" in body:   # D47: the user's own word on publishing comes first
                    snapshot["publish_policy"] = {**publish_policy(body["publish"]), "source": "user"}
                if bound:
                    snapshot["credentials"] = {"source": bound}
                if preferences:
                    snapshot["preferences"] = preferences
                if placed and worker:
                    snapshot["placement"] = placed
                if design is not None:
                    snapshot.update(purpose="workflow_design", design=design)
                if planning:
                    snapshot.update(planning={"mode": "agent", "applied": False}, workflow_name="KiroCrew 规划",
                                    match_mode="agent", match_reason="由 KiroCrew Agent 识别意图、补全需求并拆分任务")
                request = None
                if design is None:   # D55: the submission as made; a workflow design Session is not one
                    explicit = {**chosen_explicitly, **({"workflow_version": version} if wid else {})}
                    request = submissions.register(db, actor, body["request_id"], team, goal, source, explicit,
                                                   {"admission": snapshot["admission_basis"], "recovery": snapshot["recovery"],
                                                    "publish": snapshot.get("publish_policy")}, now)
                db.execute("INSERT INTO runs(id,owner_id,team_id,worker_id,workflow_id,snapshot,input,source,status,created,updated,"
                           "request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (rid, actor["id"], team, worker, wid, dumps(snapshot), goal, dumps(source),
                            "preparing" if worker else "waiting_worker", now, now, request))
                for i, step in enumerate(spec):
                    db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)",
                               (rid, step["id"], i, dumps(step)))
                from .revisions import record
                record(db, rid, 1, "created", f"创建：{len(spec)} 个任务", actor["id"], now, input=goal, steps=spec)
                journal.record(db, rid, "run.created", journal.person(actor), worker_assigned=bool(worker),
                               **({"placement": placed["kind"]} if placed and worker else {}))
                if bound:
                    journal.record(db, rid, "run.credentials", journal.person(actor),
                                   source={k: bound[k] for k in ("id", "name", "scope", "hint")})
                return {"id": rid, "session_id": rid, "version": 1, **({"request_id": request} if request else {}),
                        "status": "preparing" if worker else "waiting_worker", **({"placement": placed} if placed and worker else {})}
            return self._command(db, actor, "design" if design is not None else "start", None,
                                 {**body, "_design": design} if design is not None else body, execute)

    def placement(self, actor, team=None, rid=None):
        """Where a new Session would run in a space, or the environments a Session may move to (D49 R4, R6): the one
        answer the console and Coding Agents show, so neither keeps its own copy of the rule."""
        with self.db.read() as db:
            if rid:
                run = run_access(db, actor, rid, shares=False)   # D64: for moving it, not for shares
                user, team = executor_of(run), run["team_id"]
                options = space_workers(db, user, team)   # the one it runs as (D74; its creator unless taken over), not the operator's (R4)
                placed = json.loads(run["snapshot"]).get("placement") or {}
                answer = {"worker": next((w for w in options if w["id"] == run["worker_id"]), None),
                          "placement": placed.get("kind"), "hint": placed.get("hint"), "options": options}
            else:
                if team:
                    member(db, actor["id"], team)   # a non-member learns nothing about the Team
                    active_team(db, team)   # nor is a place offered where start() refuses a new Session
                user, answer = actor["id"], placement(db, actor["id"], team)

        def row(w):
            capabilities = json.loads(w["config"]).get("capabilities", {})
            return {"id": w["id"], "name": w["name"], "scope": w["scope"], "own": own(w, user),
                    "isolation": capabilities.get("isolation"), "models": capabilities.get("models", {}),
                    "defaults": capabilities.get("model_defaults", {})}   # D51 R5: what "auto" runs
        chosen = answer["worker"]
        return {"space": team or "personal", "hint": answer["hint"], "options": [row(w) for w in answer["options"]],
                "default": {"id": chosen["id"], "name": chosen["name"], "scope": chosen["scope"],
                            "placement": answer["placement"]} if chosen else None}

    @staticmethod
    def _worker(db, user, worker, team):
        row = next((r for r in space_workers(db, user, team) if r["id"] == worker), None)
        require(row is not None, "Worker 不属于本次执行空间", 403, "forbidden")
        return dict(row)

    def preview_entry(self, actor, rid):
        """(entry, pages): the page the preview opens first and every HTML page the Session produced, asked from the run's
        Worker; an older Worker names its entry only."""
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            require(run["worker_id"], "此 Session 尚未分配 Worker，没有可预览的产出", 409, "no_preview")
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        from .transport import worker_request
        found = worker_request(json.loads(worker["config"]), "/files", {"run_id": rid, "path": ""})
        self.audit_read(actor, rid, "preview")
        return found["entry"], list(found.get("pages") or [found["entry"]])

    def archive_entry(self, actor, rid):
        """Whether the actor may download the run's source and how large it is: a dry run on the Worker, so a
        refusal (too large, Worker offline) is an answer here rather than a page the browser opens (D36 §4.1)."""
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            require(run["worker_id"] and run["workspace"], "此会话尚未准备源码，没有可下载的内容", 409, "no_archive")
            config = json.loads(db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()["config"])
        from .transport import worker_request
        try:
            return worker_request(config, "/archive/plan", {"run_id": rid})
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                raise Error(409, "archive_unsupported", "执行环境的 Worker 尚未升级，暂不支持下载源码") from None
            raise

    def archive_source(self, rid, holder):
        """The Worker serving a download, for the link's holder who must still be allowed to read the run (B6)."""
        with self.db.read() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            require(run is not None and run["worker_id"], "下载不可用", 404, "not_found")
            user = db.execute("SELECT id,username,admin FROM users WHERE id=? AND active=1", (holder,)).fetchone()
            reader = {"id": holder, "username": user["username"] if user else None, "admin": user["admin"] if user else 0,
                      "scopes": ["read"] + (["admin"] if user and user["admin"] else [])}
            try:
                require(user is not None, "下载不可用", 404, "not_found")
                run_access(db, reader, rid, read=True)
            except Error:
                raise Error(403, "archive_forbidden", "下载权限已撤销，请在控制台重新下载") from None
            config = json.loads(db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()["config"])
            owner = db.execute("SELECT username FROM users WHERE id=?", (run["owner_id"],)).fetchone()
            name = json.loads(run["snapshot"]).get("name") or run["input"][:40]
        return config, {"name": name, "creator": owner["username"] if owner else ""}, reader

    def record_download(self, rid, reader, client, size):
        with self.db.transaction() as db:
            journal.record(db, rid, "run.downloaded", journal.person({**reader, "client": client}), bytes=size)
        self.audit_read(reader, rid, "download")

    # --- remote preview (D36 §5): the platform's record of each Session's app; the Worker runs it (octopus/apps.py) ---

    APP_TIMEOUTS = {"status": 5, "stop": 10, "start": 35}   # the console and session commands must not hang on a Worker

    @classmethod
    def _app_call(cls, config, rid, action):
        from . import transport
        try:
            return transport.worker_request(config, "/app", {"run_id": rid, "action": action}, timeout=cls.APP_TIMEOUTS[action])
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                raise Error(409, "app_unsupported", "执行环境的 Worker 尚未升级，暂不支持远程预览") from None
            raise

    def _app_sync(self, rid, row, answer):
        """Bring the record in line with what the Worker reports (ready, failed, or gone after a restart)."""
        status = answer.get("status")
        if not row or row["status"] not in remote_preview.LIVE or status == row["status"] or status not in {
                "running", "failed", "stopped", "starting"}:
            return row
        with self.db.transaction() as db:
            db.execute("UPDATE apps SET status=?,error=?,stopped=? WHERE run_id=?",
                       (status, answer.get("error"), time.time() if status in {"failed", "stopped"} else None, rid))
            if status in {"failed", "stopped"}:
                journal.record(db, rid, "app.stopped", journal.WORKER, reason="worker", status=status,
                               error=answer.get("error"))
            return db.execute("SELECT * FROM apps WHERE run_id=?", (rid,)).fetchone()

    def _stale(self, wid, instance, keep):
        """A Worker that restarted (another instance) has none of its earlier previews: their records stop before any
        new app can take one of their ports, so no old cookie reaches another Session's app."""
        if not instance:
            return
        with self.db.transaction() as db:
            rows = db.execute(f"SELECT run_id,upstream FROM apps WHERE worker_id=? AND run_id!=? AND status IN "
                              f"({','.join('?' * len(remote_preview.LIVE))})", (wid, keep, *remote_preview.LIVE)).fetchall()
            for row in rows:
                if json.loads(row["upstream"]).get("instance") != instance:
                    db.execute("UPDATE apps SET status='stopped',stopped=? WHERE run_id=?", (time.time(), row["run_id"]))
                    journal.record(db, row["run_id"], "app.stopped", journal.WORKER, reason="worker", status="stopped",
                                   error="执行环境的 Worker 已重启")

    @staticmethod
    def _app_worker(db, wid):
        """(config, retired) of a Worker; a retired one is never called again (P6)."""
        row = db.execute("SELECT config,retired FROM workers WHERE id=?", (wid,)).fetchone() if wid else None
        return (json.loads(row["config"]), bool(row["retired"])) if row else (None, False)

    def approvals(self, actor, rid):
        """The Session's pending native approvals as its Worker reports them (D38 §4.2)."""
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            config, retired = self._app_worker(db, run["worker_id"])
        if not config or retired or not run["native_parent"]:
            return {"approvals": [], "supported": bool(config) and not retired}
        from . import transport
        try:
            return {"approvals": transport.worker_request(config, "/approvals", {"run_id": rid}, timeout=10)["approvals"],
                    "supported": True}
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                return {"approvals": [], "supported": False}
            raise self._worker_refusal(exc) from None

    def forget_knowledge(self, actor, rid, body):
        """Delete one item this Session wrote to KiroCrew's lessons (D46 P4 R8), from the environment that holds it: those
        who may publish the Session, audited."""
        require(isinstance(body, dict) and set(body) == {"id"} and isinstance(body["id"], str), "只接受要删除的知识条目 id")
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(may_publish(db, actor, run), "只有会话创建者、Team owner 或管理员可以删除沉淀的知识", 403, "forbidden")
            row = db.execute("SELECT * FROM knowledge WHERE id=? AND run_id=?", (body["id"], rid)).fetchone()
            require(row is not None, "知识条目不存在", 404, "not_found")
            require(row["removed"] is None, "该条知识已删除", 409, "conflict")
            require(row["result"] in {"written", "superseded"}, "该条知识没有写入执行环境，无需删除", 409, "conflict")
            config, retired = self._app_worker(db, row["worker_id"])
        require(config and not retired, "写入该条知识的执行环境已不可用", 409, "worker_unavailable")
        from . import transport
        try:
            transport.worker_request(config, "/knowledge/forget", {"run_id": rid, "scope": row["repo"], "rule": row["rule"]},
                                     timeout=30)
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                raise Error(409, "knowledge_unsupported", "执行环境的 Worker 尚未升级，暂不支持删除知识") from None
            if exc.code in {"native_unavailable", "worker_unavailable"}:
                raise Error(503, exc.code, "未删除：KiroCrew Gateway 或执行环境暂不可用，请稍后重试") from None
            raise self._worker_refusal(exc) from None
        with self.db.transaction() as db:
            db.execute("UPDATE knowledge SET removed=?, removed_by=? WHERE id=? AND removed IS NULL", (time.time(), actor["id"], row["id"]))
            audit(db, actor, "knowledge.forget", "run", rid, {"id": row["id"], "rule": row["rule"][:100]})
            journal.record(db, rid, "run.knowledge_forgotten", journal.person(actor), reason="删除沉淀的知识：" + row["rule"][:60],
                           id=row["id"])
        return {"id": row["id"], "removed": True}

    @staticmethod
    def _worker_refusal(exc):
        """A Worker's 401 / 403 is about the platform's own credentials, not the user's: passed on as such, the console
        would sign the user out on its next poll."""
        if exc.status in {401, 403}:
            return Error(502, "worker_error", "执行环境拒绝了平台的请求（Worker Token 或 Gateway 登录需要更新）")
        return exc

    def steer(self, actor, rid, body):
        """A note for a running task (D46 P5b), through the Worker to KiroCrew's steer into the turn it is in: those who may
        publish the Session; audited and on the timeline. Only Kiro tasks: KiroCrew 0.10.0 steers the kiro backend alone
        (codex refuses; Claude Code would drop it); never as a follow-up, which would start a run the platform does not track."""
        require(isinstance(body, dict) and set(body) <= {"task_id", "message", "mode"}, "只接受 task_id、message 与 mode")
        message, mode = body.get("message"), body.get("mode", "interrupt")
        require(isinstance(message, str) and message.strip() and len(message) <= 4000, "补充说明须为 1–4000 字符")
        require(mode == "interrupt", "只支持 interrupt：补充说明送入正在运行的这一轮")
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(may_direct(db, actor, run), "只有会话创建者、执行人、Team owner 或管理员可以补充说明", 403, "forbidden")
            instruct = directs(db, actor, run)   # D49 R10
            require(instruct is None, instruct, 403, "boundary_owner_only")
            require(run_permissions(db, actor, run)["resume"], "Team 已归档，不能补充说明", 409, "team_archived")
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, body.get("task_id"))).fetchone()
            require(task is not None, "任务不存在", 404, "not_found")
            require(json.loads(task["spec"]).get("tool") == "kiro", "只有 Kiro 后端的任务支持补充说明（KiroCrew 0.10.0）", 409,
                    "steer_unsupported")
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone() \
                if task["current_attempt"] else None
            require(attempt is not None and attempt["status"] in {"running", "waiting_approval"} and attempt["native_id"],
                    "任务不在运行，不能补充说明", 409, "not_running")
            config, retired = self._app_worker(db, run["worker_id"])
        require(config and not retired, "会话没有可用的执行环境", 409, "worker_unavailable")
        require((config.get("capabilities") or {}).get("steer"), "执行环境的 Worker 尚未升级，暂不支持补充说明", 409,
                "steer_unsupported")
        from . import transport
        try:
            result = transport.worker_request(config, "/steer", {"run_id": rid, "attempt_id": attempt["id"],
                                                                 "message": message, "mode": mode}, timeout=15)
        except Error as exc:
            if exc.code in {"not_running", "native_unavailable"}:
                raise
            if exc.code == "worker_unavailable":
                raise Error(503, exc.code, "未送达：执行环境暂不可用，请稍后重试") from None
            raise self._worker_refusal(exc) from None
        with self.db.transaction() as db:
            audit(db, actor, "task.steer", "run", rid, {"task": task["id"], "mode": mode, "message": message[:200]})
            journal.record(db, rid, "task.steered", journal.person(actor), reason="补充说明：" + message[:500],
                           task=task["id"], mode=mode)
        return {"ok": True, "status": result.get("status"), "task_id": task["id"]}

    def answer_approval(self, actor, rid, body):
        """Answer one pending approval (D38 §4.2): the people who may publish the Session, audited."""
        require(isinstance(body, dict) and set(body) <= {"origin", "request_id", "request_mid", "instance", "decision"},
                "只接受 origin、request_id、request_mid、instance 与 decision")
        require(body.get("decision") in {"approve", "reject"}, "decision 只能是 approve 或 reject")
        require(body.get("origin") in {"native", "coordinator"}, "origin 只能是 native 或 coordinator")
        key = "request_mid" if body["origin"] == "native" else "instance"
        require(isinstance(body.get("request_id"), str) and body["request_id"] and isinstance(body.get(key), str) and body[key],
                f"{body['origin']} 的批准须带 request_id 与 {key}（取自 octopus_approvals 的列表）")
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(may_direct(db, actor, run), "只有会话创建者、执行人、Team owner 或管理员可以答复批准请求", 403, "forbidden")
            instruct = directs(db, actor, run)   # D49 R10
            require(instruct is None, instruct, 403, "boundary_owner_only")
            config, retired = self._app_worker(db, run["worker_id"])
        require(config and not retired and run["native_parent"], "会话没有可用的执行环境", 409, "approvals_unsupported")
        from . import transport
        listed = self.approvals(actor, rid)
        require(listed["supported"], "执行环境的 Worker 尚未升级，暂不支持答复批准请求", 409, "approvals_unsupported")
        pending = listed["approvals"]
        item = next((i for i in pending if (i["origin"], i["request_id"], i.get(key)) ==
                     (body.get("origin"), body.get("request_id"), body.get(key))), None)
        require(item is not None, "该批准请求已不在等待中（已被答复、过期或不属于此会话）", 409, "approval_not_pending")
        try:
            result = transport.worker_request(config, "/approvals/answer", {"run_id": rid, **body}, timeout=15)
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":
                raise Error(409, "approvals_unsupported", "执行环境的 Worker 尚未升级，暂不支持答复批准请求") from None
            if exc.code in {"native_unavailable", "worker_unavailable"}:   # nothing retries an answer: say so
                raise Error(503, exc.code, "未答复：KiroCrew Gateway 或执行环境暂不可用，请稍后重新答复") from None
            raise self._worker_refusal(exc) from None
        detail = {"origin": item["origin"], "request_id": item["request_id"], "tool": item.get("tool"),
                  "input": str(item.get("tool_input") or "")[:200], "decision": body["decision"]}
        with self.db.transaction() as db:
            audit(db, actor, "approval.answer", "run", rid, detail)
            journal.record(db, rid, "approval.answered", journal.person(actor), **detail)   # an event, not a row change
        return {**result, "ok": True}

    @staticmethod
    def _new_repository_policy(db, actor, team_id, source, policy):
        """A policy naming a new repository (D48): only for a new-source Session, with a write credential the actor may
        use for that repository."""
        from . import credentials as repo_credentials
        if policy.get("repository"):
            require(source.get("type") != "git", "会话来自仓库，发布到该仓库：不接受 publish.repository", 400)
            row = db.execute("SELECT * FROM credentials WHERE id=?", (policy["credential"],)).fetchone()
            path = f"{policy['repository']['owner']}/{policy['repository']['name']}"
            repo_credentials.check(db, actor, row, team_id, row["host"] if row else "", path)
            require(row["access"] == "write", f"凭证「{row['name']}」为只读，无法发布", 409, "credential_read_only")

    def set_publish_policy(self, actor, rid, body):
        """Switch a Session's automatic publish on or off (D47): those who may publish it. A new-source Session publishes
        automatically only to a repository it names or was already published to (D48)."""
        policy = publish_policy(body)
        with self.db.transaction() as db:
            run = run_access(db, actor, rid)
            require(may_direct(db, actor, run), "只有会话创建者、执行人、Team owner 或管理员可以设置发布", 403, "forbidden")
            snapshot, source = json.loads(run["snapshot"]), json.loads(run["source"])
            require(not general(source), NO_REPOSITORY, 409, "no_repository")
            self._new_repository_policy(db, actor, run["team_id"], source, policy)
            if policy.get("repository") and executor_of(run) != actor["id"]:   # it runs as the person it runs as (D74)
                try:
                    self._new_repository_policy(db, {"id": executor_of(run)}, run["team_id"], source, policy)
                except Error:
                    raise Error(403, "credential_forbidden", "自动发布以会话执行人（创建者，或接手的人）的身份进行："
                                "请选择执行人可以使用的凭证") from None
            require(not policy["auto"] or source.get("type") == "git" or policy.get("repository") or snapshot.get("publish_target"),
                    "新建源码的会话自动发布需要新仓库（repository）与有写权限的凭证（credential）", 409, "publish_target_missing")
            snapshot["publish_policy"] = {**policy, "source": "user"}
            journal.change(db, "runs", rid, journal.person(actor), "run.publish_policy", quiet=("snapshot",), snapshot=dumps(snapshot))
            journal.record(db, rid, "run.publish_policy", journal.person(actor), "完成后自动发布：" + ("开" if policy["auto"] else "关"),
                           auto=policy["auto"])
        return {"id": rid, "publish_policy": snapshot["publish_policy"]}

    def auto_publish_pending(self):
        """Publish, as the person they run as (their creator unless taken over, D74), the completed Sessions whose policy
        asks for it (D47) — once per version: a publish
        that cannot happen is recorded with its reason and waits for a person, the Session stays completed."""
        with self.db.read() as db:
            rows = [dict(r) for r in db.execute("SELECT id, version, snapshot, COALESCE(executor_id, owner_id) owner_id FROM runs "
                                                "WHERE status='completed' AND lifecycle='open'")]
        for row in rows:
            snapshot = json.loads(row["snapshot"])
            policy, done = snapshot.get("publish_policy") or {}, snapshot.get("publish_auto") or {}
            if not policy.get("auto") or done.get("version") == row["version"]:
                continue
            with self.db.read() as db:
                user = db.execute("SELECT id, username, admin FROM users WHERE id=? AND active=1", (row["owner_id"],)).fetchone()
            if user is None:
                continue
            actor = {**dict(user), "scopes": ["read", "execute", "manage"], "token_id": None, "client": "auto-publish"}
            try:
                record = self.publish(actor, row["id"], {k: policy[k] for k in ("branch", "title", "repository", "credential")
                                                         if policy.get(k)})
                result = {"ok": True, "url": record.get("url")}
            except Error as exc:
                result = {"ok": False, "code": exc.code, "error": str(exc)}
            with self.db.transaction() as db:
                current = db.execute("SELECT version, snapshot FROM runs WHERE id=?", (row["id"],)).fetchone()
                fresh = json.loads(current["snapshot"])
                fresh["publish_auto"] = {**result, "version": current["version"], "at": time.time()}
                journal.change(db, "runs", row["id"], journal.ENGINE, "run.publish_auto", quiet=("snapshot",), snapshot=dumps(fresh))
                journal.record(db, row["id"], "run.publish_auto", journal.ENGINE,
                               "自动发布 Draft PR" if result["ok"] else "自动发布未完成：" + result["error"], **result)

    def _publish_target(self, db, actor, run, snapshot, source, body):
        """Where a publish goes (D48): a git Session's own repository with its bound credential; a new-source Session's
        repository it was first published to, or the new one ``body`` names with the credential named for it."""
        from . import credentials as repo_credentials, publish as publishing
        if source.get("type") == "git":
            require(not {"repository", "credential"} & set(body), "会话来自仓库，发布到该仓库：不接受 repository 与 credential", 400)
            bound = (snapshot.get("credentials") or {}).get("source")
            require(bound, "会话没有绑定凭证：先在会话页绑定一枚有写权限的凭证", 409, "credential_unavailable")
            row = db.execute("SELECT * FROM credentials WHERE id=?", (bound["id"],)).fetchone()
            require(repo_credentials.bound_usable(db, row, run),
                    f"凭证「{bound['name']}」已不能用于此会话（已停用、删除、授权撤销或所有者已离开）", 409, "credential_unavailable")
            try:
                host, path = repo_credentials.repo_path(source.get("url", ""))
            except ValueError as exc:
                raise Error(409, "credential_https_only", str(exc)) from None
            require(row["host"] == host and repo_credentials.covers(json.loads(row["repos"]), path) is not None,
                    f"凭证「{row['name']}」不覆盖仓库 {path}", 409, "credential_unavailable")
            return {"row": row, "host": host, "path": path, "url": source["url"], "target": source.get("ref"), "new": None}
        recorded = snapshot.get("publish_target")
        if recorded:
            if body.get("repository"):
                wanted = publishing.check_repository(body["repository"])
                require(f"{wanted['owner']}/{wanted['name']}".lower() == recorded["path"].lower(),
                        f"此会话已发布到 {recorded['path']}，不能改到另一个仓库", 409, "publish_target_fixed")
            row = db.execute("SELECT * FROM credentials WHERE id=?",
                             (body.get("credential") or recorded["credential"]["id"],)).fetchone()
            repo_credentials.check(db, actor, row, run["team_id"], recorded["host"], recorded["path"])
            return {"row": row, "host": recorded["host"], "path": recorded["path"], "url": recorded["url"], "target": "main",
                    "new": None if recorded.get("baseline") else {"recorded": recorded}}
        require(body.get("repository") and body.get("credential"),
                "新建源码的会话发布时需要新仓库（repository: {owner, name}）与一枚有写权限的凭证（credential）", 409,
                "publish_target_missing")
        wanted = publishing.check_repository(body["repository"])
        row = db.execute("SELECT * FROM credentials WHERE id=?", (body["credential"],)).fetchone()
        path = f"{wanted['owner']}/{wanted['name']}"
        repo_credentials.check(db, actor, row, run["team_id"], row["host"] if row else "", path)
        return {"row": row, "host": row["host"], "path": path, "url": f"https://{row['host']}/{path}.git", "target": "main",
                "new": {"repository": wanted}}

    def _record_target(self, actor, rid, target, row, **fields):
        """Note the new-source Session's repository (D48 R6) so a retry or the next publish goes there again."""
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            current = snapshot.get("publish_target") or {
                "host": target["host"], "path": target["path"], "url": target["url"], "web": None, "created": False,
                "baseline": None, "credential": {"id": row["id"], "name": row["name"]}, "at": time.time(),
                "by": {"id": actor["id"], "username": actor.get("username")}}
            snapshot["publish_target"] = {**current, **fields}
            journal.change(db, "runs", rid, journal.person(actor), "run.repository", quiet=("snapshot",), snapshot=dumps(snapshot))
            return snapshot["publish_target"]

    def publish(self, actor, rid, body):
        """Push the Session's work as one branch and open (or reuse) a draft pull / merge request with the bound write
        credential (D35 §5.2): its creator, a Team owner or an admin, once its tasks have ended. A new-source Session goes
        to a repository the platform creates, or one that is empty, with the credential named for it (D48)."""
        import tempfile
        from . import credentials as repo_credentials, publish as publishing
        from .transport import worker_stream
        require(isinstance(body, dict) and set(body) <= {"branch", "title", "repository", "credential"},
                "只接受 branch、title、repository 与 credential")
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(may_direct(db, actor, run), "只有会话创建者、执行人、Team owner 或管理员可以发布", 403, "forbidden")
            require(not moving(run), "正在接手到新的执行环境，请稍候", 409, "takeover_in_progress")   # D74
            require(not general(json.loads(run["source"])), NO_REPOSITORY, 409, "no_repository")   # D65
            require(run["lifecycle"] == "open" and run["status"] in SETTLED and run["workspace"],
                    "会话执行结束后才能发布", 409, "publish_busy")
            snapshot, source = json.loads(run["snapshot"]), json.loads(run["source"])
            target = self._publish_target(db, actor, run, snapshot, source, body)
            row, host, path = target["row"], target["host"], target["path"]
            require(row["access"] == "write", f"凭证「{row['name']}」为只读，无法发布", 409, "credential_read_only")
            refused = repo_credentials.refused_address(row["host"], self.credentials.private_hosts)
            require(not refused, refused, 409, "credential_host_refused")
            verified = self._verified(db, rid)   # D37 §7: only what passed the gates and the review is pushed
            require(verified or snapshot.get("execution_contract", 1) < 3 or not db.execute(
                "SELECT 1 FROM attempts WHERE run_id=? AND evidence LIKE '%\"candidate\"%' LIMIT 1", (rid,)).fetchone(),
                "会话分支上最近的改动尚未通过门禁与审查，未发布：请续作会话，让改动通过后再发布", 409, "publish_unverified")
            config = json.loads(db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()["config"])
        branch = publishing.check_branch(body.get("branch") or "octopus/" + rid, source.get("ref"))
        title = text(body.get("title") or snapshot.get("name") or run["input"][:60], "标题", 200)
        secret = self.credentials.secret(row["id"])
        auth = {"kind": row["kind"], "host": row["host"], "username": repo_credentials.username(row["kind"]), "secret": secret}
        response = worker_stream(config, "/publish/bundle", {"run_id": rid, "message": f"KiroCrew ON：{title}（{rid}）",
                                                             **({"commit": verified["sha"]} if verified else {})}, timeout=120)
        with response, tempfile.TemporaryDirectory(dir=self.db.root, prefix=".publish-") as work:
            headers = response.headers
            commit = publishing.receive(response, int(headers.get("Content-Length") or 0), Path(work) / "repo",
                                        headers.get("X-Publish-Branch") or "", headers.get("X-Publish-Base") or "",
                                        headers.get("X-Publish-Commit") or "")
            new = target["new"]
            # D48: the first publish of a new-source Session makes its repository only once the Worker's bundle passed every
            # check — a refusal there leaves no empty repository behind (final review)
            if new and "repository" in new:
                if publishing.find_repository(row["kind"], host, path, secret) is None:
                    try:
                        web = publishing.create_repository(row["kind"], host, new["repository"], secret)
                    except Error as exc:
                        with self.db.transaction() as db:
                            self._used(db, actor, row["id"], rid, path, None, exc.code, action="repo.create")
                        raise
                    recorded = self._record_target(actor, rid, target, row, web=web, created=True)
                    with self.db.transaction() as db:
                        self._used(db, actor, row["id"], rid, path, None, "created", action="repo.create")
                        journal.record(db, rid, "run.repository", journal.person(actor), "已新建仓库 " + path, url=web, path=path,
                                       private=new["repository"]["private"], created=True)
                    new = {"recorded": recorded}
            try:
                if new:   # main starts at the baseline, in a repository that is empty (made now, or found empty)
                    publishing.push_baseline(Path(work) / "repo", target["url"], headers.get("X-Publish-Base") or "", auth)
                    if "recorded" not in new:
                        with self.db.transaction() as db:
                            journal.record(db, rid, "run.repository_used", journal.person(actor), "发布到已有的空仓库 " + path,
                                           path=path, url=f"https://{host}/{path}", created=False)
                    self._record_target(actor, rid, target, row, baseline=headers.get("X-Publish-Base"))
                pushed = publishing.push(Path(work) / "repo", target["url"], branch, commit, auth)
            except Error as exc:   # the credential was used all the same
                with self.db.transaction() as db:
                    self._used(db, actor, row["id"], rid, path, branch, exc.code)
                raise
        # From here the branch is on the remote: what follows is recorded whatever the pull / merge request does.
        failure, request = None, {"url": None, "number": None, "kind": None, "reused": False}
        try:
            request = publishing.pull_request(row["kind"], host, path, branch, target["target"], title,
                                              f"由 KiroCrew ON 会话 {rid} 发布。", secret)
        except Error as exc:
            failure = exc
        proposal = self._propose_knowledge(rid, source, row["kind"], host, path, secret)   # D52, never the publish's failure
        record = {"branch": branch, "commit": commit, "url": request["url"], "number": request["number"],
                  "kind": request["kind"], "reused": request["reused"], "created_branch": pushed["created"], "at": time.time(),
                  "by": {"id": actor["id"], "username": actor.get("username")}, **({"request_error": str(failure)} if failure else {}),
                  **({"knowledge": proposal} if proposal else {})}
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            if source.get("type") != "git":   # D48: where the new project went
                record["repository"] = (snapshot.get("publish_target") or {}).get("web") or f"https://{host}/{path}"
            snapshot["publish"] = record
            if proposal:   # D52: the repository's knowledge proposal of this publish
                snapshot["knowledge_proposal"] = {**proposal, "at": time.time()}
            journal.change(db, "runs", rid, journal.person(actor), "run.published",
                           event={**{k: record[k] for k in ("branch", "commit", "url", "number", "reused")},
                                  "request": record["kind"], **({"error": record["request_error"]} if failure else {}),
                                  **({"repository": record["repository"]} if record.get("repository") else {})},
                           snapshot=dumps(snapshot))
            self._used(db, actor, row["id"], rid, path, branch, "pushed" if failure else "published")
            if proposal:
                from .knowledge_repo import BRANCH
                summary = {k: proposal[k] for k in ("result", "items", "url", "number", "code") if k in proposal}
                journal.record(db, rid, "run.knowledge_proposed", journal.person(actor),
                               {"proposed": f"已提到仓库知识 PR #{proposal.get('number')}", "unchanged": "仓库知识已含这些条目",
                                "failed": "仓库知识未提出：" + str(proposal.get("reason", ""))[:160]}[proposal["result"]], **summary)
                audit(db, actor, "knowledge.proposed", "run", rid, summary)
                # read with the write credential even when nothing new was proposed (D35 §6: every use is audited)
                self._used(db, actor, row["id"], rid, path, BRANCH, proposal["result"], action="knowledge.propose")
        if failure:
            what = "Draft PR" if row["kind"] == "github_pat" else "Draft MR"
            raise Error(failure.status, failure.code, f"分支 {branch} 已推送，但 {what} 未创建：{failure}。处理后再次发布即可（沿用该分支）")
        return record

    def _propose_knowledge(self, rid, source, kind, host, path, secret):
        """D52: this Session's Knowledge items (written, superseded or kept, not removed) proposed to the repository's
        reviewed knowledge; None without items. A refusal is recorded, never raised: the code's request stands."""
        from . import knowledge_repo
        with self.db.read() as db:
            items = [{"rule": r["rule"], **({"avoid": r["avoid"]} if r["avoid"] else {})} for r in db.execute(
                "SELECT rule, avoid FROM knowledge WHERE run_id=? AND removed IS NULL AND result IN ('written','superseded','kept') "
                "ORDER BY created, rowid", (rid,))]
        if not items or source.get("type") != "git":
            return None
        try:
            return knowledge_repo.propose(kind, host, path, items, rid, secret)
        except Error as exc:
            return {"result": "failed", "code": exc.code, "reason": str(exc)[:300]}
        except Exception as exc:   # an unexpected answer from the host
            return {"result": "failed", "code": "publish_failed", "reason": type(exc).__name__}

    @staticmethod
    def _used(db, actor, credential, rid, repo, branch, result, action=None):
        """Every use of a write credential is audited, whatever came of it (D35 §6); creating a repository too (D48)."""
        db.execute("UPDATE credentials SET last_used=? WHERE id=?", (time.time(), credential))
        audit(db, actor, "credential.use", "credential", credential,
              {"run_id": rid, "repo": repo, "access": "write", "branch": branch, "result": result,
               **({"action": action} if action else {})})

    def rebind(self, actor, rid, body):
        """Change or remove a Session's source credential (spec §4): its creator, a Team owner or an admin, with a
        credential they may use for this repository; removal also by the owner of the personal credential bound."""
        from . import credentials as repo_credentials
        require(isinstance(body, dict) and "source" in body and (body["source"] is None or isinstance(body["source"], str)),
                "需要 source（凭证 ID，或 null 表示撤下）")
        with self.db.transaction() as db:
            run = run_access(db, actor, rid)
            snapshot = json.loads(run["snapshot"])
            current = (snapshot.get("credentials") or {}).get("source")
            team_owner = bool(run["team_id"]) and db.execute(
                "SELECT 1 FROM members WHERE team_id=? AND user_id=? AND role='owner'", (run["team_id"], actor["id"])).fetchone()
            own_removal = body["source"] is None and current and current["scope"] == "user" and \
                (current.get("owner") or {}).get("id") == actor["id"]
            require(run["owner_id"] == actor["id"] or executor_of(run) == actor["id"] or team_owner or elevated(actor)
                    or own_removal, "只有会话创建者、执行人、Team owner 或管理员可以更换凭证", 403, "forbidden")
            require(not moving(run), "正在接手到新的执行环境，请稍候", 409, "takeover_in_progress")
            summary = lambda value: {k: value[k] for k in ("id", "name", "scope", "hint")} if value else None
            bound = None
            if body["source"]:
                source = json.loads(run["source"])
                try:
                    host, path = repo_credentials.repo_path(source.get("url", ""))
                except ValueError as exc:
                    raise Error(409, "credential_https_only", str(exc)) from None
                row = db.execute("SELECT * FROM credentials WHERE id=?", (body["source"],)).fetchone()
                repo_credentials.check(db, actor, row, run["team_id"], host, path)
                bound = repo_credentials.binding(db, row, actor)
            # A removal is remembered: the next preparation stops instead of cloning without the credential.
            snapshot["credentials"] = {"source": bound} if bound else (
                {"source": None, "removed": {**summary(current), "by": {"id": actor["id"], "username": actor.get("username")},
                                            "at": time.time()}} if current else {})
            journal.change(db, "runs", rid, journal.person(actor), "run.credentials",
                           event={"source": [summary(current), summary(bound)]}, snapshot=dumps(snapshot))
        return {"source": bound}

    def app(self, actor, rid):
        """The Session's remote preview as the Worker reports it, with the last lines of its log."""
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            row = db.execute("SELECT * FROM apps WHERE run_id=?", (rid,)).fetchone()
            config, retired = self._app_worker(db, run["worker_id"])
            users = dict(db.execute("SELECT id,username FROM users").fetchall())
        view = {"status": "stopped", "port": None, "error": None, "log": [], "started": None, "started_by": None,
                "last_access": None, "idle_stop_at": None, "openable": False}
        if retired and row and row["status"] in remote_preview.LIVE:
            self._app_stopped(rid, journal.ENGINE, "worker_retired")
            with self.db.read() as db:
                row = db.execute("SELECT * FROM apps WHERE run_id=?", (rid,)).fetchone()
        elif config and not retired:
            try:
                answer = self._app_call(config, rid, "status")
                row = self._app_sync(rid, row, answer)
                view["log"] = answer.get("log") or []
            except Error as exc:
                if exc.code == "app_unsupported":
                    return {**view, "status": "unsupported", "error": str(exc)}
                view["worker_error"] = str(exc)
        if row:
            view.update(status=row["status"], port=row["port"], error=row["error"], started=row["started"],
                        last_access=row["last_access"], openable=row["status"] == "running",
                        started_by={"id": row["started_by"], "username": users.get(row["started_by"])})
            if row["status"] in remote_preview.LIVE:
                view["idle_stop_at"] = (row["last_access"] or row["started"]) + remote_preview.IDLE
        return view

    def app_start(self, actor, rid):
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(not moving(run), "正在接手到新的执行环境，请稍候", 409, "takeover_in_progress")   # D74
            require(run_permissions(db, actor, run)["resume"], "Team 已归档，不能启动预览", 409, "team_archived")
            require(run["lifecycle"] == "open", "会话已关闭，请先重新打开", 409, "invalid_state")
            require(run["status"] in SETTLED, "会话正在执行任务，完成后再启动预览", 409, "app_busy")
            require(run["worker_id"] and run["workspace"], "此会话尚未准备源码，无法启动预览", 409, "app_unavailable")
            row = db.execute("SELECT * FROM apps WHERE run_id=?", (rid,)).fetchone()
            config, retired = self._app_worker(db, run["worker_id"])
        require(not retired, "执行环境已退役，不能启动预览", 409, "retired")
        probe = self._app_call(config, rid, "status")
        self._stale(run["worker_id"], probe.get("instance"), rid)
        row = self._app_sync(rid, row, probe)
        if row and row["status"] in remote_preview.LIVE:
            return self.app(actor, rid)
        remote_preview.upstream(config, 0, probe.get("via"))   # a Worker host without a preview port: refuse first
        with self.db.read() as db:
            live = db.execute(f"SELECT COUNT(*) FROM apps WHERE worker_id=? AND run_id!=? AND status IN "
                              f"({','.join('?' * len(remote_preview.LIVE))})",
                              (run["worker_id"], rid, *remote_preview.LIVE)).fetchone()[0]
        require(live < remote_preview.LIMIT, f"此执行环境已有 {remote_preview.LIMIT} 个远程预览在运行，请先停止其中一个",
                409, "app_limit")
        answer = self._app_call(config, rid, "start")
        target = {**remote_preview.upstream(config, answer["port"], answer.get("via")), "instance": answer.get("instance")}
        now = time.time()
        with self.db.transaction() as db:
            db.execute("INSERT INTO apps(run_id,worker_id,status,port,path,upstream,error,started_by,started,last_access,stopped)"
                       " VALUES(?,?,?,?,?,?,NULL,?,?,?,NULL) ON CONFLICT(run_id) DO UPDATE SET worker_id=excluded.worker_id,"
                       " status=excluded.status,port=excluded.port,path=excluded.path,upstream=excluded.upstream,error=NULL,"
                       " started_by=excluded.started_by,started=excluded.started,last_access=excluded.last_access,stopped=NULL",
                       (rid, run["worker_id"], answer["status"], answer["port"], remote_preview.entry_path(answer.get("path")),
                        dumps(target), actor["id"], now, now))
            journal.record(db, rid, "app.started", journal.person(actor), port=answer["port"], source=answer.get("source"))
        return self.app(actor, rid)

    def app_stop(self, actor, rid):
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            config, retired = self._app_worker(db, run["worker_id"])
        if config and not retired:
            self._app_call(config, rid, "stop")
        self._app_stopped(rid, journal.person(actor), "user")
        return {"status": "stopped"}

    def _app_stopped(self, rid, who, reason):
        with self.db.transaction() as db:
            row = db.execute("SELECT status FROM apps WHERE run_id=?", (rid,)).fetchone()
            if row and row["status"] in remote_preview.LIVE:
                db.execute("UPDATE apps SET status='stopped',stopped=? WHERE run_id=?", (time.time(), rid))
                journal.record(db, rid, "app.stopped", who, reason=reason)

    def _stop_app(self, rid, reason):
        """Best effort, for the platform's own reasons (the Session closed or continues, nobody used it): a Worker that
        cannot be reached keeps the record live, and the next sweep tries again."""
        with self.db.read() as db:
            row = db.execute("SELECT a.status,w.config,w.retired FROM apps a JOIN workers w ON w.id=a.worker_id "
                             "WHERE a.run_id=?", (rid,)).fetchone()
        if not row or row["status"] not in remote_preview.LIVE:
            return
        if not row["retired"]:
            try:
                self._app_call(json.loads(row["config"]), rid, "stop")
            except Error as exc:
                logging.warning("Remote preview of %s not stopped (%s)", rid, exc.code)
                return
        self._app_stopped(rid, journal.ENGINE, reason if not row["retired"] else "worker_retired")

    def app_ready(self, actor, rid):
        """Before an opening link is issued: the preview must run (refreshed from the Worker)."""
        status = self.app(actor, rid)["status"]
        require(status == "running", "预览尚未就绪，请稍后再打开" if status in {"installing", "starting"}
                else "远程预览未运行，请先启动", 409, "app_not_ready")

    def app_auth(self, rid, holder, now=None):
        """For auth_request on every preview request: the holder may still read the Session, the Session is open and
        not closing, and its app runs."""
        now = now or time.time()
        with self.db.read() as db:
            if str(holder).startswith("pshare-"):   # D75: a demo share is the holder, for anyone with its link
                from . import public_shares as public
                sid = str(holder).split(":", 1)[0]
                share = db.execute("SELECT token, code FROM public_shares WHERE id=? AND run_id=? AND kind='app'", (sid, rid)).fetchone()
                try:   # the code it was opened with: a new code ends the demo's cookie as well
                    require(share is not None and holder == public.holder(sid, share["code"]), "预览不可用", 404, "not_found")
                    run = public.active(db, share["token"], now)
                except Error:
                    raise Error(403, "app_forbidden", "此公开分享已失效") from None
            else:
                user = db.execute("SELECT id,admin FROM users WHERE id=? AND active=1", (holder,)).fetchone()
                try:
                    require(user is not None, "预览不可用", 404, "not_found")
                    run = run_access(db, {"id": user["id"], "admin": user["admin"],
                                          "scopes": ["read"] + (["admin"] if user["admin"] else [])}, rid, read=True)
                except Error:
                    raise Error(403, "app_forbidden", "无权查看此会话的远程预览") from None
            row = db.execute("SELECT * FROM apps WHERE run_id=?", (rid,)).fetchone()
        require(row is not None and row["status"] == "running" and run["lifecycle"] == "open"
                and run["desired"] not in {"close", "delete"}, "远程预览已停止", 403, "app_stopped")
        if now - (row["last_access"] or 0) >= remote_preview.TOUCH:
            try:   # best effort: a busy database must not fail the request it rides on
                with self.db.transaction() as db:
                    db.execute("UPDATE apps SET last_access=? WHERE run_id=?", (now, rid))
            except sqlite3.OperationalError:
                pass
        return json.loads(row["upstream"])

    def app_enter(self, rid, holder, nonce, now=None):
        """Follow an opening link once: checked like every request, then its nonce is spent until it expires."""
        now = now or time.time()
        self.app_auth(rid, holder, now)
        with self._opened_lock:
            for used, expires in list(self._opened.items()):
                if expires < now:
                    del self._opened[used]
            require(nonce not in self._opened, "此预览链接已使用过，请在控制台重新打开远程预览", 403, "app_link_used")
            self._opened[nonce] = now + remote_preview.TICKET_TTL
        with self.db.read() as db:
            return remote_preview.entry_path(db.execute("SELECT path FROM apps WHERE run_id=?", (rid,)).fetchone()["path"])

    def sweep_apps(self, now=None):
        """Every minute, from serve: stop previews nobody used for 30 minutes, those of Sessions no longer open and of
        retired Workers, and follow what the Workers report (a Worker that restarted has none of them any more)."""
        now = now or time.time()
        with self.db.read() as db:
            rows = db.execute(f"SELECT a.*,r.lifecycle,r.desired,w.config,w.retired FROM apps a JOIN runs r ON r.id=a.run_id "
                              f"JOIN workers w ON w.id=a.worker_id WHERE a.status IN ({','.join('?' * len(remote_preview.LIVE))})",
                              remote_preview.LIVE).fetchall()
        for row in rows:
            if row["retired"]:
                self._app_stopped(row["run_id"], journal.ENGINE, "worker_retired")
            elif (row["last_access"] or row["started"] or 0) < now - remote_preview.IDLE:
                self._stop_app(row["run_id"], "idle")
            elif row["lifecycle"] != "open" or row["desired"] in {"close", "delete"}:
                self._stop_app(row["run_id"], "session")
            else:
                try:
                    self._app_sync(row["run_id"], row, self._app_call(json.loads(row["config"]), row["run_id"], "status"))
                except Error:
                    pass   # unreachable now: asked again next minute

    def preview_file(self, rid, relative, holder):
        """One project file of a run for the holder of a valid preview ticket (checked by the server), who must still
        be allowed to read the run: revoked access stops an issued link at once (B6)."""
        import base64
        with self.db.read() as db:
            run = db.execute("SELECT worker_id FROM runs WHERE id=?", (rid,)).fetchone()
            require(run is not None and run["worker_id"], "预览不可用", 404, "not_found")
            user = db.execute("SELECT id,admin FROM users WHERE id=? AND active=1", (holder,)).fetchone()
            try:
                require(user is not None, "预览不可用", 404, "not_found")
                run_access(db, {"id": user["id"], "admin": user["admin"],
                                "scopes": ["read"] + (["admin"] if user["admin"] else [])}, rid, read=True)
            except Error:
                raise Error(403, "preview_forbidden", "预览权限已撤销，请在控制台重新打开") from None
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        from .transport import worker_request
        result = worker_request(json.loads(worker["config"]), "/files", {"run_id": rid, "path": relative})
        return base64.b64decode(result["content"])

    def mark_run(self, actor, rid, body):
        """The actor's own pin and star on a Session they can read (owner 2026-10-22): ``{pinned?, starred?}``."""
        require(isinstance(body, dict) and body and set(body) <= {"pinned", "starred"}
                and all(isinstance(v, bool) for v in body.values()), "须写 pinned 或 starred，取值为 true 或 false")
        with self.db.transaction() as db:
            run_access(db, actor, rid, admin=False, read=True)
            row = db.execute("SELECT pinned,starred FROM run_marks WHERE user_id=? AND run_id=?", (actor["id"], rid)).fetchone()
            marks = {"pinned": bool(row["pinned"]) if row else False, "starred": bool(row["starred"]) if row else False, **body}
            db.execute("INSERT INTO run_marks(user_id,run_id,pinned,starred,updated) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(user_id,run_id) DO UPDATE SET pinned=excluded.pinned, starred=excluded.starred, "
                       "updated=excluded.updated", (actor["id"], rid, int(marks["pinned"]), int(marks["starred"]), time.time()))
        return marks

    def runs(self, actor, all=False, status=None, q=None, limit=None, view=None, page=False, cursor=None, shared=False,
             pinned=False, starred=False, **filters):
        """The actor's own and Team Sessions, newest first; with all=True an elevated admin gets every Session (B5).
        Filtered on the server (M3-5): ``status`` (one or a list), ``q`` (full text, every word) and ``limit``, and D56's
        ``lifecycle``, ``space``, ``repo``, ``branch``, ``stage``, ``tool``, ``client``, ``submitter``, ``since``, ``until``
        (listing.py); ``view="list"`` leaves out what only the session page shows; ``page=True`` answers
        ``{items, next, total}`` from ``cursor``, without it a list as before."""
        from . import listing
        require(view in (None, "list"), "view 只能是 list")
        require(limit is None or type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        found = listing.criteria(status=status, q=q, cursor=cursor, **filters)
        size = (limit or 50) if page else limit
        oversee = all and elevated(actor)
        sql, params = listing.sql(found)
        with self.db.read() as db:
            items, total, more = [], 0, False
            marks = {r["run_id"]: {"pinned": bool(r["pinned"]), "starred": bool(r["starred"])}
                     for r in db.execute("SELECT run_id,pinned,starred FROM run_marks WHERE user_id=?", (actor["id"],))}
            for row in db.execute(sql, params):
                mine = marks.get(row["id"], {"pinned": False, "starred": False})
                if pinned and not mine["pinned"] or starred and not mine["starred"]:
                    continue
                try:
                    if shared:   # D64 「共享给我」: only what a share brings
                        run = run_access(db, actor, row["id"], admin=False, read=True)
                        if access_of(db, actor, run)["via"] != "share":
                            continue
                    else:
                        run = run_access(db, actor, row["id"], admin=oversee, shares=False)
                except Error:
                    continue
                if not listing.matches(db, run, found):
                    continue
                total += 1
                if found["after"] and (run["created"], run["id"]) >= found["after"]:
                    continue   # on an earlier page
                if size and len(items) >= size:
                    more = True
                    if not page:
                        break
                    continue
                value = {**self._view(db, run, actor, detail=False, brief=view == "list"), "marks": mine}
                items.append(listed(value) if view == "list" else value)
            if not page:
                return items
            last = items[-1] if items and more else None
            return {"items": items, "next": listing.cursor(last["created"], last["id"]) if last else None, "total": total}

    def requests(self, actor, status=None, q=None, client=None, limit=None):
        """The actor's Requests (D55), newest first: those with a Session they can read, filtered like Sessions."""
        require(status is None or status in RUN_STATES, "status 不是会话的状态")
        require(q is None or isinstance(q, str) and 0 < len(q) <= 200, "q 须为 1–200 个字符")
        require(client is None or client in submissions.CLIENTS, "client 不是已知的客户端")
        require(limit is None or type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        needle, sql, params = (q or "").lower(), "SELECT * FROM requests", []
        if client:
            sql, params = sql + " WHERE client=?", [client]
        with self.db.read() as db:
            result = []
            for row in db.execute(sql + " ORDER BY created DESC", params):
                if needle and needle not in (row["id"] + " " + row["input"]).lower():
                    continue
                runs = submissions.sessions(db, row["id"])
                if not any(self._readable(db, actor, r["id"], admin=False) for r in runs):
                    continue
                if status and (not runs or runs[0]["status"] != status):
                    continue
                result.append(submissions.view(db, row, runs))
                if limit and len(result) >= limit:
                    break
            return result

    def request(self, actor, request_id):
        """One Request with its Sessions, for whoever can read one of them (404 otherwise, never revealing it exists)."""
        require(isinstance(request_id, str) and len(request_id) <= 100, "Request 不存在或无权限", 404, "not_found")
        with self.db.read() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            runs = submissions.sessions(db, request_id) if row else []
            require(any(self._readable(db, actor, r["id"]) for r in runs), "Request 不存在或无权限", 404, "not_found")
            value = submissions.view(db, row, runs)
            oversight = not any(self._readable(db, actor, r["id"], admin=False) for r in runs)
        if oversight:   # an administrator reading someone else's submission, as for their Sessions (A2)
            self._audit_oversight(actor, "request.view", "request", request_id)
        return value

    def policies(self, actor, team_id=None):
        """The actor's own rules and those of their Teams (D55), or one Team's; a non-member learns nothing of a Team."""
        with self.db.read() as db:
            if team_id:
                roles = {team_id: self._team_role(db, actor, team_id)}
                rows = db.execute("SELECT * FROM policies WHERE team_id=? ORDER BY kind, created", (team_id,)).fetchall()
            else:
                roles = {r["team_id"]: r["role"] for r in db.execute("SELECT team_id,role FROM members WHERE user_id=?", (actor["id"],))}
                rows = db.execute("SELECT * FROM policies WHERE owner_id=? ORDER BY kind, created", (actor["id"],)).fetchall()
                rows += [r for r in db.execute("SELECT * FROM policies WHERE team_id IS NOT NULL ORDER BY kind, created")
                         if r["team_id"] in roles or elevated(actor)]   # an administrator maintains every Team's
            names = {r["id"]: r["name"] for r in db.execute("SELECT id,name FROM teams")}
            return [policies.view(r, names.get(r["team_id"]),
                                  not r["team_id"] or elevated(actor) or roles.get(r["team_id"]) == "owner") for r in rows]

    def create_policy(self, actor, body):
        """A rule for one's own Sessions, or — for a Team's owner or an administrator — a Team's (D55); audited."""
        require(isinstance(body, dict) and set(body) <= {"kind", "scope", "team_id", "rule"}, "规则须为 {kind, scope, team_id?, rule}")
        value = policies.rule(body.get("kind"), body.get("rule"))
        require(body.get("scope") in ("personal", "team"), "scope 须为 personal 或 team")
        team = body.get("team_id") if body["scope"] == "team" else None
        require(body["scope"] == "personal" and "team_id" not in body or isinstance(team, str) and team,
                "Team 的规则需要 team_id；个人规则不带 team_id")
        with self.db.transaction() as db:
            if team:
                require(self._team_role(db, actor, team) == "owner" or elevated(actor), "只有 Team owner 或平台管理员可以维护 Team 的规则",
                        403, "forbidden")
                active_team(db, team)
            pid, now = uid("policy"), time.time()
            db.execute("INSERT INTO policies(id,kind,owner_id,team_id,rule,created_by,created) VALUES(?,?,?,?,?,?,?)",
                       (pid, body["kind"], None if team else actor["id"], team, dumps(value), actor["id"], now))
            audit(db, actor, "policy.create", "policy", pid, {"kind": body["kind"], "scope": body["scope"], "team_id": team,
                                                               "rule": value})
            row = db.execute("SELECT * FROM policies WHERE id=?", (pid,)).fetchone()
            name = db.execute("SELECT name FROM teams WHERE id=?", (team,)).fetchone() if team else None
            return policies.view(row, name["name"] if name else None, True)

    def delete_policy(self, actor, policy_id):
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
            require(row is not None and (row["team_id"] or row["owner_id"] == actor["id"]), "规则不存在或无权限", 404, "not_found")
            if row["team_id"]:
                require(self._team_role(db, actor, row["team_id"]) == "owner" or elevated(actor),
                        "只有 Team owner 或平台管理员可以维护 Team 的规则", 403, "forbidden")
            db.execute("DELETE FROM policies WHERE id=?", (policy_id,))
            audit(db, actor, "policy.delete", "policy", policy_id, {"kind": row["kind"], "team_id": row["team_id"],
                                                                     "rule": json.loads(row["rule"])})
            return {"id": policy_id, "deleted": True}

    def run_shares(self, actor, rid):
        """Who a Session is shared with (D64): for those who may share it."""
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以管理共享", 403, "forbidden")
            from . import shares
            return shares.listed(db, rid)

    def share_run(self, actor, rid, body):
        """Share a Session with a person, a Team or the whole company (viewer only); sharing again changes the role."""
        from . import shares
        with self.db.transaction() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以共享", 403, "forbidden")
            if run["team_id"]:
                active_team(db, run["team_id"])
            require(run["lifecycle"] != "deleted", "已删除的会话不能共享", 409, "invalid_state")
            kind, subject, role, name = shares.parse(db, actor, body)
            if kind == "user":   # one who already reaches it — its creator, or a member of its Team — needs no share
                member = run["team_id"] and db.execute("SELECT 1 FROM members WHERE team_id=? AND user_id=?",
                                                       (run["team_id"], subject)).fetchone()
                require(not (member or not run["team_id"] and subject == run["owner_id"]),
                        f"不必共享给 {name}：已能访问此会话（创建者或所在 Team 的成员）")
            require(not (kind == "team" and subject == run["team_id"]), "不必共享给会话所在的 Team：它的成员本来就能访问")
            sid = shares.store(db, actor, rid, kind, subject, role)
            audit(db, actor, "share.create", "run", rid, {"share": sid, "kind": kind, "subject": subject or None, "name": name,
                                                          "role": role})
            return shares.view(db, db.execute("SELECT * FROM shares WHERE id=?", (sid,)).fetchone())

    def unshare_run(self, actor, rid, sid):
        with self.db.transaction() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以管理共享", 403, "forbidden")
            row = db.execute("SELECT * FROM shares WHERE id=? AND run_id=?", (sid, rid)).fetchone()
            require(row is not None, "共享不存在", 404, "not_found")
            db.execute("DELETE FROM shares WHERE id=?", (sid,))
            audit(db, actor, "share.delete", "run", rid, {"share": sid, "kind": row["kind"], "subject": row["subject"] or None,
                                                          "role": row["role"]})
            return {"id": sid, "deleted": True}

    # D75: public shares — the sharer's side (who controls the Session, as for D64 shares) and the visitor's (no account)
    public_base = None   # set by the server: where a Session's public links live (D75)

    def _public_base(self, rid):
        """Where a Session's public links live: its own preview origin, else the shared one; None when not configured."""
        return self.public_base(rid) if self.public_base else None

    def public_shares(self, actor, rid):
        from . import public_shares as public
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以管理公开分享", 403, "forbidden")
            rows = db.execute("SELECT * FROM public_shares WHERE run_id=? AND (expires IS NULL OR expires>?) ORDER BY created DESC",
                              (rid, time.time())).fetchall()
        self.audit_read(actor, rid, "public_shares")   # an administrator reading another's codes is recorded
        return {"shares": [public.view(row, self._public_base(rid)) for row in rows],
                "available": self.public_base is None or bool(self._public_base(rid))}

    def create_public_share(self, actor, rid, body):
        from . import public_shares as public
        with self.db.transaction() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以公开分享", 403, "forbidden")
            if run["team_id"]:
                active_team(db, run["team_id"])
            require(run["lifecycle"] == "open", "会话已关闭或删除，不能公开分享", 409, "invalid_state")
            require(run["worker_id"], "会话尚未分配执行环境，没有可分享的内容", 409, "no_preview")
            require(self.public_base is None or self._public_base(rid),
                    "平台尚未配置预览域名，不能公开分享：请平台管理员配置远程预览", 409, "share_unavailable")
            wanted = public.parse(body)
        if wanted["kind"] == "pages" and not wanted["entry"]:   # settled now: a visit never walks the project
            found = self._public_worker({"worker_id": run["worker_id"], "run_id": rid}, "")
            require(found.get("entry"), "会话产出里还没有可以分享的页面（HTML）", 409, "no_pages")
            body = {**body, "entry": found["entry"]}
        with self.db.transaction() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run) and run["lifecycle"] == "open", "会话已关闭或无权公开分享", 409, "invalid_state")
            row = public.create(db, actor, run, body)
            audit(db, actor, "public_share.create", "run", rid, {"share": row["id"], "kind": row["kind"], "mode": row["mode"],
                                                                  "expires": row["expires"]})
        return public.view(row, self._public_base(rid))

    def delete_public_share(self, actor, rid, sid):
        with self.db.transaction() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以撤销公开分享", 403, "forbidden")
            row = db.execute("SELECT * FROM public_shares WHERE id=? AND run_id=?", (sid, rid)).fetchone()
            require(row is not None, "公开分享不存在", 404, "not_found")
            db.execute("DELETE FROM public_shares WHERE id=?", (sid,))
            audit(db, actor, "public_share.delete", "run", rid, {"share": sid, "kind": row["kind"], "views": row["views"]})
        return {"id": sid, "deleted": True}

    def public_share_code(self, actor, rid, sid):
        """A new access code: every browser the old one opened asks again, and a lock ends."""
        from . import public_shares as public
        with self.db.transaction() as db:
            run = run_access(db, actor, rid, read=True)
            require(may_publish(db, actor, run), "只有会话的创建者、Team owner 或平台管理员可以重新生成授权码", 403, "forbidden")
            row = db.execute("SELECT * FROM public_shares WHERE id=? AND run_id=?", (sid, rid)).fetchone()
            require(row is not None, "公开分享不存在", 404, "not_found")
            require(row["mode"] == "code", "此分享无需授权码", 409, "share_open")
            db.execute("UPDATE public_shares SET code=?, failures=0, failed_since=NULL, locked_until=NULL, total_failures=0, "
                       "code_retired=NULL WHERE id=?", (public.new_code(), sid))
            audit(db, actor, "public_share.code", "run", rid, {"share": sid})
            row = db.execute("SELECT * FROM public_shares WHERE id=?", (sid,)).fetchone()
        return public.view(row, self._public_base(rid))

    def public_share_check(self, token, code, address, now=None):
        """A visitor's access code: the share it opens, or why not (wrong, too many tries, locked)."""
        from . import public_shares as public
        now = now or time.time()
        with self.db.read() as db:
            share = public.active(db, token, now)
        public.admit(share, self._share_tries, address, now)   # refusals here write nothing
        if public.right(share, code):
            if share["failures"]:
                with self.db.transaction() as db:
                    db.execute("UPDATE public_shares SET failures=0, failed_since=NULL WHERE id=?", (share["id"],))
            return share
        self._share_tries.failed(token, address, now)
        with self.db.transaction() as db:
            public.failed(db, share["id"], now)
        raise Error(401, "share_code_wrong", "授权码不正确")

    def public_share_visit(self, token, address, granted=False, now=None):
        """A visitor opening a link: the share, with the page it opens on (pages) — the code asked first unless it is open
        or this browser already gave it. Counted as one view."""
        from . import public_shares as public
        with self.db.read() as db:
            share = public.active(db, token, now)
        if share["mode"] == "code" and not granted:
            raise Error(401, "share_code_required", "需要授权码")
        self._share_viewed(share["id"], now or time.time())
        return {"id": share["id"], "run_id": share["run_id"], "kind": share["kind"], "mode": share["mode"],
                "entry": share["entry"], "code": share["code"]}

    def _share_viewed(self, sid, now):
        """Views counted in memory and written at most every 30 seconds a share: visitors cannot hold the write lock."""
        from . import public_shares as public
        with self._opened_lock:
            pending, written = self._share_views.get(sid, (0, 0.0))
            pending += 1
            if now - written < public.VIEWS_EVERY:
                self._share_views[sid] = (pending, written)
                return
            self._share_views[sid] = (0, now)
        try:
            with self.db.transaction() as db:
                db.execute("UPDATE public_shares SET views=views+?, last_viewed=? WHERE id=?", (pending, now, sid))
        except sqlite3.OperationalError:
            pass   # a busy database must not keep a visitor out

    def public_share_file(self, token, relative):
        """One file of a pages share, read from the Session's workspace as 「预览产出」 reads it."""
        import base64
        from . import public_shares as public
        with self.db.read() as db:
            share = public.active(db, token)
        require(share["kind"] == "pages" and public.web_file(relative), "页面不存在", 404, "not_found")
        data = base64.b64decode(self._public_worker(share, relative)["content"])
        from .sources import find_secrets   # what looks like a credential never leaves through a public link
        require(not find_secrets(data, []), "页面不存在", 404, "not_found")
        return data

    def public_share_of(self, token):
        """The share a token names while it may be seen (the server checks the visitor's grant against its code)."""
        from . import public_shares as public
        with self.db.read() as db:
            return public.active(db, token)

    def _public_worker(self, share, relative):
        from .transport import worker_request
        with self.db.read() as db:
            worker = db.execute("SELECT config, retired FROM workers WHERE id=?", (share["worker_id"],)).fetchone()
        require(worker is not None and not worker["retired"], "页面暂不可用", 404, "not_found")
        return worker_request(json.loads(worker["config"]), "/files", {"run_id": share["run_id"], "path": relative})

    def allocate_boundary(self, actor, body):
        """A new boundary's identity and OS user id (MVP 0.2.0, S9), for ``deploy.sh boundary add``: an administrator's."""
        from . import boundaries
        require(elevated(actor), "只有平台管理员可以分配执行边界", 403, "forbidden")
        require(isinstance(body, dict) and set(body) <= {"owner", "name"} and isinstance(body.get("owner"), str),
                "需要 owner（平台用户名），可选 name")
        with self.db.transaction() as db:
            user = db.execute("SELECT id FROM users WHERE username=?", (body["owner"].lower(),)).fetchone()
            require(user is not None, f"平台上没有用户 {body['owner']}", 404, "not_found")
            row = boundaries.allocate(db, user["id"], body.get("name"))
            audit(db, actor, "boundary.allocate", "boundary", row["id"], {"uid": row["uid"], "name": row["name"],
                                                                           "owner": user["id"]})
        return row

    def issue_enrollment(self, actor, body, platform):
        """A one-time code that registers one Worker where the administrator binds it (N §3–§5); the code and the command
        to run on the Worker host are shown only in this answer."""
        from . import enrollments
        require(elevated(actor), "只有平台管理员可以签发接入码", 403, "forbidden")
        require(isinstance(body, dict) and set(body) <= {"name", "owner_id", "team_id", "spare", "expires_in"},
                "需要 name，以及 owner_id、team_id、spare 三者之一，可选 expires_in")
        spare = body.get("spare") is True
        require(sum((bool(body.get("owner_id")), bool(body.get("team_id")), spare)) == 1,
                "接入码须唯一绑定一个用户、一个 Team，或空闲执行环境（spare）")
        kind, binding = ("spare", None) if spare else ("team", body["team_id"]) if body.get("team_id") else ("user", body["owner_id"])
        worker_name = name(body.get("name"))
        with self.db.transaction() as db:
            if kind == "user":
                require(db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (binding,)).fetchone(), "用户不存在", 404,
                        "not_found")
            elif kind == "team":
                require(db.execute("SELECT 1 FROM teams WHERE id=?", (binding,)).fetchone(), "Team 不存在", 404, "not_found")
                require(team_environment(db, binding) is None, "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
            issued = enrollments.issue(db, actor["id"], worker_name, kind, binding, body.get("expires_in"))
            audit(db, actor, "worker.enrollment", "enrollment", issued["id"], {"binding_kind": kind, "binding_id": binding,
                                                                               "name": worker_name, "expires": issued["expires"]})
        # The code is pasted at the prompt, so it stays out of ps and the shell history (scripts set OCTOPUS_ENROLL_CODE)
        command = ("PYTHONPATH=/opt/kirocrew-on/current /opt/kirocrew-on/venv/bin/python -m octopus "
                   f"--config /etc/kirocrew-on/boundary-<边界名>.toml worker enroll --code-stdin --platform {platform} "
                   "--url https://<Worker 私网地址>:8943")   # as installed on a host, run as root (it reads the token file); D53: no sudo in code text
        return {**issued, "name": worker_name, "binding_kind": kind, "binding_id": binding, "command": command}

    def provision_boundary(self, actor, body):
        """「新建边界」 on this host (design §4.2.5, S30): checked here, carried out by the root service; a rerun only fills
        in what is missing, as boundary.sh does."""
        from .provision import boundary_name, valid_name
        require(elevated(actor), "只有平台管理员可以新建边界", 403, "forbidden")
        console_login(actor)   # it runs a root step on the host: never an Agent's or an API token's to start
        require(self.provisioning is not None, "这台主机没有开启控制台新建边界（单机版提供；集群版由部署套件新建）", 409,
                "provisioning_unavailable")
        require(isinstance(body, dict) and set(body) <= {"owner_id", "name"} and isinstance(body.get("owner_id"), str),
                "需要 owner_id，可选 name")
        with self.db.read() as db:
            person = db.execute("SELECT id,username FROM users WHERE id=? AND active=1", (body["owner_id"],)).fetchone()
            require(person is not None, "用户不存在或已停用", 404, "not_found")
            title = body.get("name") or boundary_name(person["username"])
            require(valid_name(title), "边界名称为小写字母开头的 2–20 位小写字母、数字或连字符，不能是 personal", 400)
            require(not db.execute("SELECT 1 FROM workers WHERE owner_id=? AND team_id IS NULL AND scope='dedicated' AND enabled=1 "
                                   "AND retired=0 AND name!=?", (person["id"], title)).fetchone(),
                    f"{person['username']} 已有自己的执行环境：每人一个边界", 409, "has_own_environment")
            require(not db.execute("SELECT 1 FROM workers WHERE name=? AND retired=0 AND (owner_id IS NULL OR owner_id!=?)",
                                   (title, person["id"])).fetchone(), f"名称 {title} 已被其他执行环境使用", 409, "name_taken")
        require(not self.provisioning.busy(title), f"边界 {title} 正在新建", 409, "provision_in_progress")
        owner = person["username"].lower()   # boundary.sh and the platform keep user names in lower case
        item = self.provisioning.submit(title, owner)
        with self.db.transaction() as db:
            audit(db, actor, "boundary.provision", "boundary", item["id"], {"name": title, "owner": owner})
        return item

    def provisions(self, actor):
        require(elevated(actor), "只有平台管理员可以查看新建边界的进度", 403, "forbidden")
        if self.provisioning is None:
            return {"available": False, "items": []}
        return {"available": True, "items": self.provisioning.items()}

    def enrollments(self, actor):
        from . import enrollments
        require(elevated(actor), "只有平台管理员可以查看接入码", 403, "forbidden")
        with self.db.read() as db:
            return enrollments.listed(db)

    def revoke_enrollment(self, actor, eid):
        from . import enrollments
        require(elevated(actor), "只有平台管理员可以撤销接入码", 403, "forbidden")
        with self.db.transaction() as db:
            enrollments.revoke(db, eid)
            audit(db, actor, "worker.enrollment_revoke", "enrollment", eid)
        return {"ok": True}

    def metrics(self, actor):
        """The platform's counts now (MVP 0.2.0, design §12.1), as each minute's EMF line carries them."""
        from . import metrics
        require(elevated(actor), "只有平台管理员可以查看平台指标", 403, "forbidden")
        now = time.time()
        with self.db.read() as db:
            return {**metrics.snapshot(db, now), "at": now}

    def kiro_login(self, actor, wid, start):
        """Sign a boundary's kiro-cli in with a device code from the console (MVP 0.2.0, §6.4): its owner only — not an
        administrator either: the code signs the boundary in as whoever enters it. The address and code pass through
        and are kept nowhere; the audit records that a login was started, not what it showed."""
        from . import transport
        with self.db.read() as db:
            row = db.execute("SELECT id, owner_id, config FROM workers WHERE id=? AND retired=0", (wid,)).fetchone()
        require(row is not None and row["owner_id"] == actor["id"], "执行环境不存在或不是你的", 404, "not_found")
        answer = transport.worker_request(json.loads(row["config"]), "/kiro/login" if start else "/kiro/login/status", {},
                                          timeout=20)
        require(isinstance(answer, dict) and answer.get("state") in ("idle", "starting", "waiting", "done", "failed"),
                "执行环境未返回登录状态", 503, "worker_unavailable")
        if start:
            with self.db.transaction() as db:
                audit(db, actor, "worker.kiro_login", "worker", wid, {"state": answer["state"]})
        keep = ("state", "url", "code", "expires") if answer["state"] == "waiting" else ("state",)
        return {key: answer[key] for key in keep if key in answer}

    def quotas(self, actor):
        """The actor's limit on running tasks and their Teams' (D61); an administrator also sees the default and each person's."""
        with self.db.read() as db:
            return quotas.view(db, actor, elevated(actor))

    def set_quota(self, actor, body):
        """The default per person or a person's own (administrators), a Team's (its owner or an administrator); audited."""
        scope, subject, running = quotas.parse(body)
        with self.db.transaction() as db:
            if scope == "team":
                require(self._team_role(db, actor, subject) == "owner" or elevated(actor),
                        "只有 Team owner 或平台管理员可以设 Team 的并发上限", 403, "forbidden")
                active_team(db, subject)
            else:
                require(elevated(actor), "只有平台管理员可以设每人默认与个人的并发上限", 403, "forbidden")
                require(scope == "default" or db.execute("SELECT 1 FROM users WHERE id=?", (subject,)).fetchone(),
                        "用户不存在", 404, "not_found")
            quotas.store(db, actor, scope, subject, running)
            audit(db, actor, "quota.set", "quota", f"{scope}:{subject}" if subject else scope, {"running": running})
            return quotas.view(db, actor, elevated(actor))

    @staticmethod
    def _team_role(db, actor, team):
        """The actor's role in the Team; an administrator who is not a member is treated as one with no role."""
        if elevated(actor):
            found = db.execute("SELECT role FROM members WHERE user_id=? AND team_id=?", (actor["id"], team)).fetchone()
            require(found is not None or db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone() is not None,
                    "Team 不存在或无权限", 404, "not_found")
            return found["role"] if found else None
        return member(db, actor["id"], team)

    def sync(self, actor, rid, data, head, request_id, expected_version, note=None, old=None):
        """A local fast-forward as the Session's new candidate (D58 §6): checked and applied by the Worker, then the last
        task that writes code gets a 「本地同步」 Attempt — its checks run as any Attempt's — the tasks after it run again
        (a review binds the new candidate) and a revision says so. Who may sync is who may continue."""
        from . import evaluation, sync as remote
        from .sources import SHA
        require(isinstance(head, str) and SHA.fullmatch(head) and isinstance(old, str) and SHA.fullmatch(old),
                "head 与 old 须为完整的提交 ID")
        require(head != old, "本地没有新的提交，不需要同步", 409, "nothing_to_sync")
        require(note is None or isinstance(note, str) and len(note) <= 2000, "说明最多 2000 个字符")
        require(isinstance(request_id, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{8,100}", request_id), "需要 8–100 字符 request_id")
        body = {"request_id": request_id, "head": head, "old": old, "note": note,
                "bundle": hashlib.sha256(data).hexdigest()}   # the command's digest covers the bytes, not the bytes
        with self.db.read() as db:
            run = run_access(db, actor, rid)
            require(not moving(run), "正在接手到新的执行环境，请稍候", 409, "takeover_in_progress")   # D74
            oversight = self._oversight(db, actor, rid)   # an elevated admin's: audited (A2)
            done = db.execute("SELECT hash, response FROM commands WHERE actor=? AND key=?", (actor["id"], request_id)).fetchone()
            if done is None:
                require(type(expected_version) is int and expected_version == run["version"], "Session 版本已更新", 409,
                        "version_conflict")
                if run["team_id"]:
                    active_team(db, run["team_id"])
                instruct = directs(db, actor, run)
                require(instruct is None, instruct, 403, "boundary_owner_only")
                require(json.loads(run["snapshot"]).get("purpose") != "workflow_design", "工作流建议会话不能同步", 409, "invalid_state")
                require(run["lifecycle"] == "open" and run["status"] in SETTLED, "请先等待结束或停止", 409, "busy")
                require(run["worker_id"] and run["workspace"], "会话尚未准备源码，不能同步", 409, "no_workspace")
                reason = unserved(db, run)
                require(reason is None, reason, 409, "environment_unavailable")
                worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
                config = {k: v for k, v in json.loads(worker["config"]).items() if k in ("url", "token", "token_sealed")}
                tasks = [{**dict(t), "spec": json.loads(t["spec"])} for t in
                         db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
                require(tasks, "会话没有任务", 409, "invalid_state")
                writer = next((t for t in reversed(tasks) if t["spec"].get("kind") in evaluation.WRITERS), tasks[-1])
                require(all(t["status"] == "accepted" for t in tasks if t["ordinal"] < writer["ordinal"]), "先完成上游 Task",
                        409, "dependency")   # as a continue of the task that writes code requires
        if done is not None:   # the same request again: its answer, the Worker untouched (as _command, before it)
            require(done["hash"] == hashlib.sha256(dumps(["sync", rid, body]).encode()).hexdigest(), "request_id 对应不同请求",
                    409, "idempotency_conflict")
            return json.loads(done["response"])
        candidate = remote.apply(config, rid, data, old, head)
        aid = uid("attempt")
        commands = writer["spec"].get("verify") or []
        try:
            job = remote.start_checks(config, rid, aid, commands) if commands else None
        except Error:   # the branch has moved: record it all the same; the engine starts the checks (as after a restart)
            job = "unstarted-" + aid

        def execute():
            fresh = db.execute("SELECT version, status FROM runs WHERE id=?", (rid,)).fetchone()
            require(fresh["version"] == run["version"], "同步期间会话被改动：分支已到新提交，请刷新后续作", 409, "version_conflict")
            now, who, version = time.time(), journal.person(actor), run["version"] + 1
            number = db.execute("SELECT count(*) FROM attempts WHERE run_id=? AND task_id=?", (rid, writer["id"])).fetchone()[0] + 1
            from . import gates
            evidence = {"synced": {"by": {"id": actor["id"], "username": actor.get("username")}, "client": actor.get("client", "api"),
                                   "commits": candidate.get("commits"), "note": note, "from": old, "head": head},
                        "candidate": {**{k: candidate.get(k) for k in ("sha", "parent", "changed", "files", "insertions", "deletions",
                                                                       "names")}, "moved": True, "synced": True},
                        "gates": [gates.record("native_completed", "skip", "本地提交（同步）"),
                                  gates.record("diff_present", "pass", f"{candidate.get('files')} 个文件"),
                                  gates.record("no_secrets", "pass")]}
            checking = bool(commands and job)
            if commands and not job:
                evidence["gates"].append(gates.record("checks_pass", "skip", gates.OLD_WORKER))
            elif not commands:
                evidence["gates"].append(gates.record("checks_pass", "skip", "任务没有检查命令"))
            if checking:
                evidence["checks"] = {"job": job, "started": now, "commands": len(commands)}
            status = "running" if checking else "accepted"
            handoff = f"本地同步：{candidate.get('commits')} 个本地提交（{actor.get('username') or actor['id']}）" + (f"：{note}" if note else "")
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,result,created,updated) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (aid, rid, writer["id"], number, dumps(writer["spec"]), status, dumps(evidence), handoff, now, now))
            journal.change(db, "tasks", (rid, writer["id"]), who, "attempt.created", "本地同步",
                           event={"id": aid, "task_id": writer["id"], "number": number}, current_attempt=aid, status=status)
            later = [t["id"] for t in tasks if t["ordinal"] > writer["ordinal"]]
            if later:
                journal.change_rows(db, "tasks", f"run_id=? AND id IN ({','.join('?' * len(later))})", (rid, *later), who,
                                    "task.reset", status="pending", current_attempt=None)
            restart_budget(db, rid, who)
            reason = unserved(db, run)
            journal.change(db, "runs", rid, who, "run.synced", event={"version": version, "head": head, "commits": candidate.get("commits"),
                                                                       "task_id": writer["id"], "attempt_id": aid},
                           version=version, updated=now, desired="active", error=reason,
                           status="running" if checking else "ready" if not reason else "waiting_worker")
            if oversight:
                audit(db, actor, "run.sync", "run", rid, {"version": version, "head": head})
            from .revisions import record
            record(db, rid, version, "synced", f"本地同步：{candidate.get('commits')} 个本地提交，到 {head[:7]}"
                   + (f"（{note[:60]}）" if note else ""), actor["id"], now)
            return {"id": rid, "session_id": rid, "version": version, "attempt_id": aid, "task_id": writer["id"], "head": head,
                    "commits": candidate.get("commits"), "status": "running" if checking else "ready"}
        with self.db.transaction() as db:
            result = self._command(db, actor, "sync", rid, body, execute)
        self._stop_app(rid, "session")   # as a continue does: the preview ran the code before it
        return result

    def chat_changes(self, actor, rid):
        """D73: what the native chat changed in the workspace that no candidate has yet — the continue dialog's reminder."""
        from . import chat_changes
        return chat_changes.pending(self, actor, rid)

    def bundle_entry(self, actor, rid):
        """Whether the actor may pull the Session's branch (D58 §5): read access and a prepared workspace."""
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            require(run["worker_id"] and run["workspace"], "此会话尚未准备源码，没有可拉回的分支", 409, "no_workspace")
        self.audit_read(actor, rid, "pull")
        return run

    def attempt_diff(self, actor, rid, attempt_id):
        """What an Attempt's candidate changed (D56): files and the clipped diff, through the Session's Worker; read with the
        Session's own access check (and its admin audit). A candidate the secret check kept off the branch is never shown."""
        from . import diffs
        with self.db.read() as db:
            run = run_access(db, actor, rid, read=True)
            row = db.execute("SELECT evidence FROM attempts WHERE id=? AND run_id=?", (attempt_id, rid)).fetchone()
            require(row is not None, "Attempt 不存在或无权限", 404, "not_found")
            candidate = json.loads(row["evidence"] or "{}").get("candidate") or {}
            require(candidate.get("sha"), "这次 Attempt 没有候选提交", 404, "no_candidate")
            require(candidate.get("moved", True), "这个候选中有疑似凭证或 .env 文件，不显示它的改动", 409, "candidate_secret")
            worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone() if run["worker_id"] else None
            require(worker is not None, "会话没有执行环境，读不到它的改动", 409, "no_worker")
            config = {key: value for key, value in json.loads(worker["config"]).items() if key in ("url", "token", "token_sealed")}
        self.audit_read(actor, rid, "diff")
        return {**diffs.read(config, rid, attempt_id, candidate), "sha": candidate["sha"], "parent": candidate.get("parent")}

    @staticmethod
    def _oversight(db, actor, rid, read=False):
        """Whether the actor does this (a write unless ``read``) only as an elevated admin (A2: audited) — not through
        their own access, a Team or a share that allows it (D64: a collaborator's sync is not an administrator's oversight;
        an administrator holding a viewer share who writes is)."""
        try:
            run_access(db, actor, rid, admin=False, read=read)
            return False
        except Error:
            return True

    @staticmethod
    def _readable(db, actor, rid, admin=True):
        try:
            run_access(db, actor, rid, admin=admin, shares=False)   # D64: a share does not show the Request
            return True
        except Error:
            return False

    def report(self, actor, rid, fmt):
        """The Session's Feature report (D37 phase 2), as it can be seen: the same access and read audit as its detail."""
        from . import report
        view = self.run(actor, rid)
        return report.html(view) if fmt == "html" else report.markdown(view)

    def run(self, actor, rid):
        with self.db.read() as db:
            value = self._view(db, run_access(db, actor, rid, read=True), actor)
        self.audit_read(actor, rid, "view")
        return value

    @staticmethod
    def _verified(db, rid):
        """What a publish pushes (D37 §7): the candidate the Session's branch holds, when its own gates all passed and —
        with review tasks — a passing review came after every code change except delivery. None otherwise, and for
        Sessions from before D37 (no candidates)."""
        kinds = {r["id"]: json.loads(r["spec"]).get("kind") for r in db.execute("SELECT id,spec FROM tasks WHERE run_id=?", (rid,))}
        current = {r[0] for r in db.execute("SELECT current_attempt FROM tasks WHERE run_id=? AND status='accepted'", (rid,))}
        head, sound, unreviewed, reviewed = None, False, False, False
        for row in db.execute("SELECT id,task_id,evidence FROM attempts WHERE run_id=? ORDER BY updated, created", (rid,)):
            evidence = json.loads(row["evidence"] or "{}")
            # only a fail fails: a warn (D57 §4) passes, as pass and skip do
            passed = row["id"] in current and not any(g.get("status") == "fail" for g in evidence.get("gates", []))
            candidate = evidence.get("candidate") or {}
            if candidate.get("sha") and candidate.get("moved", True):
                head, sound = {"sha": candidate["sha"], "task_id": row["task_id"]}, passed
                unreviewed = unreviewed or kinds.get(row["task_id"]) != "deliver"
            if kinds.get(row["task_id"]) == "review" and evidence.get("reviewed") and passed:
                reviewed, unreviewed = True, False   # the review covers what the branch held before it
        if head is None or not sound:
            return None
        if "review" in kinds.values() and (not reviewed or unreviewed):
            return None
        return head

    def _view(self, db, run, actor, detail=True, brief=False):
        """A Session as the API shows it; ``detail`` adds every Attempt (the session page). ``brief`` (the list view) leaves
        out what no list shows and what costs the most to read: the usage summed over every Attempt's evidence, the
        knowledge, the credential's availability and the verification."""
        value = {k: v for k, v in run.items() if k not in {"snapshot", "request_id"}}
        request = db.execute("SELECT id,client FROM requests WHERE id=?", (run["request_id"],)).fetchone() \
            if run.get("request_id") else None
        value["request"] = dict(request) if request else None   # D55; Sessions from before have none
        value["permissions"] = run_permissions(db, actor, run)
        value["access"] = access_of(db, actor, run)   # D64
        if value["access"]["via"] == "share":
            value["request"] = None   # D55's submission is not shared with its Session
        snapshot = json.loads(run["snapshot"])
        value.update({key: snapshot.get(key) for key in ("name", "workflow_name", "workflow_version",
                                                       "selection", "match_mode", "match_reason", "purpose", "planning", "replan",
                                                       "admission", "admission_basis", "confirmed", "publish_policy", "publish_auto",
                                                       "publish_target", "publish_suggestion", "placement", "takeover")})
        value["source"] = json.loads(value["source"])
        value["session_id"] = run["id"]
        value["quota_wait"] = quotas.waiting(snapshot, run) or None if run["status"] == "ready" else None   # D61
        creator = db.execute("SELECT username FROM users WHERE id=?", (run["owner_id"],)).fetchone()
        value["created_by"] = {"id": run["owner_id"], "username": creator["username"] if creator else None}
        if executor_of(run) != run["owner_id"]:   # D74: taken over, it runs as someone else
            executor = db.execute("SELECT username FROM users WHERE id=?", (executor_of(run),)).fetchone()
            value["executed_by"] = {"id": executor_of(run), "username": executor["username"] if executor else None}
        value["publish"] = snapshot.get("publish")
        if not brief:
            from . import credentials as repo_credentials
            pinned = snapshot.get("credentials") or {}
            value["credentials"] = ({"source": repo_credentials.availability(db, pinned["source"], run)} if pinned.get("source")
                                    else {"source": None, "removed": pinned["removed"]} if pinned.get("removed") else None)
            value["verified"] = self._verified(db, run["id"])
            from . import knowledge
            scope = db.execute("SELECT scope FROM workers WHERE id=?", (run["worker_id"],)).fetchone() if run["worker_id"] else None
            value["knowledge"] = knowledge.view(db, run, scope["scope"] if scope else None)
        value["tasks"] = []
        for row in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (run["id"],)):
            task = dict(row)
            task["spec"] = json.loads(task["spec"])
            task["task_id"] = run["id"] + "/" + task["id"]
            current = db.execute("SELECT evidence FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone() \
                if task["current_attempt"] else None
            task["mapping"] = mapping(snapshot, task, json.loads(current["evidence"] or "{}") if current else None)
            if detail:
                task["attempts"] = []
                for attempt in db.execute("SELECT * FROM attempts WHERE run_id=? AND task_id=? ORDER BY number",
                                          (run["id"], task["id"])):
                    item = dict(attempt)
                    item["spec"], item["evidence"] = json.loads(item["spec"]), json.loads(item["evidence"])
                    task["attempts"].append(item)
            value["tasks"].append(task)
        if not brief:
            from . import usage   # D46 P6a: what the Session's Attempts used
            rows = db.execute("SELECT evidence, created FROM attempts WHERE run_id=? ORDER BY created", (run["id"],)).fetchall()
            value["usage"] = usage.total([json.loads(r["evidence"] or "{}") for r in rows])
            split = usage.by_person(db, run, rows) if value["usage"] else None
            if split:   # D74: taken over, it ran as more than one person
                value["usage"]["by"] = split
        value["progress"] = {"accepted": sum(t["status"] == "accepted" for t in value["tasks"]),
                             "total": len(value["tasks"])}
        if detail:
            from .recovery import summary
            value["recovery"] = summary(db, run)
            kept = self._kept(db, run["id"])
            if kept:
                value["kept"] = kept
            value["attention"] = self._attention(value, self._space(db, run) if run["status"] == "waiting_worker" else None)
        return value

    @staticmethod
    def _kept(db, rid):
        """Tasks the latest continue left as they were (D34 §7), while they still carry that version's result."""
        for row in db.execute("SELECT version,created,content FROM revisions WHERE run_id=? ORDER BY version DESC", (rid,)):
            content = json.loads(row["content"])
            if content.get("kind") != "continued":
                continue
            tasks = [t for t in content.get("kept", []) if db.execute(
                "SELECT 1 FROM tasks t JOIN attempts a ON a.id=t.current_attempt WHERE t.run_id=? AND t.id=? "
                "AND t.status='accepted' AND a.created<=?", (rid, t, row["created"])).fetchone()]
            return {"version": row["version"], "tasks": tasks} if tasks else None
        return None

    @staticmethod
    def _space(db, run):
        """The run's space for the waiting_worker hint: Team name (None for a personal space) and enabled Workers."""
        workers = len(space_workers(db, executor_of(run), run["team_id"]))
        if run["team_id"]:
            team = db.execute("SELECT name FROM teams WHERE id=?", (run["team_id"],)).fetchone()
            return {"team": team["name"] if team else run["team_id"], "workers": workers,
                    "disabled": not workers and team_environment(db, run["team_id"]) is not None}
        return {"team": None, "workers": workers, "disabled": False}

    @staticmethod
    def _attention(run, space=None):
        task = next((t for t in run["tasks"] if t["status"] not in {"accepted", "pending"}), None)
        latest = run["recovery"]["latest"]
        if run["status"] == "waiting_worker":
            space = space or {"team": None, "workers": 0}
            where = f"Team「{space['team']}」" if space["team"] else "个人空间"
            if run.get("worker_id"):
                # It ran on an environment that no longer runs it (§6.2).
                title = f"{where}的{run.get('error') or '执行环境不可用'}"
                detail = ("这是执行环境的问题，不是你的任务。已完成的任务保留；换到另一个执行环境时，工作区在新环境中重新准备，"
                          "原环境中的产出不会迁移。")
                action = ("点「分配 Worker」选择另一个执行环境后继续执行。" if space["workers"]
                          else "请平台管理员为该空间分配执行环境后重新分配 Worker。")
                return {"title": title, "detail": detail, "action": action, "workers": space["workers"], "task_id": None}
            if space["workers"]:
                title, detail = "需求已保存，等待选择 Worker", f"{where}有 {space['workers']} 个可用 Worker，提交时未指定。"
                action = "点「分配 Worker」选择后开始执行。"
            elif space["team"] and space.get("disabled"):
                title, detail = f"{where}的执行环境已停用", "该 Team 的执行环境被管理员停用，暂时无法执行。"
                action = "请平台管理员重新启用该执行环境后分配 Worker；也可以改在个人空间重新提交。"
            elif space["team"]:
                title = f"{where}没有可用 Worker"
                detail = "该 Team 尚未分配执行环境（共享环境或独立的执行环境）。"
                action = "请平台管理员在控制台为该 Team 分配执行环境后分配 Worker；也可以改在个人空间重新提交。"
            else:
                title, detail = f"{where}没有可用 Worker", "尚未为你登记执行环境。"
                action = "请平台管理员为你登记执行环境后分配 Worker。"
            return {"title": title, "detail": detail, "action": action, "workers": space["workers"], "task_id": None}
        approve = ("在会话页「待批准」中查看工具与命令后批准或拒绝；Coding Agent 可用 octopus_approvals 查看、"
                   "经用户同意后用 octopus_approve 答复。")
        if run["status"] == "waiting_approval":
            return {"title": "等待批准", "detail": run["error"] or "原生任务正在等待批准一次工具调用。",
                    "action": approve, "task_id": task["id"] if task else None}
        if run["status"] == "completed" and (run.get("publish_auto") or {}).get("ok") is False:
            return {"title": "自动发布未完成", "detail": run["publish_auto"].get("error"),
                    "action": "处理后在会话页点「发布到仓库」（例如绑定一枚有写权限的凭证）；会话的工作已完成。"
                    if (run.get("source") or {}).get("type") == "git" else
                    "处理后在会话页「发布到新仓库」重新发布（例如换一枚能建仓库的写凭证，或改用新的仓库名）；会话的工作已完成。",
                    "task_id": None}
        suggested = (run.get("publish_suggestion") or {}).get("repository")
        if run["status"] == "completed" and suggested and not run.get("publish_target"):
            name = f"{suggested['owner']}/{suggested['name']}"   # D48 R7: never created on its own
            return {"title": "发布到新仓库待确认", "detail": f"需求要求新建仓库 {name}，平台不会自动创建。",
                    "action": "在会话页核对 owner、仓库名与凭证后点「新建仓库并发布」；Coding Agent 经用户同意后用 octopus_publish "
                              "发布（写明新仓库与凭证）。", "task_id": None}
        if run["status"] == "awaiting_acceptance":   # D71: an earlier version's wait, until the Engine accepts it
            return {"title": "即将继续", "detail": "验收确认已取消，待人判断的项记为提示。", "action": "无需操作。", "task_id": None}
        if run["status"] == "awaiting_confirmation":   # D70: an earlier version's wait, until the Engine starts it
            return {"title": "即将开始", "detail": "计划确认已取消，平台正在直接开始。", "action": "无需操作。", "task_id": None}
        if run["status"] == "diagnosing":
            from .recovery import LIVE, WHY, unfinished
            told = "缺少凭证或需求信息时会告诉你缺什么。也可以停止本会话。"   # D71: the only waits a diagnosis ends in
            if (latest or {}).get("status") == "waiting_approval":   # the diagnosis's own tool call (ApprovalsCard)
                return {"title": "诊断在等待批准一次工具调用", "detail": "协调者的诊断请求执行一次需要批准的工具调用，答复之前诊断不会继续。",
                        "action": approve, "task_id": task["id"] if task else None}
            fault = (((latest or {}).get("context") or {}).get("retry") or {}).get("transient")
            if fault in WHY and latest["status"] in LIVE:   # D57 §6.3: the platform's fault, dispatched again on its own
                return {"title": unfinished(fault) + "，正在自动重试",
                        "detail": f"平台临时故障：{WHY[fault]}。平台已自动重试 1 次，正在等结果。"
                                  "这是平台的问题，不是你的任务；你的任务结果都在。",
                        "action": "无需操作：诊断完成后自动按结果修复；" + told, "task_id": task["id"] if task else None}
            return {"title": "协调者正在诊断与安排修复", "detail": run["error"] or "正在读取失败证据，等待原生诊断结果。",
                    "action": "无需操作：诊断完成后自动按结果修复（重试、返工或换工具）；" + told,
                    "task_id": task["id"] if task else None}
        if run["status"] != "blocked":
            from .waiting import queue_attention, quota_attention
            return queue_attention(run, task) or quota_attention(run)
        attempt = (task.get("attempts") or [{}])[-1] if task else {}
        failure = attempt.get("evidence", {}).get("failure_type")
        from .waiting import handed_over, kiro_login_failure
        error = run.get("error") or attempt.get("error") or "尚无可核实的错误原因"
        outcome = ((attempt.get("evidence") or {}).get("outcome") or {})
        detail = error
        if failure in {"auth", "input", None, "native_error"} and pushing(" ".join(filter(None, [
                error, outcome.get("summary"), " ".join(map(str, outcome.get("findings") or []))]))):
            # D47: an Agent has no repository write credential by design; publishing is the platform's
            title = "Agent 不能推送或创建 PR"
            if (run.get("source") or {}).get("type") == "git":
                action = ("推送与 Draft PR 由平台完成：会话完成后在会话页点「发布到仓库」，或开启「完成后自动发布」；"
                          "可续作去掉要求推送的任务。")
            elif general(run.get("source")):   # D65
                action = NO_REPOSITORY + "；可续作去掉要求推送或建仓库的任务。"
            else:   # a new project goes to a new repository the platform makes (EC2 run-f7670bfb…, D48)
                action = ("此会话的源码不来自远端仓库：推送与建仓库由平台完成——会话完成后在会话页填写新仓库（owner、仓库名）与有写权限的凭证，"
                          "点「新建仓库并发布」；可续作去掉要求建仓库或推送的任务。")
        elif run.get("error") == "Worker 已停用":   # D43: continues on its own once the Worker is enabled again
            title, action = "执行环境已停用", "无需操作：这是执行环境的问题，管理员重新启用后自动继续；急用请联系平台管理员。"
        elif "原生任务已停止" in error:
            title = (task["spec"]["name"] + "：已停止") if task else "原生任务已停止，尚未完成"
            action = ("停止原因没有报告（可能是有人在原生页面停止了它），平台不会自动重跑。确认后点「请求协调者诊断」，"
                      "或在该任务上「配置并续作」重跑。")
        elif not task and not run.get("native_parent") and (
                ((run.get("credentials") or {}).get("source") or {}).get("unavailable") or (run.get("credentials") or {}).get("removed")):
            title, action = "仓库凭证不可用", "在会话页更换凭证，或请凭证管理者重新启用；凭证可用后自动继续。"
        elif not task and not run.get("native_parent"):
            title = "项目准备未完成"
            action = ("任务还没开始，问题在源码准备：检查仓库地址、访问权限与执行环境，处理详情中的错误后，"
                      "在第一个任务上「配置并续作」重新准备。")
        elif ((attempt.get("evidence") or {}).get("watchdog") or {}).get("reason"):
            title = "原生任务卡住，看门狗已停止"
            action = ("任务长时间没有进展：检查它是否在运行不会结束的命令（如开发服务器）或在等待输入，在该任务上「配置并续作」"
                      "调整说明后重跑；构建与测试确实较慢时，可让 Coding Agent 调大会话的无进展时限后重跑。")
        elif failure == "tool" and (attempt.get("evidence") or {}).get("requested"):
            title = "需求指定的工具或模型不可用"
            action = ("需求点名了它，平台不会自动换用：请平台管理员在执行环境中装好或开通后续作，"
                      "或在该任务上「配置并续作」改用可用的工具或模型。")
        elif failure == "tool":
            title = "需要换用工具或模型"
            action = ("协调者可推荐换用可用的工具（自动纠错开启且允许换工具时）；也可在「配置并续作」中换用可用的工具或模型后重跑，"
                      "详情中是失败原因或协调者的建议。")
        elif failure == "input":
            title = "等待补充项目或任务信息"
            action = ("缺少：只有你能提供的源码来源或需求信息（详情中是任务的说明）。由你补充：在该任务上「配置并续作」写入后，"
                      "继续同一会话。")
        elif task and kiro_login_failure(attempt.get("evidence") or {}, attempt.get("error"),
                                         (attempt.get("spec") or task["spec"]).get("tool")):
            if handed_over((attempt.get("evidence") or {}).get("waiting") or {}):
                title = "Kiro 登录正常，需要处理"
                action = ("失败后检查到 Kiro 登录正常（未见过期，或检查前已恢复），平台不会自动重跑：确认后在该任务上「配置并续作」。")
            else:   # the platform reads Kiro's login and continues on its own (D43)
                title = "等待 Kiro 登录恢复"
                action = ("请边界主人在「我的设置 › 执行环境」里点「登录 Kiro」，用自己的账号授权页面给出的设备码。"
                          "登录恢复后自动继续（平台每分钟检查一次）。")
        elif failure in {"auth", "model"}:
            title = "等待处理认证、权限或模型配置"
            action = ("任务报告认证、权限或模型有问题（详情中是原因），平台不会自动重跑。缺少的登录或权限由你或平台管理员在原生 KiroCrew "
                      "中处理；模型不可用时在该任务上「配置并续作」改用可用的模型。")
        elif latest and latest["status"] == "blocked" and latest.get("failed_attempt_id") == attempt.get("id"):
            title, detail, action = Service._diagnosis_stop(latest, error, run["recovery"].get("budget"))
        elif failure == "native_error":
            title = "原生任务出错，没有完成"
            action = ("这通常是工具或执行环境的问题，不是你的需求（详情中是原生错误）。点「请求协调者诊断」让协调者判断重试还是换工具，"
                      "或在该任务上「配置并续作」后重跑。")
        elif failure == "environment":
            title = "执行环境有问题，任务没有完成"
            action = ("任务报告执行环境缺东西或出错（详情中是原因），不是你的需求：请平台管理员处理执行环境后，点「请求协调者诊断」"
                      "或在该任务上「配置并续作」重跑。")
        else:
            title = "任务未通过交接或质量检查"
            action = ("任务的产出没有通过检查：原因见详情，证据在该任务的「执行证据与历史 Attempt」中。点「请求协调者诊断」"
                      "让协调者安排修复，或在该任务上「配置并续作」后重跑。")
        # What refused automatic recovery of this failure (D42), unless a diagnosis ran for it after all.
        refusal = (attempt.get("evidence") or {}).get("budget_refused")
        if refusal and run["recovery"].get("budget") and not (latest and latest.get("failed_attempt_id") == attempt.get("id")):
            from .recovery import budget_note
            action += " " + budget_note(run["recovery"]["budget"], refusal)
        return {"title": title, "detail": detail, "action": action, "task_id": task["id"] if task else None,
                "task_name": task["spec"]["name"] if task else None}

    @staticmethod
    def _diagnosis_stop(latest, error, budget=None):
        """(title, detail, action) of a Session its latest diagnosis stopped (Task 15, D57 §8): what it waits for and who
        supplies it (D71 recovery.WAIT_FOR), the platform's fault, a diagnosis that never started, or one that was
        stopped."""
        from .recovery import WHY, limit_reached, unfinished
        said = latest.get("error") or error
        missing = (latest.get("decision") or {}).get("missing")
        fault = (latest.get("evidence") or {}).get("platform_fault")
        look = "或展开该任务的「执行证据与历史 Attempt」查看结果。"
        if missing == "credentials":
            return "需要你提供凭证或授权", error, ("缺少：仓库凭证、授权或认证。由你或凭证管理者提供：在会话页更换凭证或按详情准备好授权后，"
                                          "点「请求协调者诊断」，或在该任务上「配置并续作」。")
        if missing == "input":
            return "需要你补充需求信息", error, "缺少：只有你能提供的需求信息。由你补充：在该任务上「配置并续作」写入需求后继续同一会话。"
        if fault in WHY:   # D57 §6.3: retried once, or the budget refused it (which limit: recovery.Coordinator._retry)
            key = (latest.get("evidence") or {}).get("budget_refused")
            again = "已自动重试 1 次，仍未成功。" if (latest.get("context") or {}).get("retry_of") else \
                (limit_reached(budget, key) + "，" if key and budget else "") + "没有再试。"
            return (unfinished(fault), f"平台临时故障：{WHY[fault]}。{again}这是平台的问题，不是你的任务；你的任务结果都在。",
                    "点「请求协调者诊断」重新诊断；" + look)
        if said.startswith(("协调者启动请求被拒绝：", "协调者未能启动：")):   # recovery.Coordinator.advance: never dispatched
            return ("诊断员没能启动", said.rstrip("。") + "。这是执行环境的问题，不是你的任务；你的任务结果都在。",
                    "稍后点「请求协调者诊断」重新诊断，反复出现请联系平台管理员；" + look)
        if said.startswith("自动诊断未形成可执行决定"):   # stopped before it decided (recovery: a person's, never retried)
            return ("诊断已停止，没有形成决定", said.rstrip("。") + "。诊断在完成前被停止（例如有人在原生页面停止了它）；你的任务结果都在。",
                    "停止的诊断不会自动再派。需要时点「请求协调者诊断」重新诊断；或在该任务上「配置并续作」。")
        return "协调者需要你处理具体阻塞", error, "按详情中协调者的说明处理后，点「请求协调者诊断」，或在该任务上「配置并续作」。"

    def takeover(self, actor, rid, body):
        """Take a Session that runs on someone else's own boundary over to one's own (D74): recorded here, carried out
        by the Engine (the workspace moves as a git bundle, which may take longer than a request). Those who may
        continue it may take it over — a collaborator, a Team member, its creator taking it back — never through an
        administrator's oversight; a viewer only reads."""
        require(isinstance(body, dict) and set(body) <= {"request_id", "expected_version"}, "只接受 request_id 与 expected_version")
        with self.db.transaction() as db:
            try:
                run = run_access(db, actor, rid, admin=False)
            except Error as exc:   # an administrator sees it through oversight: say why, as the native entry does
                if exc.status == 404 and elevated(actor) and db.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
                    raise Error(403, "forbidden", "管理员不因管理员身份接手会话：接手会以你的身份运行别人的会话") from None
                raise
            if run["team_id"]:
                active_team(db, run["team_id"])
            def execute():
                require(body.get("expected_version") == run["version"], "Session 版本已更新", 409, "version_conflict")
                worker, reason, code = takeover_target(db, actor, run)
                require(worker is not None, reason, 409, code)
                snapshot = json.loads(run["snapshot"])
                takeover = {"state": "moving", "to": worker["id"], "from": run["worker_id"], "by": actor["id"],
                            "by_name": actor["username"], "requested_at": time.time()}
                version = run["version"] + 1
                journal.change(db, "runs", rid, journal.person(actor), "run.takeover_requested", event={
                    "version": version, "to": worker["id"], "from": run["worker_id"]},
                    snapshot=dumps({**snapshot, "takeover": takeover}), version=version, updated=time.time())
                return {"id": rid, "version": version, "takeover": takeover}
            return self._command(db, actor, "takeover", rid, body, execute)

    def carry_takeovers(self, rid=None):
        """Carry out the takeovers recorded and not yet done (D74 §4): the request's own one from its thread, and every
        minute from serve those a restart interrupted. One move of a Session at a time."""
        from . import takeover
        with self.db.read() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM runs WHERE json_extract(snapshot,'$.takeover.state')='moving'"
                                               + (" AND id=?" if rid else ""), (rid,) if rid else ())]
        for each in ids:
            with self._opened_lock:
                if each in self._moving:
                    continue
                self._moving.add(each)
            try:
                takeover.move(self, each)
            finally:
                with self._opened_lock:
                    self._moving.discard(each)

    def operate(self, actor, rid, action, body):
        require(action in {"continue", "recover", "stop", "close", "reopen", "delete", "assign-worker"},
                "不支持的操作")
        from . import chat_changes   # D73: what the native chat changed, committed before a continue (it calls the Worker)
        chat = chat_changes.collect(self, actor, rid, body) if action == "continue" and isinstance(body, dict) else None
        if action == "recover":
            chat_changes.refuse(self, actor, rid)
        with self.db.transaction() as db:
            # D64: a collaborator does what an ordinary Team member does, except move the Session to another environment
            run = run_access(db, actor, rid, owner=action == "delete", shares=action != "assign-worker")
            try:
                run_access(db, actor, rid, owner=action == "delete", admin=False, shares=action != "assign-worker")
                oversight = False
            except Error:
                oversight = True   # allowed only as an elevated admin (A2): audited
            require(not moving(run), "正在接手到新的执行环境，请稍候", 409, "takeover_in_progress")   # D74
            if run["team_id"] and action in {"continue", "recover", "reopen", "assign-worker"}:
                active_team(db, run["team_id"])   # an archived Team's Sessions stay readable and stoppable (P3)
            if action in {"continue", "recover"}:
                instruct = directs(db, actor, run)
                require(instruct is None, instruct, 403, "boundary_owner_only")
            require(action not in {"continue", "recover"} or json.loads(run["snapshot"]).get("purpose") != "workflow_design",
                    "工作流建议请按新需求重新生成；原建议保留作为证据", 409, "invalid_state")
            # A retired Worker is never called again (P6): its Sessions settle here, and cannot be reopened on it.
            retired = bool(run["worker_id"]) and bool(
                db.execute("SELECT retired FROM workers WHERE id=?", (run["worker_id"],)).fetchone()["retired"])
            require(not (retired and action == "reopen"), "执行环境已退役，会话不能重新打开；可以按此任务列表在其他空间发起新开发",
                    409, "retired")
            def execute():
                require(body.get("expected_version") == run["version"], "Session 版本已更新", 409, "version_conflict")
                require(run["lifecycle"] != "deleted", "Session 已删除", 409, "invalid_state")
                version = run["version"] + 1
                who = journal.person(actor)
                # This command's writes to the Session row, recorded as one command event with its parameters (D34).
                fields, done, extra = {}, [], {}
                def commit():
                    if done:
                        return
                    done.append(True)
                    fields.update(version=version, updated=time.time())
                    journal.change(db, "runs", rid, who, "command." + action, event={
                        "version": version, **{k: body[k] for k in ("task_id", "worker_id", "rerun") if k in body},
                        **extra,
                        **({"oversight": True, "by": {"id": actor["id"], "username": actor["username"]}} if oversight else {})},
                        **fields)
                if action == "continue":
                    require(run["lifecycle"] == "open" and run["status"] in SETTLED, "请先等待结束或停止", 409, "busy")
                    ordered = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
                    index = next((i for i, t in enumerate(ordered) if t["id"] == body.get("task_id")), None)
                    require(index is not None, "Task 不存在", 404, "not_found")
                    require(all(t["status"] == "accepted" for t in ordered[:index]), "先完成上游 Task", 409, "dependency")
                    override = body.get("execution", {})
                    require(isinstance(override, dict) and set(override) <= {"name", "tool", "model", "effort", "prompt", *CONTRACT},
                            "任务覆盖字段无效")
                    before = json.loads(ordered[index]["spec"])
                    # D45: the user's own new tool or model for the task takes the place of what the requirement named
                    changed = any(key in override and override[key] != before.get(key) for key in ("tool", "model"))
                    merged = {**{k: v for k, v in before.items() if not (changed and k == "requested")}, **override}
                    # D57 §5.3: checks the user changed are their own, all required; sent back as they are (a form
                    # echoes them), they keep which must pass
                    if "verify" in override and "verify_required" not in override \
                            and override["verify"] != before.get("verify", []):
                        merged["verify_required"] = override["verify"]
                    if override.get("role", "x") in (None, ""):   # the user takes the role away; the kind stays
                        merged.pop("role")
                    current = steps([merged], known=[t["id"] for t in ordered[:index]], planned=True)[0]
                    # D46 P2: the user's own change never puts a reviewer on the tool that wrote the change (one the plan
                    # already had was noted when it was planned, and does not stop the continue)
                    from . import roles
                    specs = [json.loads(t["spec"]) for t in ordered]
                    found = lambda tasks: {(t["id"], tool) for t, tool in roles.unindependent(tasks)}
                    fresh = found([*specs[:index], current, *specs[index + 1:]]) - found(specs)
                    require(not fresh, "审查不独立：" + "；".join(f"任务 {tid} 与被审查改动的作者使用同一工具 {tool}" for tid, tool in sorted(fresh)))
                    goal = text(body.get("input", run["input"]), "开发需求", minimum=3)
                    if "recovery" in body:
                        from .recovery import policy
                        snapshot = json.loads(run["snapshot"])
                        old = snapshot.get("recovery")
                        snapshot["recovery"] = policy(body["recovery"])
                        if snapshot["recovery"]["enabled"]:
                            snapshot["execution_contract"] = max(snapshot.get("execution_contract", 1), 2)
                        journal.change(db, "runs", rid, who, "run.settings", event={"recovery": [old, snapshot["recovery"]]},
                                       snapshot=dumps(snapshot))
                    later = {row["id"] for row in ordered[index + 1:]}
                    rerun = body.get("rerun")
                    require(rerun is None or (isinstance(rerun, list) and all(isinstance(x, str) for x in rerun)
                                              and set(rerun) <= later), "rerun 只能列出所选任务之后的任务")
                    reset, kept, reasons = rerun_plan(ordered, index, rerun)
                    extra.update(kept=kept)
                    journal.change_rows(db, "tasks", f"run_id=? AND id IN ({','.join('?' * len(reset))})", (rid, *reset),
                                        who, "task.reset", status="pending", current_attempt=None)
                    journal.change(db, "tasks", (rid, current["id"]), who, "task.spec", spec=dumps(current))
                    reason = unserved(db, run) if run["worker_id"] else None
                    restart_budget(db, rid, who)
                    note, checking = chat_changes.apply(db, actor, rid, chat) if chat else (None, False)
                    if goal != run["input"]:   # D46: the plan's completion of the requirement no longer matches it
                        fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
                        if (fresh.get("planning") or {}).get("applied"):
                            fresh["planning"]["outdated"] = True
                            journal.change(db, "runs", rid, who, "run.planning", quiet=("snapshot",), snapshot=dumps(fresh))
                    fields.update(input=goal, desired="active", error=reason,
                                  status=("running" if checking else "ready") if run["worker_id"] and not reason else "waiting_worker")
                    commit()   # before the revision: its snapshot reads the new requirement
                    from .revisions import changes, record
                    detail = "、".join(filter(None, ["需求" if goal != run["input"] else "", changes(before, current)]))
                    names = {row["id"]: json.loads(row["spec"])["name"] for row in ordered}
                    scope = f"重跑 {'、'.join(names[t] for t in reset)}，沿用 {'、'.join(names[t] for t in kept)}" if kept else ""
                    summary = "；".join(filter(None, [detail, scope]))
                    record(db, rid, version, "continued", "；".join(filter(None, [
                               note, f"续作「{current['name']}」" + (f"：{summary}" if summary else "")])),
                           actor["id"], time.time(), input=goal, task=current, invalidated=reset, rerun=reset, kept=kept,
                           reasons=reasons, **({"recovery": snapshot["recovery"]} if "recovery" in body else {}))
                elif action == "recover":
                    from .recovery import schedule
                    require(run["lifecycle"] == "open" and run["worker_id"] and run["native_parent"] and
                            run["status"] in {"blocked", "failed", "stopped"}, "此 Session 当前不能启动诊断", 409, "busy")
                    reason = unserved(db, run)
                    require(not reason, f"{reason}：请续作后重新分配 Worker", 409, "environment_changed")
                    task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1",
                                      (rid,)).fetchone()
                    attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone() if task else None
                    require(attempt is not None, "尚无可诊断的失败执行；请补充输入并重跑", 409, "not_attempted")
                    snapshot = json.loads(run["snapshot"])
                    old = snapshot.get("execution_contract", 1)
                    snapshot["execution_contract"] = max(old, 2)
                    journal.change(db, "runs", rid, who, "run.settings", event={"execution_contract": [old, snapshot["execution_contract"]]},
                                   snapshot=dumps(snapshot))
                    restart_budget(db, rid, who)   # as a continue: the budget counts from the user's request
                    schedule(db, self.db, run, task, attempt, manual=True, actor=who)   # D71: every request is diagnosed
                elif action == "assign-worker":
                    require(run["status"] == "waiting_worker" and run["lifecycle"] == "open",
                            "运行已分配 Worker 或已关闭", 409, "invalid_state")
                    # The Session's own space — of the person it runs as (D74) — not the operator's (an admin may assign
                    # for someone else); one's own boundary is offered in one's own space and runs it as oneself.
                    chosen = db.execute("SELECT * FROM workers WHERE id=?", (body.get("worker_id"),)).fetchone()
                    person = actor["id"] if chosen is not None and own(chosen, actor["id"]) else executor_of(run)
                    target = self._worker(db, person, body.get("worker_id"), run["team_id"])
                    # D49 R10: putting the work on a person's own boundary runs it as them: only they may
                    require(not own(target, target["owner_id"]) or target["owner_id"] == actor["id"],
                            "只有边界主人能把会话放到自己的执行边界上；其他人可以分配 Team 的环境", 403, "boundary_owner_only")
                    if person != executor_of(run):
                        fields.update(executor_id=person)
                    restart_budget(db, rid, who)   # waiting for an environment is not automatic work
                    # where it now runs, as the platform would have said (D49 R5; the hint was for the old choice)
                    placed = {"kind": "own" if own(target, person) else "team" if run["team_id"] else "only"}
                    current = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
                    fields.update(snapshot=dumps({**current, "placement": placed}))   # after restart_budget's own write
                    if run["worker_id"] not in (None, body["worker_id"]):
                        # Another environment has no native parent of this Session: prepare there afresh (P5).
                        fields.update(worker_id=body["worker_id"], native_parent=None, workspace=None, status="preparing", error=None)
                    else:
                        fields.update(worker_id=body["worker_id"], status="ready" if run["native_parent"] else "preparing", error=None)
                elif action == "reopen":
                    require(run["lifecycle"] == "closed", "只有已关闭的 Session 可重新打开", 409, "invalid_state")
                    fields.update(desired="reopen", status="reopening")
                else:
                    require(action != "delete" or body.get("confirmation") == rid, "删除需完整 Session ID 确认")
                    fields.update(desired=action, status="stopping")
                    if action in {"close", "delete"}:   # D75: its public links end for good (reopening does not revive them)
                        ended = db.execute("DELETE FROM public_shares WHERE run_id=?", (rid,)).rowcount
                        if ended:
                            audit(db, actor, "public_share.delete", "run", rid, {"reason": action, "count": ended})
                if (not run["worker_id"] or retired) and action in {"stop", "close", "delete", "reopen"}:
                    lifecycle = {"close": "closed", "delete": "deleted", "reopen": "open"}.get(action, run["lifecycle"])
                    fields.update(desired="stopped", status="stopped", lifecycle=lifecycle)
                commit()
                if oversight:
                    audit(db, actor, "run." + action, "run", rid, {"version": version})
                return {"id": rid, "session_id": rid, "version": version, "command": action, "status": "accepted"}
            result = self._command(db, actor, action, rid, body, execute)
        if action in {"continue", "recover", "close", "delete"}:
            # The agents work in the same project again, or the Session ends: its remote preview stops (D36 §5.1).
            self._stop_app(rid, "session")
        return result

    # --- users (spec §4): only an elevated admin manages others; everyone changes their own password ---

    def change_password(self, actor, body):
        # Throttled per user, and checked outside the write lock: a limited token must not brute-force the password
        # (which would yield every scope of the account) or stall other writes with scrypt.
        now = time.time()
        with _password_lock:
            recent = [t for t in _password_attempts.get(actor["id"], []) if now - t < 60]
            require(len(recent) < 5, "原密码错误次数过多，请 1 分钟后再试", 429, "rate_limited")
        with self.db.read() as db:
            saved = db.execute("SELECT password FROM users WHERE id=?", (actor["id"],)).fetchone()["password"]
        if not check_password(body.get("current"), saved):
            with _password_lock:
                _password_attempts[actor["id"]] = recent + [now]
            raise Error(403, "forbidden", "原密码不正确")
        with _password_lock:
            _password_attempts.pop(actor["id"], None)
        hashed = password_hash(body.get("password"))
        with self.db.transaction() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (hashed, actor["id"]))
            revoke_tokens(db, actor["id"], keep=actor["token_id"])
        return {"ok": True}

    def _audit_oversight(self, actor, action, kind, target):
        """One audit row per administrator, action and target an hour; best effort: never fails the read."""
        try:
            with self.db.transaction() as db:
                if not db.execute("SELECT 1 FROM audit WHERE actor_id=? AND action=? AND target=? AND at>?",
                                  (actor["id"], action, target, time.time() - 3600)).fetchone():
                    audit(db, actor, action, kind, target)
        except sqlite3.OperationalError:
            pass

    def audit_read(self, actor, rid, action):
        """An elevated admin reading someone else's Session (A2): one audit row per admin, Session and action an hour
        (the console polls). Best effort: never fails the read."""
        if not elevated(actor):
            return
        try:
            with self.db.transaction() as db:
                try:
                    run_access(db, actor, rid, admin=False, read=True)
                    return
                except Error:
                    pass
                seen = db.execute("SELECT 1 FROM audit WHERE actor_id=? AND action=? AND target=? AND at>?",
                                  (actor["id"], "run." + action, rid, time.time() - 3600)).fetchone()
                if not seen:
                    audit(db, actor, "run." + action, "run", rid)
        except sqlite3.OperationalError:
            pass

    def _target(self, db, actor, uid):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        require(row is not None, "用户不存在", 404, "not_found")
        return row

    def reset_password(self, actor, uid, body):
        """An administrator sets someone else's password, from a console login only: through an Agent's or the API's
        token it would hand the account to whatever holds that token (peer review 2026-10-27)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        console_login(actor)
        require(uid != actor["id"], "改自己的密码请在「我的设置」里进行，需要输入原密码", 400, "use_me_password")
        with self.db.transaction() as db:
            self._target(db, actor, uid)
            db.execute("UPDATE users SET password=? WHERE id=?", (password_hash(body.get("password")), uid))
            revoke_tokens(db, uid)
            audit(db, actor, "user.reset_password", "user", uid)
        return {"ok": True}

    def set_active(self, actor, uid, body):
        require(isinstance(body.get("active"), bool), "active 必须为布尔值")
        if body["active"]:   # giving access back needs a console login, as granting it does; taking it away does not
            require(elevated(actor), "需要平台管理员", 403, "forbidden")
            console_login(actor)
        with self.db.transaction() as db:
            row = self._target(db, actor, uid)
            if not body["active"] and row["active"]:
                sole = [r["name"] for r in db.execute(
                    "SELECT t.name FROM members m JOIN teams t ON t.id=m.team_id WHERE m.user_id=? AND m.role='owner' "
                    "AND NOT EXISTS(SELECT 1 FROM members o JOIN users u ON u.id=o.user_id WHERE o.team_id=m.team_id "
                    "AND o.role='owner' AND o.user_id!=? AND u.active=1) ORDER BY t.name", (uid, uid))]
                require(not sole, f"{row['username']} 是 Team「{'」「'.join(sole)}」唯一的 owner，请先指定另一位 owner",
                        409, "last_owner")
                require(not row["admin"] or _active_admins(db) > 1, "至少保留一位有效的平台管理员", 409, "last_admin")
                revoke_tokens(db, uid)
            db.execute("UPDATE users SET active=? WHERE id=?", (int(body["active"]), uid))
            audit(db, actor, "user.activate" if body["active"] else "user.deactivate", "user", uid)
        return {"id": uid, "active": body["active"]}

    def set_admin(self, actor, uid, body):
        require(isinstance(body.get("admin"), bool), "admin 必须为布尔值")
        if body["admin"]:   # granting needs a console login; taking it away stays open to tokens, for incident response
            require(elevated(actor), "需要平台管理员", 403, "forbidden")
            console_login(actor)
        with self.db.transaction() as db:
            row = self._target(db, actor, uid)
            if not body["admin"] and row["admin"] and row["active"]:
                require(_active_admins(db) > 1, "至少保留一位有效的平台管理员", 409, "last_admin")
            db.execute("UPDATE users SET admin=? WHERE id=?", (int(body["admin"]), uid))
            audit(db, actor, "user.promote" if body["admin"] else "user.demote", "user", uid)
        return {"id": uid, "admin": body["admin"]}

    def admin_users(self, actor):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        now = time.time()
        with self.db.read() as db:
            # The Sessions each person has in progress, counted as the environment list counts them (open, not settled).
            running = dict(db.execute(
                "SELECT owner_id,COUNT(*) FROM runs WHERE lifecycle='open' AND status NOT IN "
                f"({','.join('?' * len(SETTLED))}) GROUP BY owner_id", SETTLED).fetchall())
            result = []
            for row in db.execute("SELECT * FROM users ORDER BY username"):
                result.append({"id": row["id"], "username": row["username"], "admin": bool(row["admin"]),
                               "active": bool(row["active"]), "created": row["created"], "last_login": row["last_login"],
                               "teams": [dict(t) for t in db.execute(
                                   "SELECT t.id,t.name,m.role FROM members m JOIN teams t ON t.id=m.team_id "
                                   "WHERE m.user_id=? ORDER BY t.name", (row["id"],))],
                               "tokens": db.execute("SELECT count(*) FROM tokens WHERE user_id=? AND expires>?",
                                                    (row["id"], now)).fetchone()[0],
                               "running": running.get(row["id"], 0)})
            return result

    def user_tokens(self, actor, uid):
        with self.db.read() as db:
            self._target(db, actor, uid)
            return [{**dict(r), "scopes": json.loads(r["scopes"]), "expires": expiry(r["expires"])} for r in db.execute(
                "SELECT id,label,scopes,expires,last_used FROM tokens WHERE user_id=? AND expires>? ORDER BY expires DESC",
                (uid, time.time()))]

    def revoke_user_token(self, actor, uid, tid):
        with self.db.transaction() as db:
            self._target(db, actor, uid)
            changed = db.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (tid, uid)).rowcount
            require(changed == 1, "Token 不存在", 404, "not_found")
            audit(db, actor, "user.revoke_token", "user", uid, {"token_id": tid})
        return {"ok": True}

    # --- revisions (D32) ---

    def revisions(self, actor, rid):
        """Every revision of a run, oldest first, without snapshots (see revision())."""
        from .revisions import history
        with self.db.read() as db:
            run_access(db, actor, rid, read=True)
            entries = history(db, rid)
        self.audit_read(actor, rid, "view")
        return [{k: v for k, v in entry.items() if k not in {"snapshot", "content"}} for entry in entries]

    def changes(self, actor, rid, who=None, kind=None, before=None, limit=50, audited=True):
        """The Session's change journal, newest first (D34 §4.3): who changed what, from what, to what and why. Events
        written before the journal carry a user id string (commands) or no actor (system, listed as legacy)."""
        require(who in (None, "user", "system"), "who 只能是 user 或 system")
        require(kind is None or (isinstance(kind, str) and len(kind) <= 40), "kind 不超过 40 个字符")
        require(type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        person = "coalesce(json_type(payload,'$.actor')='text' OR json_extract(payload,'$.actor.kind')='user', 0)"
        sql, params = "SELECT * FROM events WHERE run_id=? AND id<?", [rid, before or 2 ** 62]
        if who:
            sql += f" AND {'' if who == 'user' else 'NOT '}{person}"
        if kind:
            sql += " AND kind LIKE ? ESCAPE '\\'"
            params.append(kind.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        with self.db.read() as db:
            run_access(db, actor, rid, read=True)
            users = dict(db.execute("SELECT id,username FROM users").fetchall())
            rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        if audited:   # the export audits itself once
            self.audit_read(actor, rid, "changes")
        result = []
        for row in rows:
            payload = json.loads(row["payload"])
            by = payload.pop("actor", None)
            if isinstance(by, str):
                by = {"kind": "user", "id": by, "username": users.get(by)}
            result.append({"id": row["id"], "at": row["created"], "kind": row["kind"], "actor": by or {"kind": "legacy"},
                           "changes": payload.pop("changes", None), "reason": payload.pop("reason", None), "detail": payload})
        return result

    def revision(self, actor, rid, version):
        from .revisions import history
        with self.db.read() as db:
            run_access(db, actor, rid, read=True)
            entry = next((e for e in history(db, rid) if e["version"] == version), None)
        require(entry is not None, "修订不存在", 404, "not_found")
        self.audit_read(actor, rid, "view")
        return entry

    def revision_diff(self, actor, rid, before=None, after=None):
        """Differences between two revisions; by default the latest and the one before it."""
        from .revisions import diff, history
        with self.db.read() as db:
            run_access(db, actor, rid, read=True)
            entries = {e["version"]: e for e in history(db, rid)}
        versions = sorted(entries)
        after = versions[-1] if after is None else after
        require(after in entries, "修订不存在", 404, "not_found")
        if before is None:
            earlier = [v for v in versions if v < after]
            before = earlier[-1] if earlier else after
        require(before in entries, "修订不存在", 404, "not_found")
        old, new = entries[before]["snapshot"], entries[after]["snapshot"]
        require(old is not None and new is not None, "该修订没有可比较的内容", 409, "no_snapshot")
        self.audit_read(actor, rid, "view")
        return {"from": before, "to": after, "from_summary": entries[before]["summary"],
                "to_summary": entries[after]["summary"],
                "reconstructed": entries[before]["reconstructed"] or entries[after]["reconstructed"], **diff(old, new)}

    def audit_log(self, actor, before=None, limit=50):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        with self.db.read() as db:
            rows = db.execute("SELECT a.*, u.username FROM audit a JOIN users u ON u.id=a.actor_id "
                              "WHERE a.id < ? ORDER BY a.id DESC LIMIT ?", (before or 2 ** 62, limit)).fetchall()
        return [{"id": r["id"], "at": r["at"], "actor": {"id": r["actor_id"], "username": r["username"]},
                 "action": r["action"], "kind": r["kind"], "target": r["target"], "detail": json.loads(r["detail"])}
                for r in rows]

    def events(self, actor, rid, after=0):
        with self.db.read() as db:
            run_access(db, actor, rid, read=True)
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", (rid, after))]
