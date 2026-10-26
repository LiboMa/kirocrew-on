"""Knowledge (D46 P4): what a code-changing Session learned about its repository — gathered by a Knowledge task the platform
appends after the Evaluator, checked here, and written by the Worker as KiroCrew lessons scoped to the repository's mark, so
the repository's later tasks are given them. Lessons only on a dedicated execution environment: a shared one cannot keep one
space's lessons from another (KiroCrew members are routing, not a boundary), so it neither writes nor injects them (owner
2026-10-18); what its Knowledge task gathers is kept for the repository's reviewed knowledge file, proposed when the Session
is published (D52 R4)."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

ROLE = "knowledge"
ITEM = "列出可复用的项目知识（可为空）"
LIMIT, RULE, AVOID = 5, 300, 200
# D57 §3.3: what the platform judged of an entry it stored masked, as the Agent wrote it (engine._judged); only the platform
# writes it — an Agent's own value under this key is dropped before the outcome is stored
MARK = "_platform_judged"
SHARED = "共享执行环境不写入、不注入 lessons：条目在发布时提到仓库的知识 PR（D52）"
KEPT = "共享执行环境：仅在发布时提到仓库的知识 PR"
REMOVED = "有人删除过同一条知识，不再写入"
PROMPT = ("只读阅读本会话的需求、前序任务的交接与改动，列出长期有效的项目知识（约定、决策、踩过的坑、可复用的做法），"
          "至多 5 条写入 outcome.json 的 knowledge。不写本次进度、个人信息、秘密或模型身份；没有就写空列表。不修改文件。")
WRITERS = ("implement", "deliver")
RETRY = 600   # seconds an unavailable environment is waited for before the items are recorded as not written
BACKOFF = 30   # seconds between tries while it is waited for


def applies(snapshot, source, scope, config):
    capabilities = config.get("capabilities") or {}
    return (snapshot.get("execution_contract", 1) >= 4 and (source or {}).get("type") == "git" and scope == "dedicated"
            and bool(capabilities.get("role_cards")) and bool(capabilities.get("knowledge")))


def gathers(snapshot, source, scope, config):
    """A Knowledge task is added (D52 R4): where lessons are written (``applies``), and on a shared environment too, whose
    items go only to the repository's reviewed knowledge when the Session is published."""
    capabilities = config.get("capabilities") or {}
    return (snapshot.get("execution_contract", 1) >= 4 and (source or {}).get("type") == "git" and bool(capabilities.get("role_cards"))
            and (scope == "shared" or applies(snapshot, source, scope, config)))


def platform(snapshot, task_id):
    """A Knowledge task the platform added (not one a person submitted with the role)."""
    return task_id in ((snapshot.get("knowledge_added") or {}).get("tasks") or [])


def space(run):
    """A Team Session's Team, else the person it runs as (D74: its creator unless taken over)."""
    return run["team_id"] or (run["executor_id"] if "executor_id" in run.keys() and run["executor_id"] else run["owner_id"])


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
    from .planning import replans
    snapshot = json.loads(run["snapshot"])
    added = (snapshot.get("knowledge_added") or {}).get("tasks") or []
    rows = [dict(r) for r in db.execute("SELECT id, ordinal, spec FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
    tasks = [json.loads(r["spec"]) for r in rows]
    tool = _tool(config)
    if not tool or not gathers(snapshot, json.loads(run["source"]), scope, config) or not needed(tasks, added) \
            or not key(json.loads(run["source"]), space(run)):
        return None
    ids = {r["id"] for r in rows} | {r[0] for r in db.execute("SELECT DISTINCT task_id FROM attempts WHERE run_id=?", (rid,))} \
        | {t.get("id") for plan in replans(snapshot) for t in plan.get("replaced") or []}   # never an id used before
    tid = next(name for name in ["knowledge", *(f"knowledge-{n}" for n in range(2, 100))] if name not in ids)
    spec = {"id": tid, "name": "Knowledge 沉淀", "tool": tool, "model": "auto", "effort": "", "prompt": PROMPT,
            "kind": "design", "role": ROLE, "acceptance": [ITEM]}
    db.execute("INSERT INTO tasks(run_id,id,ordinal,spec) VALUES(?,?,?,?)", (rid, tid, rows[-1]["ordinal"] + 1, dumps(spec)))
    snapshot["knowledge_added"] = {**(snapshot.get("knowledge_added") or {}), "tasks": [*added, tid]}
    from .revisions import appended
    journal.change(db, "runs", rid, journal.ENGINE, "run.knowledge_task", quiet=("snapshot",), snapshot=dumps(snapshot),
                   version=appended(db, rid, tid, "平台追加 Knowledge 沉淀"), updated=time.time())
    journal.record(db, rid, "run.knowledge_task", journal.ENGINE, reason="平台追加 Knowledge 沉淀：整理本次会话中可复用的项目知识",
                   task=tid, tool=tool)
    return tid


def items(report, known=None):
    """(what to write, what is not written and why): at most five, each within its length, none with what looks like a
    secret (the publish scan) — whose text is then not kept either — and none this environment already holds for the
    repository or a person removed there (``known``: {rule: removed})."""
    known = known or {}
    raw = (report or {}).get("knowledge")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [{"rule": "（knowledge 不是列表）", "result": "skipped", "reason": "knowledge 应为 [{rule, avoid}] 列表，已忽略"}]
    keep, rest, kept = [], [], set()
    for entry in raw[:30]:
        rule, avoid = _fields(entry)
        verdict = marked(entry) or judged(entry)   # D57 §3.3: as written, for an entry stored masked
        if not rule:
            rest.append({"rule": "（空条目）", "result": "skipped", "reason": "条目缺少 rule"})
        elif verdict.get("suspicious"):
            rest.append({"rule": "（含疑似秘密，内容未保存）", "result": "skipped", "reason": "含疑似秘密，未写入"})
        elif verdict.get("too_long"):
            rest.append({"rule": rule[:RULE] + ("…" if len(rule) > RULE else ""), "avoid": avoid[:AVOID] or None,
                         "result": "skipped", "reason": f"超过长度（rule 至多 {RULE}、avoid 至多 {AVOID} 字符），未写入"})
        # what this environment holds is what was written, masked as this entry is now; one in this list, as written
        elif rule in known or verdict.get("key") in kept:
            rest.append({"rule": rule, "avoid": avoid or None, "result": "skipped",
                         "reason": REMOVED if known.get(rule) else "此执行环境已记录同一条知识"})
        elif len(keep) >= LIMIT:
            rest.append({"rule": rule, "avoid": avoid or None, "result": "skipped", "reason": f"超过 {LIMIT} 条，未写入"})
        else:
            keep.append({"rule": rule, **({"avoid": avoid} if avoid else {})})
            kept.add(verdict.get("key"))
    return keep, rest


def _fields(entry):
    rule = entry.get("rule") if isinstance(entry, dict) else entry
    avoid = entry.get("avoid") if isinstance(entry, dict) else None
    return (rule.strip() if isinstance(rule, str) else ""), (avoid.strip() if isinstance(avoid, str) else "")


def unmarked(report):
    """An Agent's outcome as it is read: its knowledge entries without a MARK of their own (only the platform writes
    one, when it stores them masked: engine._judged)."""
    entries = report.get("knowledge")
    if not isinstance(entries, list):
        return report
    return {**report, "knowledge": [{k: v for k, v in entry.items() if k != MARK} if isinstance(entry, dict) else entry
                                    for entry in entries]}


def marked(entry):
    """The MARK the platform put on an entry it stored masked (engine._judged), when it has exactly the shape the
    platform writes ({suspicious, too_long, key}: two booleans and a SHA-256); None for anything else there."""
    mark = entry.get(MARK) if isinstance(entry, dict) else None
    shaped = isinstance(mark, dict) and set(mark) == {"suspicious", "too_long", "key"}
    if shaped and type(mark["suspicious"]) is bool and type(mark["too_long"]) is bool and isinstance(mark["key"], str) \
            and re.fullmatch(r"[0-9a-f]{64}", mark["key"]):
        return mark
    return None


def judged(entry):
    """What ``items`` judges of an entry by its text: whether it looks like it holds a secret (the publish scan), whether
    it is too long, and its key among the entries of one list. The platform keeps this for an entry it stores masked
    (D57 §3.3, under MARK), so that the entry is judged as it was written."""
    from .sources import SUSPICIOUS
    rule, avoid = _fields(entry)
    return {"suspicious": bool(SUSPICIOUS.search(f"{rule}\n{avoid}".encode())),
            "too_long": len(rule) > RULE or len(avoid) > AVOID, "key": hashlib.sha256(rule.encode()).hexdigest()}


def known(db, worker, repo):
    """{rule: removed} for what this environment wrote for the repository: one a person removed stays removed."""
    return {r[0]: bool(r[1]) for r in db.execute(
        "SELECT rule, MIN(removed IS NOT NULL) FROM knowledge WHERE worker_id=? AND repo=? "
        "AND result IN ('written','superseded') GROUP BY rule", (worker, repo))}


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
    skipped = (snapshot.get("knowledge_added") or {}).get("skipped")
    note = (SHARED if scope == "shared" and snapshot.get("execution_contract", 1) >= 4
            else "Knowledge 沉淀未完成：" + skipped if skipped else None)
    proposal = snapshot.get("knowledge_proposal")   # D52: where the items went in the repository's reviewed knowledge
    if rows or note or snapshot.get("knowledge_added") or proposal:
        return {"items": rows, "note": note, **({"proposal": proposal} if proposal else {})}
    return None
