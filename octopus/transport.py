"""Authenticated platform-to-worker transport; Worker tokens stay server-side."""
import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .db import dumps
from .security import Error, require


_context = None


def configure(ca_file):
    """Trust only this CA for HTTPS Workers (the platform's private CA, D28); None restores the system trust store."""
    global _context
    _context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else None


def worker_stream(config, path, body, timeout=60):
    """A Worker response read as a stream (a download); errors answer like worker_request's."""
    request = urllib.request.Request(config["url"].rstrip("/") + path, data=dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + config["token"], "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=timeout, context=_context)
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
    request = urllib.request.Request(config["url"].rstrip("/") + path, data=data, method="POST",
                                     headers={"Authorization": "Bearer " + config["token"],
                                              "Content-Type": "application/octet-stream", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context) as response:
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


def worker_request(config, path, body=None, timeout=35):
    url = config["url"].rstrip("/") + path
    request = urllib.request.Request(url, data=dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + config["token"],
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context) as response:
            return json.load(response)
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


def validate_worker(config):
    require(isinstance(config, dict) and set(config) == {"url", "token"}, "Worker 仅接受 URL 和访问 Token")
    parts = urlsplit(config["url"])
    require(parts.scheme == "https" or parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"},
            "远程 Worker 必须使用 HTTPS；HTTP 仅限本机")
    require(parts.hostname and not parts.username and not parts.password and parts.path in {"", "/"}
            and not parts.query and not parts.fragment, "Worker URL 无效")
    require(isinstance(config["token"], str) and len(config["token"]) >= 32, "Worker Token 至少 32 字符")
    return worker_request(config, "/health")
