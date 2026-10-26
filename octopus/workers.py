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

