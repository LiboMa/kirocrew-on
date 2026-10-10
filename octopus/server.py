"""Bearer-authenticated Team API; native browser login is an explicit no-store exchange."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .db import dumps, uid
from .domain import Service, name
from .security import Error, authenticate, check_password, create_user, issue_token, require, scope
from .transport import validate_worker, worker_request
from .schema import openapi

UI = Path(__file__).parent / "ui"


def server(database, port=8918, host="127.0.0.1"):
    service = Service(database)
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
                             "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                             "object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
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
                if method == "GET" and path in {"/", "/app.js", "/launch.js", "/recommend.js", "/style.css",
                                               "/crew.js", "/crew-entry.html", "/recovery-ui.js"}:
                    file = {"/": "index.html", "/app.js": "app.js", "/launch.js": "launch.js",
                             "/recommend.js": "recommend.js", "/style.css": "style.css",
                             "/crew.js": "crew.js", "/crew-entry.html": "crew-entry.html",
                             "/recovery-ui.js": "recovery-ui.js"}[path]
                    kind = {"/": "text/html; charset=utf-8", "/app.js": "text/javascript; charset=utf-8",
                            "/launch.js": "text/javascript; charset=utf-8",
                             "/recommend.js": "text/javascript; charset=utf-8",
                             "/crew.js": "text/javascript; charset=utf-8",
                             "/crew-entry.html": "text/html; charset=utf-8",
                             "/recovery-ui.js": "text/javascript; charset=utf-8",
                            "/style.css": "text/css; charset=utf-8"}[path]
                    return self.respond((UI / file).read_bytes(), kind=kind)
                if method == "GET" and path == "/health":
                    return self.respond({"version": __version__, "service": "octopus-team", "stage": "MVP development"})
                if method == "GET" and path == "/v1/openapi.json":
                    return self.respond(openapi())
                if method == "POST" and path == "/v1/login":
                    address = self.client_address[0]
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
                scope(actor, "read" if method == "GET" or path == "/v1/workflows/resolve" else (
                    "execute" if path.startswith(("/v1/runs", "/v1/designs")) else "manage"))
                segments = path.strip("/").split("/")
                if path == "/v1/me":
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond({k: v for k, v in actor.items() if k != "token_id"})
                if path == "/v1/users":
                    with database.transaction() as db:
                        if method == "POST":
                            require(actor["admin"], "需要平台管理员", 403, "forbidden")
                            return self.respond(create_user(db, body.get("username"), body.get("password"), body.get("admin", False)), 201)
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
                        with database.read() as db:
                            rows = []
                            for row in db.execute("SELECT * FROM workers"):
                                if actor["admin"] or row["owner_id"] == actor["id"] or db.execute(
                                    "SELECT 1 FROM members WHERE user_id=? AND team_id=?", (actor["id"], row["team_id"])).fetchone():
                                    value = {k: row[k] for k in ("id", "name", "team_id", "owner_id", "enabled")}
                                    value["capabilities"] = json.loads(row["config"]).get("capabilities", {})
                                    rows.append(value)
                        return self.respond(rows)
                    require(method == "POST" and actor["admin"], "需要平台管理员", 403, "forbidden")
                    config = body.get("connection")
                    health = validate_worker(config)
                    team, user = body.get("team_id"), body.get("owner_id")
                    require(bool(team) != bool(user), "Worker 必须唯一绑定 Team 或 User")
                    require(not team or health.get("isolation") == "dedicated",
                            "Team 执行需要专属 Worker；trusted-local 仅允许个人开发空间", 409, "isolation_required")
                    config = {**config, "capabilities": health}
                    with database.transaction() as db:
                        require(db.execute("SELECT 1 FROM teams WHERE id=?", (team,)).fetchone() if team else
                                db.execute("SELECT 1 FROM users WHERE id=?", (user,)).fetchone(), "执行空间不存在", 404, "not_found")
                        local_principal = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()
                        if health.get("host_principal") == local_principal:
                            owner = db.execute("SELECT admin FROM users WHERE id=?", (user,)).fetchone() if user else None
                            require(owner and owner["admin"] and health.get("isolation") == "trusted-local",
                                    "平台宿主账号仅用于管理员本机验证；Team 和普通用户须使用独立执行账号/容器/主机",
                                    409, "isolation_required")
                        for row in db.execute("SELECT config,team_id,owner_id FROM workers WHERE enabled=1"):
                            previous = json.loads(row["config"])["capabilities"]
                            require(previous.get("boundary") != health.get("boundary"), "该 Gateway 边界已登记", 409, "worker_conflict")
                            if (team, user) != (row["team_id"], row["owner_id"]):
                                require(previous.get("host_principal") != health.get("host_principal"),
                                        "不同执行空间不能复用同一宿主账户；请使用独立账号/容器/主机", 409, "isolation_required")
                        wid = uid("worker")
                        db.execute("INSERT INTO workers(id,name,owner_id,team_id,config) VALUES(?,?,?,?,?)",
                                   (wid, name(body.get("name")), user, team, dumps(config)))
                    return self.respond({"id": wid, "name": body["name"], "capabilities": health}, 201)
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

    return ThreadingHTTPServer((host, port), Handler)
