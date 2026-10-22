"""Waiting that ends on its own (D43): what a blocked or waiting Session waits for, and continuing it once that is back.

Every condition is judged from the platform's own records; what only the Worker knows — its Kiro login — is written onto
the waiting Attempt by the Engine's health check (``observe``). Continuing is the platform doing the user's continue of
the failed task: it is not the user's word, so the recovery budget neither resets nor pays for it (D42).
"""
import json
import time

from . import journal
from .db import dumps
from .gates import EXPIRED

KIRO_BACK = "Kiro 登录已恢复，自动继续"
WORKER_BACK, PLACE_BACK = "执行环境已重新启用，自动继续", "执行环境已重新服务此空间，自动继续"
CREDENTIAL_BACK = "仓库凭证已可用，自动继续"
DISABLED = "Worker 已停用"   # the Engine's reason when a Session's Worker is disabled under it
# Every refusal of a credential that its manager or the Session's owner can lift ends so (credentials.material).
CREDENTIAL_REFUSED = "更换凭证后续作"
RESUMABLE = {"preparing", "ready", "running", "waiting_approval", "dispatch_unknown"}
# What a person reads about a KiroCrew queue (D60): the kind of wait, never the host's numbers (those stay in the event).
QUEUE_REASONS = {"low_memory": "执行环境内存不足", "posture_critical": "执行环境负载过高", "adaptive_cap_zero": "执行环境暂停接收新任务"}


def queue_title(reason):
    return "排队：等待执行环境资源（" + QUEUE_REASONS.get(reason, "执行环境资源不足") + "）"


def quota_title(full, team_session):
    """The wait for a place under a limit (D61), naming the scope that is full."""
    who = (f"Team「{full.get('team_name')}」" if full["scope"] == "team" else "提交者" if team_session else "你")
    return f"排队：已达到并发上限（{who}同时运行的任务已达 {full['used']} 个，上限 {full['limit']}）"


def quota_attention(run):
    mark = run.get("quota_wait") if run["status"] == "ready" else None
    if not mark:
        return None
    return {"title": quota_title(mark, bool(run.get("team_id"))),
            "detail": "同时运行的任务数已到上限，此会话的下一个任务在排队。",
            "action": "无需操作：有任务结束后按排队先后自动开始；需要更多并发请联系平台管理员"
                      + ("或 Team owner。" if mark["scope"] == "team" else "。"), "task_id": None}


def queue_attention(run, task):
    """「需要处理」 of a Session whose running task KiroCrew deferred (D60): nothing to do, it starts on its own."""
    mark = ((((task or {}).get("attempts") or [{}])[-1].get("evidence") or {}).get("queued") or {}) \
        if run["status"] == "running" else {}
    if not mark.get("since") or mark.get("until"):
        return None
    return {"title": queue_title(mark.get("reason")),
            "detail": "执行环境暂时没有资源启动此任务；排队期间不计无进展与时长预算。",
            "action": "无需操作：资源可用后自动开始。长时间排队时请执行环境的管理员结束空闲的进程或扩容。",
            "task_id": task["id"]}


def kiro_login_failure(evidence, error, tool):
    """A Kiro task whose native run failed on an expired login — the one login the platform can read. An ``auth`` failure
    the Agent reports itself (a token without push rights, a quota) is not one, and other tools' logins stay with a person."""
    return evidence.get("failure_type") == "auth" and tool == "kiro" and bool(EXPIRED.search(error or ""))


def handed_over(mark):
    """A good login seen after the failure without an expiry first: fixed before the check, or never the cause — the
    platform does not rerun it and stops watching (a person continues)."""
    return bool(mark.get("ok_seen_at")) and not mark.get("expired_at")


def kiro_waiting(db, run_id=None):
    """Blocked Sessions whose failed Attempt is a Kiro login failure (``kiro_login_failure``)."""
    found = []
    for row in db.execute(
            "SELECT r.id AS run_id, r.worker_id, t.id AS task_id, a.id AS attempt_id, a.evidence, a.spec, a.error FROM runs r "
            "JOIN tasks t ON t.run_id=r.id AND t.status='blocked' AND t.current_attempt IS NOT NULL "
            "JOIN attempts a ON a.id=t.current_attempt "
            "WHERE r.status='blocked' AND r.lifecycle='open' AND r.desired='active' AND r.worker_id IS NOT NULL"
            + (" AND r.id=?" if run_id else ""), (run_id,) if run_id else ()):
        evidence = json.loads(row["evidence"] or "{}")
        if kiro_login_failure(evidence, row["error"], json.loads(row["spec"]).get("tool")):
            found.append({**dict(row), "evidence": evidence})
    return found


def observe(db, worker_id, login, now):
    """What a health check saw of the Worker's Kiro login, for the Attempts waiting on it: an expiry seen after the failure,
    then a good login, is what lets ``resume`` continue — a failure the login did not cause is never rerun on its own."""
    for item in kiro_waiting(db):
        if item["worker_id"] != worker_id:
            continue
        mark = dict(item["evidence"].get("waiting") or {"for": "kiro_login"})
        if handed_over(mark):
            continue
        if login == "expired" and not mark.get("expired_at"):
            mark["expired_at"] = now
        elif login == "ok" and mark.get("expired_at") and not mark.get("recovered_at"):
            mark["recovered_at"] = now
        elif login == "ok" and not mark.get("expired_at"):
            mark["ok_seen_at"] = now
        else:
            continue
        journal.change(db, "attempts", item["attempt_id"], journal.ENGINE, "attempt.waiting", quiet=("evidence",),
                       evidence=dumps({**item["evidence"], "waiting": mark}))


def _continue(db, run_id, task_id, reason, condition):
    journal.change(db, "tasks", (run_id, task_id), journal.ENGINE, "task.reset", reason, status="pending", current_attempt=None)
    _resumed(db, run_id, "ready", reason, condition)


def _resumed(db, run_id, status, reason, condition):
    # The time it waited is not automatic work: the time budget does not count it (D42), until the user's next word.
    row = db.execute("SELECT snapshot, created, updated FROM runs WHERE id=?", (run_id,)).fetchone()
    snapshot, now = json.loads(row["snapshot"]), time.time()
    since = snapshot.get("budget_since") or row["created"]
    snapshot["budget_waited"] = (snapshot.get("budget_waited") or 0) + max(0.0, now - max(row["updated"], since))
    if snapshot.get("quota_wait"):   # D61: a wait for a place that went on through this one is not credited twice
        snapshot["quota_wait"]["credited"] = now
    journal.change(db, "runs", run_id, journal.ENGINE, "run.budget", quiet=("snapshot",), snapshot=dumps(snapshot))
    journal.change(db, "runs", run_id, journal.ENGINE, "run.resumed", reason, event={"status": status, "condition": condition},
                   status=status, error=None, updated=now)


def _before_disabled(db, run_id):
    """The state the Session was in when its Worker was disabled under it: a task may still be running there."""
    for row in db.execute("SELECT payload FROM events WHERE run_id=? AND kind='run.state' ORDER BY id DESC LIMIT 5", (run_id,)):
        change = (json.loads(row["payload"]).get("changes") or {}).get("status")
        if change and change[1] == "blocked":
            return change[0] if change[0] in RESUMABLE else "ready"
    return "ready"


def resume(db, skip=frozenset()):
    """Continue every Session whose wait is over (called by the Engine each cycle; ``skip``: Sessions being advanced)."""
    from .credentials import opens_for
    from .domain import unserved
    for item in kiro_waiting(db):
        if item["run_id"] not in skip and (item["evidence"].get("waiting") or {}).get("recovered_at"):
            _continue(db, item["run_id"], item["task_id"], KIRO_BACK, "kiro_login")
    active = "r.lifecycle='open' AND r.desired='active'"
    for run in db.execute(f"SELECT r.id FROM runs r JOIN workers w ON w.id=r.worker_id WHERE {active} AND r.status='blocked' "
                          "AND r.error=? AND w.enabled=1", (DISABLED,)).fetchall():
        if run["id"] not in skip:
            _resumed(db, run["id"], _before_disabled(db, run["id"]), WORKER_BACK, "worker_enabled")
    for run in db.execute(f"SELECT r.* FROM runs r WHERE {active} AND r.status='waiting_worker' AND r.worker_id IS NOT NULL").fetchall():
        if run["id"] not in skip and not unserved(db, run):   # as an assignment would continue it
            _resumed(db, run["id"], "ready" if run["native_parent"] else "preparing", PLACE_BACK, "environment")
    for run in db.execute(f"SELECT r.* FROM runs r WHERE {active} AND r.status='blocked' AND r.native_parent IS NULL "
                          "AND instr(r.error, ?) > 0 AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.run_id=r.id)",
                          (CREDENTIAL_REFUSED,)).fetchall():
        bound = (json.loads(run["snapshot"]).get("credentials") or {}).get("source")
        row = db.execute("SELECT * FROM credentials WHERE id=?", (bound["id"],)).fetchone() if bound else None
        if run["id"] not in skip and row is not None and opens_for(db, row, run):
            _resumed(db, run["id"], "preparing", CREDENTIAL_BACK, "credentials")
