"""The remote preview's platform side (D36 §5.2): how a browser on the preview domain reaches a Session's app.

- ``POST /v1/runs/<id>/app/open`` gives an opening link ``<origin>/_kirocrew/open/<ticket>``: ten minutes, single use,
  signed for this purpose only. The origin is the Session's own, ``https://<run-id>.<preview_domain>`` (phase 3: several
  previews side by side, each app's cookies and storage its own), or the one shared ``preview_url``, where following the
  link first clears what an earlier Session's app left. Either way it sets a cookie for that origin alone (HttpOnly,
  eight hours) naming the Session, and a per-session host accepts only its own Session's cookie and links.
- The preview domain's nginx asks ``/v1/preview-auth`` before every request (auth_request): the cookie's holder must
  still be allowed to read the Session and its app must run; the answer says where the app is.
- On the platform host the app is reached over loopback; on a Worker host through that host's nginx preview port (TLS
  with the private CA), with a check header derived from the Worker's token, so only the platform reaches it.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

from .security import Error, require

TICKET_TTL = 600
COOKIE = "__Host-kirocrew_preview"
COOKIE_TTL = 8 * 3600
IDLE = 1800          # a preview nobody opened for 30 minutes is stopped
TOUCH = 60           # the last access is written at most once a minute
LIMIT = 3            # per execution environment, as the Worker enforces too
LIVE = ("installing", "starting", "running")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
APP_PORTS = range(47000, 48000)   # what a Worker may report, as its host's nginx forwards only these
NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
RUN_LABEL = re.compile(r"run-[0-9a-f]{32}")


def session_host(host, domain):
    """The Session a per-session preview host names (D36 phase 3: ``<run-id>.<domain>``, one wildcard certificate), or
    None when the host is not under ``domain`` — the distribution's own cloudfront.net name keeps the single-origin mode.
    Anything else under ``domain`` is no preview: 404."""
    if not domain or not host:
        return None
    name, domain = plain_host(host), domain.lower().strip(".")
    if name != domain and not name.endswith("." + domain):
        return None
    label = name[:-len(domain) - 1] if name != domain else ""
    require(RUN_LABEL.fullmatch(label), "预览地址无效", 404, "not_found")
    return label


def plain_host(host):
    """A Host header as a bare name: lowercase, without a port or the trailing dot of a fully qualified name."""
    name = (host or "").strip().lower()
    if re.fullmatch(r".+:\d{1,5}", name):
        name = name.rsplit(":", 1)[0]
    return name.rstrip(".")


def session_url(domain, rid):
    return f"https://{rid}.{domain}"


def _sign(key, payload):
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()[:18]).decode()


def verify_value(token):
    """The check header for a Worker host's preview port; its installer renders the same value into nginx."""
    return hmac.new(token.encode(), b"kirocrew-preview", hashlib.sha256).hexdigest()


def gate_value(origin_secret):
    """The header the preview domain's nginx sends the API: proof a request came through it, not from another process
    on the platform host (agents of the personal space run there). Rendered into nginx by al2023.sh render_preview."""
    return hmac.new(origin_secret.encode(), b"kirocrew-preview-gate", hashlib.sha256).hexdigest()


def upstream(config, port, via):
    """Where nginx sends the app's requests: loopback for a Worker on this host, else the Worker host's preview port.
    What the Worker reports is checked: it ends up in nginx's upstream and in response headers."""
    require(isinstance(port, int) and not isinstance(port, bool) and (port == 0 or port in APP_PORTS),
            "Worker 报告的端口无效", 502, "worker_invalid")
    host = urlsplit(config["url"]).hostname
    if host in LOOPBACK:
        return {"url": f"http://127.0.0.1:{port}", "port": port}
    if via is not None:
        require(isinstance(via, dict) and isinstance(via.get("port"), int) and not isinstance(via["port"], bool)
                and 1 <= via["port"] <= 65535 and isinstance(via.get("name"), str) and NAME.fullmatch(via["name"]),
                "Worker 报告的预览端口或名称无效", 502, "worker_invalid")
    require(via, "此 Worker 主机尚未配置预览端口：执行 deploy.sh worker-host push <名称>", 409, "app_unreachable")
    return {"url": f"https://{host}:{via['port']}", "name": via["name"], "verify": verify_value(config["token"]),
            "port": port}


def open_ticket(rid, user, key, now=None):
    payload = f"{rid}.{user}.{secrets.token_hex(8)}.{int((now or time.time()) + TICKET_TTL)}"
    return f"{payload}.{_sign(key, 'app.' + payload)}"


def open_run(ticket, key, now=None):
    """(run, user, nonce) an opening link grants; 403 when forged, of another purpose, or late."""
    parts = ticket.split(".")
    require(len(parts) == 5 and parts[3].isdigit() and hmac.compare_digest(
        parts[4].encode(), _sign(key, "app." + ".".join(parts[:4])).encode()),
        "预览链接无效，请在控制台重新打开远程预览", 403, "app_link_invalid")
    require(int(parts[3]) >= (now or time.time()), "预览链接已过期（10 分钟有效），请在控制台重新打开远程预览", 403,
            "app_link_expired")
    return parts[0], parts[1], parts[2]


def cookie(rid, user, key, now=None):
    payload = f"{rid}.{user}.{int((now or time.time()) + COOKIE_TTL)}"
    return f"{payload}.{_sign(key, 'app-cookie.' + payload)}"


def cookie_run(value, key, now=None):
    """(run, user) of the preview domain's cookie; 401 without a valid, current one."""
    parts = (value or "").split(".")
    require(len(parts) == 4 and parts[2].isdigit() and hmac.compare_digest(
        parts[3].encode(), _sign(key, "app-cookie." + ".".join(parts[:3])).encode())
        and int(parts[2]) >= (now or time.time()), "远程预览未打开或已过期", 401, "app_cookie_invalid")
    return parts[0], parts[1]


def cookie_value(header):
    """Our cookie from a Cookie header, ignoring the app's own cookies."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return ""


def entry_path(path):
    """Where the browser goes after opening: the app's ready path, never another site (``//host``)."""
    return path if isinstance(path, str) and path.startswith("/") and not path.startswith(("//", "/\\")) else "/"
