"""D74 (owner 2026-10-25, 方案一「接手」): a Session that runs on someone else's own boundary, taken over to one's own.
What moves between the two Workers: the Session's branch with its whole history (D58's full bundle) and the files of its
attempts the next tasks read (handoffs, outcomes) — one tar the new boundary adopts as the same workspace."""
import base64
import io
import json
import re
import tarfile
import time

from . import chat_changes, credentials, evaluation, journal, knowledge, revisions, sources, transport
from .db import dumps, uid
from .domain import moving, worker_source
from .security import Error, require

ATTEMPT = re.compile(r"attempt-[a-f0-9]{32}")
# What a later task, a review or a diagnosis reads of an earlier attempt; the old Gateway's ids (dispatch.json) stay behind
FILES = ("handoff.md", "outcome.json", "decision.json", "instructions.md", "context.json")
LIMIT = 20 * 1024 * 1024   # the attempt files of one Session


def pack(bundle, files):
    """The tar a takeover sends to the new boundary: ``session.bundle`` and ``attempts/<attempt>/<file>``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in (("session.bundle", bundle), *((f"attempts/{k}", v.encode()) for k, v in sorted(files.items()))):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def unpack(data):
    """The bundle and the attempt files of a takeover's tar, or ValueError: regular files only, ``session.bundle`` once and
    ``attempts/<attempt>/<one of FILES>`` — never a path elsewhere, a link or anything the old boundary should keep."""
    bundle, files = None, {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                if not member.isreg():
                    raise ValueError("接手的内容只能是普通文件")
                if member.name == "session.bundle" and bundle is None:
                    bundle = archive.extractfile(member).read()
                    continue
                parts = member.name.split("/")
                if len(parts) != 3 or parts[0] != "attempts" or not ATTEMPT.fullmatch(parts[1]) or parts[2] not in FILES \
                        or member.name[len("attempts/"):] in files:
                    raise ValueError("接手的内容含有不认识的文件：" + member.name[:120])
                files[member.name[len("attempts/"):]] = archive.extractfile(member).read()
    except tarfile.TarError as exc:
        raise ValueError("接手的内容不是有效的归档") from exc
    if bundle is None:
        raise ValueError("接手的内容缺少会话分支")
    return bundle, files


# The platform's half (D74 §4): the move the takeover command recorded, carried out in the background — a large
# repository's bundle may take longer than a request. Each step that fails leaves the Session where it was.
BUNDLE = 100 * 1024 * 1024   # as a pull (the Worker's PUBLISH_LIMIT)
TRIES = 3   # the new boundary's /adopt, when it is briefly unreachable


def move(service, rid):
    """Carry out the Session's takeover: stop its remote preview, commit what a chat left in the old workspace, bring
    its branch and attempt files from the old boundary to the new one, then — in one transaction — run it there as the
    person who took it over. A failure is recorded with its reason, and the Session stays as it was."""
    with service.db.read() as db:
        run = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        takeover = moving(run) if run else None
        if takeover is None:
            return None
        run = dict(run)
        old = db.execute("SELECT config, retired FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
        new = db.execute("SELECT * FROM workers WHERE id=?", (takeover["to"],)).fetchone()
        person = db.execute("SELECT id, username, admin FROM users WHERE id=? AND active=1", (takeover["by"],)).fetchone()
    try:
        if person is None:
            raise Error(409, "takeover_failed", "接手人的账号已停用")
        if new is None or not new["enabled"] or new["retired"]:
            raise Error(409, "takeover_failed", "要接手到的执行环境已停用或退役")
        actor = {**dict(person), "client": "takeover"}
        moved = None
        if run["native_parent"] and run["workspace"]:   # a Session never prepared is prepared afresh there instead
            if old is None or old["retired"]:
                raise Error(409, "takeover_failed", "原执行环境已退役，取不出会话的工作区")
            config = json.loads(old["config"])
            service._stop_app(rid, "takeover")   # best effort: the sweep stops one it could not reach
            attempts = transport.worker_request(config, "/pull/attempts", {"run_id": rid}, timeout=120)
            project = _project(run, attempts.get("project"))
            _commit_chat(service, run, config, actor, project)
            bundle = _bundle(config, rid)
            moved = _adopt(json.loads(new["config"]), rid, pack(bundle, _masked(attempts.get("files") or {})), project,
                           _scope(run, takeover, new))
        return _finish(service, rid, takeover, actor, moved)
    except Error as exc:
        if exc.status == 404 and exc.code in ("not_found", "worker_error"):
            return _failed(service, rid, takeover, "执行环境的 Worker 版本较旧，还不能接手。请平台管理员升级执行环境", "worker_outdated")
        return _failed(service, rid, takeover, str(exc), exc.code)
    except Exception as exc:   # the class only: a message may carry transport details
        return _failed(service, rid, takeover, f"接手未完成（{type(exc).__name__}）", "takeover_failed")


def _project(run, record):
    """The record the new boundary rebuilds the workspace from: the old Worker's kind, baseline and branch, which must
    be this Session's source as the platform holds it."""
    source = worker_source(json.loads(run["source"]))
    try:
        expected = (None, "new") if source.get("type") == "new" else sources.normalize_source(source.get("url"))
    except ValueError as exc:
        raise Error(409, "takeover_failed", str(exc)) from None
    if not isinstance(record, dict) or (record.get("source"), record.get("kind")) != expected:
        raise Error(409, "takeover_failed", "原执行环境的工作区与会话的来源不一致，不能接手")
    return {k: record.get(k) for k in ("kind", "source", "requested_ref", "commit", "branch")}


def _commit_chat(service, run, config, actor, project):
    """What a chat left in the old workspace, committed there as the Session's next candidate before the branch moves —
    D73's 「对话中的改动」, without running checks (that environment no longer runs it). A credential among them stops
    the takeover: it would leave the creator's environment."""
    rid = run["id"]
    with service.db.read() as db:
        head = chat_changes.latest(db, rid) or {"sha": project.get("commit")}
        ordered = [dict(t) for t in db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal", (rid,))]
    if not ordered or not head.get("sha") or not chat_changes._changed(config, rid, head):
        return None
    aid = uid("attempt")
    candidate = transport.worker_request(config, "/candidate", {"run_id": rid, "attempt_id": aid, "mode": "commit", "parent": head["sha"],
                                                      "message": f"KiroCrew ON：对话中的改动（{rid}）"}, timeout=180)
    require(not candidate.get("secrets"), "原执行环境里对话中的改动有疑似凭证或 .env 文件，没有接手："
            + "、".join(candidate["secrets"][:20]) + "。请会话创建者先在聊天里移除它们", 409, "chat_secret")
    if not candidate.get("changed"):
        return None
    writer = next((t for t in reversed(ordered) if json.loads(t["spec"]).get("kind") in evaluation.WRITERS), ordered[-1])
    chat = {"attempt": aid, "candidate": candidate, "writer": writer["id"], "rerun": False, "commands": 0, "job": None,
            "unchecked": True}
    with service.db.transaction() as db:
        chat_changes.apply(db, actor, rid, chat)
    return aid


def _bundle(config, rid):
    """The Session's branch with its whole history (D58's full pull), checked there like a pull."""
    with transport.worker_stream(config, "/pull/bundle", {"run_id": rid, "full": True}, timeout=300) as response:
        require(response.headers.get("X-Pull-Full") == "1", "原执行环境没有给出完整的会话分支", 409, "takeover_failed")
        data = response.read(BUNDLE + 1)
    require(0 < len(data) <= BUNDLE, f"会话分支超过 {BUNDLE // 1048576} MB，不能接手", 413, "takeover_too_large")
    return data


def _masked(files):
    """The attempt files as the platform keeps what Agents wrote (D57 §3.3): a credential's text never moves."""
    return {name: sources.mask_text(text)[0] for name, text in files.items() if isinstance(text, str)}


def _scope(run, takeover, worker):
    """The repository mark of the person it runs as there (D46 P4), where lessons are written on that boundary."""
    config, source = json.loads(worker["config"]), worker_source(json.loads(run["source"]))
    if not knowledge.applies(json.loads(run["snapshot"]), source, worker["scope"], config):
        return None
    return knowledge.key(source, knowledge.space({**run, "executor_id": takeover["by"]}))


def _adopt(config, rid, data, project, scope):
    """The new boundary's /adopt: tried again while it is briefly unreachable, never after it refused."""
    header = base64.b64encode(json.dumps({"run_id": rid, "project": project, "knowledge_scope": scope}).encode()).decode()
    for attempt in range(TRIES):
        try:
            return transport.worker_upload(config, "/adopt", data, {"X-Octopus-Adopt": header}, timeout=600)
        except Error as exc:
            if exc.status != 503 or attempt == TRIES - 1:
                raise
            time.sleep(5 * (attempt + 1))


def _finish(service, rid, takeover, actor, moved):
    """One transaction: the Session runs on the new boundary as the person who took it over — its workspace and native
    parent there, its handoffs where they now are, a credential they may use (or none), a version and a revision."""
    now = time.time()
    with service.db.transaction() as db:
        run = dict(db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())
        current = moving(run)
        if current is None or current.get("requested_at") != takeover["requested_at"]:
            return None
        if moved:
            for row in db.execute("SELECT id, evidence FROM attempts WHERE run_id=?", (rid,)).fetchall():
                evidence = json.loads(row["evidence"] or "{}")
                if evidence.get("handoff_path"):   # the old host's path means nothing on the new one
                    path = (moved.get("handoffs") or {}).get(row["id"])
                    evidence = {**evidence, "handoff_path": path} if path else \
                        {k: v for k, v in evidence.items() if k != "handoff_path"}
                    # one Session-level event (run.taken_over below) says it; not one per Attempt
                    journal.change(db, "attempts", row["id"], journal.person(actor), "run.taken_over", quiet=("evidence",),
                                   evidence=dumps(evidence))
        snapshot = {k: v for k, v in json.loads(run["snapshot"]).items() if k != "takeover"}
        after = {**run, "executor_id": actor["id"], "worker_id": takeover["to"]}
        snapshot["credentials"] = _rebound(db, snapshot.get("credentials") or {}, after, actor, now)
        snapshot["placement"] = {"kind": "taken_over", "from": takeover["from"], "by": actor["id"], "by_name": actor["username"],
                                 "at": now}
        previous = db.execute("SELECT u.username FROM users u JOIN workers w ON w.owner_id=u.id WHERE w.id=?",
                              (takeover["from"],)).fetchone()
        version = run["version"] + 1
        bound = {"workspace": moved["workspace"], "native_parent": moved["native_parent"]} if moved else \
            {"workspace": None, "native_parent": None}   # prepared afresh there
        journal.change(db, "runs", rid, journal.person(actor), "run.taken_over",
                       event={"version": version, "from": takeover["from"], "to": takeover["to"]},
                       worker_id=takeover["to"], executor_id=actor["id"], snapshot=dumps(snapshot), version=version,
                       updated=now, **bound)
        source = f"（来自 {previous['username']} 的执行环境）" if previous else ""
        revisions.record(db, rid, version, "taken_over", f"{actor['username']} 接手了会话{source}", actor["id"], now)
    return "taken_over"


def _rebound(db, pinned, run, actor, now):
    """The source credential after a takeover: kept while the person it now runs as may use it; else the one they would be
    given for this repository; else none, remembered as removed (they bind one of their own to publish)."""
    bound = pinned.get("source")
    if not bound:
        return pinned
    row = db.execute("SELECT * FROM credentials WHERE id=?", (bound["id"],)).fetchone()
    if credentials.opens_for(db, row, run):
        return pinned
    try:
        replaced = credentials.resolve(db, actor, json.loads(run["source"]), run["team_id"])
    except Error:
        replaced = None   # several fit equally: theirs to choose
    if replaced:
        return {"source": replaced}
    summary = {k: bound.get(k) for k in ("id", "name", "scope", "hint")}
    return {"source": None, "removed": {**summary, "by": {"id": actor["id"], "username": actor["username"]}, "at": now,
                                        "reason": "takeover"}}


def _failed(service, rid, takeover, reason, code):
    """The takeover did not happen: why, on the Session, which stays where it was; it may be asked for again."""
    reason = sources.mask_text(reason)[0]
    with service.db.transaction() as db:
        run = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        current = moving(run) if run else None
        if current is None or current.get("requested_at") != takeover["requested_at"]:
            return None
        snapshot = json.loads(run["snapshot"])
        snapshot["takeover"] = {**current, "state": "failed", "reason": reason, "code": code, "failed_at": time.time()}
        journal.change(db, "runs", rid, journal.ENGINE, "run.takeover_failed", reason, event={"code": code},
                       snapshot=dumps(snapshot), updated=time.time())
    return "failed"
