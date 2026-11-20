"""Worker connections as the platform stores them (D59, K10): the access token sealed with the D35 key, never plaintext."""
from . import vault


def seal(config, key):
    """``config`` with its token sealed: ``token`` removed, ``token_sealed`` in its place."""
    return {**{k: v for k, v in config.items() if k != "token"}, "token_sealed": vault.seal(key, config["token"])}


def seal_stored(db, key):
    """Seal every Worker token still stored in plaintext (data from before D59); returns how many were rewritten.
    Run at the platform's start, after its migrations (whose backup comes first); again, it does nothing."""
    import json
    from .db import dumps
    rewritten = 0
    for row in db.execute("SELECT id, config FROM workers").fetchall():
        config = json.loads(row["config"])
        if config.get("token"):
            db.execute("UPDATE workers SET config=? WHERE id=?", (dumps(seal(config, key)), row["id"]))
            rewritten += 1
    return rewritten


def registration_conflicts(db, health, team, user, spare):
    """The rules every way of registering a Worker applies (the console's form, an enrollment code): the platform host's
    own account only for an administrator's trusted-local Worker; a boundary never registered before on any Worker —
    disabled and retired ones too, or a volume's identity could be handed to another person (CE9); no host account
    shared across spaces among the enabled Workers (any Worker, for a spare one)."""
    import hashlib
    import json
    import os
    import socket
    from .security import require
    local_principal = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
    if health.get("host_principal") == local_principal:
        owner = db.execute("SELECT admin FROM users WHERE id=?", (user,)).fetchone() if user else None
        require(owner and owner["admin"] and health.get("isolation") == "trusted-local",
                "平台宿主账号仅用于管理员本机验证；Team 和普通用户须使用独立执行账号/容器/主机", 409, "isolation_required")
    for row in db.execute("SELECT config,team_id,owner_id,enabled FROM workers"):
        previous = json.loads(row["config"]).get("capabilities") or {}
        require(previous.get("boundary") != health.get("boundary"), "该 Gateway 边界已登记", 409, "worker_conflict")
        if (spare or row["enabled"]) and (spare or (team, user) != (row["team_id"], row["owner_id"])):
            require(previous.get("host_principal") != health.get("host_principal"),
                    "不同执行空间不能复用同一宿主账户；请使用独立账号/容器/主机", 409, "isolation_required")


def check_binding(health, team, user, spare):
    """Where a Worker may be bound, by what it reports (before any transaction): a spare one to no space and dedicated
    (D28, T4); otherwise to exactly one Team or user, a Team's only when dedicated."""
    from .security import require
    if spare:
        require(not team and not user, "空闲执行环境不绑定 Team 或 User")
        require(health.get("isolation") == "dedicated", "空闲执行环境须为专属隔离（dedicated）", 409, "isolation_required")
    else:
        require(bool(team) != bool(user), "Worker 必须唯一绑定 Team 或 User")
        require(not team or health.get("isolation") == "dedicated",
                "Team 执行需要专属 Worker；trusted-local 仅允许个人开发空间", 409, "isolation_required")


def register(db, holder, worker_name, team, user, spare, connection, health, key):
    """Register a Worker inside the caller's transaction, by the same rules for every way of registering: its space exists,
    a Team has no environment yet, its boundary and host account are new (registration_conflicts). The token is sealed
    (D59). ``holder``: who holds a spare one (the registering administrator). Returns the Worker's id."""
    import json
    from .db import dumps, uid
    from .domain import team_environment
    from .security import require
    if not spare:
        require(db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone() if team else
                db.execute("SELECT 1 FROM users WHERE id=?", (user,)).fetchone(), "执行空间不存在", 404, "not_found")
        require(not team or team_environment(db, team) is None, "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
    registration_conflicts(db, health, team, user, spare)
    wid = uid("worker")
    db.execute("INSERT INTO workers(id,name,owner_id,team_id,config,scope) VALUES(?,?,?,?,?,?)",
               (wid, worker_name, holder if spare else user, team, dumps(seal({**connection, "capabilities": health}, key)),
                "spare" if spare else "dedicated"))
    return wid

