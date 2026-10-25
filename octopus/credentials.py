"""Repository credentials (D35 §3): GitHub and GitLab tokens kept by a person, a Team or the platform.

Scopes, like Jenkins' System / Folder credentials:
- personal: managed and used by its owner only (for their own Sessions and the Team Sessions they submit or bind);
- Team: managed by the Team's owners and elevated admins, used by that Team's Sessions;
- platform: managed by elevated admins, used where it is granted (a Team, or a user).
A credential others may not see answers 404; one they may see but not manage, 403. The secret is write-only: it is
sealed (octopus/vault.py) on the way in and only ever opened for a host check or a clone, never returned.
"""
import calendar
import ipaddress
import json
import re
import socket
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
HOST = re.compile(r"([a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+|\[[0-9a-f:]+\])(:[0-9]{1,5})?")
SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
REPO = re.compile(rf"{SEGMENT}(/{SEGMENT})*(/\*)?")
SECRET = re.compile(r"[\x21-\x7e]{8,500}")   # printable, no spaces or line breaks


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # a redirect answers as an error: the token is never sent on to another address


OPENER = urllib.request.build_opener(_NoRedirect)


def refused_address(host, allowed=()):
    """Why a credential's host may not be used — it names or resolves to this host, a private or link-local network
    (the token would go there with the host check or a clone) — or None. Admins list self-hosted instances on a private
    network in credentials_private_hosts."""
    if host in allowed:
        return None
    name = host[1:host.index("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    try:
        addresses = {ipaddress.ip_address(name)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)}
        except (OSError, UnicodeError):
            return None   # not resolvable now: a check or clone would fail on its own
    if any(a.is_loopback or a.is_link_local or a.is_private or a.is_unspecified or a.is_multicast or a.is_reserved
           for a in addresses):
        return f"主机 {host} 指向本机、内网或链路本地地址：自建实例请由平台管理员加入 credentials_private_hosts"
    return None


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


def bound_usable(db, row, run):
    """May this Session (still) use the credential it pinned? The rule of every use (spec §7), and what the Session
    shows: not disabled; a personal one only while its owner is active and — in a Team Session — still a member; a Team
    one for that Team's Sessions; a platform one while it is granted to the Session's Team or creator."""
    if row is None or row["disabled"]:
        return False
    # D74: the person the Session runs as — its creator, unless taken over; a personal token never follows it to another
    person = (run["executor_id"] if "executor_id" in run.keys() else None) or run["owner_id"]
    if row["scope"] == "user":
        owner = db.execute("SELECT active FROM users WHERE id=?", (row["owner_id"],)).fetchone()
        if owner is None or not owner["active"] or row["owner_id"] != person:
            return False
        return _member_role(db, row["owner_id"], run["team_id"]) is not None if run["team_id"] else True
    if row["scope"] == "team":
        return row["owner_id"] == run["team_id"]
    return any((g["team_id"] and g["team_id"] == run["team_id"]) or (g["user_id"] and g["user_id"] == person)
               for g in db.execute("SELECT team_id,user_id FROM credential_grants WHERE credential_id=?", (row["id"],)))


class CredentialStore:
    def __init__(self, database, key_file=None, private_hosts=()):
        self.db, self.configured, self.private_hosts = database, key_file, frozenset(private_hosts)

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
        refused = refused_address(host, self.private_hosts)
        require(not refused, refused, 409, "credential_host_refused")
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

    def material(self, run):
        """The bound credential, opened at the moment of a clone (spec §5.1): ``git_auth`` for the Worker, or None
        without a binding. A disabled, deleted or no longer matching credential is refused; each use is audited, with
        the Session's creator as the one on whose behalf it is used."""
        from .domain import audit
        pinned = json.loads(run["snapshot"]).get("credentials") or {}
        bound = pinned.get("source")
        require(bound or not pinned.get("removed"),
                f"此会话的凭证「{(pinned.get('removed') or {}).get('name', '')}」已被撤下：请在会话页更换凭证后续作", 409,
                "credential_unavailable")
        if not bound:
            return None
        with self.db.read() as db:
            row = db.execute("SELECT * FROM credentials WHERE id=?", (bound["id"],)).fetchone()
            usable_now = bound_usable(db, row, run)
        require(usable_now, f"凭证「{bound['name']}」已不能用于此会话（已停用、删除、授权撤销或所有者已离开）："
                            "请在会话页更换凭证后续作", 409, "credential_unavailable")
        refused = refused_address(row["host"], self.private_hosts)
        require(not refused, refused, 409, "credential_host_refused")
        source = json.loads(run["source"])
        host, path = repo_path(source.get("url", ""))
        require(row["host"] == host and covers(json.loads(row["repos"]), path) is not None,
                f"凭证「{row['name']}」已不覆盖仓库 {path}：请更换凭证后续作", 409, "credential_unavailable")
        secret = self.secret(row["id"])
        with self.db.transaction() as db:
            db.execute("UPDATE credentials SET last_used=? WHERE id=?", (time.time(), row["id"]))
            audit(db, {"id": run["owner_id"]}, "credential.use", "credential", row["id"],
                  {"run_id": run["id"], "repo": path, "access": "read"})
        return {"kind": row["kind"], "host": row["host"], "username": username(row["kind"]), "secret": secret}

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
            with OPENER.open(request, timeout=15) as response:
                value = json.loads(response.read(65536))
                if not isinstance(value, dict):
                    raise ValueError("not an object")
                return value, response.headers
        result = {"ok": False}
        refused = refused_address(row["host"], self.private_hosts)
        if refused:
            return {"ok": False, "error": refused}
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
            result = {"ok": False, "error": f"托管方拒绝了这枚凭证（HTTP {exc.code}）" if exc.code in (401, 403)
                      else f"托管方返回 HTTP {exc.code}（不跟随跳转）"}
        except ValueError:
            result = {"ok": False, "error": "托管方的回答无法识别"}
        except (urllib.error.URLError, OSError):
            result = {"ok": False, "error": f"无法连接 {row['host']} 的 API"}
        with self.db.transaction() as db:
            if result.get("expires"):
                db.execute("UPDATE credentials SET expires=? WHERE id=?", (result["expires"], cid))
            audit(db, actor, "credential.test", "credential", cid, {"ok": result["ok"], "account": result.get("account")})
        return result


def binding(db, row, actor):
    """What a Session pins (spec §4): the credential's identity and who bound it, never the secret."""
    owner = None
    if row["scope"] == "user":
        user = db.execute("SELECT username FROM users WHERE id=?", (row["owner_id"],)).fetchone()
        owner = {"id": row["owner_id"], "username": user["username"] if user else None}
    elif row["scope"] == "team":
        team = db.execute("SELECT name FROM teams WHERE id=?", (row["owner_id"],)).fetchone()
        owner = {"id": row["owner_id"], "name": team["name"] if team else None}
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "scope": row["scope"], "owner": owner,
            "hint": row["hint"], "bound_by": {"id": actor["id"], "username": actor.get("username")}, "at": time.time()}


def check(db, actor, row, team_id, host, path):
    """A credential the actor names for this repository: usable by them in this space, for this host and range."""
    require(row is not None and usable(db, actor["id"], row, team_id), "无权使用此凭证（已停用、已删除或不在你的空间）",
            403, "credential_forbidden")
    require(row["host"] == host, f"凭证「{row['name']}」用于 {row['host']}，仓库在 {host}", 409, "credential_scope")
    require(covers(json.loads(row["repos"]), path) is not None, f"凭证「{row['name']}」的仓库范围不含 {path}", 409,
            "credential_scope")


def resolve(db, actor, source, team_id, explicit=None, default=None):
    """The source credential of a new Session (R1): the one named, else the workflow's default when the submitter may
    use it, else the best match by tier (Team: Team → platform granted to the Team → the submitter's own; personal:
    own → platform granted to them) and, within a tier, the most specific range. None: no credential (public)."""
    if source.get("type") != "git":
        require(not explicit, "新建项目不需要凭证", 409, "credential_scope")
        return None
    try:
        host, path = repo_path(source["url"])
    except ValueError as exc:
        require(not explicit, str(exc), 409, "credential_https_only")
        return None   # ssh / scp addresses use git's own authentication
    fetch = lambda cid: db.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
    if explicit:
        row = fetch(explicit)
        check(db, actor, row, team_id, host, path)
        return binding(db, row, actor)
    if default:
        row = fetch(default)
        try:
            check(db, actor, row, team_id, host, path)
            return binding(db, row, actor)
        except Error:
            pass   # a shared workflow's default the submitter may not use: matching decides
    rows = [row for row in db.execute("SELECT * FROM credentials WHERE host=? AND disabled=0 ORDER BY created", (host,))
            if usable(db, actor["id"], row, team_id) and covers(json.loads(row["repos"]), path) is not None]
    granted = lambda row, column, value: db.execute(
        f"SELECT 1 FROM credential_grants WHERE credential_id=? AND {column}=?", (row["id"], value)).fetchone() is not None
    if team_id:
        tiers = [lambda r: r["scope"] == "team" and r["owner_id"] == team_id,
                 lambda r: r["scope"] == "platform" and granted(r, "team_id", team_id),
                 lambda r: r["scope"] == "user"]
    else:
        tiers = [lambda r: r["scope"] == "user", lambda r: r["scope"] == "platform" and granted(r, "user_id", actor["id"])]
    for tier in tiers:
        candidates = [(covers(json.loads(row["repos"]), path), row) for row in rows if tier(row)]
        if candidates:
            best = max(rank for rank, _ in candidates)
            top = [row for rank, row in candidates if rank == best]
            require(len(top) == 1, "有多枚凭证同样适用于此仓库，请选择一枚：" + "、".join(row["name"] for row in top), 409,
                    "credential_ambiguous")
            return binding(db, top[0], actor)
    return None


def opens_for(db, row, run):
    """Would ``material`` open this credential for the Session now — usable and still covering its repository? What lets a
    Session blocked on it continue on its own (D43); anything else would be refused again on the next preparation."""
    if not bound_usable(db, row, run):
        return False
    try:
        host, path = repo_path(json.loads(run["source"]).get("url", ""))
    except ValueError:
        return False
    return row["host"] == host and covers(json.loads(row["repos"]), path) is not None


def availability(db, bound, run):
    """A pinned binding as the Session shows it: unavailable when its next use would be refused (bound_usable)."""
    if not bound:
        return bound
    row = db.execute("SELECT * FROM credentials WHERE id=?", (bound["id"],)).fetchone()
    return {**bound, "access": row["access"] if row else None,   # what it may do now (publishing needs write)
            **({} if bound_usable(db, row, run) else {"unavailable": True})}
