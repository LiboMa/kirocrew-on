"""One-time enrollment codes (MVP 0.2.0 P1, N §3–§6, S6): an administrator issues a code that fixes where a Worker
registers and under which name; the Worker brings its own URL and token with it (``octopus worker enroll``), so the token
never passes through a person, a clipboard or a command history. Only the code's SHA-256 is kept. A code is used once,
lasts 30 minutes unless chosen (a day at most), can be revoked, and is retired after five failed registrations."""
import hashlib
import secrets
import threading
import time

from .db import uid
from .security import require

PREFIX = "kce_"
DEFAULT_TTL, LONGEST = 1800, 86400
RETIRE_AFTER = 5   # failed registrations (the Worker unreachable, a registration rule refused)
PER_MINUTE = 10    # enroll requests per source, right or wrong
KEPT = 7 * 86400   # how long a used, revoked or lapsed code stays in the list


def digest(code):
    return hashlib.sha256(code.encode()).hexdigest()


def issue(db, actor_id, name, kind, binding, expires_in=None, now=None):
    """A new code bound to ``kind`` ('user' / 'team' / 'spare') and ``binding`` (the user's or Team's id; None for a
    spare one). Returns ``{id, code, expires}``: the code itself only here."""
    ttl = DEFAULT_TTL if expires_in is None else expires_in
    require(isinstance(ttl, int) and not isinstance(ttl, bool) and 60 <= ttl <= LONGEST,
            "expires_in 须为 60–86400 秒的整数（默认 30 分钟）")
    now = now or time.time()
    code, eid = PREFIX + secrets.token_urlsafe(32), uid("enroll")
    db.execute("INSERT INTO worker_enrollments(id,code_hash,name,binding_kind,binding_id,created_by,created,expires) "
               "VALUES(?,?,?,?,?,?,?,?)", (eid, digest(code), name, kind, binding, actor_id, now, now + ttl))
    return {"id": eid, "code": code, "expires": now + ttl}


def state(row, now):
    if row["used"]:
        return "used"
    if row["revoked"]:
        return "revoked"
    if row["failures"] >= RETIRE_AFTER:
        return "retired"
    return "expired" if row["expires"] <= now else "unused"


def listed(db, now=None):
    """The unused codes and those used, revoked or lapsed in the last week, newest first; never the code."""
    now = now or time.time()
    rows = db.execute("SELECT id,name,binding_kind,binding_id,created_by,created,expires,used,used_by_worker,failures,revoked "
                      "FROM worker_enrollments WHERE created>? OR (used IS NULL AND revoked IS NULL AND expires>?) "
                      "ORDER BY created DESC", (now - KEPT, now)).fetchall()
    return [{**dict(row), "state": state(row, now)} for row in rows]


def revoke(db, eid, now=None):
    require(db.execute("UPDATE worker_enrollments SET revoked=? WHERE id=? AND used IS NULL AND revoked IS NULL",
                       (now or time.time(), eid)).rowcount, "接入码不存在、已使用或已撤销", 404, "not_found")


def live(db, code, now=None):
    """The unused, unexpired, unrevoked code ``code`` names; else 404 ``enrollment_invalid`` — the same answer whatever
    is wrong, so a guess learns nothing."""
    row = db.execute("SELECT * FROM worker_enrollments WHERE code_hash=?",
                     (digest(code) if isinstance(code, str) and code.startswith(PREFIX) else "",)).fetchone()
    require(row is not None and state(row, now or time.time()) == "unused", "接入码无效、已过期或已使用", 404,
            "enrollment_invalid")
    return row


def failed(db, eid):
    """A registration with the code failed; the fifth retires it."""
    db.execute("UPDATE worker_enrollments SET failures=failures+1 WHERE id=?", (eid,))


def consume(db, eid, now=None):
    """Mark the code used, first thing in the registering transaction: a code another request used meanwhile is 404, so
    one code registers one Worker (and a failed registration rolls this back)."""
    now = now or time.time()
    require(db.execute("UPDATE worker_enrollments SET used=? WHERE id=? AND used IS NULL AND revoked IS NULL AND failures<? "
                       "AND expires>?", (now, eid, RETIRE_AFTER, now)).rowcount, "接入码无效、已过期或已使用", 404,
            "enrollment_invalid")


def registered(db, eid, worker_id):
    db.execute("UPDATE worker_enrollments SET used_by_worker=? WHERE id=?", (worker_id, eid))


class Sources:
    """Enroll requests per source address in the last minute, in memory."""
    def __init__(self):
        self.lock, self.seen = threading.Lock(), {}

    def allow(self, address, now):
        with self.lock:
            recent = [t for t in self.seen.get(address, []) if now - t < 60]
            if len(self.seen) > 10000:   # a flood of distinct addresses must not grow this without bound
                self.seen = {k: v for k, v in self.seen.items() if v and now - v[-1] < 60}
            self.seen[address] = recent + [now]
            return len(recent) < PER_MINUTE
