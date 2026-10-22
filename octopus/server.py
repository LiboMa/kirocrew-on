"""Bearer-authenticated Team API; native browser login is an explicit no-store exchange."""
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import hashlib
import hmac
import html
import os
import re
from pathlib import Path
import socket
import threading
import time
import base64
import secrets
import sqlite3
import struct
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, remote_preview
from .db import dumps, uid
from .domain import Service, audit, name, team_environment
from .security import Error, authenticate, check_password, create_user, elevated, issue_token, require, scope
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
PREVIEW_TTL = 600  # B6: ten minutes; every file it serves re-checks its holder's access
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


def preview_ticket(rid, user, key, now=None):
    payload = f"{rid}.{user}.{int((now or time.time()) + PREVIEW_TTL)}"
    return f"{payload}.{preview_signature(key, payload)}"


def preview_run(ticket, key, now=None):
    """(run, user) a preview ticket grants; 403 preview_invalid when it is forged, signed by another key or in the old
    format, 403 preview_expired when it is genuine but late."""
    parts = ticket.split(".")
    require(len(parts) == 4 and parts[2].isdigit() and hmac.compare_digest(
        parts[3].encode(), preview_signature(key, ".".join(parts[:3])).encode()),
        "预览链接无效，请在控制台重新打开预览", 403, "preview_invalid")
    require(int(parts[2]) >= (now or time.time()), "预览链接已过期（10 分钟有效），请在控制台重新打开预览", 403,
            "preview_expired")
    return parts[0], parts[1]


def archive_ticket(rid, user, client, key, now=None):
    """A download link (D36 §4.1): like a preview ticket, signed for the archive purpose only, and naming the client
    the link was asked from (for the change journal)."""
    payload = f"{rid}.{user}.{client}.{int((now or time.time()) + PREVIEW_TTL)}"
    return f"{payload}.{preview_signature(key, 'archive.' + payload)}"


def archive_run(ticket, key, now=None):
    parts = ticket.split(".")
    require(len(parts) == 5 and parts[3].isdigit() and hmac.compare_digest(
        parts[4].encode(), preview_signature(key, "archive." + ".".join(parts[:4])).encode()),
        "下载链接无效，请在控制台重新下载", 403, "archive_invalid")
    require(int(parts[3]) >= (now or time.time()), "下载链接已过期（10 分钟有效），请重新下载", 403, "archive_expired")
    return parts[0], parts[1], parts[2]


def zip_complete(tail, total):
    """Whether a zip stream of ``total`` bytes ending in ``tail`` (its last 256 bytes or more) was written to its end:
    the end record closes the stream (no comment) and the central directory it names ends right before it, or right
    before the zip64 records. An archive nested in the stream has the signature too, but not at offsets that fit."""
    end = tail[-22:]
    if len(end) < 22 or end[:4] != b"PK\x05\x06" or end[20:22] != b"\0\0":
        return False
    size, offset = struct.unpack("<II", end[12:20])
    if offset + size + 22 == total:
        return True
    locator, record = tail[-42:-22], tail[-98:-42]
    if len(record) < 56 or locator[:4] != b"PK\x06\x07" or record[:4] != b"PK\x06\x06":
        return False
    start = struct.unpack("<Q", locator[8:16])[0]
    size, offset = struct.unpack("<QQ", record[40:56])
    return start == total - 98 and offset + size == start


READ_POSTS = {"/v1/workflows/resolve", "/v1/workflows/parse"}


def required_scope(method, path):
    """The token scope a route needs: platform administration needs admin (A3); writes to one's own configuration
    need manage; Session and design writes need execute; reads need read."""
    segments = path.strip("/").split("/")
    if path.startswith("/v1/admin/") or (segments[:2] == ["v1", "workers"] and method != "GET") or \
            (segments[:2] == ["v1", "credentials"] and len(segments) == 4 and segments[3] == "grants") or \
            (segments[:2] == ["v1", "users"] and (method != "GET" or len(segments) > 2)) or \
            (path == "/v1/teams" and method == "POST") or \
            (segments[:2] == ["v1", "teams"] and len(segments) == 4 and segments[3] in {"archive", "environment"}):
        return "admin"
    # A download is a read; /v1/workflows/<id>/archive (archiving a workflow) is not, hence no endswith() here.
    if method == "GET" or path in READ_POSTS or path.endswith("/preview") or \
            (segments[:2] == ["v1", "runs"] and len(segments) == 4 and segments[3] == "archive") or \
            (segments[:2] == ["v1", "runs"] and len(segments) == 5 and segments[3:] == ["app", "open"]):
        return "read"
    return "execute" if path.startswith(("/v1/runs", "/v1/designs")) else "manage"


def every_change(service, actor, rid):
    """The whole change journal of a Session, newest first, for its export (the query API pages by 200)."""
    result, before = [], None
    while True:
        page = service.changes(actor, rid, before=before, limit=200, audited=False)
        result += page
        if len(page) < 200:
            return result
        before = page[-1]["id"]


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


# The preview domain's own pages (D36 §5.2): plain HTML, no script, nothing of the console.
PREVIEW_PAGE = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>KiroCrew ON 远程预览</title>{head}"
                "<style>body{{font:15px/1.6 system-ui,sans-serif;max-width:36rem;margin:18vh auto;padding:0 1.5rem;color:#333}}"
                "</style></head><body><h1 style=\"font-size:1.2rem\">KiroCrew ON 远程预览</h1><p>{text}</p></body></html>")


def server(database, port=8918, host="127.0.0.1", allowed_hosts=(), origin_secret=None, preview_url="", preview_domain="",
           credentials_key_file=None, credentials_private_hosts=(), default_admission="auto"):
    if origin_secret is not None and len(origin_secret) < 32:
        # An empty or truncated secret file must fail closed instead of turning origin enforcement off.
        raise ValueError("源站密钥为空或过短（至少 32 个字符）；检查 origin_secret_file")
    service = Service(database, default_admission)
    service.credentials.configured = credentials_key_file
    service.credentials.private_hosts = frozenset(credentials_private_hosts)
    signing_key = preview_key(database.root)
    hosts, local_hosts = set(), set()
    failures, limiter_lock = {}, threading.Lock()

    shared = remote_preview.plain_host(urlsplit(preview_url).hostname) if preview_url else ""   # the preview distribution's own name

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

        def respond_status(self, status, headers=()):
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def respond_page(self, text, status=200, head="", headers=()):
            data = PREVIEW_PAGE.format(head=head, text=html.escape(text)).encode()
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

        def preview_gate(self, path):
            """The preview domain's nginx (on this host) asking: who may see the app (auth_request), opening links, and
            the page shown when it may not."""
            try:   # a per-session host (phase 3) names its Session; the distribution's own name names none
                own = remote_preview.session_host(self.headers.get("X-Preview-Host"), preview_domain)
                # With per-session hosts configured, the shared mode is for the distribution's own name alone: no
                # other spelling, unknown host or missing header falls back to it.
                require(own or not preview_domain or remote_preview.plain_host(self.headers.get("X-Preview-Host")) == shared,
                        "预览地址无效", 404, "not_found")
            except Error as exc:
                return self.respond_status(403) if path == "/v1/preview-auth" else self.respond_page(str(exc), 404)
            mine = lambda rid: require(own is None or rid == own, "此链接或 Cookie 属于另一个会话的远程预览", 403, "app_other_session")
            if path == "/v1/preview-auth":
                try:
                    rid, holder = remote_preview.cookie_run(remote_preview.cookie_value(self.headers.get("Cookie")),
                                                            signing_key)
                    mine(rid)
                    target = service.app_auth(rid, holder)
                except Error as exc:
                    return self.respond_status(401 if exc.status == 401 else 403)
                headers = [("X-Preview-Upstream", target["url"]), ("X-Preview-Port", str(target["port"]))]
                if target.get("name"):
                    headers += [("X-Preview-Name", target["name"]), ("X-Preview-Verify", target["verify"])]
                return self.respond_status(204, headers)
            try:
                if path.startswith("/_kirocrew/open/"):
                    ticket = path[len("/_kirocrew/open/"):]
                    mine(remote_preview.open_run(ticket, signing_key)[0])   # checked here, spent on the next step
                    # On the shared origin another Session's app may have used it before: its cookies, storage (service
                    # workers included), cached responses and open pages go first. A Session's own origin keeps its app's.
                    target = "/_kirocrew/enter/" + ticket
                    return self.respond_page("正在打开远程预览…", head=f'<meta http-equiv="refresh" content="0;url={html.escape(target)}">',
                                             headers=[] if own else [("Clear-Site-Data", '"cache", "cookies", "storage", "executionContexts"')])
                if path.startswith("/_kirocrew/enter/"):
                    rid, holder, nonce = remote_preview.open_run(path[len("/_kirocrew/enter/"):], signing_key)
                    mine(rid)
                    entry = service.app_enter(rid, holder, nonce)
                    value = remote_preview.cookie(rid, holder, signing_key)
                    self.send_response(302)
                    self.send_header("Location", entry)
                    self.send_header("Set-Cookie", f"{remote_preview.COOKIE}={value}; Path=/; Secure; HttpOnly; "
                                                   f"SameSite=Lax; Max-Age={remote_preview.COOKIE_TTL}")
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if path == "/_kirocrew/denied":
                    return self.respond_page("远程预览未打开、已停止，或你无权查看此会话：请回到控制台会话页，点「打开远程预览」。",
                                             403)
            except Error as exc:
                return self.respond_page(str(exc), exc.status if exc.status in (401, 403, 404, 409) else 403)
            return self.respond_page("页面不存在", 404)

        def respond_report(self, data, rid, fmt):
            """A Session's Feature report (D37 phase 2): HTML shown as a page with nothing it may load or run, Markdown as
            a file."""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if fmt == "html" else "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'{"inline" if fmt == "html" else "attachment"}; filename="{rid}-report.{fmt}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
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

        def stream_archive(self, rid, config, meta, reader, client):
            """Relay the Worker's zip as it arrives (chunked, nothing on disk). Only a stream that reached the end of the
            archive is completed and recorded; otherwise the connection is cut so no partial file looks finished."""
            from .transport import worker_stream
            response = worker_stream(config, "/archive", {"run_id": rid, "meta": meta})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{rid}.zip"; '
                                                    f"filename*=UTF-8''{quote((meta.get('name') or rid) + '.zip')}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            total, tail = 0, b""
            try:
                with response:
                    while chunk := response.read(65536):
                        self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                        total, tail = total + len(chunk), (tail + chunk)[-256:]
            except (OSError, HTTPException):   # HTTPException: a chunked upstream (a Worker host's nginx) cut short
                self.close_connection = True
                return
            if not zip_complete(tail, total):   # the Worker stopped before the end of the archive
                self.close_connection = True
                return
            self.wfile.write(b"0\r\n\r\n")
            try:   # the download is complete; a failed record must not write a second response into it
                service.record_download(rid, reader, client, total)
            except Exception as exc:
                self.close_connection = True
                logging.error("Download of %s not recorded (%s)", rid, type(exc).__name__)

        def identity(self):
            header = self.headers.get("Authorization", "")
            with database.read() as db:
                actor = authenticate(db, header.removeprefix("Bearer ") if header.startswith("Bearer ") else "")
            now = time.time()
            if actor["last_used"] is None or now - actor["last_used"] >= 60:
                # At most one write a minute per token, in its own short transaction, and best effort: a busy
                # database must never fail the request it rides on.
                try:
                    with database.transaction() as db:
                        db.execute("UPDATE tokens SET last_used=? WHERE id=?", (now, actor["token_id"]))
                except sqlite3.OperationalError:
                    pass
            return actor

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def do_DELETE(self):
            self.dispatch("DELETE")

        def do_PATCH(self):
            self.dispatch("PATCH")

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
                    origin = self.headers.get("Origin")
                    require(not origin or urlsplit(origin).netloc == self.headers.get("Host"), "跨站写入被拒绝", 403, "origin")
                    size = int(self.headers.get("Content-Length", "0"))
                    # A cross-site DELETE always needs a CORS preflight, so it may come without a JSON body.
                    if not (method == "DELETE" and size == 0):
                        require(self.headers.get("Content-Type", "").split(";")[0] == "application/json", "需要 application/json")
                        require(0 < size <= 1_000_000, "请求体过大或为空")
                        body = json.loads(self.rfile.read(size))
                        require(isinstance(body, dict), "请求体必须是对象")
                if method == "GET" and (static := console_file(path)):
                    file, kind = static
                    return self.respond(file.read_bytes(), kind=kind)
                require(not path.startswith("/assets/"), "Not found", 404, "not_found")
                if method == "GET" and path.startswith("/preview/"):
                    ticket, _, relative = path[len("/preview/"):].partition("/")
                    rid, holder = preview_run(ticket, signing_key)
                    relative = unquote(relative) or "index.html"
                    data = service.preview_file(rid, relative, holder)
                    kind = TYPES.get(Path(relative).suffix.lower()) or {
                        ".json": "application/json", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                        ".webp": "image/webp", ".mjs": "text/javascript; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                        ".md": "text/plain; charset=utf-8", ".wasm": "application/wasm", ".woff": "font/woff",
                        ".ttf": "font/ttf", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                    }.get(Path(relative).suffix.lower(), "application/octet-stream")
                    return self.respond_preview(data, kind)
                if path == "/v1/preview-auth" or path.startswith("/_kirocrew/"):
                    # Only the preview domain's nginx on this host: through the console's distribution these are absent,
                    # and with an origin secret the nginx proves itself with the gate (other local processes cannot).
                    gate = self.headers.get("X-Preview-Gate", "")
                    require(method == "GET" and local and not self.origin_verified and (
                        not origin_secret or hmac.compare_digest(gate, remote_preview.gate_value(origin_secret))),
                        "Not found", 404, "not_found")
                    return self.preview_gate(path)
                if method == "GET" and path.startswith("/archive/"):
                    ticket, _, filename = path[len("/archive/"):].partition("/")   # not "name": that is the validator
                    rid, holder, client = archive_run(ticket, signing_key)
                    require(filename == rid + ".zip", "下载链接无效", 404, "not_found")
                    config, meta, reader = service.archive_source(rid, holder)
                    return self.stream_archive(rid, config, meta, reader, client)
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
                        db.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), user["id"]))
                        db.execute("DELETE FROM tokens WHERE expires<?", (time.time(),))
                    with limiter_lock:
                        failures.pop(address, None)
                    return self.respond(token)

                actor = self.identity()
                if path == "/v1/logout" and method == "POST":
                    with database.transaction() as db:
                        db.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (actor["token_id"], actor["id"]))
                    return self.respond({"ok": True})
                scope(actor, required_scope(method, path))
                segments = path.strip("/").split("/")
                if path == "/v1/me":
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond({k: v for k, v in actor.items() if k not in {"token_id", "last_used"}})
                if path == "/v1/users":
                    with database.transaction() as db:
                        if method == "POST":
                            require(elevated(actor), "需要平台管理员", 403, "forbidden")
                            created = create_user(db, body.get("username"), body.get("password"), body.get("admin", False))
                            audit(db, actor, "user.create", "user", created["id"], {"admin": created["admin"]})
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
                if segments[:2] == ["v1", "credentials"]:
                    store = service.credentials
                    if len(segments) == 2:
                        require(method in {"GET", "POST"}, "Method not allowed", 405)
                        if method == "POST":
                            return self.respond(store.create(actor, body), 201)
                        return self.respond(store.list(actor, query.get("host", [None])[0], query.get("scope", [None])[0]))
                    cid = segments[2]
                    if len(segments) == 3 and method == "PATCH":
                        return self.respond(store.update(actor, cid, body))
                    if len(segments) == 3 and method == "DELETE":
                        return self.respond(store.delete(actor, cid))
                    if segments[3:] == ["test"] and method == "POST":
                        return self.respond(store.test(actor, cid))
                    if segments[3:] == ["grants"] and method in {"POST", "DELETE"}:
                        return self.respond(store.grant(actor, cid, body) if method == "POST" else store.revoke(actor, cid, body))
                    raise Error(404, "not_found", "接口不存在")
                if path == "/v1/tokens":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    with database.transaction() as db:
                        if method == "POST":
                            requested = body.get("scopes", actor["scopes"])
                            require(isinstance(requested, list) and set(requested) <= set(actor["scopes"]), "不能扩大 Token scope", 403, "forbidden")
                            return self.respond(issue_token(db, actor["id"], requested, body.get("label", "API"), body.get("ttl", 86400)), 201)
                        return self.respond([dict(r) for r in db.execute(
                            "SELECT id,label,scopes,expires,last_used FROM tokens WHERE user_id=? AND expires>? "
                            "ORDER BY expires DESC", (actor["id"], time.time()))])
                if len(segments) == 3 and segments[:2] == ["v1", "tokens"] and method == "DELETE":
                    with database.transaction() as db:
                        db.execute("DELETE FROM tokens WHERE id=? AND user_id=?", (segments[2], actor["id"]))
                    return self.respond({"ok": True})
                if path == "/v1/teams":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    return self.respond(service.teams(actor) if method == "GET" else service.create_team(actor, body))
                if len(segments) == 3 and segments[:2] == ["v1", "teams"]:
                    require(method == "PATCH", "Method not allowed", 405)
                    return self.respond(service.rename_team(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "teams"] and segments[3] == "archive":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.archive_team(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "teams"] and segments[3] == "environment":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.change_environment(actor, segments[2], body))
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
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "publish":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.publish(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "steer":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.steer(actor, segments[2], body))
                if len(segments) == 5 and segments[:2] == ["v1", "runs"] and segments[3:] == ["knowledge", "forget"]:
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.forget_knowledge(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "publish-policy":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.set_publish_policy(actor, segments[2], body))
                if len(segments) in (4, 5) and segments[:2] == ["v1", "runs"] and segments[3] == "approvals":
                    if len(segments) == 4:
                        require(method == "GET", "Method not allowed", 405)
                        return self.respond(service.approvals(actor, segments[2]))
                    require(segments[4] == "answer" and method == "POST", "Method not allowed", 405)
                    return self.respond(service.answer_approval(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "credentials":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.rebind(actor, segments[2], body))
                if len(segments) in (4, 5) and segments[:2] == ["v1", "runs"] and segments[3] == "app":
                    rid = segments[2]
                    if len(segments) == 4 and method == "GET":
                        return self.respond(service.app(actor, rid))
                    if len(segments) == 4 and method == "POST":
                        require(body.get("action") in {"start", "stop"}, "action 只能是 start 或 stop")
                        return self.respond(service.app_start(actor, rid) if body["action"] == "start"
                                            else service.app_stop(actor, rid))
                    require(segments[4:] == ["open"] and method == "POST", "接口不存在", 404, "not_found")
                    require(preview_url or preview_domain, "平台尚未配置预览域名（preview_url / preview_domain），远程预览暂不可用",
                            409, "app_preview_unconfigured")
                    service.app_ready(actor, rid)
                    ticket = remote_preview.open_ticket(rid, actor["id"], signing_key)
                    origin = remote_preview.session_url(preview_domain, rid) if preview_domain else preview_url.rstrip("/")
                    return self.respond({"url": origin + "/_kirocrew/open/" + ticket,
                                         "expires_in": remote_preview.TICKET_TTL})
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "archive":
                    require(method == "POST", "Method not allowed", 405)
                    estimate = service.archive_entry(actor, segments[2])
                    ticket = archive_ticket(segments[2], actor["id"], actor.get("client", "api"), signing_key)
                    return self.respond({"url": f"/archive/{ticket}/{segments[2]}.zip", "expires_in": PREVIEW_TTL,
                                         "bytes_estimate": estimate["bytes"], "files": estimate["files"]})
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "preview" and method == "POST":
                    entry = service.preview_entry(actor, segments[2])
                    return self.respond({"url": "/preview/" + preview_ticket(segments[2], actor["id"], signing_key) + "/" + entry,
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
                if path == "/v1/me/password":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.change_password(actor, body))
                if len(segments) == 4 and segments[:2] == ["v1", "users"]:
                    require(method == "POST", "Method not allowed", 405)
                    handler = {"password": service.reset_password, "active": service.set_active,
                               "admin": service.set_admin}.get(segments[3])
                    require(handler is not None, "接口不存在", 404, "not_found")
                    return self.respond(handler(actor, segments[2], body))
                if path == "/v1/admin/users":
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond(service.admin_users(actor))
                if len(segments) in {5, 6} and segments[:3] == ["v1", "admin", "users"] and segments[4] == "tokens":
                    if len(segments) == 5:
                        require(method == "GET", "Method not allowed", 405)
                        return self.respond(service.user_tokens(actor, segments[3]))
                    require(method == "DELETE", "Method not allowed", 405)
                    return self.respond(service.revoke_user_token(actor, segments[3], segments[5]))
                if path == "/v1/admin/audit":
                    require(method == "GET", "Method not allowed", 405)
                    paging = {}
                    for key in ("before", "limit"):
                        if key in query:
                            require(query[key][0].isdigit(), f"{key} 须为非负整数")
                            paging[key] = int(query[key][0])
                    return self.respond(service.audit_log(actor, **paging))
                if path == "/v1/admin/environments":
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond(service.environments(actor))
                if len(segments) == 3 and segments[:2] == ["v1", "workers"]:
                    require(method == "PATCH", "Method not allowed", 405)
                    return self.respond(service.rename_worker(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "retire":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.retire_worker(actor, segments[2]))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "dedicate":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.dedicate_worker(actor, segments[2]))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "share":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.share_worker(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "spaces":
                    require(method in {"POST", "DELETE"}, "Method not allowed", 405)
                    return self.respond(service.worker_space(actor, segments[2], body, remove=method == "DELETE"))
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "connection":
                    # After a Worker host is replaced (new address, token, boundary) its environment keeps serving the
                    # same spaces; the new endpoint is re-validated like a registration (D28, T4 forbids rebinding only).
                    require(method == "POST" and elevated(actor), "需要平台管理员", 403, "forbidden")
                    config = body.get("connection")
                    health = validate_worker(config)
                    with database.transaction() as db:
                        row = Service._live_worker(db, segments[2])
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
                        moved = json.loads(row["config"]).get("capabilities", {}).get("boundary") != health.get("boundary")
                        # A new boundary is a redeployed host: what an earlier Team left there is gone (P2).
                        db.execute("UPDATE workers SET config=?, last_team=CASE WHEN ? THEN NULL ELSE last_team END "
                                   "WHERE id=?", (dumps({**config, "capabilities": health}), int(moved), row["id"]))
                        audit(db, actor, "worker.connection", "worker", row["id"], {"boundary_changed": moved})
                    return self.respond({"id": row["id"], "capabilities": health})
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "enabled":
                    require(method == "POST" and elevated(actor), "需要平台管理员", 403, "forbidden")
                    require(isinstance(body.get("enabled"), bool), "enabled 必须为布尔值")
                    with database.transaction() as db:
                        Service._live_worker(db, segments[2])
                        db.execute("UPDATE workers SET enabled=? WHERE id=?", (int(body["enabled"]), segments[2]))
                        audit(db, actor, "worker.enabled", "worker", segments[2], {"enabled": body["enabled"]})
                    return self.respond({"ok": True, "enabled": body["enabled"]})
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "capabilities":
                    require(method == "GET", "Method not allowed", 405)
                    # Everyone the environment serves may refresh its model catalogue (spec §6.1), shared ones included.
                    require(any(w["id"] == segments[2] for w in service.workers(actor)), "Worker 不存在或无权限", 404, "not_found")
                    with database.read() as db:
                        config = json.loads(db.execute("SELECT config FROM workers WHERE id=?", (segments[2],)).fetchone()["config"])
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
                    require(method == "POST" and elevated(actor), "需要平台管理员", 403, "forbidden")
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
                        audit(db, actor, "worker.register", "worker", wid, {"scope": "spare" if spare else "dedicated",
                                                                            "team_id": team, "owner_id": None if spare else user})
                    return self.respond({"id": wid, "name": body["name"], "scope": "spare" if spare else "dedicated",
                                         "capabilities": health}, 201)
                if path == "/v1/placement":   # D49: where a new Session would run, or where a Session may move
                    require(method == "GET", "Method not allowed", 405)
                    return self.respond(service.placement(actor, query.get("team_id", [None])[0] or None,
                                                          query.get("run_id", [None])[0] or None))
                if path == "/v1/runs":
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    if method == "POST":
                        return self.respond(service.start(actor, body))
                    one = lambda key: (query.get(key) or [None])[-1]
                    limit = one("limit")
                    require(limit is None or limit.isdigit(), "limit 须为 1–200 的整数")
                    status = [s for s in (one("status") or "").split(",") if s] or None   # D56: one or several
                    filters = {key: one(key) for key in ("lifecycle", "space", "repo", "branch", "stage", "tool", "client",
                                                         "submitter", "since", "until") if one(key)}
                    return self.respond(service.runs(actor, all=query.get("all") == ["1"], status=status, q=one("q"),
                                                     limit=int(limit) if limit else None, view=one("view"),
                                                     page=one("page") == "1", cursor=one("cursor"), **filters))
                if segments[:2] == ["v1", "policies"] and len(segments) in {2, 3}:   # D55: admission rules, mapping preferences
                    if len(segments) == 3:
                        require(method == "DELETE", "Method not allowed", 405)
                        return self.respond(service.delete_policy(actor, segments[2]))
                    require(method in {"GET", "POST"}, "Method not allowed", 405)
                    if method == "POST":
                        return self.respond(service.create_policy(actor, body), 201)
                    return self.respond(service.policies(actor, (query.get("team_id") or [None])[-1] or None))
                if len(segments) == 6 and segments[:2] == ["v1", "runs"] and segments[3] == "attempts" and segments[5] == "diff":
                    require(method == "GET", "Method not allowed", 405)   # D56: what a candidate changed
                    return self.respond(service.attempt_diff(actor, segments[2], segments[4]))
                if segments[:2] == ["v1", "requests"] and len(segments) in {2, 3}:   # D55: submissions as made
                    require(method == "GET", "Method not allowed", 405)
                    if len(segments) == 3:
                        return self.respond(service.request(actor, segments[2]))
                    one = lambda key: (query.get(key) or [None])[-1]
                    limit = one("limit")
                    require(limit is None or limit.isdigit(), "limit 须为 1–200 的整数")
                    return self.respond(service.requests(actor, status=one("status"), q=one("q"), client=one("client"),
                                                         limit=int(limit) if limit else None))
                if len(segments) in {4, 5} and segments[:2] == ["v1", "runs"] and segments[3] == "revisions":
                    require(method == "GET", "Method not allowed", 405)
                    rid = segments[2]
                    if len(segments) == 4:
                        return self.respond(service.revisions(actor, rid))
                    if segments[4] == "diff":
                        versions = {}
                        for key in ("from", "to"):
                            if key in query:
                                require(query[key][0].isdigit(), f"{key} 须为修订版本号")
                                versions[key] = int(query[key][0])
                        return self.respond(service.revision_diff(actor, rid, versions.get("from"), versions.get("to")))
                    require(segments[4].isdigit(), "修订版本号无效", 404, "not_found")
                    return self.respond(service.revision(actor, rid, int(segments[4])))
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "report":
                    require(method == "GET", "Method not allowed", 405)
                    fmt = query.get("format", ["md"])[0]
                    require(fmt in {"md", "html"}, "format 只能是 md 或 html")
                    text = service.report(actor, segments[2], fmt).encode()
                    return self.respond_report(text, segments[2], fmt)
                if len(segments) == 4 and segments[:2] == ["v1", "runs"] and segments[3] == "changes":
                    require(method == "GET", "Method not allowed", 405)
                    paging = {}
                    for key in ("before", "limit"):
                        if key in query:
                            require(query[key][0].isdigit(), f"{key} 须为非负整数")
                            paging[key] = int(query[key][0])
                    return self.respond(service.changes(actor, segments[2], who=query.get("who", [None])[0],
                                                        kind=query.get("kind", [None])[0], **paging))
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
                        if action == "observe" and method == "GET":
                            from .crew_entry import observe
                            paging = {}
                            for key in ("before", "limit"):
                                if key in query:
                                    require(query[key][0].isdigit(), f"{key} 须为非负整数")
                                    paging[key] = int(query[key][0])
                            return self.respond(observe(service, actor, rid, **paging))
                        if action == "export" and method == "GET":
                            run = service.run(actor, rid)
                            service.audit_read(actor, rid, "export")
                            with database.read() as db:
                                revisions = [{**dict(row), "content": json.loads(row["content"])} for row in db.execute(
                                    "SELECT * FROM revisions WHERE run_id=? ORDER BY version", (rid,))]
                            return self.respond({"schema": "octopus.session/1", "session": run, "revisions": revisions,
                                                 "changes": every_change(service, actor, rid),
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
    httpd.service = service   # serve sweeps idle remote previews with it
    return httpd
