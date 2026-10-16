"""Waiting that ends on its own (D43): what a blocked or waiting Session waits for, and continuing it once that is back.

Every condition is judged from the platform's own records; what only the Worker knows — its Kiro login — is written onto
the waiting Attempt by the Engine's health check (``observe``). Continuing is the platform doing the user's continue of
the failed task: it is not the user's word, so the recovery budget neither resets nor pays for it (D42).
"""
import json
import time

from . import journal
from .db import dumps

KIRO_BACK = "Kiro 登录已恢复，自动继续"
WORKER_BACK, PLACE_BACK = "执行环境已重新启用，自动继续", "执行环境已重新服务此空间，自动继续"
CREDENTIAL_BACK = "仓库凭证已可用，自动继续"
DISABLED = "Worker 已停用"   # the Engine's reason when a Session's Worker is disabled under it
# Every refusal of a credential that its manager or the Session's owner can lift ends so (credentials.material).
CREDENTIAL_REFUSED = "更换凭证后续作"
RESUMABLE = {"preparing", "ready", "running", "waiting_approval", "dispatch_unknown"}


def kiro_waiting(db, run_id=None):
    """Blocked Sessions whose failed Attempt is a Kiro task's login failure (failure_type ``auth``): only Kiro's login is
    one the platform can read. Other tools' logins stay with a person."""
    found = []
    for row in db.execute(
            "SELECT r.id AS run_id, r.worker_id, t.id AS task_id, a.id AS attempt_id, a.evidence, a.spec FROM runs r "
            "JOIN tasks t ON t.run_id=r.id AND t.status='blocked' AND t.current_attempt IS NOT NULL "
            "JOIN attempts a ON a.id=t.current_attempt "
            "WHERE r.status='blocked' AND r.lifecycle='open' AND r.desired='active' AND r.worker_id IS NOT NULL"
            + (" AND r.id=?" if run_id else ""), (run_id,) if run_id else ()):
        evidence = json.loads(row["evidence"] or "{}")
        if evidence.get("failure_type") == "auth" and json.loads(row["spec"]).get("tool") == "kiro":
            found.append({**dict(row), "evidence": evidence})
    return found


def observe(db, worker_id, login, now):
    """What a health check saw of the Worker's Kiro login, for the Attempts waiting on it: an expiry seen after the failure,
    then a good login, is what lets ``resume`` continue — a failure the login did not cause is never rerun on its own."""
    for item in kiro_waiting(db):
        if item["worker_id"] != worker_id:
            continue
        mark = dict(item["evidence"].get("waiting") or {"for": "kiro_login"})
        if login == "expired" and not mark.get("expired_at"):
            mark["expired_at"] = now
        elif login == "ok" and mark.get("expired_at") and not mark.get("recovered_at"):
            mark["recovered_at"] = now
        else:
            continue
        journal.change(db, "attempts", item["attempt_id"], journal.ENGINE, "attempt.waiting", quiet=("evidence",),
                       evidence=dumps({**item["evidence"], "waiting": mark}))


def _continue(db, run_id, task_id, reason, condition):
    journal.change(db, "tasks", (run_id, task_id), journal.ENGINE, "task.reset", reason, status="pending", current_attempt=None)
    _resumed(db, run_id, "ready", reason, condition)


def _resumed(db, run_id, status, reason, condition):
    journal.change(db, "runs", run_id, journal.ENGINE, "run.resumed", reason, event={"status": status, "condition": condition},
                   status=status, error=None, updated=time.time())


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
