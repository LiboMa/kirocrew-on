"""Small API client usable by humans, scripts and Coding Agents."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from .db import dumps


class Client:
    def __init__(self, url="http://127.0.0.1:8918", token=""):
        parts = urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Remote API connections require HTTPS")
        self.url, self.token = url.rstrip("/"), token

    def request(self, method, path, body=None):
        if not path.startswith("/v1/") or path.startswith("//"):
            raise ValueError("Use a /v1/ API path")
        req = urllib.request.Request(self.url + path, method=method,
                                     data=dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read()
            try:
                result = json.loads(raw)
            except ValueError:
                # A proxy (CloudFront) answered for an unavailable origin with a non-JSON page.
                raise RuntimeError(f"{exc.code} {exc.reason}: API 暂不可用，稍后重试") from None
            raise RuntimeError(f"{exc.code} {result.get('code')}: {result.get('error')}") from None

    def events(self, run_id, after=0):
        """One bounded SSE connection; resume using the last yielded event id."""
        path = "/v1/runs/" + run_id + "/events?" + urlencode({"stream": 1, "after": after})
        req = urllib.request.Request(self.url + path, headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=45) as stream:
            event_type = ""
            for raw in stream:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                if line.startswith("data:"):
                    if event_type == "access_revoked":
                        raise RuntimeError("Session access or Token was revoked")
                    yield json.loads(line[5:].strip())
