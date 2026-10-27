"""「新建边界」 from the console (MVP 0.2.0 P2 Task 6, design §4.2.5, S30): the API side. The API runs with NoNewPrivileges
and never becomes root; it leaves a request — a checked name and user name — in a directory of its own, and a root
service a systemd path unit starts (deploy/install/boundary-provision.py) checks both again and runs boundary.sh with
them. Progress comes back through a root-owned directory the API only reads."""
import json
import os
from pathlib import Path
import re
import secrets

# deploy/install/boundary.sh's own patterns: a boundary name, a platform user name
NAME = re.compile(r"[a-z][a-z0-9-]{1,19}")
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ID = re.compile(r"prv-[0-9a-f]{16}")
REQUESTS = "boundary-requests"   # under the platform's data directory
RESULTS = Path("/var/lib/octopus/boundary-provision")
LOG_LINES = 100
KEPT = 20


def boundary_name(username):
    """The name a person's boundary gets by default: their user name in boundary.sh's terms, or None."""
    name = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", username.lower())).strip("-")
    name = re.sub(r"^[^a-z]+", "", name)[:20].rstrip("-")
    return name if NAME.fullmatch(name) and name != "personal" else None


def valid_name(name):
    return isinstance(name, str) and bool(NAME.fullmatch(name)) and name != "personal"


class Provisions:
    def __init__(self, requests, results=RESULTS):
        self.requests, self.results = Path(requests), Path(results)

    def submit(self, name, owner):
        """A request for the root service: written beside its final name, then renamed, so it is never read half-done."""
        if not (valid_name(name) and isinstance(owner, str) and OWNER.fullmatch(owner)):
            raise ValueError("边界名称或用户名不对")
        self.requests.mkdir(mode=0o700, parents=True, exist_ok=True)
        rid = "prv-" + secrets.token_hex(8)
        partial = self.requests / f"{rid}.json.part"
        with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            json.dump({"id": rid, "name": name, "owner": owner}, file)
        os.rename(partial, self.requests / f"{rid}.json")
        return {"id": rid, "name": name, "owner": owner, "state": "queued", "started": None}

    def busy(self, name):
        return any(item["name"] == name and item["state"] in ("queued", "running") for item in self.items())

    def items(self):
        """Requests still waiting, then what the root service recorded, newest first."""
        found = []
        for path in sorted(self.requests.glob("prv-*.json")) if self.requests.is_dir() else []:
            try:
                request = json.loads(path.read_text())
                found.append({"id": path.stem, "name": request["name"], "owner": request["owner"], "state": "queued",
                              "started": path.stat().st_mtime, "log": []})
            except (OSError, ValueError, KeyError):
                continue
        recorded = []
        for path in self.results.glob("prv-*.json") if self.results.is_dir() else []:
            if not ID.fullmatch(path.stem):
                continue
            try:
                result = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            log = path.with_suffix(".log")
            try:
                lines = log.read_text(errors="replace").splitlines()[-LOG_LINES:] if log.is_file() else []
            except OSError:
                lines = []
            item = {key: result.get(key) for key in ("name", "owner", "state", "exit", "error", "started", "finished")}
            item.update(id=path.stem, log=lines)
            if item["state"] == "done":
                item["next"] = (f"需要从公网打开 KiroCrew 时，请管理员在自己电脑或 CloudShell 上运行 kirocrew-on single native "
                                f"{item['name']}；在此之前经本机端口转发使用。{item['owner']} 在「我的设置 › 执行环境」里登录 Kiro。")
            recorded.append(item)
        recorded.sort(key=lambda item: item.get("started") or 0, reverse=True)
        return found + recorded[:KEPT]
