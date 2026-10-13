"""Prepare a pinned project checkout before native workflow dispatch.

Invoked by the KiroCrew coordinator through its existing Shell tool. No model, Coding CLI or new Gateway endpoint is
introduced. A private repository's token (D35 §5.1) reaches git only through GIT_ASKPASS and a 0600 file that exists
for the clone alone; it is never copied into the workspace, the remote URL or any record.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

HIDDEN = "[凭证已隐去]"

# Left out of a download (D36 §4.2) and of a published commit (D35 §5.2), ignored by .gitignore or not.
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "target", ".gradle",
             ".turbo", ".parcel-cache", ".cache"}
ENV_KEPT = {".env.example", ".env.sample"}
# What a credential looks like in a diff: GitHub (fine-grained, classic, OAuth, app), GitLab, AWS keys, private keys.
SUSPICIOUS = re.compile(rb"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
                        rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----")


def env_file(name):
    """A .env file or folder (or .env.<anything>), which holds secrets; the examples stay."""
    return (name == ".env" or name.startswith(".env.")) and name not in ENV_KEPT


def left_out(path):
    """Whether a project path is never packed or published: a dependency or cache folder, or a .env file or folder."""
    parts = path.split("/")
    return any(part in SKIP_DIRS or env_file(part) for part in parts[:-1]) or env_file(parts[-1]) \
        or any(parts[i:i + 2] == [".next", "cache"] for i in range(len(parts) - 1))


# The same rules as git pathspecs: excluded folders are never even read into git's object store.
LEFT_OUT = tuple(f":(exclude,glob)**/{name}/**" for name in sorted(SKIP_DIRS)) + (
    ":(exclude,glob)**/.next/cache/**", ":(exclude,glob)**/.env", ":(exclude,glob)**/.env/**",
    ":(exclude,glob)**/.env.*", ":(exclude,glob)**/.env.*/**")
KEPT = tuple(f":(glob)**/{name}" for name in sorted(ENV_KEPT))
COMMITTER = ("-c", "user.name=KiroCrew ON", "-c", "user.email=kirocrew-on@localhost", "-c", "commit.gpgsign=false")
OVERLAP = 4096   # longer than any match SUSPICIOUS needs, so a match across two read chunks is still found


def publish_candidate(project, message, branch, scratch):
    """The commit a publish offers: HEAD plus the workspace's pending changes, without what a download leaves out (a .env
    or dependency folder the project forgot to ignore stays in the workspace, uncommitted). Built in a temporary index in
    ``scratch`` (outside the project) and not yet on any branch: the workspace's branch and index move only once the
    commit has been checked (``advance``). HEAD itself when nothing is pending."""
    current = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise ValueError(f"工作区不在分支 {branch}（当前 {current}），未发布")
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    index = Path(scratch) / ".publish-index"
    env = {"GIT_INDEX_FILE": str(index)}
    for leftover in (index, index.with_name(index.name + ".lock")):   # from an interrupted publish
        leftover.unlink(missing_ok=True)
    try:
        _git(project, "read-tree", head, env=env)
        _git(project, "add", "-A", "--", ".", *LEFT_OUT, env=env)
        kept = [path for path in _git_bytes(project, "ls-files", "-z", "-o", "-m", "-d", "--exclude-standard", "--", *KEPT,
                                            env=env).split(b"\0") if path and not left_out(path.decode(errors="replace"))]
        if kept:
            _git(project, "add", "-A", "--", *(b":(literal)" + path for path in kept), env=env)
        tree = _git(project, "write-tree", env=env)
    finally:
        for leftover in (index, index.with_name(index.name + ".lock")):
            leftover.unlink(missing_ok=True)
    if tree == _git(project, "rev-parse", head + "^{tree}"):
        return head
    return _git(project, *COMMITTER, "commit-tree", tree, "-p", head, "-m", message)   # no hooks, no signing


def unpublishable(project, base, head):
    """What in ``base..head`` must not be published, as names to show (never content): every object new since ``base`` is
    read as git stores it — any file type, any name, every commit message — and every path a new commit introduces is
    checked for a .env, so a secret committed and later removed is found in the history the bundle carries. Fails
    closed: any git failure is an error, not a pass."""
    commits = _git(project, "rev-list", head, "--not", base).split()
    objects = _git(project, "rev-list", "--objects", "--no-object-names", head, "--not", base).split()
    if not commits:
        return []
    new, introduced, found = set(objects), {}, []   # introduced: new blob → the paths new commits put it at
    tokens = _git_bytes(project, "diff-tree", "--stdin", "-r", "-z", "-m", "--root", "--no-renames", "--no-commit-id",
                        stdin="".join(c + "\n" for c in commits).encode()).split(b"\0")
    i = 0
    while i < len(tokens):
        fields = tokens[i][1:].split(b" ") if tokens[i].startswith(b":") else []
        if len(fields) == 5 and i + 1 < len(tokens):   # ":<mode> <mode> <sha> <sha> <status>", then the path
            sha, path = fields[3].decode(), tokens[i + 1].decode(errors="replace")
            if sha in new:
                introduced.setdefault(sha, set()).add(path)
                if any(env_file(part) for part in path.split("/")):
                    found.append(path)
            i += 2
        else:
            i += 1
    for sha, kind in _suspicious_objects(project, objects):
        found.extend([f"提交 {sha[:7]} 的说明"] if kind == "commit" else sorted(introduced.get(sha) or {sha[:12]}))
    return list(dict.fromkeys(found))


def _suspicious_objects(project, objects):
    """(sha, type) of the blobs and commits among ``objects`` whose stored bytes match SUSPICIOUS, read through one
    ``git cat-file --batch`` in chunks (a large file is never held whole)."""
    hits = []
    with subprocess.Popen(["git", "cat-file", "--batch"], cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}) as process:
        def feed():
            try:
                process.stdin.write("".join(o + "\n" for o in objects).encode())
                process.stdin.close()
            except (OSError, ValueError):   # git ended, or the pipe was closed after a failure
                pass
        threading.Thread(target=feed, daemon=True).start()
        try:
            for _ in objects:
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise ValueError("Git 对象读取失败：" + b" ".join(header).decode(errors="replace")[:200])
                sha, kind, remaining = header[0].decode(), header[1].decode(), int(header[2])
                tail, hit = b"", False
                while remaining:
                    chunk = process.stdout.read(min(remaining, 1 << 20))
                    if not chunk:
                        raise ValueError("Git 对象读取中断")
                    remaining -= len(chunk)
                    window = tail + chunk
                    hit = hit or (kind != "tree" and SUSPICIOUS.search(window) is not None)
                    tail = window[-OVERLAP:]
                process.stdout.read(1)   # the newline after each object
                if hit:
                    hits.append((sha, kind))
        finally:
            process.kill()
    return hits


def advance(project, branch, commit, old):
    """Put the checked ``commit`` on ``branch`` (still at ``old``) and the workspace's index to match; files untouched."""
    if commit == old:
        return
    if _git(project, "rev-parse", "--verify", "HEAD^{commit}") != old \
            or _git(project, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise ValueError("发布期间工作区的分支被改动，未发布，请重试")
    _git(project, "reset", "-q", "--mixed", commit)


def scrub(text, secret=None):
    """Git output as it may be shown or stored: no URL userinfo, no Authorization / PRIVATE-TOKEN line, no secret."""
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1", str(text))
    text = re.sub(r"(?im)^.*\b(authorization|private-token)\s*:.*$", HIDDEN, text)
    return text.replace(secret, HIDDEN) if secret else text


# Authenticated git ignores the system and the user's global configuration — an agent of the same OS user could
# redirect the token there (url.<x>.insteadOf) or add a helper — speaks HTTPS only and follows no redirect.
SAFE_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
SAFE_GIT_ARGS = ("-c", "credential.helper=", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                 "-c", "http.followRedirects=false")


@contextmanager
def askpass(run, auth):
    """GIT_ASKPASS answering git's Username / Password prompts from a 0600 file, both removed when the clone ends. It
    answers only the prompts for its own host (and user), so the token goes nowhere else."""
    host, user = str(auth.get("host", "")), str(auth.get("username", ""))
    if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host) or not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        raise ValueError("凭证的主机或用户名无效")
    folder = run / ".askpass"
    shutil.rmtree(folder, ignore_errors=True)   # left by a crash
    folder.mkdir(mode=0o700)
    try:
        secret = folder / "secret"
        fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(auth["secret"])
        script = folder / "askpass.sh"
        script.write_text("#!/bin/sh\ncase \"$1\" in\n"
                          f"  \"Username for 'https://{host}'\"*) printf '%s\\n' {shlex.quote(user)} ;;\n"
                          f"  \"Password for 'https://{user}@{host}'\"*) cat {shlex.quote(str(secret))} ;;\n"
                          "  *) exit 1 ;;\nesac\n")
        script.chmod(0o700)
        yield {**SAFE_GIT_ENV, "GIT_ASKPASS": str(script)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def read_record(run):
    path = run / "project.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("项目记录不能是符号链接。")
    return json.loads(path.read_text())


def _write(run, value):
    temporary = run / (".project-" + uuid.uuid4().hex + ".json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, run / "project.json")


def _git(cwd, *args, env=None):
    return _git_bytes(cwd, *args, env=env).decode(errors="replace").strip()


def _git_bytes(cwd, *args, env=None, stdin=None):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                                capture_output=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError(f"Git 操作超时（{args[0] if args else 'git'}，180 秒）") from None
    if result.returncode:
        raise ValueError(f"Git 操作失败（exit {result.returncode}）："
                         + (result.stderr.strip() or result.stdout.strip())[-3000:].decode(errors="replace"))
    return result.stdout


def normalize_source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError("请明确提供用户指定的 Git 仓库，或选择新建项目。")
    source = source.strip()
    if any(ord(c) < 32 for c in source) or source.startswith("-"):
        raise ValueError("仓库来源包含非法字符。")
    if source.startswith(("https://", "ssh://")):
        url = urlsplit(source)
        if not url.hostname or url.password or url.query or url.fragment:
            raise ValueError("仓库地址不能包含密码、查询参数或片段；使用 Git 原有认证。")
        if url.scheme == "https" and url.username:
            raise ValueError("HTTPS 仓库地址不能嵌入凭据；使用 Git credential helper。")
        return source, "git"
    if re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s]+", source):
        return source, "git"
    path = Path(source).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("支持 HTTPS、SSH 仓库或绝对路径的本地 Git 仓库。")
    return str(path.resolve()), "local-git"


def project_dir(run):
    record = read_record(run)
    if not record or record.get("status") != "ready":
        raise ValueError("项目源码尚未就绪，请先完成 workspace 准备。")
    expected = run / "workspace/project"
    if (run / "workspace").is_symlink() or expected.is_symlink():
        raise ValueError("项目目录不能通过符号链接指向其他项目。")
    if record.get("directory") != "workspace/project" or not expected.is_dir():
        raise ValueError("已记录的项目目录不存在或不属于本次运行。")
    if record["kind"] != "new":
        if record["kind"] not in {"git", "local-git"} or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", str(record.get("commit", ""))):
            raise ValueError("项目记录的源码类型或 commit 无效。")
        if not (expected / ".git").is_dir() or (expected / ".git").is_symlink():
            raise ValueError("项目 Git 元数据缺失或被替换；停止派发并核查。")
        # The baseline commit must still exist. User-authorized commits may
        # advance HEAD, so readiness does not force-reset later development.
        _git(expected, "cat-file", "-e", record["commit"] + "^{commit}")
    return expected


def prepare_project(run, *, source=None, ref=None, new=False, auth=None):
    run = Path(run)
    if not run.is_dir() or not (run / "workflow-run.json").is_file():
        raise ValueError("缺少有效 Workflow Run。")
    if new == bool(source):
        raise ValueError("明确选择 --source 仓库，或 --new 新建项目。")
    if new and ref:
        raise ValueError("新建项目不使用仓库 ref。")
    source, kind = (None, "new") if new else normalize_source(source)
    ref = ref.strip() if isinstance(ref, str) else ref
    if ref is not None and (not ref or ref.startswith("-") or "\x00" in ref):
        raise ValueError("仓库 ref 无效。")
    workspace = run / "workspace"
    if workspace.is_symlink():
        raise ValueError("运行工作区不能是符号链接。")
    workspace.mkdir(exist_ok=True)
    destination = workspace / "project"
    with (run / ".project.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read_record(run)
        if previous and previous.get("status") == "ready":
            if (previous["source"], previous["requested_ref"], previous["kind"]) != (source, ref, kind):
                raise ValueError("本次源码来源已经固定；更换来源或 ref 请创建新运行。")
            project_dir(run)
            return previous
        if destination.exists() or destination.is_symlink():
            raise ValueError("项目目录已有未登记文件；保留文件，请核实来源后处理。")
        state = {"schema": 1, "status": "preparing", "kind": kind, "source": source,
                 "requested_ref": ref, "directory": "workspace/project", "at": time.time()}
        _write(run, state)
        staging = workspace / (".checkout-" + uuid.uuid4().hex)
        try:
            if new:
                # Like a cloned source: a repository on octopus/<run> with an empty first commit, so the download
                # carries the history and the agents' commits have a baseline. The platform's own commit runs no hooks.
                staging.mkdir()
                branch = "octopus/" + run.name
                _git(staging, "init", "-q", "-b", branch)
                _git(staging, "-c", "user.name=KiroCrew ON", "-c", "user.email=kirocrew-on@localhost",
                     "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--allow-empty",
                     "-m", "KiroCrew ON：新建项目")
                commit = _git(staging, "rev-parse", "HEAD")
            else:
                if kind == "local-git":
                    source_path = Path(source)
                    _git(source_path, "rev-parse", "--show-toplevel")
                    if not ref and _git(source_path, "status", "--porcelain", "--untracked-files=normal"):
                        raise ValueError("本地来源有未提交改动；本操作检出 Git 版本，不会隐式丢弃或提交改动。"
                                         "请用 --ref 明确选择已提交基线，或先保存所需源码版本。")
                if auth:
                    # No other credential helper answers; the remote keeps the plain URL; agents cannot push with it.
                    with askpass(run, auth) as env:
                        _git(workspace, *SAFE_GIT_ARGS, "clone", "--no-hardlinks", "--no-checkout", "--",
                             source, str(staging), env=env)
                    _git(staging, "remote", "set-url", "--push", "origin", "DISABLED_NO_PUSH")
                else:
                    _git(workspace, "clone", "--no-hardlinks", "--no-checkout", "--", source, str(staging))
                selected = ref or "HEAD"
                try:
                    commit = _git(staging, "rev-parse", "--verify", "--end-of-options", selected + "^{commit}")
                except ValueError:
                    if not ref:
                        raise
                    commit = _git(staging, "rev-parse", "--verify", "--end-of-options", "origin/" + ref + "^{commit}")
                branch = "octopus/" + run.name
                _git(staging, "checkout", "-b", branch, commit)
                if not any(p.name != ".git" for p in staging.iterdir()):
                    raise ValueError("仓库检出不含工作文件，无法作为已有项目开始研发。")
            os.replace(staging, destination)
            state.update(status="ready", commit=commit, branch=branch, at=time.time())
            _write(run, state)
            return state
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            reason = scrub(exc, auth and auth.get("secret"))
            state.update(status="failed", error=reason, at=time.time())
            if staging.exists():
                state["retained_attempt"] = str(staging.relative_to(run))
            _write(run, state)
            raise ValueError("源码准备失败：" + reason) from None
