"""Repository credentials (D35 §3): GitHub and GitLab tokens kept by a person, a Team or the platform.

Scopes, like Jenkins' System / Folder credentials:
- personal: managed and used by its owner only (for their own Sessions and the Team Sessions they submit or bind);
- Team: managed by the Team's owners and elevated admins, used by that Team's Sessions;
- platform: managed by elevated admins, used where it is granted (a Team, or a user).
A credential others may not see answers 404; one they may see but not manage, 403. The secret is write-only: it is
sealed (octopus/vault.py) on the way in and only ever opened for a host check or a clone, never returned.
"""
import calendar
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import vault
from .db import dumps, uid
from .security import Error, elevated, require

KINDS = ("github_pat", "gitlab_token")      # github_app arrives in phase 3
SCOPES = ("user", "team", "platform")
ACCESS = ("read", "write")
HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?")
SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
REPO = re.compile(rf"{SEGMENT}(/{SEGMENT})*(/\*)?")
SECRET = re.compile(r"[\x21-\x7e]{8,500}")   # printable, no spaces or line breaks


def repo_path(url):
    """(host, path) of an HTTPS repository URL; tokens only work over HTTPS, so anything else is refused."""
    parts = urlsplit(str(url))
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("凭证只用于 HTTPS 仓库地址")
    path = parts.path.strip("/").removesuffix(".git")
    if not re.fullmatch(rf"{SEGMENT}(/{SEGMENT})+", path):
        raise ValueError("仓库地址需为 https://<主机>/<所有者>/<仓库>")
    return parts.netloc.lower(), path


def covers(repos, path):
    """How specifically ``repos`` covers a repository path: 3 exactly, 2 a sub-group (a/b/*), 1 an owner (a/*), 0 every
    repository on the host; None when it does not cover it."""
    if not repos:
        return 0
    best = None
    for pattern in repos:
        if pattern == path:
            return 3
        if pattern.endswith("/*") and path.startswith(pattern[:-1]):
            rank = 2 if pattern.count("/") > 1 else 1
            best = max(best or 0, rank)
    return best


def api_base(kind, host):
    """The host's API: github.com, a GitHub Enterprise Server, or a GitLab instance (tests point it elsewhere)."""
    if kind == "github_pat":
        return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    return f"https://{host}/api/v4"


def username(kind):
    """The user name a token is presented with over HTTPS git."""
    return "x-access-token" if kind == "github_pat" else "oauth2"


def _member_role(db, user, team):
    row = db.execute("SELECT role FROM members WHERE user_id=? AND team_id=?", (user, team)).fetchone()
    return row["role"] if row else None


def usable(db, actor_id, row, team_id=None):
    """May ``actor_id`` use the credential for a Session of ``team_id`` (None: their personal space)? With
    team_id="*": in any of their spaces (listings)."""
    if row["disabled"]:
        return False
    if row["scope"] == "user":
        return row["owner_id"] == actor_id
    teams = ({t for (t,) in db.execute("SELECT team_id FROM members WHERE user_id=?", (actor_id,))} if team_id == "*"
             else {team_id} if team_id and _member_role(db, actor_id, team_id) else set())
    if row["scope"] == "team":
        return row["owner_id"] in teams
    grants = db.execute("SELECT team_id,user_id FROM credential_grants WHERE credential_id=?", (row["id"],)).fetchall()
    return any(g["user_id"] == actor_id or (g["team_id"] and g["team_id"] in teams) for g in grants)


def manageable(db, actor, row):
    if row["scope"] == "user":
        return row["owner_id"] == actor["id"]
    if row["scope"] == "team":
        return elevated(actor) or _member_role(db, actor["id"], row["owner_id"]) == "owner"
    return elevated(actor)


class CredentialStore:
    def __init__(self, database, key_file=None):
        self.db, self.configured = database, key_file

    @property
    def key(self):
        try:
            return vault.key_file(self.configured, self.db.root)
        except vault.VaultError as exc:
            raise Error(503, "credentials_unavailable", str(exc)) from None

    def view(self, db, row, actor):
        owner = None
        if row["scope"] == "user":
            user = db.execute("SELECT username FROM users WHERE id=?", (row["owner_id"],)).fetchone()
            owner = {"id": row["owner_id"], "username": user["username"] if user else None}
        elif row["scope"] == "team":
            team = db.execute("SELECT name FROM teams WHERE id=?", (row["owner_id"],)).fetchone()
            owner = {"id": row["owner_id"], "name": team["name"] if team else None}
        value = {key: row[key] for key in ("id", "name", "kind", "host", "scope", "access", "hint", "expires", "created",
                                           "updated", "last_used")}
        value.update(owner=owner, repos=json.loads(row["repos"]), disabled=bool(row["disabled"]), inject=bool(row["inject"]),
                     can_manage=manageable(db, actor, row))
        if row["scope"] == "platform" and value["can_manage"]:
            value["grants"] = [{"team_id": g["team_id"]} if g["team_id"] else {"user_id": g["user_id"]} for g in db.execute(
                "SELECT team_id,user_id FROM credential_grants WHERE credential_id=? ORDER BY rowid", (row["id"],))]
        return value

    def _row(self, db, actor, cid, manage=True):
        row = db.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
        seen = row is not None and (manageable(db, actor, row) or usable(db, actor["id"], row, "*")
                                    or (row["scope"] == "team" and _member_role(db, actor["id"], row["owner_id"])))
        require(seen, "凭证不存在或无权查看", 404, "not_found")
        require(not manage or manageable(db, actor, row), "只有凭证的管理者可以执行此操作", 403, "forbidden")
        return row

    def list(self, actor, host=None, scope=None):
        with self.db.read() as db:
            rows = db.execute("SELECT * FROM credentials ORDER BY created").fetchall()
            return [self.view(db, row, actor) for row in rows
                    if (manageable(db, actor, row) or usable(db, actor["id"], row, "*")
                        or (row["scope"] == "team" and _member_role(db, actor["id"], row["owner_id"])))
                    and (not host or row["host"] == host) and (not scope or row["scope"] == scope)]

    @staticmethod
    def _fields(body, partial=False):
        """The validated metadata of a create (or of an update with ``partial``)."""
        from .domain import name as valid_name
        values = {}
        if not partial or "name" in body:
            values["name"] = valid_name(body.get("name"))
        if "repos" in body or not partial:
            repos = body.get("repos", [])
            require(isinstance(repos, list) and len(repos) <= 50 and all(isinstance(r, str) for r in repos),
                    "repos 须为最多 50 个仓库范围的列表")
            repos = [r.strip().removesuffix(".git") for r in repos]
            require(all(REPO.fullmatch(r) for r in repos), "仓库范围须为 owner/repo、owner/* 或 group/sub/*")
            values["repos"] = dumps(sorted(set(repos)))
        if "access" in body or not partial:
            require(body.get("access", "read") in ACCESS, "access 只能是 read 或 write")
            values["access"] = body.get("access", "read")
        if body.get("inject"):
            raise Error(409, "unsupported", "注入专属环境在第 3 期支持")
        if "disabled" in body:
            require(isinstance(body["disabled"], bool), "disabled 须为 true / false")
            values["disabled"] = int(body["disabled"])
        if "secret" in body or not partial:
            require(isinstance(body.get("secret"), str) and SECRET.fullmatch(body["secret"]),
                    "secret 须为 8–500 个可见字符（不含空格与换行）")
        return values

    def create(self, actor, body):
        from .domain import audit, member
        require(body.get("kind") in KINDS, "kind 只能是 github_pat 或 gitlab_token")
        host = str(body.get("host", "")).strip().lower()
        require(HOST.fullmatch(host), "host 须为主机名（如 github.com、gitlab.example.com），不含 https:// 与路径")
        scope = body.get("scope", "user")
        require(scope in SCOPES, "scope 只能是 user、team 或 platform")
        values = self._fields(body)
        now, cid = time.time(), uid("cred")
        sealed = vault.seal(self.key, body["secret"])
        with self.db.transaction() as db:
            owner = None
            if scope == "user":
                owner = actor["id"]
            elif scope == "team":
                require(isinstance(body.get("team_id"), str), "Team 凭证需要 team_id")
                if not elevated(actor):
                    member(db, actor["id"], body["team_id"], owner=True)
                require(db.execute("SELECT 1 FROM teams WHERE id=?", (body["team_id"],)).fetchone(), "Team 不存在", 404,
                        "not_found")
                owner = body["team_id"]
            else:
                require(elevated(actor), "平台凭证只能由平台管理员创建", 403, "forbidden")
            db.execute("INSERT INTO credentials(id,name,kind,host,scope,owner_id,repos,access,inject,hint,secret,created_by,"
                       "created,updated) VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                       (cid, values["name"], body["kind"], host, scope, owner, values["repos"], values["access"],
                        vault.hint(body["secret"]), sealed, actor["id"], now, now))
            row = db.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
            audit(db, actor, "credential.create", "credential", cid, self._detail(row))
            return self.view(db, row, actor)

    @staticmethod
    def _detail(row):
        return {key: row[key] for key in ("name", "kind", "host", "scope", "owner_id", "access")} | {"repos": json.loads(row["repos"])}

    def update(self, actor, cid, body):
        from .domain import audit
        values = self._fields(body, partial=True)
        sealed = vault.seal(self.key, body["secret"]) if "secret" in body else None
        with self.db.transaction() as db:
            row = self._row(db, actor, cid)
            if sealed:
                values.update(secret=sealed, hint=vault.hint(body["secret"]))
            if values:
                values["updated"] = time.time()
                db.execute(f"UPDATE credentials SET {','.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), cid))
            action = ("credential.rotate" if sealed else
                      ("credential.disable" if body["disabled"] else "credential.enable") if "disabled" in body
                      else "credential.update")
            row = db.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
            audit(db, actor, action, "credential", cid, self._detail(row))
            return self.view(db, row, actor)

    def delete(self, actor, cid):
        from .domain import audit
        with self.db.transaction() as db:
            row = self._row(db, actor, cid)
            db.execute("DELETE FROM credential_grants WHERE credential_id=?", (cid,))
            db.execute("DELETE FROM credentials WHERE id=?", (cid,))
            audit(db, actor, "credential.delete", "credential", cid, self._detail(row))
        return {"ok": True}

    def _grantee(self, db, body):
        if body.get("team_id"):
            require(db.execute("SELECT 1 FROM teams WHERE id=?", (body["team_id"],)).fetchone(), "Team 不存在", 404, "not_found")
            return "team_id", body["team_id"]
        user = db.execute("SELECT id FROM users WHERE username=?", (str(body.get("username", "")).lower(),)).fetchone()
        require(user is not None, "用户不存在", 404, "not_found")
        return "user_id", user["id"]

    def grant(self, actor, cid, body):
        from .domain import audit
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._row(db, actor, cid)
            require(row["scope"] == "platform", "只有平台凭证可以授权", 409, "invalid_state")
            column, value = self._grantee(db, body)
            db.execute(f"INSERT OR IGNORE INTO credential_grants(credential_id,{column}) VALUES(?,?)", (cid, value))
            audit(db, actor, "credential.grant", "credential", cid, {column: value})
            return self.view(db, row, actor)

    def revoke(self, actor, cid, body):
        from .domain import audit
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._row(db, actor, cid)
            column, value = self._grantee(db, body)
            db.execute(f"DELETE FROM credential_grants WHERE credential_id=? AND {column}=?", (cid, value))
            audit(db, actor, "credential.revoke", "credential", cid, {column: value})
            return self.view(db, row, actor)

    def secret(self, cid):
        """The plaintext, opened at the moment of use only (a host check, a clone): never stored or returned."""
        with self.db.read() as db:
            row = db.execute("SELECT secret FROM credentials WHERE id=?", (cid,)).fetchone()
        require(row is not None, "凭证不存在", 404, "not_found")
        try:
            return vault.open_(self.key, row["secret"])
        except vault.VaultError as exc:
            raise Error(503, "credentials_unavailable", str(exc)) from None

    def test(self, actor, cid):
        """Ask the host who the token belongs to and when it expires; the answer never carries the secret."""
        from .domain import audit
        with self.db.read() as db:
            row = self._row(db, actor, cid)
        secret, base = self.secret(cid), api_base(row["kind"], row["host"])
        headers = ({"Authorization": "Bearer " + secret, "Accept": "application/vnd.github+json"}
                   if row["kind"] == "github_pat" else {"PRIVATE-TOKEN": secret})
        headers["User-Agent"] = "kirocrew-on"

        def get(path):
            request = urllib.request.Request(base + path, headers=headers)
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response), response.headers
        result = {"ok": False}
        try:
            user, answer = get("/user")
            result = {"ok": True, "account": user.get("login") or user.get("username"), "expires": None}
            if row["kind"] == "github_pat" and answer.get("github-authentication-token-expiration"):
                result["expires"] = calendar.timegm(time.strptime(
                    answer["github-authentication-token-expiration"], "%Y-%m-%d %H:%M:%S UTC"))
            elif row["kind"] == "gitlab_token":
                try:
                    expires = get("/personal_access_tokens/self")[0].get("expires_at")
                    result["expires"] = calendar.timegm(time.strptime(expires, "%Y-%m-%d")) if expires else None
                except (urllib.error.URLError, ValueError, OSError):
                    pass   # project and group tokens may not read themselves
        except urllib.error.HTTPError as exc:
            exc.close()
            result["error"] = f"托管方拒绝了这枚凭证（HTTP {exc.code}）"
        except (urllib.error.URLError, OSError, ValueError):
            result["error"] = f"无法连接 {row['host']} 的 API"
        with self.db.transaction() as db:
            if result.get("expires"):
                db.execute("UPDATE credentials SET expires=? WHERE id=?", (result["expires"], cid))
            audit(db, actor, "credential.test", "credential", cid, {"ok": result["ok"], "account": result.get("account")})
        return result
