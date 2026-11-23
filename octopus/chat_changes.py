"""D73 (owner 2026-10-23): a person works in the Session's native chat, with the parent Session's Agent, then continues on
the platform from the state the chat left. A continue first commits what the chat changed in the workspace as the
Session's next candidate — 「对话中的改动」, an Attempt of the last task that writes code, as D58's local sync is — because
otherwise a review's put-back (sources.rewind) would restore those files and the next code task would take them as its
own. The tasks after it run again on it. A credential among them refuses the continue; a diagnosis request is refused
while they are uncommitted (it would diagnose a failure the chat may have changed). Nothing new is asked of KiroCrew: the
Worker's /candidate does it. Design: docs/superpowers/specs/2026-10-23-chat-and-structured-iteration-design.md §5.3."""
import json
import time

from . import journal
from .db import dumps, uid
from .security import Error, require
from .transport import worker_request

FILES = 50   # the file names a record keeps
SKIPPED = {"workspace_missing", "candidate_unsupported"}   # nothing to compare with: left as it was


def latest(db, rid):
    """The candidate the Session's branch holds — Engine._head_candidate's answer (the newest that moved it)."""
    for row in db.execute("SELECT evidence FROM attempts WHERE run_id=? AND evidence LIKE '%\"candidate\"%' "
                          "ORDER BY updated DESC, created DESC", (rid,)):
        candidate = json.loads(row["evidence"] or "{}").get("candidate")
        if candidate and candidate.get("sha") and candidate.get("moved", True):
            return candidate
    return None


def _settled(service, actor, rid, version=None, directing=True):
    """The run, its tasks, its Worker's connection and its branch's candidate when the workspace may hold what a chat
    changed; None when the command would be refused anyway (its own checks then say why) or there is nothing to compare.
    ``directing=False``: only to look — a takeover's dialog (D74) shows it to someone the boundary does not take
    instructions from."""
    from .domain import SETTLED, active_team, directs, moving, run_access, unserved
    with service.db.read() as db:
        try:
            run = run_access(db, actor, rid)
            if run["team_id"]:
                active_team(db, run["team_id"])
        except Error:
            return None
        # the continue's own checks (operate): one it would refuse never reaches the Worker
        if (version is not None and version != run["version"]) or run["lifecycle"] != "open" or run["status"] not in SETTLED \
                or moving(run) \
                or not run["worker_id"] or not run["workspace"] or unserved(db, run) \
                or (directing and directs(db, actor, run) is not None) \
                or json.loads(run["snapshot"]).get("purpose") == "workflow_design":
            return None
        head = latest(db, rid)
        if head is None:
            return None
        worker = db.execute("SELECT config FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        ordered = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
    return {"run": dict(run), "ordered": ordered, "config": json.loads(worker["config"]), "head": head} if ordered else None


def _changed(config, rid, head):
    """Whether the workspace holds what no candidate has: files changed against the branch's candidate, or commits on it.
    False when the Worker cannot say (before D37, or no workspace)."""
    state = _check(config, rid, head)
    return state is not None and bool(state["files"] or state["commits"])


def _check(config, rid, head):
    """What the workspace holds beyond the branch's candidate: changed tracked files, files created since the platform last
    looked (a Worker before this fix leaves those out) and whether commits were made on it; None when it cannot say."""
    try:
        state = worker_request(config, "/candidate", {"run_id": rid, "mode": "check", "expected": head["sha"], "untracked": True},
                               timeout=60)
    except Error as exc:
        if exc.status == 404 or exc.code in SKIPPED:
            return None
        raise
    files = list(dict.fromkeys([*(state.get("changed_files") or []), *(state.get("untracked") or [])]))
    return {"files": files, "commits": state.get("head") != head["sha"]}


def collect(service, actor, rid, body):
    """Before a continue's transaction (it calls the Worker): the chat's change committed as the next candidate, and the
    checks of the task it is put on started unless the continue reruns that task anyway. None when there is nothing."""
    from . import evaluation
    from .domain import rerun_plan, runs_checks
    state = _settled(service, actor, rid, body.get("expected_version"))
    if state is None:
        return None
    ordered, config = state["ordered"], state["config"]
    index = next((i for i, t in enumerate(ordered) if t["id"] == body.get("task_id")), None)
    if index is None:
        return None
    try:
        reset = set(rerun_plan(ordered, index, body.get("rerun"))[0])
    except (ValueError, TypeError, KeyError):
        return None   # the continue refuses it
    if not _changed(config, rid, state["head"]):
        return None
    aid = uid("attempt")
    try:
        candidate = worker_request(config, "/candidate", {"run_id": rid, "attempt_id": aid, "mode": "commit", "parent": state["head"]["sha"],
                                                          "message": f"KiroCrew ON：对话中的改动（{rid}）"}, timeout=180)
    except Error as exc:
        if exc.status == 404 or exc.code in SKIPPED:
            return None
        raise
    require(not candidate.get("secrets"), "对话中的改动里有疑似凭证或 .env 文件，会话分支未移动：" + "、".join(candidate["secrets"][:20])
            + "。请先在聊天里移除它们，再续作", 409, "chat_secret")
    if not candidate.get("changed"):
        return None
    writer = next((t for t in reversed(ordered) if json.loads(t["spec"]).get("kind") in evaluation.WRITERS), ordered[-1])
    spec = json.loads(writer["spec"])
    rerun = writer["id"] in reset
    commands = [] if rerun else ((spec.get("verify") or []) if runs_checks(spec) else [])
    job = None
    if commands:
        try:
            job = worker_request(config, "/verify/start", {"run_id": rid, "attempt_id": aid, "commands": commands})["job"]
        except Error:   # the engine starts them (as after a Worker restart: check_unknown)
            job = "unstarted-" + aid
    return {"attempt": aid, "candidate": candidate, "writer": writer["id"], "rerun": rerun, "commands": len(commands), "job": job}


def apply(db, actor, rid, chat):
    """In the continue's transaction, after its own changes: the Attempt holding the chat's change on its task, and the
    tasks after that task that are still accepted reset (they judged the old state). Returns the revision's note and
    whether checks are running (the Session then runs them first)."""
    from . import gates
    now, who = time.time(), journal.person(actor)
    writer = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, chat["writer"])).fetchone()
    number = db.execute("SELECT count(*) FROM attempts WHERE run_id=? AND task_id=?", (rid, writer["id"])).fetchone()[0] + 1
    candidate, files = chat["candidate"], chat["candidate"].get("files") or 0
    records = [gates.record("native_completed", "skip", "对话中的改动（原生聊天）"),
               gates.record("diff_present", "pass", f"{files} 个文件"), gates.record("no_secrets", "pass")]
    if chat.get("unchecked"):   # D74: committed as the Session moved to another boundary
        records.append(gates.record("checks_pass", "skip", "接手前提交，没有运行检查"))
    elif chat["rerun"]:
        records.append(gates.record("checks_pass", "skip", "任务将重新执行"))
    elif not chat["commands"]:
        records.append(gates.record("checks_pass", "skip", "任务没有检查命令"))
    names = list(candidate.get("names") or [])
    evidence = {"chat": {"by": {"id": actor["id"], "username": actor.get("username")}, "files": names[:FILES],
                         "head": candidate["sha"]},
                "candidate": {**{k: candidate.get(k) for k in ("sha", "parent", "changed", "files", "insertions", "deletions", "rewritten")},
                              "moved": True, "chat": True, "names": names[:200]},
                "gates": records}
    status = "running" if chat["job"] else "accepted"
    if chat["job"]:
        evidence["checks"] = {"job": chat["job"], "started": now, "commands": chat["commands"]}
    else:
        evidence["warnings"] = gates.warnings(records)
    db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (chat["attempt"], rid, writer["id"], number, writer["spec"], status, dumps(evidence),
                f"对话中的改动：{files} 个文件（{actor.get('username') or actor['id']}）", now, now))
    if not chat["rerun"]:   # the continue left the task as it was: this Attempt is its current one
        journal.change(db, "tasks", (rid, writer["id"]), who, "attempt.created", "对话中的改动",
                       event={"id": chat["attempt"], "task_id": writer["id"], "number": number},
                       current_attempt=chat["attempt"], status=status)
        journal.change_rows(db, "tasks", "run_id=? AND ordinal>? AND status='accepted'", (rid, writer["ordinal"]), who,
                            "task.reset", "对话中的改动", status="pending", current_attempt=None)
    note = f"对话中的改动：{files} 个文件"
    journal.record(db, rid, "run.chat_changes", who, reason=f"{note}（提交 {candidate['sha'][:7]}）", files=files,
                   task=writer["id"], attempt=chat["attempt"], head=candidate["sha"])
    return note, bool(chat["job"])


def pending(service, actor, rid):
    """The continue and takeover dialogs' reminder (owner 2026-10-23: 只提醒、不拦截): the files the workspace holds that the
    branch's candidate has not, and whether commits were made on it. It only looks; nothing when it cannot say."""
    state = _settled(service, actor, rid, directing=False)
    found = _check(state["config"], rid, state["head"]) if state else None
    files = (found or {}).get("files") or []
    return {"files": files[:FILES], "count": len(files), "commits": bool(found and found["commits"])}


def refuse(service, actor, rid):
    """A diagnosis request while the workspace holds the chat's uncommitted change: refused, pointing at continue."""
    state = _settled(service, actor, rid)
    if state is not None and _changed(state["config"], rid, state["head"]):
        raise Error(409, "chat_changes", "工作区里有对话中的改动尚未提交：请用续作，续作会先把它们提交为新的候选，再继续")
