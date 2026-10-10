"""Operational probes for service managers; no credentials are printed."""
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request


def wait_ready(checks, timeout, interval=0.5, opener=urllib.request.urlopen):
    deadline, pending = time.monotonic() + timeout, list(checks)
    while pending:
        remaining = []
        for url, headers in pending:
            try:
                with opener(urllib.request.Request(url, headers=headers), timeout=5) as response:
                    if getattr(response, "status", 200) != 200:
                        remaining.append((url, headers))
            except (urllib.error.URLError, OSError, ValueError):
                remaining.append((url, headers))
        pending = remaining
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return [url for url, _ in pending]


def runtime_marker_ok(path):
    try:
        marker = json.loads(Path(path).read_text())
        os.kill(int(marker["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return marker.get("composition") == "PocProviderRegistry" and "codex_effort_pair_v1" in marker.get("composition_features", [])
