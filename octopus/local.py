"""The user's side of local / remote (D58 M3-4): a local repository linked to a Session (.octopus/session.json), the
Session's branch and context pulled into it as a git bundle, local commits synced back as one, and the commits a
submission's remote could not see. The CLI and the MCP tools share these; the platform checks everything again."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

LINK = ".octopus/session.json"
CONTEXT = ".octopus/sessions"
PERSONAL = (".octopus/session.json", ".octopus/sessions/")   # never committed: .git/info/exclude
SYNC_LIMIT = 20 * 1024 * 1024   # as the platform's


def _run(cwd, *args, timeout=300):
    """git, never waiting for a terminal (a credential prompt would hang an MCP call) nor longer than ``timeout``."""
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise ValueError(f"git {args[0]} 超过 {timeout} 秒未完成") from None


def _git(cwd, *args):
    result = _run(cwd, *args)
    if result.returncode:
        raise ValueError(f"git {args[0]} 失败：" + (result.stderr.strip() or result.stdout.strip())[-500:])
    return result.stdout.strip()


def repository(directory):
    """The repository ``directory`` is in (its top level), or None."""
    result = _run(directory, "rev-parse", "--show-toplevel") if Path(directory).is_dir() else None
    return Path(result.stdout.strip()).resolve() if result is not None and result.returncode == 0 else None


def _repo(directory):
    repo = repository(directory)
    if repo is None:
        raise ValueError(f"{directory} 不在 Git 仓库中")
    return repo


def https(url):
    """The HTTPS form of an ssh:// (port dropped) or git@host:path address — the execution environment clones over HTTPS
    only; anything else (https, git://, file://, a path) as it is."""
    ssh = re.fullmatch(r"ssh://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+?)/?", url)
    scp = re.fullmatch(r"[^@/:]+@([^:/]+):(?!/)(.+?)/?", url)
    found = ssh or scp
    return f"https://{found.group(1)}/{found.group(2)}" if found else url


def source(directory):
    """A submission's source from the repository: origin's address (as HTTPS) and the current branch; a new project
    without an origin."""
    repo = _repo(directory)
    origin = _run(repo, "remote", "get-url", "origin")
    if origin.returncode:
        return {"type": "new"}
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return {"type": "git", "url": https(origin.stdout.strip()), **({"ref": branch} if branch != "HEAD" else {})}


def unpushed(directory):
    """How many commits of HEAD origin does not have: ahead of origin/<branch>, or — for a branch origin lacks — every
    commit no origin branch has. The execution environment clones from origin and cannot see them."""
    repo = _repo(directory)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if _run(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}").returncode == 0:
        return int(_git(repo, "rev-list", "--count", f"origin/{branch}..HEAD"))
    return int(_git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes=origin"))


def missing_branch(directory):
    """The current branch, when origin has no such branch (the execution environment could not clone it); else None."""
    repo = _repo(directory)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD" or _run(repo, "remote", "get-url", "origin").returncode:
        return None
    return None if _run(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}").returncode == 0 else branch


def push_base(directory):
    """Push the current branch to origin with the user's own credentials (--push-base), so the remote can clone it."""
    _git(_repo(directory), "push", "-q", "-u", "origin", "HEAD")


def linked(directory):
    repo = repository(directory)
    path = repo / LINK if repo else None
    try:
        return json.loads(path.read_text()) if path and path.is_file() else None
    except ValueError:
        return None


def link(directory, values):
    """Link the repository to a Session (.octopus/session.json), kept out of commits."""
    repo = _repo(directory)
    path = repo / LINK
    path.parent.mkdir(parents=True, exist_ok=True)
    current = linked(repo) or {}
    if current.get("run_id") not in (None, values.get("run_id")):
        current = {}   # another Session now: nothing of the old link carries over
    path.write_text(json.dumps({**current, **{k: v for k, v in values.items() if v is not None}, "updated": time.time()},
                               ensure_ascii=False, indent=2) + "\n")
    exclude = Path(_git(repo, "rev-parse", "--git-path", "info/exclude"))
    exclude = exclude if exclude.is_absolute() else repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text().splitlines() if exclude.is_file() else []
    missing = [line for line in PERSONAL if line not in lines]
    if missing:
        exclude.write_text("\n".join(lines + missing) + "\n")
    return path


def _ancestor(repo, old, new):
    return _run(repo, "merge-base", "--is-ancestor", old, new).returncode == 0


def _context(client, rid, repo):
    """What a pull brings besides the branch: the report, the plan and each task's latest handoff."""
    folder = repo / CONTEXT / rid
    (folder / "handoffs").mkdir(parents=True, exist_ok=True)
    view = client.request("GET", f"/v1/runs/{rid}")
    (folder / "report.md").write_text(client.text("GET", f"/v1/runs/{rid}/report?format=md"))
    keep = ("name", "tool", "model", "kind", "role", "acceptance", "verify", "paths")
    plan = {"id": rid, "name": view.get("name"), "input": view.get("input"),
            "summary": (view.get("planning") or {}).get("summary"),
            "tasks": [{"id": t["id"], "status": t.get("status"), **{k: t["spec"][k] for k in keep if k in t.get("spec", {})}}
                      for t in view.get("tasks") or []]}
    (folder / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    for task in view.get("tasks") or []:
        latest = next((a for a in reversed(task.get("attempts") or []) if a.get("result")), None)
        if latest:
            (folder / "handoffs" / (re.sub(r"[^A-Za-z0-9._-]", "_", task["id"]) + ".md")).write_text(latest["result"])
    return folder, view


def pull(client, rid, directory, checkout=False, full=False):
    """Bring the Session's branch into the local repository as ``octopus/<run>`` (never over local commits of its own),
    with its context; the whole history when the repository lacks the Session's baseline."""
    repo = _repo(directory)
    url = client.request("POST", f"/v1/runs/{rid}/bundle", {})["url"]
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "pull.bundle"

        def fetch(whole):
            with open(bundle, "wb") as file:
                client.download(url + ("?full=1" if whole else ""), file)
            return _run(repo, "bundle", "verify", "-q", str(bundle)).returncode == 0
        whole = full
        if not fetch(whole) and not whole:   # the repository lacks the baseline the bundle starts from
            whole = True
            if not fetch(whole):
                raise ValueError("拉回的分支无法导入这个仓库")
        heads = [line.split(" ", 1) for line in _git(repo, "bundle", "list-heads", str(bundle)).splitlines()]
        sha, ref = next((h for h in heads if h[1].startswith("refs/heads/")), (None, None))
        if not sha:
            raise ValueError("拉回的内容里没有会话分支")
        branch = ref.removeprefix("refs/heads/")
        existing = _run(repo, "rev-parse", "--verify", "--quiet", ref).stdout.strip()
        _git(repo, "fetch", "-q", "--no-tags", str(bundle), ref)
        current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        extra = {}
        if existing and existing != sha and _ancestor(repo, sha, existing):   # only the local branch moved: nothing to bring
            extra = {"ahead": int(_git(repo, "rev-list", "--count", f"{sha}..{existing}"))}
        elif existing and existing != sha and not _ancestor(repo, existing, sha):   # both moved: keep the remote aside
            aside = f"refs/octopus/{rid}/remote"
            _git(repo, "update-ref", aside, sha)
            extra = {"diverged": True, "remote_ref": aside,
                     "hint": f"本地分支 {branch} 与会话分支都有新提交：会话的最新提交已取为 {aside}。请在 {branch} 上 "
                             f"git rebase {aside}（或 merge）后再 sync"}
        elif current == branch:
            if existing != sha:
                _git(repo, "merge", "-q", "--ff-only", sha)
        else:
            _git(repo, "update-ref", ref, sha, *([existing] if existing else []))
        if checkout and current != branch:
            _git(repo, "switch", "-q", branch)
    folder, view = _context(client, rid, repo)
    link(repo, {"run_id": rid, "request_id": (view.get("request") or {}).get("id"), "url": getattr(client, "url", None),
                "branch": branch, "head": sha})
    return {"branch": branch, "head": sha, "full": whole, "checked_out": checkout or current == branch, "context": str(folder),
            **extra}


def sync(client, rid, directory, note=None):
    """Send the local commits made on top of the Session's head (as a bundle); the platform moves the Session's branch
    only for a fast-forward with nothing that looks like a credential, and checks and reviews them again."""
    repo = _repo(directory)
    info = linked(repo) or {}
    old = info.get("head") if info.get("run_id") == rid else None
    if not old:
        raise ValueError(f"这个仓库还没有拉回会话 {rid}：请先 pull")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("工作区有未提交的改动：sync 只同步提交，请先提交")
    head = _git(repo, "rev-parse", "HEAD")
    if head == old:
        raise ValueError("没有新的本地提交可以同步")
    if not _ancestor(repo, old, head):
        raise ValueError(f"当前分支不包含会话分支的最新提交 {old[:7]}：请先 pull，在会话分支上修改并提交后再同步")
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "sync.bundle"
        _git(repo, "bundle", "create", "-q", str(bundle), f"{old}..HEAD")
        data = bundle.read_bytes()
    if len(data) > SYNC_LIMIT:
        raise ValueError(f"要同步的内容超过 {SYNC_LIMIT // 1048576} MB")
    version = client.request("GET", f"/v1/runs/{rid}")["version"]
    request = "sync-" + hashlib.sha256(f"{rid}:{old}:{head}".encode()).hexdigest()[:24]   # a retry is the same sync
    query = urlencode({"request_id": request, "expected_version": version, "old": old, "head": head, **({"note": note} if note else {})})
    result = client.upload(f"/v1/runs/{rid}/sync?{query}", data)
    link(repo, {"run_id": rid, "head": head})
    return {**result, "old": old}
