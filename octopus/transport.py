"""Authenticated platform-to-worker transport; Worker tokens stay server-side."""
import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .db import dumps
from .security import Error, require


_context = None
HEALTH_LIMIT = 1 << 20   # bytes: a health answer is small; a Worker URL is not a way to make the platform read a stream


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Workers never redirect: following one would carry the Worker token to another address (review 2026-10-26)."""
    def redirect_request(self, *args, **kwargs):
        return None


def _open(request, timeout):
    return urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_context)).open(request, timeout=timeout)


def configure(ca_file):
    """Trust only this CA for HTTPS Workers (the platform's private CA, D28); None restores the system trust store."""
    global _context
    _context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else None


_KEY = None   # D59: the D35 key that opens a Worker's sealed token (set by the platform at start)
_OPENED = {}   # sealed → token: opened once per process (an openssl run each Worker call was ~33 ms)


def configure_key(key_path):
    """The key that opens sealed Worker tokens (D59); ``configure`` above is the Worker TLS authority."""
    global _KEY
    _KEY = key_path
    forget()


def forget():
    """Drop the tokens opened so far (a new key, or a test)."""
    _OPENED.clear()


def connection(config):
    """A Worker's address and token (D59, K10): the stored token is sealed (``token_sealed``) and opened here, for the
    request only — never written back, logged or answered; a plaintext ``token`` (tests, data from before) as it is."""
    if config.get("token"):
        return {"url": config["url"], "token": config["token"]}
    from . import vault
    sealed = config.get("token_sealed")
    try:
        if not sealed or _KEY is None:
            raise vault.VaultError("没有可用的访问凭据")
        if sealed not in _OPENED:
            if len(_OPENED) > 256:
                _OPENED.clear()
            _OPENED[sealed] = vault.open_(_KEY, sealed)
        return {"url": config["url"], "token": _OPENED[sealed]}
    except (vault.VaultError, OSError):   # a key file gone or unreadable is the same answer
        raise Error(503, "worker_secret", "执行环境的访问凭据无法解开：请平台管理员重新连接该执行环境") from None


def worker_token(config):
    """The token the platform calls this Worker with, only to mask it where an Agent there may have printed it (D57 §3.3);
    None when it cannot be opened, or is shorter than any Worker's (validate_worker)."""
    try:
        token = connection(config)["token"]
    except (Error, KeyError, TypeError, AttributeError):
        return None
    return token if isinstance(token, str) and len(token) >= 32 else None


def worker_stream(config, path, body, timeout=60):
    """A Worker response read as a stream (a download); errors answer like worker_request's."""
    config = connection(config)
    request = urllib.request.Request(config["url"].rstrip("/") + path, data=dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + config["token"], "Content-Type": "application/json"})
    try:
        return _open(request, timeout)
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                data = json.load(exc)
            except ValueError:
                data = {}
        raise Error(exc.code, data.get("code", "worker_error"), data.get("error", "Worker 请求失败")) from None
    except (OSError, ValueError) as exc:
        raise Error(503, "worker_unavailable", "Worker 未返回可核实回执") from exc


def worker_upload(config, path, data, headers, timeout=120):
    """Bytes sent to the Worker as they are (D58 sync's bundle); the answer and errors as worker_request's."""
    config = connection(config)
    request = urllib.request.Request(config["url"].rstrip("/") + path, data=data, method="POST",
                                     headers={"Authorization": "Bearer " + config["token"],
                                              "Content-Type": "application/octet-stream", **headers})
    try:
        with _open(request, timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                data = json.load(exc)
            except ValueError:
                data = {}
        raise Error(exc.code, data.get("code", "worker_error"), data.get("error", "Worker 请求失败")) from None
    except (OSError, ValueError) as exc:
        raise Error(503, "worker_unavailable", "Worker 未返回可核实回执") from exc


def worker_request(config, path, body=None, timeout=35, limit=None):
    config = connection(config)
    url = config["url"].rstrip("/") + path
    request = urllib.request.Request(url, data=dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + config["token"],
                                              "Content-Type": "application/json"})
    try:
        with _open(request, timeout) as response:
            if limit is None:
                return json.load(response)
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("answer over the size limit")
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                data = json.load(exc)
            except ValueError:
                data = {}
        raise Error(exc.code, data.get("code", "worker_error"), data.get("error", "Worker 请求失败")) from None
    except (OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, ssl.SSLError):
            raise Error(503, "worker_unavailable", "Worker TLS 证书校验失败（未受信任的 CA 或地址不匹配）") from exc
        raise Error(503, "worker_unavailable", "Worker 未返回可核实回执") from exc


def check_connection(config):
    """A Worker connection's form, before anything is sent: HTTPS (HTTP only to this machine), a bare origin, a token."""
    require(isinstance(config, dict) and set(config) == {"url", "token"}, "Worker 仅接受 URL 和访问 Token")
    require(isinstance(config["url"], str) and len(config["url"]) <= 2048, "Worker URL 无效")
    parts = urlsplit(config["url"])
    require(parts.scheme == "https" or parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"},
            "远程 Worker 必须使用 HTTPS；HTTP 仅限本机")
    require(parts.hostname and not parts.username and not parts.password and parts.path in {"", "/"}
            and not parts.query and not parts.fragment, "Worker URL 无效")
    require(isinstance(config["token"], str) and len(config["token"]) >= 32, "Worker Token 至少 32 字符")
    return parts


def validate_worker(config):
    check_connection(config)
    return worker_request(config, "/health", limit=HEALTH_LIMIT)
