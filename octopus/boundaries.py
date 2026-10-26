"""Boundaries the control plane names (MVP 0.2.0, S8–S9): an identity (``boundary_id``) and an OS user id that every host
mounting the boundary's volume uses — UID = GID = 100000 + a sequence number that is never reused, clear of the default
ranges of ``login.defs``. Boundaries made before keep the user id their host gave them."""
import re
import time
import uuid

from .security import require

BASE = 100000
NAME = re.compile(r"[a-z][a-z0-9-]{1,19}")


def allocate(db, user_id, name=None, now=None):
    """A new boundary for ``user_id`` (an administrator's boundary keeps its ``name``; else ``u<seq>``). Called inside a
    write transaction, which serialises the sequence."""
    seq = (db.execute("SELECT COALESCE(MAX(seq), 0) FROM boundaries").fetchone()[0] or 0) + 1
    name = name or f"u{seq}"
    require(NAME.fullmatch(name) and name != "personal", "边界名为小写字母开头的 2–20 位小写字母、数字或连字符，不能是 personal")
    require(not db.execute("SELECT 1 FROM boundaries WHERE name=? AND state!='released'", (name,)).fetchone(),
            f"边界 {name} 已存在", 409, "boundary_exists")
    row = {"id": "bnd-" + uuid.uuid4().hex, "seq": seq, "uid": BASE + seq, "name": name}
    db.execute("INSERT INTO boundaries(id,seq,uid,name,user_id,created) VALUES(?,?,?,?,?,?)",
               (row["id"], seq, row["uid"], name, user_id, now or time.time()))
    return row


def release(db, boundary_id):
    """The boundary is gone (its user deactivated, its volume kept aside): its number stays taken, its name is free."""
    db.execute("UPDATE boundaries SET state='released' WHERE id=?", (boundary_id,))
