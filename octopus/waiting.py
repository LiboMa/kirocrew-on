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
    journal.change(db, "runs", run_id, journal.ENGINE, "run.resumed", reason, event={"status": "ready", "condition": condition},
                   status="ready", error=None, updated=time.time())


def resume(db, skip=frozenset()):
    """Continue every Session whose wait is over (called by the Engine each cycle; ``skip``: Sessions being advanced)."""
    for item in kiro_waiting(db):
        if item["run_id"] not in skip and (item["evidence"].get("waiting") or {}).get("recovered_at"):
            _continue(db, item["run_id"], item["task_id"], KIRO_BACK, "kiro_login")
