"""How many tasks a person or a Team runs at once (D61, M4-4 part 2). An administrator sets the default per person and a
person's own limit; a Team's owner (or an administrator) the Team's. A scope nobody set is not limited.

A running task is an Attempt — or a Coordinator's diagnosis — that holds a native run or its check commands, in a
Session that still goes on: what a stopped, closed or blocked Session left behind (a disabled Worker's ``running`` row)
holds no place."""
import time

from .security import require

LIMITS = (1, 50)
RUNNING = ("dispatching", "running", "waiting_approval", "dispatch_unknown")
SETTLED = ("blocked", "failed", "stopped", "completed", "waiting_worker")
SCOPES = ("default", "user", "team")


def _limit(db, scope, subject):
    row = db.execute("SELECT running FROM quotas WHERE scope=? AND subject=?", (scope, subject)).fetchone()
    return row["running"] if row else None


def limits(db, owner_id, team_id):
    """The limits a Session of ``owner_id`` (in ``team_id``) runs under: the person's own, else the default; the Team's."""
    own = _limit(db, "user", owner_id)
    return {"user": own if own is not None else _limit(db, "default", ""),
            "team": _limit(db, "team", team_id) if team_id else None}


def usage(db, *, owner_id=None, team_id=None):
    """Running tasks of a person (every Session they submitted, a Team's too) or of a Team."""
    column, value = ("r.owner_id", owner_id) if owner_id else ("r.team_id", team_id)
    where = (f"{column}=? AND r.desired='active' AND r.lifecycle='open' AND r.status NOT IN ({','.join('?' * len(SETTLED))})"
             f" AND x.status IN ({','.join('?' * len(RUNNING))})")
    params = (value, *SETTLED, *RUNNING)
    return sum(db.execute(f"SELECT count(*) FROM {table} x JOIN runs r ON r.id=x.run_id WHERE {where}", params).fetchone()[0]
               for table in ("attempts", "recoveries"))


def full(db, run):
    """The first scope that has no room for one more task of ``run`` — the person's, then the Team's — or None."""
    limit = limits(db, run["owner_id"], run["team_id"])
    if limit["user"] is not None and (used := usage(db, owner_id=run["owner_id"])) >= limit["user"]:
        return {"scope": "user", "limit": limit["user"], "used": used}
    if limit["team"] is not None and (used := usage(db, team_id=run["team_id"])) >= limit["team"]:
        team = db.execute("SELECT name FROM teams WHERE id=?", (run["team_id"],)).fetchone()
        return {"scope": "team", "limit": limit["team"], "used": used, "team_name": team["name"] if team else run["team_id"]}
    return None


def parse(body):
    """``{"scope": "default"|"user"|"team", "id"?: …, "running": 1–50 | null}`` → (scope, subject, running)."""
    require(isinstance(body, dict) and set(body) <= {"scope", "id", "running"} and "running" in body,
            "并发上限须为 {\"scope\": \"default|user|team\", \"id\": \"…\", \"running\": 1–50 或 null}")
    scope, running = body.get("scope"), body["running"]
    require(scope in SCOPES, "scope 须为 default（每人默认）、user（某个人）或 team（某个 Team）")
    require(running is None or type(running) is int and LIMITS[0] <= running <= LIMITS[1],
            f"running 须为 {LIMITS[0]}–{LIMITS[1]} 的整数，或 null 取消上限")
    subject = body.get("id", "")
    require(scope == "default" and "id" not in body or scope != "default" and isinstance(subject, str) and subject,
            "user 与 team 需要 id；default 不带 id")
    return scope, subject, running


def store(db, actor, scope, subject, running):
    if running is None:
        db.execute("DELETE FROM quotas WHERE scope=? AND subject=?", (scope, subject))
    else:
        db.execute("INSERT INTO quotas(scope,subject,running,updated_by,updated) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(scope,subject) DO UPDATE SET running=excluded.running, updated_by=excluded.updated_by, "
                   "updated=excluded.updated", (scope, subject, running, actor["id"], time.time()))


def view(db, actor, admin):
    """What ``actor`` sees: their own limit and running tasks, their Teams' (every Team for an administrator, who also sees
    the default and each person's own limit)."""
    roles = {r["team_id"]: r["role"] for r in db.execute("SELECT team_id, role FROM members WHERE user_id=?", (actor["id"],))}
    teams = [t for t in db.execute("SELECT id, name FROM teams WHERE archived IS NULL OR archived=0 ORDER BY name")
             if admin or t["id"] in roles]
    value = {"mine": {"limit": limits(db, actor["id"], None)["user"], "used": usage(db, owner_id=actor["id"])},
             "teams": [{"id": t["id"], "name": t["name"], "limit": _limit(db, "team", t["id"]), "used": usage(db, team_id=t["id"]),
                        "manage": admin or roles.get(t["id"]) == "owner"} for t in teams]}
    if admin:
        value["default"] = _limit(db, "default", "")
        value["users"] = [dict(r) for r in db.execute(
            "SELECT u.id, u.username AS name, q.running AS \"limit\" FROM quotas q JOIN users u ON u.id=q.subject "
            "WHERE q.scope='user' ORDER BY u.username")]
    return value
