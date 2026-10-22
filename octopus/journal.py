"""Session change journal (D34 §4): the only way to update a Session's rows. Each write compares old and new values and
records, in the same transaction, who changed which fields from what to what and why — as an event on the Session."""
import hashlib
import time

from .db import dumps

ENGINE, WORKER, KIROCREW, RECOVERY = ({"kind": kind} for kind in ("engine", "worker", "kirocrew", "recovery"))
KEYS = {"runs": ("id",), "tasks": ("run_id", "id"), "attempts": ("id",), "recoveries": ("id",)}
QUIET = {"updated"}   # timestamps change on every write and are not a change worth recording
LONG = 500


def client(label):
    """Where a user's change came from, by the label of the token that made it."""
    return "console" if label == "Web login" else "mcp" if "MCP" in (label or "") else "api"


def person(actor):
    return {"kind": "user", "id": actor["id"], "username": actor.get("username"), "client": actor.get("client", "api")}


def shown(value):
    """A value as the journal keeps it: long text by length and hash, never in full."""
    if isinstance(value, str) and len(value) > LONG:
        return {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


def record(db, rid, kind, actor, changes=None, reason=None, **extra):
    payload = {**extra, "actor": actor, **({"changes": changes} if changes else {}), **({"reason": reason} if reason else {})}
    db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)", (rid, kind, dumps(payload), time.time()))


def _diff(row, fields, quiet=()):
    return {k: [shown(row[k]), shown(v)] for k, v in fields.items() if k not in QUIET and k not in quiet and row[k] != v}


def _run(table, row):
    return row["id"] if table == "runs" else row["run_id"]


def change(db, table, key, actor, kind, reason=None, event=None, quiet=(), **fields):
    """Write ``fields`` to one row (``tasks`` keyed by (run_id, id)) and record what changed; returns the changes.
    ``quiet`` fields are written but not recorded (a running task's partial reply would otherwise log every poll)."""
    columns = KEYS[table]
    where = " AND ".join(f"{column}=?" for column in columns)
    params = tuple(key) if isinstance(key, tuple) else (key,)
    row = db.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchone()
    if row is None:
        return {}
    db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) + f" WHERE {where}", (*fields.values(), *params))
    diff = _diff(row, fields, quiet)
    if diff:
        record(db, _run(table, row), kind, actor, diff, reason, **(event or {}))
    return diff


def change_rows(db, table, where, params, actor, kind, reason=None, event=None, **fields):
    """Write ``fields`` to every row matching ``where``; one event per Session lists each row's changes."""
    rows = db.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
    if not rows:
        return []
    db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) + f" WHERE {where}", (*fields.values(), *params))
    by_run = {}
    for row in rows:
        diff = _diff(row, fields)
        if diff:
            by_run.setdefault(_run(table, row), []).append({"id": row["id"], "changes": diff})
    for rid, items in by_run.items():
        record(db, rid, kind, actor, items, reason, **(event or {}))
    return [item for items in by_run.values() for item in items]


def remove_rows(db, table, where, params, actor, kind, reason=None, event=None):
    """Delete the rows matching ``where`` and record which went (D41: tasks replaced by a replan; their Attempts stay)."""
    rows = db.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
    if not rows:
        return []
    db.execute(f"DELETE FROM {table} WHERE {where}", params)
    gone = [row["id"] for row in rows]
    by_run = {}
    for row in rows:
        by_run.setdefault(_run(table, row), []).append(row["id"])
    for rid, ids in by_run.items():
        record(db, rid, kind, actor, None, reason, **{"removed": ids, **(event or {})})
    return gone
