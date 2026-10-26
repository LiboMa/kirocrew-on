"""Boundaries the control plane names (MVP 0.2.0, S8–S9): an identity (``boundary_id``) and an OS user id that every host
mounting the boundary's volume uses — UID = GID = 70000 + a sequence number that is never reused. 70000–99999 is clear of
``login.defs``' users (1000–60000), nobody (65534), systemd's dynamic and home ranges, and the subordinate ids useradd
hands out from 100000 (on AL2023 ec2-user holds 100000–165535; review 2026-10-26). Boundaries made before keep the user
id their host gave them."""
import re
import time
import uuid

from .security import require

BASE = 70000
LAST = 29999   # the last sequence number: BASE + LAST < 100000
NAME = re.compile(r"[a-z][a-z0-9-]{1,19}")


def allocate(db, user_id, name=None, now=None):
    """A new boundary for ``user_id`` (an administrator's boundary keeps its ``name``; else ``u<seq>``), or the one already
    allocated to them under that name (a re-run of ``boundary add`` after a failure gets the same identity and id). Called
    inside a write transaction, which serialises the sequence."""
    if name:
        held = db.execute("SELECT id,seq,uid,name,user_id FROM boundaries WHERE name=? AND state!='released'", (name,)).fetchone()
        if held:
            require(held["user_id"] == user_id, f"边界 {name} 已存在", 409, "boundary_exists")
            return {"id": held["id"], "seq": held["seq"], "uid": held["uid"], "name": held["name"]}
    seq = (db.execute("SELECT COALESCE(MAX(seq), 0) FROM boundaries").fetchone()[0] or 0) + 1
    require(seq <= LAST, "全局 UID 已分配完", 409, "boundaries_exhausted")
    name = name or f"u{seq}"
    require(NAME.fullmatch(name) and name != "personal", "边界名为小写字母开头的 2–20 位小写字母、数字或连字符，不能是 personal")
    row = {"id": "bnd-" + uuid.uuid4().hex, "seq": seq, "uid": BASE + seq, "name": name}
    db.execute("INSERT INTO boundaries(id,seq,uid,name,user_id,created) VALUES(?,?,?,?,?,?)",
               (row["id"], seq, row["uid"], name, user_id, now or time.time()))
    return row


def release(db, boundary_id):
    """The boundary is gone (its user deactivated, its volume kept aside): its number stays taken, its name is free."""
    db.execute("UPDATE boundaries SET state='released' WHERE id=?", (boundary_id,))
