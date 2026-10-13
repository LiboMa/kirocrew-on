"""Small API client usable by humans, scripts and Coding Agents."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from .db import dumps


class ApiError(RuntimeError):
    """An API error response: HTTP status, the API's stable code and its message (spec §7.2: Agents act on these)."""

    def __init__(self, status, code, error, text=None):
        super().__init__(text or f"{status} {code}: {error}")
        self.status, self.code, self.error = status, code, error


def http_error(exc):
    """A readable error for an API error response, including non-JSON pages from a proxy (CloudFront)."""
    with exc:
        raw = exc.read()
    try:
        result = json.loads(raw)
    except ValueError:
        hint = "：API 暂不可用，稍后重试" if exc.code >= 500 else ""
        return ApiError(exc.code, "http_error", f"{exc.reason}{hint}", f"{exc.code} {exc.reason}{hint.replace('：', ': ', 1)}")
    if not isinstance(result, dict):
        return ApiError(exc.code, "http_error", str(exc.reason), f"{exc.code} {exc.reason}")
    return ApiError(exc.code, result.get("code"), result.get("error"))


class Client:
    def __init__(self, url="http://127.0.0.1:8918", token=""):
        parts = urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Remote API connections require HTTPS")
        self.url, self.token = url.rstrip("/"), token

    def request(self, method, path, body=None, timeout=40):
        if not path.startswith("/v1/") or path.startswith("//"):
            raise ValueError("Use a /v1/ API path")
        req = urllib.request.Request(self.url + path, method=method,
                                     data=dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise http_error(exc) from None

    def events(self, run_id, after=0):
        """One bounded SSE connection; resume using the last yielded event id."""
        path = "/v1/runs/" + run_id + "/events?" + urlencode({"stream": 1, "after": after})
        req = urllib.request.Request(self.url + path, headers={"Authorization": "Bearer " + self.token})
        try:
            stream = urllib.request.urlopen(req, timeout=45)
        except urllib.error.HTTPError as exc:
            raise http_error(exc) from None
        with stream:
            event_type = ""
            for raw in stream:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                if line.startswith("data:"):
                    if event_type == "access_revoked":
                        raise RuntimeError("Session access or Token was revoked")
                    yield json.loads(line[5:].strip())
