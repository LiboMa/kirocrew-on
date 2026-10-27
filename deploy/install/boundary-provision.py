"""The root side of the console's 「新建边界」 (MVP 0.2.0 P2 Task 6, design §4.2.5, S30). octopus-boundary-provision.path
starts it as root (octopus-boundary-provision.service) when the API leaves a request in its own directory. Each request
is read without following links, only from a regular file the API's user owns, checked again with boundary.sh's own
patterns, removed, and carried out by boundary.sh with that name and owner and nothing else, one at a time. Progress
goes to a root-owned directory the API reads (group octopus); anything token-like is masked first.

Standard library only (python -I).
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import time

NAME = re.compile(r"[a-z][a-z0-9-]{1,19}")
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ID = re.compile(r"prv-[0-9a-f]{16}")
SECRET = re.compile(r"[A-Za-z0-9_\-]{32,}")   # tokens and codes never reach a log the console shows
LIMIT = 4096
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/root"}


def record(results, rid, result):
    """Written beside its final name and renamed: the API never reads half of it. The directory is root's."""
    partial = results / f"{rid}.json.part"
    with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640), "w") as file:
        json.dump(result, file)
    os.replace(partial, results / f"{rid}.json")


def take(path, owner_uid):
    """The request's content, or None; either way the file is gone (a link is removed, never followed)."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        path.unlink(missing_ok=True)
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or info.st_size > LIMIT:
            return None
        return json.loads(os.read(descriptor, LIMIT + 1))
    except (OSError, ValueError):
        return None
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def carry_out(results, script, rid, body):
    name, owner = body.get("name"), body.get("owner")
    plain = isinstance(name, str) and NAME.fullmatch(name) and name != "personal" and isinstance(owner, str) \
        and OWNER.fullmatch(owner)
    result = {"id": rid, "name": name if isinstance(name, str) and NAME.fullmatch(name) else None,
              "owner": owner if isinstance(owner, str) and OWNER.fullmatch(owner) else None, "started": time.time()}
    if not plain:
        record(results, rid, {**result, "state": "failed", "error": "边界名称或用户名不对", "finished": time.time()})
        return
    record(results, rid, {**result, "state": "running"})
    with open(os.open(results / f"{rid}.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640), "w") as log:
        process = subprocess.Popen(["/bin/bash", str(script), name, "--owner", owner], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors="replace",
                                   env={**ENV, **({"AWS_REGION": os.environ["AWS_REGION"]} if os.environ.get("AWS_REGION") else {})})
        for line in process.stdout:
            log.write(SECRET.sub("…", line))
            log.flush()
        code = process.wait()
    record(results, rid, {**result, "state": "done" if code == 0 else "failed", "exit": code, "finished": time.time()})


def main(argv=None):
    parser = argparse.ArgumentParser(description="新建控制台要求的边界（root；只运行 boundary.sh）")
    parser.add_argument("--requests", default="/var/lib/octopus/platform/boundary-requests")
    parser.add_argument("--results", default="/var/lib/octopus/boundary-provision")
    parser.add_argument("--script", default="/opt/kirocrew-on/current/deploy/install/boundary.sh")
    parser.add_argument("--owner-uid", type=int, help="请求文件的属主（默认 octopus 用户）")
    args = parser.parse_args(argv)
    owner_uid = args.owner_uid if args.owner_uid is not None else pwd.getpwnam("octopus").pw_uid
    requests, results = Path(args.requests), Path(args.results)
    while True:   # what arrives while one runs is taken next
        waiting = []
        for entry in os.scandir(requests):
            if entry.name.endswith(".json") and ID.fullmatch(entry.name[:-5]):
                waiting.append((entry.stat(follow_symlinks=False).st_mtime, entry.name))
        if not waiting:
            return 0
        for _, filename in sorted(waiting):
            body = take(requests / filename, owner_uid)
            if isinstance(body, dict):
                carry_out(results, args.script, filename[:-5], body)


if __name__ == "__main__":
    raise SystemExit(main())
