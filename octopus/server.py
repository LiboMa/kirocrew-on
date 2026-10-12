"""Bearer-authenticated Team API; native browser login is an explicit no-store exchange."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import hmac
import os
import re
from pathlib import Path
import socket
import threading
import time
import base64
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .db import dumps, uid
from .domain import Service, name, team_environment
from .security import Error, authenticate, check_password, create_user, issue_token, require, scope
from .transport import validate_worker, worker_request
from .schema import openapi

UI = Path(__file__).parent / "ui"
LOOPBACK = {"127.0.0.1", "::1"}


def client_ip(handler, verified):
    """Viewer address for rate limits: CloudFront's header is trusted only on a verified origin request."""
    viewer = handler.headers.get("CloudFront-Viewer-Address", "") if verified else ""
    return viewer.rsplit(":", 1)[0] if ":" in viewer else handler.client_address[0]


def loopback_host(value):
    """True for a Host naming this machine (any port): local operations and SSM port-forwarding tunnels."""
    try:
        return urlsplit("//" + value).hostname in {"127.0.0.1", "localhost", "::1"}
    except ValueError:
        return False


CLASSIC = {"/app.js": "app.js", "/launch.js": "launch.js", "/recommend.js": "recommend.js", "/style.css": "style.css",
           "/crew.js": "crew.js", "/crew-entry.html": "crew-entry.html", "/recovery-ui.js": "recovery-ui.js"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".ico": "image/x-icon"}


# Preview links carry their own short-lived capability: an iframe or new tab cannot send the console's bearer token.
# The signing key is kept in the platform data directory, so links stay valid across API restarts and deploys.
PREVIEW_TTL = 3600
PREVIEW_POLICY = ("sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads; "
                  "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; frame-ancestors 'self'")


def preview_key(root):
    """The preview signing key in <root>/preview.key (0600), created on first use. A damaged file is replaced, which
    only invalidates links already issued."""
    path = Path(root) / "preview.key"
    key = path.read_bytes() if path.is_file() else b""
    if len(key) != 32:
        key = secrets.token_bytes(32)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
            file.write(key)
        os.chmod(path, 0o600)
    return key


def preview_signature(key, payload):
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()[:18]).decode()


def preview_ticket(rid, key, now=None):
    payload = f"{rid}.{int((now or time.time()) + PREVIEW_TTL)}"
    return f"{payload}.{preview_signature(key, payload)}"


def preview_run(ticket, key, now=None):
    """The run a preview ticket grants; 403 preview_invalid when it is forged or signed by another key, 403
    preview_expired when it is genuine but late."""
    parts = ticket.split(".")
    require(len(parts) == 3 and parts[1].isdigit() and hmac.compare_digest(
        parts[2].encode(), preview_signature(key, f"{parts[0]}.{parts[1]}").encode()),
        "预览链接无效，请在控制台重新打开预览", 403, "preview_invalid")
    require(int(parts[1]) >= (now or time.time()), "预览链接已过期（1 小时有效），请在控制台重新打开预览", 403, "preview_expired")
    return parts[0]


def console_file(path):
    """Static console files: the React build (ui/dist) at / and /assets/*, the classic console at /classic/ and its
    original root paths. Returns (file, content type) or None; asset names are flat, so nothing escapes ui/dist/assets."""
    react = UI / "dist" / "index.html"
    if path == "/":
        file = react if react.is_file() else UI / "index.html"
    elif path in {"/classic", "/classic/"}:
        file = UI / "index.html"
    elif path in CLASSIC:
        file = UI / CLASSIC[path]
    elif path.startswith("/assets/") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", path[8:] or "-") \
            and ".." not in path and Path(path).suffix in TYPES:
        file = UI / "dist" / "assets" / path[8:]
    else:
        return None
    return (file, TYPES[file.suffix]) if file.is_file() else None


def server(database, port=8918, host="127.0.0.1", allowed_hosts=(), origin_secret=None):
    if origin_secret is not None and len(origin_secret) < 32:
        # An empty or truncated secret file must fail closed instead of turning origin enforcement off.
        raise ValueError("源站密钥为空或过短（至少 32 个字符）；检查 origin_secret_file")
    service = Service(database)
    signing_key = preview_key(database.root)
    hosts, local_hosts = set(), set()
    failures, limiter_lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass  # No tokens, request bodies or query strings in access logs.

        def respond(self, value, status=200, kind="application/json; charset=utf-8"):
            data = value if isinstance(value, bytes) else dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                             "object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def respond_preview(self, data, kind, status=200):
            # The previewed app runs sandboxed in an opaque origin: it cannot read the console's storage or call its API
            # as the user. Module scripts from that origin are cross-origin requests, hence the CORS header.
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Security-Policy", PREVIEW_POLICY)
            self.end_headers()
            self.wfile.write(data)

        def identity(self):
            header = self.headers.get("Authorization", "")
            with database.read() as db:
                return authenticate(db, header.removeprefix("Bearer ") if header.startswith("Bearer ") else "")

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def do_DELETE(self):
            self.dispatch("DELETE")

        def dispatch(self, method):
            try:
                require(self.headers.get("Host", "") in hosts, "Host 不在允许列表；隧道或代理地址需写入 allowed_hosts",
                        421, "host_not_allowed")
                supplied = self.headers.get("X-Origin-Verify", "")
                self.origin_verified = bool(origin_secret) and hmac.compare_digest(supplied, origin_secret)
                # Behind CloudFront only requests carrying the origin secret are served; local operations and SSM
                # tunnels (loopback peer AND an allowed Host naming this machine) are exempt.
                local = self.client_address[0] in LOOPBACK and loopback_host(self.headers.get("Host", ""))
                require(not origin_secret or self.origin_verified or local, "请求未经 CloudFront 源站认证",
                        403, "origin_unverified")
                url = urlsplit(self.path)
                path, query = url.path, parse_qs(url.query)
                body = {}
                if method != "GET":
                    require(self.headers.get("Content-Type", "").split(";")[0] == "application/json", "需要 application/json")
                    origin = self.headers.get("Origin")
                    require(not origin or urlsplit(origin).netloc == self.headers.get("Host"), "跨站写入被拒绝", 403, "origin")
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 < size <= 1_000_000, "请求体过大或为空")
                    body = json.loads(self.rfile.read(size))
                    require(isinstance(body, dict), "请求体必须是对象")
                if method == "GET" and (static := console_file(path)):
                    file, kind = static
                    return self.respond(file.read_bytes(), kind=kind)
                require(not path.startswith("/assets/"), "Not found", 404, "not_found")
                if method == "GET" and path.startswith("/preview/"):
                    ticket, _, relative = path[len("/preview/"):].partition("/")
                    rid = preview_run(ticket, signing_key)
                    relative = unquote(relative) or "index.html"
                    data = service.preview_file(rid, relative)
                    kind = TYPES.get(Path(relative).suffix.lower()) or {
                        ".json": "application/json", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                        ".webp": "image/webp", ".mjs": "text/javascript; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                        ".md": "text/plain; charset=utf-8", ".wasm": "application/wasm", ".woff": "font/woff",
                        ".ttf": "font/ttf", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                    }.get(Path(relative).suffix.lower(), "application/octet-stream")
                    return self.respond_preview(data, kind)
                if method == "GET" and path == "/health":
                    return self.respond({"version": __version__, "service": "octopus-team", "stage": "MVP development"})
                if method == "GET" and path == "/v1/openapi.json":
                    return self.respond(openapi())
                if method == "POST" and path == "/v1/login":
                    address = client_ip(self, self.origin_verified)
                    with limiter_lock:
                        recent = [t for t in failures.get(address, []) if time.time() - t < 60]
                        require(len(recent) < 15, "登录尝试过多，稍后重试", 429, "rate_limited")
                        failures[address] = recent + [time.time()]
                    with database.transaction() as db:
                        user = db.execute("SELECT * FROM users WHERE username=? AND active=1",
                                          (str(body.get("username", "")).lower(),)).fetchone()
                        require(user is not None and check_password(body.get("password"), user["password"]),
                                "用户名或密码错误", 401, "unauthorized")
                        token = issue_token(db, user["id"], label="Web login")
                    with limiter_lock:
                        failures.pop(address, None)
                    return self.respond(token)

                actor = self.identity()
                if path == "/v1/logout" and method == "POST":
                    with database.transaction() as db:
                        db.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (actor["token_id"], actor["id"]))
                    return self.respond({"ok": True})
                scope(actor, "read" if method == "GET" or path in {"/v1/workflows/resolve", "/v1/workflows/parse"}
                      or path.endswith("/preview") else (
                    "execute" if path.startswith(("/v1/runs", "/v1/designs")) else "manage"))
                segments = path.strip("/").split("/")
                if path == "/v1/me":
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond({k: v for k, v in actor.items() if k != "token_id"})
                if path == "/v1/users":
                    with database.transaction() as db:
                        if method == "POST":
                            require(actor["admin"], "需要平台管理员", 403, "forbidden")
                            created = create_user(db, body.get("username"), body.get("password"), body.get("admin", False))
                            if body.get("worker_id"):
                                # The new user's personal space runs on a shared execution environment (D28).
                                shared = db.execute("SELECT 1 FROM workers WHERE id=? AND scope='shared' AND enabled=1",
                                                    (body["worker_id"],)).fetchone()
                                require(shared is not None, "只能分配共享执行环境", 409, "worker_unavailable")
                                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)",
                                           (body["worker_id"], created["id"]))
                            return self.respond(created, 201)
                        require(method == "GET", "Method not allowed", 405)
                        return self.respond([dict(r) for r in db.execute("SELECT id,username FROM users WHERE active=1 ORDER BY username")])
                if path == "/v1/tokens":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    with database.transaction() as db:
                        if method == "POST":
                            requested = body.get("scopes", actor["scopes"])
                            require(isinstance(requested, list) and set(requested) <= set(actor["scopes"]), "不能扩大 Token scope", 403, "forbidden")
                            return self.respond(issue_token(db, actor["id"], requested, body.get("label", "API"), body.get("ttl", 86400)), 201)
                        return self.respond([dict(r) for r in db.execute(
                            "SELECT id,label,scopes,expires FROM tokens WHERE user_id=?", (actor["id"],))])
                if len(segments) == 3 and segments[:2] == ["v1", "tokens"] and method == "DELETE":
                    with database.transaction() as db:
                        db.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (segments[2], actor["id"]))
                    return self.respond({"ok": True})
                if path == "/v1/teams":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    return self.respond(service.teams(actor) if method == "GET" else service.create_team(actor, body))
                if len(segments) == 4 and segments[:2] == ["v1", "teams"] and segments[3] == "members":
                    require(method in {"POST", "DELETE"}, "Method not allowed", 405)
                    return self.respond(service.membership(actor, segments[2], body, remove=method == "DELETE"))
                if path == "/v1/workflows":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    return self.respond(service.workflows(actor) if method == "GET" else service.save_workflow(actor, body))
                if path == "/v1/workflow-templates":
                    require(method == "GET", "Method not allowed", 405)
                    from .intent import catalog
                    return self.respond(catalog())
                if path == "/v1/workflows/resolve":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.resolve_workflow(actor, body))
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "preview" and method == "POST":
                    entry = service.preview_entry(actor, segments[2])
                    return self.respond({"url": "/preview/" + preview_ticket(segments[2], signing_key) + "/" + entry,
                                         "entry": entry, "expires_in": PREVIEW_TTL})
                if path == "/v1/workflows/parse":
                    require(method == "POST", "Method not allowed", 405)
                    from .workflow_import import import_workflow
                    return self.respond(import_workflow(body.get("content"), body.get("format")))
                if path == "/v1/designs" and method == "POST":
                    from .designs import create
                    return self.respond(create(service, actor, body), 201)
                if len(segments) == 3 and segments[:2] == ["v1", "designs"] and method == "GET":
                    from .designs import status
                    return self.respond(status(service, actor, segments[2]))
                if len(segments) == 4 and segments[:2] == ["v1", "workflows"]:
                    wid, action = segments[2:]
                    if action == "versions" and method == "POST":
                        return self.respond(service.save_workflow(actor, body, wid))
                    if action == "shares" and method in {"POST", "DELETE"}:
                        return self.respond(service.share(actor, wid, body, revoke=method == "DELETE"))
                    if action == "archive" and method == "POST":
                        return self.respond(service.archive_workflow(actor, wid, body))
                if path == "/v1/admin/environments":
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond(service.environments(actor))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "share":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.share_worker(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "spaces":
                    require(method in {"POST", "DELETE"}, "Method not allowed", 405)
                    return self.respond(service.worker_space(actor, segments[2], body, remove=method == "DELETE"))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "connection":
                    # After a Worker host is replaced (new address, token, boundary) its environment keeps serving the
                    # same spaces; the new endpoint is re-validated like a registration (D28, T4 forbids rebinding only).
                    require(method == "POST" and actor["admin"], "需要平台管理员", 403, "forbidden")
                    config = body.get("connection")
                    health = validate_worker(config)
                    with database.transaction() as db:
                        row = db.execute("SELECT * FROM workers WHERE id=?", (segments[2],)).fetchone()
                        require(row is not None, "Worker 不存在", 404, "not_found")
                        personal = row["scope"] == "dedicated" and not row["team_id"]
                        require(health.get("isolation") == "dedicated" or personal or row["scope"] == "shared",
                                "Team 与空闲执行环境须为专属隔离（dedicated）", 409, "isolation_required")
                        local_principal = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
                        if health.get("host_principal") == local_principal:
                            owner = db.execute("SELECT admin FROM users WHERE id=?", (row["owner_id"],)).fetchone()
                            require(personal and owner and owner["admin"] and health.get("isolation") == "trusted-local",
                                    "平台宿主账号仅用于管理员本机验证；Team 和普通用户须使用独立执行账号/容器/主机",
                                    409, "isolation_required")
                        for other in db.execute("SELECT config,team_id,owner_id FROM workers WHERE id!=?", (row["id"],)):
                            previous = json.loads(other["config"]).get("capabilities", {})
                            require(previous.get("boundary") != health.get("boundary"), "该 Gateway 边界已登记", 409,
                                    "worker_conflict")
                            if (other["team_id"], other["owner_id"]) != (row["team_id"], row["owner_id"]):
                                require(previous.get("host_principal") != health.get("host_principal"),
                                        "不同执行空间不能复用同一宿主账户；请使用独立账号/容器/主机", 409, "isolation_required")
                        db.execute("UPDATE workers SET config=? WHERE id=?",
                                   (dumps({**config, "capabilities": health}), row["id"]))
                    return self.respond({"id": row["id"], "capabilities": health})
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "enabled":
                    require(method == "POST" and actor["admin"], "需要平台管理员", 403, "forbidden")
                    require(isinstance(body.get("enabled"), bool), "enabled 必须为布尔值")
                    with database.transaction() as db:
                        require(db.execute("SELECT 1 FROM workers WHERE id=?", (segments[2],)).fetchone(),
                                "Worker 不存在", 404, "not_found")
                        db.execute("UPDATE workers SET enabled=? WHERE id=?", (int(body["enabled"]), segments[2]))
                    return self.respond({"ok": True, "enabled": body["enabled"]})
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "capabilities":
                    require(method == "GET", "Method not allowed", 405)
                    with database.read() as db:
                        row = db.execute("SELECT * FROM workers WHERE id=?", (segments[2],)).fetchone()
                        require(row and (actor["admin"] or row["owner_id"] == actor["id"] or db.execute(
                            "SELECT 1 FROM members WHERE user_id=? AND team_id=?", (actor["id"], row["team_id"])).fetchone()),
                                "Worker 不存在或无权限", 404, "not_found")
                        config = json.loads(row["config"])
                    health = worker_request(config, "/health")
                    require(health.get("boundary") == config["capabilities"]["boundary"],
                            "Worker 执行边界发生变化，请由管理员核查", 409, "worker_changed")
                    with database.transaction() as db:
                        db.execute("UPDATE workers SET config=? WHERE id=?",
                                   (dumps({**config, "capabilities": health}), segments[2]))
                    return self.respond(health)
                if path == "/v1/workers":
                    if method == "GET":
                        return self.respond(service.workers(actor))
                    require(method == "POST" and actor["admin"], "需要平台管理员", 403, "forbidden")
                    config = body.get("connection")
                    health = validate_worker(config)
                    team, user = body.get("team_id"), body.get("owner_id")
                    # A spare execution environment belongs to no space until an admin assigns it to a Team (D28, T4).
                    spare = body.get("spare") is True
                    if spare:
                        require(not team and not user, "空闲执行环境不绑定 Team 或 User")
                        require(health.get("isolation") == "dedicated",
                                "空闲执行环境须为专属隔离（dedicated）", 409, "isolation_required")
                    else:
                        require(bool(team) != bool(user), "Worker 必须唯一绑定 Team 或 User")
                        require(not team or health.get("isolation") == "dedicated",
                                "Team 执行需要专属 Worker；trusted-local 仅允许个人开发空间", 409, "isolation_required")
                    config = {**config, "capabilities": health}
                    with database.transaction() as db:
                        if not spare:
                            require(db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone() if team else
                                    db.execute("SELECT 1 FROM users WHERE id=?", (user,)).fetchone(),
                                    "执行空间不存在", 404, "not_found")
                            require(not team or team_environment(db, team) is None,
                                    "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
                        local_principal = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
                        if health.get("host_principal") == local_principal:
                            owner = db.execute("SELECT admin FROM users WHERE id=?", (user,)).fetchone() if user else None
                            require(owner and owner["admin"] and health.get("isolation") == "trusted-local",
                                    "平台宿主账号仅用于管理员本机验证；Team 和普通用户须使用独立执行账号/容器/主机",
                                    409, "isolation_required")
                        # A spare boundary must never have been registered, not even on a disabled Worker (T4).
                        others = "SELECT config,team_id,owner_id FROM workers" + ("" if spare else " WHERE enabled=1")
                        for row in db.execute(others):
                            previous = json.loads(row["config"])["capabilities"]
                            require(previous.get("boundary") != health.get("boundary"), "该 Gateway 边界已登记", 409, "worker_conflict")
                            if spare or (team, user) != (row["team_id"], row["owner_id"]):
                                require(previous.get("host_principal") != health.get("host_principal"),
                                        "不同执行空间不能复用同一宿主账户；请使用独立账号/容器/主机", 409, "isolation_required")
                        wid = uid("worker")
                        db.execute("INSERT INTO workers(id,name,owner_id,team_id,config,scope) VALUES(?,?,?,?,?,?)",
                                   (wid, name(body.get("name")), actor["id"] if spare else user, team, dumps(config),
                                    "spare" if spare else "dedicated"))
                    return self.respond({"id": wid, "name": body["name"], "scope": "spare" if spare else "dedicated",
                                         "capabilities": health}, 201)
                if path == "/v1/runs":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    return self.respond(service.runs(actor) if method == "GET" else service.start(actor, body))
                if len(segments) in {3, 4} and segments[:2] == ["v1", "runs"]:
                    rid = segments[2]
                    if len(segments) == 3 and method == "GET":
                        return self.respond(service.run(actor, rid))
                    if len(segments) == 4:
                        action = segments[3]
                        if action == "crew-entry":
                            require(method == "POST", "Method not allowed", 405)
                            require(not body, "原生入口只接受当前 Run，不接受自定义 Session 或 URL")
                            from .crew_entry import create
                            result = create(service, actor, rid)
                            scope(self.identity(), "execute")
                            return self.respond(result)
                        if action == "export" and method == "GET":
                            run = service.run(actor, rid)
                            with database.read() as db:
                                revisions = [{**dict(row), "content": json.loads(row["content"])} for row in db.execute(
                                    "SELECT * FROM revisions WHERE run_id=? ORDER BY version", (rid,))]
                            return self.respond({"schema": "octopus.session/1", "session": run, "revisions": revisions,
                                                 "note": "平台状态及交接记录；源码与原生会话备份保留在授权 Worker。"})
                        if action == "events" and method == "GET":
                            after = int(query.get("after", [self.headers.get("Last-Event-ID", "0")])[0])
                            if query.get("stream") == ["1"]:
                                return self.events(actor, rid, after)
                            return self.respond(service.events(actor, rid, after))
                        if method == "POST":
                            return self.respond(service.operate(actor, rid, action, body))
                raise Error(404, "not_found", "接口不存在")
            except (BrokenPipeError, ConnectionResetError):
                return
            except Error as exc:
                return self.respond({"error": str(exc), "code": exc.code}, exc.status)
            except (ValueError, TypeError, KeyError):
                return self.respond({"error": "请求字段无效或不完整", "code": "invalid_request"}, 400)
            except Exception:
                return self.respond({"error": "操作未完成；未确认的执行请查询 Session 状态", "code": "internal_error"}, 500)

        def events(self, actor, rid, after):
            service.run(actor, rid)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            for _ in range(30):
                try:
                    actor = self.identity()  # Revocation/membership applies to open streams.
                    scope(actor, "read")
                    events = service.events(actor, rid, after)
                except Error:
                    self.wfile.write(b"event: access_revoked\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                for event in events:
                    self.wfile.write(f"id: {event['id']}\nevent: update\ndata: {dumps(event)}\n\n".encode())
                    after = event["id"]
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(1)

    httpd = ThreadingHTTPServer((host, port), Handler)
    bound = httpd.server_address[1]
    local_hosts.update({f"127.0.0.1:{bound}", f"localhost:{bound}", f"[::1]:{bound}"})
    hosts.update({*local_hosts, *allowed_hosts})
    return httpd
