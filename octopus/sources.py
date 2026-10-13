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


def left_out(path):
    """Whether a project path is never packed or published: a dependency or cache folder, or a .env file."""
    parts = path.split("/")
    name = parts[-1]
    return any(part in SKIP_DIRS for part in parts[:-1]) \
        or any(parts[i:i + 2] == [".next", "cache"] for i in range(len(parts) - 1)) \
        or ((name == ".env" or name.startswith(".env.")) and name not in ENV_KEPT)


def commit_pending(project, message, branch):
    """Commit the workspace's pending changes on ``branch`` for publishing, without what a download leaves out (a .env
    or dependency folder the project forgot to ignore stays in the workspace, uncommitted). The new commit, or None."""
    current = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise ValueError(f"工作区不在分支 {branch}（当前 {current}），未发布")
    _git(project, "add", "-A")
    staged = [p for p in _git(project, "diff", "--cached", "--name-only", "-z").split("\0") if p]
    dropped = [p for p in staged if left_out(p)]
    for start in range(0, len(dropped), 200):
        _git(project, "reset", "-q", "--", *dropped[start:start + 200])   # back to HEAD's version (or unstaged)
    if not _git(project, "diff", "--cached", "--name-only"):
        return None
    _git(project, "-c", "user.name=KiroCrew ON", "-c", "user.email=kirocrew-on@localhost", "-c", "commit.gpgsign=false",
         "commit", "-q", "--no-verify", "-m", message)
    return _git(project, "rev-parse", "HEAD")


def suspicious(project, base):
    """Files whose added lines since ``base`` look like a credential (only the names, never the content)."""
    diff = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff", "-U0", f"{base}..HEAD"], cwd=project,
                          capture_output=True, timeout=120, stdin=subprocess.DEVNULL).stdout
    found, current = [], None
    for line in diff.split(b"\n"):
        if line.startswith(b"+++ "):
            current = line[6:].decode(errors="replace") if line.startswith(b"+++ b/") else None
        elif line.startswith(b"+") and current and current not in found and SUSPICIOUS.search(line):
            found.append(current)
    return found


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
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    result = subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=180, env=env)
    if result.returncode:
        raise ValueError(f"Git 操作失败（exit {result.returncode}）："
                         + (result.stderr.strip() or result.stdout.strip())[-3000:])
    return result.stdout.strip()


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
