"""Finding Sessions (D56 M3-5): the session list's filters, full-text search and pages. Columns the runs table holds are
filtered in SQL; the repository, branch, stage, tools and text are matched per Session as it is read, after its access
check — a scan, which at a few hundred Sessions takes milliseconds (an index waits until there are thousands)."""
import base64
import calendar
import json
import re
import time

from .security import require

STAGES = ("plan", "implement", "review", "test", "deliver", "design", "done")
LIFECYCLES = ("open", "closed")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _text(value, field, maximum=200):
    require(value is None or isinstance(value, str) and 0 < len(value.strip()) <= maximum, f"{field} 须为 1–{maximum} 个字符")
    return value.strip() if value else None


def _moment(value, field, end):
    """Unix seconds, or a UTC day: its start for ``since``, the start of the next day for ``until`` (exclusive)."""
    if value is None:
        return None
    value = str(value).strip()
    if DATE.fullmatch(value):
        try:
            start = calendar.timegm(time.strptime(value, "%Y-%m-%d"))
        except ValueError:
            require(False, f"{field} 须为 Unix 秒或 YYYY-MM-DD")
        return (start + 86400, False) if end else (start, True)
    require(re.fullmatch(r"\d{1,12}(\.\d+)?", value) is not None, f"{field} 须为 Unix 秒或 YYYY-MM-DD")
    return float(value), True


def cursor(created, rid):
    return base64.urlsafe_b64encode(json.dumps([created, rid]).encode()).decode().rstrip("=")


def after(value):
    """The (created, id) a page's cursor stands for; 400 for anything else."""
    try:
        created, rid = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        assert isinstance(created, (int, float)) and isinstance(rid, str) and len(rid) <= 100
        return float(created), rid
    except Exception:
        require(False, "cursor 无效：请使用上一页返回的 next")


def criteria(status=None, lifecycle=None, space=None, repo=None, branch=None, stage=None, tool=None, client=None,
             submitter=None, since=None, until=None, q=None, cursor=None):
    from .domain import RUN_STATES, TOOLS
    from .policies import repo_of
    from .requests import CLIENTS
    states = [status] if isinstance(status, str) else list(status or [])
    require(all(s in RUN_STATES for s in states) and len(states) <= len(RUN_STATES), "status 不是会话的状态")
    require(lifecycle in (None, *LIFECYCLES), "lifecycle 只能是 open 或 closed")
    require(stage in (None, *STAGES), "stage 只能是 " + " / ".join(STAGES))
    require(tool in (None, *TOOLS), "tool 须为 " + " / ".join(TOOLS))
    require(client in (None, *CLIENTS), "client 不是已知的客户端")
    repo = _text(repo, "repo")
    if repo and ("://" in repo or "@" in repo):   # a pasted address: the same host/owner/repo the Sessions are compared by
        repo = repo_of({"type": "git", "url": repo}) or repo
    q = _text(q, "q")
    return {"status": states, "lifecycle": lifecycle, "space": _text(space, "space", 100), "stage": stage, "tool": tool,
            "client": client, "submitter": _text(submitter, "submitter", 100),
            "repo": repo.lower().removesuffix(".git") if repo else None, "branch": _text(branch, "branch"),
            "since": _moment(since, "since", False), "until": _moment(until, "until", True),
            "words": q.lower().split() if q else [], "after": after(cursor) if cursor else None}


def sql(found):
    """The query for what the runs table holds, newest first (ties by id, so a page's cursor is exact)."""
    where, params = ["lifecycle!='deleted'"], []   # a deleted Session is gone to its users

    def add(clause, *values):
        where.append(clause)
        params.extend(values)
    if found["status"]:
        add(f"status IN ({','.join('?' * len(found['status']))})", *found["status"])
    if found["lifecycle"]:
        add("lifecycle=?", found["lifecycle"])
    if found["space"] == "personal":
        add("team_id IS NULL")
    elif found["space"]:
        add("team_id=?", found["space"])
    if found["submitter"]:
        add("owner_id IN (SELECT id FROM users WHERE id=? OR username=?)", found["submitter"], found["submitter"])
    if found["client"]:
        add("request_id IN (SELECT id FROM requests WHERE client=?)", found["client"])
    if found["since"]:
        add("created>=?", found["since"][0])
    if found["until"]:
        value, inclusive = found["until"]
        add("created<=?" if inclusive else "created<?", value)
    return ("SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created DESC, id DESC",
            params)


def stage(tasks):
    """The first task not yet accepted — 'plan' for a planning task — or 'done'."""
    current = next((t for t in tasks if t["status"] != "accepted"), None)
    if current is None:
        return "done"
    if current["id"] == "plan" or current["id"].startswith("replan-"):
        return "plan"
    return current["spec"].get("kind")


def matches(db, run, found):
    """Whether a Session the actor may read meets what the runs table cannot answer: repository, branch, stage, tool, text."""
    needs_tasks = found["stage"] or found["tool"] or found["words"]
    source, snapshot = json.loads(run["source"]), json.loads(run["snapshot"])
    if found["repo"]:
        from .policies import repo_of
        if found["repo"] not in (repo_of(source) or "\0"):
            return False
    if found["branch"]:
        branches = [source.get("ref"), (snapshot.get("publish") or {}).get("branch"), (snapshot.get("publish_policy") or {}).get("branch")]
        if not any(found["branch"].lower() in str(b).lower() for b in branches if b):
            return False
    if not needs_tasks:
        return True
    tasks = [{**dict(r), "spec": json.loads(r["spec"])} for r in
             db.execute("SELECT id,status,spec,current_attempt FROM tasks WHERE run_id=? ORDER BY ordinal", (run["id"],))]
    if found["stage"] and stage(tasks) != found["stage"]:
        return False
    if found["tool"] and not any(t["spec"].get("tool") == found["tool"] for t in tasks):
        return False
    if found["words"]:
        from .requests import NAMES
        planned = snapshot.get("planning") or {}
        client = db.execute("SELECT client FROM requests WHERE id=?", (run["request_id"],)).fetchone() if run.get("request_id") else None
        parts = [run["id"], snapshot.get("name"), run["input"], run["error"], planned.get("intent"), planned.get("summary"),
                 (snapshot.get("replan") or {}).get("reason"), client and NAMES.get(client["client"])]   # D55: 「Codex」
        for t in tasks:
            parts += [t["spec"].get("name"), t["spec"].get("prompt")]
            if t["current_attempt"]:
                row = db.execute("SELECT evidence,error FROM attempts WHERE id=?", (t["current_attempt"],)).fetchone()
                if row:
                    parts += [((json.loads(row["evidence"] or "{}").get("outcome") or {}).get("summary")), row["error"]]
        text = " ".join(str(p) for p in parts if p).lower()
        if not all(word in text for word in found["words"]):
            return False
    return True
