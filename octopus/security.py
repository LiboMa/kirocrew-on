"""Passwords and opaque, revocable client credentials; never native owner cookies."""
import hashlib
import hmac
import json
import re
import secrets
import time

from .db import dumps, uid


class Error(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def require(condition, message, status=400, code="invalid_request"):
    if not condition:
        raise Error(status, code, message)


def password_hash(password, salt=None):
    require(isinstance(password, str) and 12 <= len(password) <= 256,
            "密码长度须为 12–256 个字符")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + digest


def check_password(password, saved):
    try:
        return hmac.compare_digest(password_hash(password, saved.split(":")[0]), saved)
    except (ValueError, Error):
        return False


def create_user(db, username, password, admin=False):
    require(isinstance(admin, bool), "admin 必须为布尔值")
    require(isinstance(username, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{3,64}", username),
            "用户名须为 3–64 个字母、数字或 ._-")
    username = username.lower()
    require(not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone(),
            "用户名已存在", 409, "conflict")
    user = {"id": uid("user"), "username": username, "admin": bool(admin)}
    db.execute("INSERT INTO users(id,username,password,admin,created) VALUES(?,?,?,?,?)",
               (user["id"], username, password_hash(password), int(admin), time.time()))
    return user


SCOPES = ("read", "execute", "manage", "admin")


def elevated(actor):
    """Platform administration needs both: an admin account and a token that carries the admin scope (A3)."""
    return bool(actor.get("admin")) and "admin" in actor.get("scopes", ())


# D62: how long an API token lasts, chosen when it is issued; one that never expires is kept with this far date (the
# column stays NOT NULL and every check stays expires > now) and reads expires: null.
EXPIRIES = {"7d": 7 * 86400, "30d": 30 * 86400, "365d": 365 * 86400, "never": None}
NEVER = 253402300799.0   # 9999-12-31T23:59:59Z
ADMIN_EXPIRIES = ("7d", "30d")   # a token that carries admin: at most 30 days (the security floor)


def expiry(value):
    """A token's expiry as the API shows it: None for one that never expires."""
    return None if value is not None and value >= NEVER else value


def issue_token(db, user, scopes=None, label="API", ttl=86400, expires_in=None):
    """``expires_in`` (7d / 30d / 365d / never) when given; else ``ttl`` seconds as before D62, at most 30 days."""
    if scopes is None:  # everything the account may hold: administrators also get admin
        row = db.execute("SELECT admin FROM users WHERE id=?", (user,)).fetchone()
        scopes = ["read", "execute", "manage"] + (["admin"] if row and row["admin"] else [])
    require(isinstance(scopes, list) and bool(scopes) and set(scopes) <= set(SCOPES), "不支持的 Token scope")
    if expires_in is None:
        expires = time.time() + min(max(int(ttl), 60), 30 * 86400)
    else:
        require(isinstance(expires_in, str) and expires_in in EXPIRIES, "有效期 expires_in 须为 7d、30d、365d 或 never")
        require("admin" not in scopes or expires_in in ADMIN_EXPIRIES, "带 admin 权限的 Token 最长 30 天（7d 或 30d）",
                403, "forbidden")
        expires = NEVER if EXPIRIES[expires_in] is None else time.time() + EXPIRIES[expires_in]
    raw = "oct_" + secrets.token_urlsafe(32)
    value = {"id": uid("token"), "token": raw, "scopes": scopes, "expires": expiry(expires)}
    db.execute("INSERT INTO tokens(hash,id,user_id,scopes,expires,label) VALUES(?,?,?,?,?,?)",
               (hashlib.sha256(raw.encode()).hexdigest(), value["id"], user,
                dumps(scopes), expires, str(label)[:80]))
    return value


def regenerate_token(db, user, tid, expires_in=None, current=None):
    """Owner 2026-10-24: the same token entry (name, scopes) with a new secret, shown once; the old secret stops at once.
    Its expiry stays unless ``expires_in`` chooses a new lifetime (as issue_token's, admin at most 30 days). The token the
    request itself signs in with (``current``) is refused: regenerating it would sign the person out."""
    row = db.execute("SELECT id,label,scopes,expires FROM tokens WHERE id=? AND user_id=? AND expires>?",
                     (tid, user, time.time())).fetchone()
    require(row is not None, "Token 不存在或已失效", 404, "not_found")
    require(tid != current, "这是你当前登录正在使用的 Token：重新生成会让你立刻退出。请退出后重新登录，或在别的客户端里操作", 409,
            "token_in_use")
    scopes = json.loads(row["scopes"])
    expires = row["expires"]
    if expires_in is not None:
        require(isinstance(expires_in, str) and expires_in in EXPIRIES, "有效期 expires_in 须为 7d、30d、365d 或 never")
        require("admin" not in scopes or expires_in in ADMIN_EXPIRIES, "带 admin 权限的 Token 最长 30 天（7d 或 30d）",
                403, "forbidden")
        expires = NEVER if EXPIRIES[expires_in] is None else time.time() + EXPIRIES[expires_in]
    raw = "oct_" + secrets.token_urlsafe(32)
    db.execute("UPDATE tokens SET hash=?, expires=?, last_used=NULL WHERE id=? AND user_id=?",
               (hashlib.sha256(raw.encode()).hexdigest(), expires, tid, user))
    return {"id": tid, "token": raw, "label": row["label"], "scopes": scopes, "expires": expiry(expires)}


def authenticate(db, raw):
    require(isinstance(raw, str) and raw.startswith("oct_"), "需要 API Token", 401, "unauthorized")
    row = db.execute(
        "SELECT u.id,u.username,u.admin,t.id token_id,t.scopes,t.last_used,t.label FROM tokens t JOIN users u ON u.id=t.user_id "
        "WHERE t.hash=? AND t.expires>? AND u.active=1",
        (hashlib.sha256(raw.encode()).hexdigest(), time.time())).fetchone()
    require(row is not None, "Token 已失效或撤销", 401, "unauthorized")
    from .journal import client
    value = dict(row)
    value["scopes"] = json.loads(value["scopes"])
    value["token_label"] = value.pop("label")   # which client a Request came from (D55)
    value["client"] = client(value["token_label"])   # console / Coding Agent (MCP) / API, for the change journal (D34)
    return value


def scope(actor, permission):
    require(permission in actor["scopes"],
            "Token 缺少 admin scope（平台管理需要管理员以含 admin 的 Token 调用）" if permission == "admin"
            else f"Token 缺少 {permission} scope", 403, "forbidden")
