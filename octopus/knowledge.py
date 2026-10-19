"""Knowledge (D46 P4): what a code-changing Session learned about its repository — gathered by a Knowledge task the platform
appends after the Evaluator, checked here, and written by the Worker as KiroCrew lessons scoped to the repository's mark, so
the repository's later tasks are given them. Only on a dedicated execution environment: a shared one cannot keep one space's
lessons from another (KiroCrew members are routing, not a boundary), so it neither gathers nor injects (owner 2026-10-18)."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

ROLE = "knowledge"
ITEM = "列出可复用的项目知识（可为空）"
LIMIT, RULE, AVOID = 5, 300, 200
SHARED = "共享执行环境不沉淀、不注入知识"
PROMPT = ("只读阅读本会话的需求、前序任务的交接与改动，列出长期有效的项目知识（约定、决策、踩过的坑、可复用的做法），"
          "至多 5 条写入 outcome.json 的 knowledge。不写本次进度、个人信息、秘密或模型身份；没有就写空列表。不修改文件。")
WRITERS = ("implement", "deliver")
RETRY = 600   # seconds an unavailable environment is waited for before the items are recorded as not written


def applies(snapshot, source, scope, config):
    capabilities = config.get("capabilities") or {}
    return (snapshot.get("execution_contract", 1) >= 4 and (source or {}).get("type") == "git" and scope == "dedicated"
            and bool(capabilities.get("role_cards")) and bool(capabilities.get("knowledge")))


def space(run):
    return run["team_id"] or run["owner_id"]


def key(source, owner):
    """The repository mark: host_owner_repo of the source and the space it is gathered for — a Worker given to another
    space (a spare assigned to a new Team) never hands that space the lessons of the one before. None for an address
    without a host and a path."""
    url = str((source or {}).get("url") or "").strip()
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:   # scp-like: user@host:owner/repo
        match = re.fullmatch(r"[\w.-]+@([\w.-]+):(.+)", url)
        if not match:
            return None
        host, path = match.groups()
    path = re.sub(r"\.git$", "", path.strip("/"))
    if not host or not path:
        return None
    repo = re.sub(r"[^a-z0-9._-]", "_", f"{host}_{path}".lower()).lstrip("._-")[:100]
    return repo and f"{repo}.{hashlib.sha256(owner.encode()).hexdigest()[:12]}"


def needed(tasks, added):
    """A code-changing Session with no Knowledge task the platform added (``added``) after its last change."""
    last = max((i for i, t in enumerate(tasks) if t.get("kind") in WRITERS), default=None)
    return last is not None and not any(t.get("id") in added for t in tasks[last + 1:])


def _tool(config):
    """Kiro, the platform's planning and diagnosis tool, when the environment has it; else the first available one."""
    facts = (config.get("capabilities") or {}).get("tools") or {}
    available = [tool for tool, state in facts.items() if isinstance(state, dict) and state.get("available")]
    return "kiro" if "kiro" in available else available[0] if available else None


def append(db, rid, run, scope, config):
    """Add the Knowledge task a code-changing Session lacks (R3); returns its id, or None when none is added."""
    from . import journal
    from .db import dumps
    snapshot = json.loads(run["snapshot"])
    added = (snapshot.get("knowledge_added") or {}).get("tasks") or []
    rows = [dict(r) for r in db.execute("SELECT id, ordinal, spec FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
    tasks = [json.loads(r["spec"]) for r in rows]
    tool = _tool(config)
    if not tool or not applies(snapshot, json.loads(run["source"]), scope, config) or not needed(tasks, added) \
            or not key(json.loads(run["source"]), space(run)):
        return None
    ids = {r["id"] for r in rows} | {r[0] for r in db.execute("SELECT DISTINCT task_id FROM attempts WHERE run_id=?", (rid,))} \
        | {t.get("id") for t in (snapshot.get("replan") or {}).get("replaced") or []}   # never an id used before
    tid = next(name for name in ["knowledge", *(f"knowledge-{n}" for n in range(2, 100))] if name not in ids)
    spec = {"id": tid, "name": "Knowledge 沉淀", "tool": tool, "model": "auto", "effort": "", "prompt": PROMPT,
            "kind": "design", "role": ROLE, "acceptance": [ITEM]}
    db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, tid, rows[-1]["ordinal"] + 1, dumps(spec)))
    snapshot["knowledge_added"] = {**(snapshot.get("knowledge_added") or {}), "tasks": [*added, tid]}
    journal.change(db, "runs", rid, journal.ENGINE, "run.knowledge_task", quiet=("snapshot",), snapshot=dumps(snapshot),
                   updated=time.time())
    journal.record(db, rid, "run.knowledge_task", journal.ENGINE, reason="平台追加 Knowledge 沉淀：整理本次会话中可复用的项目知识",
                   task=tid, tool=tool)
    return tid


def items(report, known=()):
    """(what to write, what is not written and why): at most five, each within its length, none with what looks like a
    secret (the publish scan) — whose text is then not kept either — and none this environment already holds for the
    repository."""
    from .sources import SUSPICIOUS
    raw = (report or {}).get("knowledge")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [{"rule": "（knowledge 不是列表）", "result": "skipped", "reason": "knowledge 应为 [{rule, avoid}] 列表，已忽略"}]
    keep, rest = [], []
    for entry in raw[:30]:
        rule = entry.get("rule") if isinstance(entry, dict) else entry
        avoid = entry.get("avoid") if isinstance(entry, dict) else None
        rule = rule.strip() if isinstance(rule, str) else ""
        avoid = avoid.strip() if isinstance(avoid, str) else ""
        if not rule:
            rest.append({"rule": "（空条目）", "result": "skipped", "reason": "条目缺少 rule"})
        elif SUSPICIOUS.search(f"{rule}\n{avoid}".encode()):
            rest.append({"rule": "（含疑似秘密，内容未保存）", "result": "skipped", "reason": "含疑似秘密，未写入"})
        elif len(rule) > RULE or len(avoid) > AVOID:
            rest.append({"rule": rule[:RULE] + ("…" if len(rule) > RULE else ""), "avoid": avoid[:AVOID] or None,
                         "result": "skipped", "reason": f"超过长度（rule 至多 {RULE}、avoid 至多 {AVOID} 字符），未写入"})
        elif rule in known or any(k["rule"] == rule for k in keep):
            rest.append({"rule": rule, "avoid": avoid or None, "result": "skipped", "reason": "此执行环境已记录同一条知识"})
        elif len(keep) >= LIMIT:
            rest.append({"rule": rule, "avoid": avoid or None, "result": "skipped", "reason": f"超过 {LIMIT} 条，未写入"})
        else:
            keep.append({"rule": rule, **({"avoid": avoid} if avoid else {})})
    return keep, rest


def known(db, worker, repo):
    return {r[0] for r in db.execute("SELECT rule FROM knowledge WHERE worker_id=? AND repo=? AND removed IS NULL "
                                     "AND result IN ('written','superseded')", (worker, repo))}


def record(db, rid, task, worker, repo, rows):
    from .db import uid
    now = time.time()
    for row in rows:
        db.execute("INSERT INTO knowledge(id,run_id,task_id,worker_id,repo,rule,avoid,result,reason,created) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)", (uid("knowledge"), rid, task, worker, repo, row["rule"][:RULE + 1],
                                                   row.get("avoid"), row["result"], row.get("reason"), now))


def view(db, run, scope):
    """The session page's 「本次沉淀的知识」 (R8): this Session's entries, and why a shared environment has none."""
    snapshot = json.loads(run["snapshot"])
    rows = [dict(r) for r in db.execute(
        "SELECT k.id,k.task_id,k.rule,k.avoid,k.result,k.reason,k.created,k.removed,u.username removed_by FROM knowledge k "
        "LEFT JOIN users u ON u.id=k.removed_by WHERE k.run_id=? ORDER BY k.created, k.rowid", (run["id"],))]
    note = SHARED if scope == "shared" and snapshot.get("execution_contract", 1) >= 4 else None
    return {"items": rows, "note": note} if rows or note or snapshot.get("knowledge_added") else None
