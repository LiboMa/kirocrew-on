"""Owner-authenticated local Gateway inspection; secrets never printed."""
import json
from pathlib import Path
import urllib.request
import urllib.error
import threading
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent


class GatewayError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"{status}: {message}")


class ReadOnlyGateway:
    """Host-side observation with a short-lived cached native local token."""

    def __init__(self, port=5476):
        self.port = port
        self._token = ""
        self._until = 0
        self._lock = threading.Lock()

    def get(self, path):
        if not (path in {"/api/spawn", "/api/approvals"} or path.startswith("/api/chat/slots/")):
            raise ValueError("Observer read is outside its allowed native endpoints")
        with self._lock:
            if time.monotonic() >= self._until:
                self._token = token(self.port)
                self._until = time.monotonic() + 180
            credential = self._token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Cookie": f"mc_token_{self.port}={credential}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                with self._lock:
                    self._until = 0
            raise RuntimeError(f"Native observer GET {path}: HTTP {exc.code}") from None


def token(port=5476, home=None, *, ttl=None):
    home = home or Path.home() / ".kiro/crew"
    secret = (home / ".local_secret").read_text().strip()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/token/local" + ("?" + urlencode({"ttl": ttl}) if ttl else ""),
        headers={"X-Local-Secret": secret},
    )
    return json.load(urllib.request.urlopen(req, timeout=20))["token"]


def chat_entry_url(slot, port=5476, *, home=None, origin=None, ttl="8h"):
    """Use Crew's native local bootstrap and one-use URL-to-cookie exchange."""
    # Never cache this URL in reports, manifests, markup or log output.
    credential = token(port, home, ttl=ttl) if home is not None else token(port, ttl=ttl)
    return (origin or f"http://localhost:{port}").rstrip("/") + "/chat?" + urlencode({
        # Native tokens distinguish the short link window (at most 5 minutes)
        # from the browser session lifetime. A 60s TTL would log the user out.
        "sid": slot, "token": credential,
    })


def request(path, body=None, method=None, *, port=5476, home=None, timeout=30):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Cookie": f"mc_token_{port}={token(port, home)}", "Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{port}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise GatewayError(exc.code, exc.read().decode()) from None


def export_session(slot, port=5476):
    """Download Crew's native redacted Layer A bundle; never opt into Layer B."""
    from urllib.parse import quote
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/chat/slots/{quote(slot, safe='')}/export",
        headers={"Cookie": f"mc_token_{port}={token(port)}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError("原生 Session 导出超过 32 MB。")
        return data
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"原生 Session 导出失败：HTTP {exc.code}。请在 KiroCrew 确认会话可见且有消息。") from None
