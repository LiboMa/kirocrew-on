"""Public shares (D75): a Session's pages or running demo for anyone on the Internet, by a link with an unguessable token
and, in the code mode, a four-digit access code. Served on the preview domain, never the console's: anyone who may share
could otherwise put a look-alike login page under the console's own address. Design:
docs/superpowers/specs/2026-10-26-public-share-design.md."""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

from .db import uid
from .security import Error, require

KINDS = ("pages", "app")
MODES = ("code", "open")
EXPIRES = {"1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "never": None}
TOKEN = re.compile(r"[A-Za-z0-9_-]{24}")
PER_RUN = 20
GRANT_TTL = 12 * 3600   # what a right code opens, on this browser
VISITOR_TRIES, VISITOR_WINDOW = 5, 600   # wrong codes one visitor may try on one share in ten minutes
SHARE_TRIES, SHARE_WINDOW, LOCK = 30, 3600, 3600   # wrong codes on one share in an hour, then it locks for an hour
PATH = "/_kirocrew/s/"


def new_token():
    return secrets.token_urlsafe(18)


def new_code():
    return f"{secrets.randbelow(10000):04d}"


def parse(body):
    """What a share asks for: its content, mode, how long it lasts and, for pages, the page it opens on."""
    require(isinstance(body, dict) and set(body) <= {"kind", "mode", "expires", "entry"},
            "只接受 kind、mode、expires 与 entry")
    kind, mode, expires = body.get("kind", "pages"), body.get("mode", "code"), body.get("expires", "7d")
    require(kind in KINDS, "kind 只能是 pages（页面）或 app（演示）")
    require(mode in MODES, "mode 只能是 code（需要授权码）或 open（无需授权码）")
    require(expires in EXPIRES, "expires 只能是 1d、7d、30d 或 never")
    entry = body.get("entry")
    if entry is not None:
        require(kind == "pages", "只有页面分享可以指定入口页")
        require(isinstance(entry, str) and 0 < len(entry) <= 500 and not entry.startswith("/") and not re.search(r"[\x00-\x1f\x7f]", entry)
                and not any(part in ("", ".", "..") or part.startswith(".") for part in entry.split("/")),
                "入口页须为项目内的相对路径（不含 .. 与点开头的部分）")
    return {"kind": kind, "mode": mode, "expires": expires, "entry": entry}


def url(base, token):
    """The link a person sends: on the Session's own preview origin (or the shared one)."""
    return base.rstrip("/") + PATH + token


def view(row, base=None):
    """A share as its sharer sees it — the code included."""
    value = {k: row[k] for k in ("id", "run_id", "token", "kind", "mode", "code", "entry", "created", "expires", "views",
                                 "last_viewed")}
    value["created_by"] = row["created_by"]
    value["locked_until"] = row["locked_until"] if (row["locked_until"] or 0) > time.time() else None
    value["url"] = url(base, row["token"]) if base else None
    return value


def _sign(key, payload):
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()[:18]).decode()


def grant(token, code, key, now=None):
    """What a right code leaves in the visitor's browser (a cookie for this link's path): signed with the code it was
    granted for, so a new code ends every earlier grant."""
    payload = f"{token}.{int((now or time.time()) + GRANT_TTL)}"
    return f"{payload}.{_sign(key, 'public-share.' + (code or '') + '.' + payload)}"


def granted(value, token, code, key, now=None):
    parts = (value or "").split(".")
    return (len(parts) == 3 and parts[0] == token and parts[1].isdigit() and int(parts[1]) >= (now or time.time())
            and hmac.compare_digest(parts[2].encode(), _sign(key, "public-share." + (code or "") + "." + ".".join(parts[:2])).encode()))


def cookie_name(token):
    return "kirocrew_share_" + hashlib.sha256(token.encode()).hexdigest()[:12]


def active(db, token, now=None):
    """The share a token names while it may be seen: not expired, its Session open and its sharer's account active.
    404 otherwise — a stranger learns nothing of why."""
    now = now or time.time()
    require(isinstance(token, str) and TOKEN.fullmatch(token), "分享链接不存在或已失效", 404, "share_not_found")
    row = db.execute("SELECT s.*, r.lifecycle, r.desired, r.worker_id, r.workspace, u.active sharer_active FROM public_shares s "
                     "JOIN runs r ON r.id=s.run_id JOIN users u ON u.id=s.created_by WHERE s.token=?", (token,)).fetchone()
    require(row is not None and (row["expires"] is None or row["expires"] > now) and row["lifecycle"] == "open"
            and row["desired"] not in {"close", "delete"} and row["sharer_active"],
            "分享链接不存在或已失效", 404, "share_not_found")
    return dict(row)


class Tries:
    """Wrong codes one visitor tried on one share, kept in memory (the share's own count is in the database)."""
    def __init__(self):
        self.lock, self.seen = threading.Lock(), {}

    def allowed(self, token, address, now):
        with self.lock:
            recent = [t for t in self.seen.get((token, address), []) if now - t < VISITOR_WINDOW]
            self.seen[(token, address)] = recent
            return len(recent) < VISITOR_TRIES

    def failed(self, token, address, now):
        with self.lock:
            self.seen.setdefault((token, address), []).append(now)
            if len(self.seen) > 10000:   # bounded: the oldest visitors go first
                for key in sorted(self.seen, key=lambda k: max(self.seen[k] or [0]))[:5000]:
                    del self.seen[key]


def check(db, tries, token, code, address, now=None):
    """The share a right code opens, or None for a wrong one — counted in ``db``, which the caller commits before it
    answers 401; 429 when this visitor or the share has tried too often."""
    now = now or time.time()
    share = active(db, token, now)
    require(share["mode"] == "code", "此分享无需授权码", 409, "share_open")
    require((share["locked_until"] or 0) <= now, "尝试次数过多，此分享已暂时锁定：请稍后再试，或请分享人重新生成授权码",
            429, "share_locked")
    require(tries.allowed(token, address, now), "尝试次数过多：请 10 分钟后再试", 429, "share_rate_limited")
    if isinstance(code, str) and re.fullmatch(r"[0-9]{4}", code) and hmac.compare_digest(code, share["code"] or ""):
        if share["failures"]:
            db.execute("UPDATE public_shares SET failures=0, failed_since=NULL WHERE id=?", (share["id"],))
        return share
    tries.failed(token, address, now)
    since = share["failed_since"] if share["failed_since"] and now - share["failed_since"] < SHARE_WINDOW else now
    failures = (share["failures"] if since == share["failed_since"] else 0) + 1
    db.execute("UPDATE public_shares SET failures=?, failed_since=?, locked_until=? WHERE id=?",
               (failures, since, now + LOCK if failures >= SHARE_TRIES else share["locked_until"], share["id"]))
    return None


def create(db, actor, run, body, now=None):
    now = now or time.time()
    wanted = parse(body)
    count = db.execute("SELECT count(*) FROM public_shares WHERE run_id=? AND (expires IS NULL OR expires>?)",
                       (run["id"], now)).fetchone()[0]
    require(count < PER_RUN, f"一个会话最多 {PER_RUN} 条公开分享：请先撤销不用的", 409, "share_limit")
    sid, seconds = uid("pshare"), EXPIRES[wanted["expires"]]
    db.execute("INSERT INTO public_shares(id,run_id,token,kind,mode,code,entry,created_by,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (sid, run["id"], new_token(), wanted["kind"], wanted["mode"], new_code() if wanted["mode"] == "code" else None,
                wanted["entry"], actor["id"], now, now + seconds if seconds else None))
    return db.execute("SELECT * FROM public_shares WHERE id=?", (sid,)).fetchone()
