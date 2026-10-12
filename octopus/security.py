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


def issue_token(db, user, scopes=None, label="API", ttl=86400):
    if scopes is None:  # everything the account may hold: administrators also get admin
        row = db.execute("SELECT admin FROM users WHERE id=?", (user,)).fetchone()
        scopes = ["read", "execute", "manage"] + (["admin"] if row and row["admin"] else [])
    require(isinstance(scopes, list) and bool(scopes) and set(scopes) <= set(SCOPES), "不支持的 Token scope")
    raw = "oct_" + secrets.token_urlsafe(32)
    value = {"id": uid("token"), "token": raw, "scopes": scopes,
             "expires": time.time() + min(max(int(ttl), 60), 30 * 86400)}
    db.execute("INSERT INTO tokens(hash,id,user_id,scopes,expires,label) VALUES(?,?,?,?,?,?)",
               (hashlib.sha256(raw.encode()).hexdigest(), value["id"], user,
                dumps(scopes), value["expires"], str(label)[:80]))
    return value


def authenticate(db, raw):
    require(isinstance(raw, str) and raw.startswith("oct_"), "需要 API Token", 401, "unauthorized")
    row = db.execute(
        "SELECT u.id,u.username,u.admin,t.id token_id,t.scopes,t.last_used FROM tokens t JOIN users u ON u.id=t.user_id "
        "WHERE t.hash=? AND t.expires>? AND u.active=1",
        (hashlib.sha256(raw.encode()).hexdigest(), time.time())).fetchone()
    require(row is not None, "Token 已失效或撤销", 401, "unauthorized")
    value = dict(row)
    value["scopes"] = json.loads(value["scopes"])
    return value


def scope(actor, permission):
    require(permission in actor["scopes"],
            "Token 缺少 admin scope（平台管理需要管理员以含 admin 的 Token 调用）" if permission == "admin"
            else f"Token 缺少 {permission} scope", 403, "forbidden")
