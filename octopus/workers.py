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
