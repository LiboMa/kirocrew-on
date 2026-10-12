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


def wait_for_file(path, timeout, interval=0.2):
    deadline = time.monotonic() + timeout
    while not Path(path).is_file():
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


def runtime_marker_ok(path):
    """The Gateway started with the composition AND its serving process built the composed factory."""
    try:
        marker = json.loads(Path(path).read_text())
        factory = json.loads(Path(path).with_name("gateway-factory.json").read_text())
        os.kill(int(marker["pid"]), 0)
        built_by_server = int(factory["pid"]) == int(marker["pid"]) and float(factory["built_at"]) >= float(marker["started_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (built_by_server and marker.get("composition") == "PocProviderRegistry"
            and "codex_effort_pair_v1" in marker.get("composition_features", []))
