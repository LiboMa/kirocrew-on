"""Publishing a Session's work (D35 §5.2): the platform, never the execution environment, pushes one branch to the
Session's repository with the bound write credential and opens a draft pull request (GitHub) or merge request (GitLab).

The Worker exports the branch as a bundle; the platform checks it in a temporary bare repository of its own, pushes only
that branch and only as a fast-forward (no force, never the default branch), and reuses an open request for the branch.
"""
import http.client
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import credentials as repo_credentials
from . import sources
from .security import Error, require

BRANCH = re.compile(r"octopus/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")
LIMIT = 100 * 1024 * 1024   # D35 §5.2: one bundle (as the Worker's PUBLISH_LIMIT)


def check_branch(branch, ref=None):
    """Only octopus/… branches, valid as git refs, and never the branch the Session came from."""
    require(isinstance(branch, str) and len(branch) <= 120 and BRANCH.fullmatch(branch) and ".." not in branch
            and not branch.endswith((".lock", ".")) and "/." not in branch, "分支名须为 octopus/<名称>（字母、数字、. _ - /）")
    require(branch != ref, "不能发布到会话的源分支", 400)
    return branch


def _git(cwd, *args, env=None, secret=None):
    try:
        return sources._git(cwd, *args, env=env)
    except ValueError as exc:
        raise Error(409, "publish_failed", sources.scrub(exc, secret)) from None


def receive(stream, length, workdir, branch, base, commit):
    """Store exactly ``length`` bytes of the Worker's bundle and fetch ``branch`` from it into a new bare repository at
    ``workdir``; the bundle must be at most LIMIT, complete, valid, built on the clone's baseline ``base``, and end at the
    ``commit`` the Worker checked."""
    require(length > 0, "待发布的内容没有完整收到", 409, "publish_invalid")
    require(length <= LIMIT, f"待发布的内容超过 {LIMIT // 1048576} MB", 413, "publish_too_large")
    workdir = Path(workdir)
    workdir.mkdir(parents=True)
    _git(workdir, "init", "-q", "--bare")
    bundle, remaining = workdir / "incoming.bundle", length
    try:
        with open(bundle, "wb") as sink:
            while remaining > 0:
                chunk = stream.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                sink.write(chunk)
                remaining -= len(chunk)
        require(remaining == 0, "待发布的内容没有完整收到", 409, "publish_invalid")
        sources._git(workdir, "bundle", "verify", "-q", str(bundle))
        sources._git(workdir, "fetch", "-q", str(bundle), f"refs/heads/{branch}:refs/heads/{branch}")
        head = sources._git(workdir, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
        sources._git(workdir, "merge-base", "--is-ancestor", base, head)
        if head != commit:
            raise ValueError("not the commit the Worker checked")
    except (ValueError, OSError, http.client.HTTPException):   # a connection that broke off reads as an incomplete bundle
        raise Error(409, "publish_invalid", "待发布的内容无效（不完整、被改动或不基于会话克隆时的提交）") from None
    finally:
        bundle.unlink(missing_ok=True)
    return head


def push(workdir, url, branch, commit, auth):
    """Push ``commit`` to ``branch`` at ``url`` as a fast-forward only: a branch someone else moved is left alone."""
    with sources.askpass(Path(workdir), auth) as env:
        listed = _git(workdir, *sources.SAFE_GIT_ARGS, "ls-remote", "--heads", url, f"refs/heads/{branch}",
                      env=env, secret=auth["secret"])
        theirs = listed.split()[0] if listed else None
        if theirs and theirs != commit:
            known = subprocess.run(["git", "-C", str(workdir), "cat-file", "-e", f"{theirs}^{{commit}}"]).returncode == 0
            ahead = known and subprocess.run(["git", "-C", str(workdir), "merge-base", "--is-ancestor", theirs, commit]).returncode == 0
            require(ahead, f"远端分支 {branch} 已被他人改动：未覆盖。请合并后再发布，或改用新的分支名", 409, "publish_conflict")
        if theirs != commit:
            # A plain refspec (no +, no --force): git itself also refuses anything but a fast-forward.
            try:
                _git(workdir, *sources.SAFE_GIT_ARGS, "push", "-q", url, f"{commit}:refs/heads/{branch}",
                     env=env, secret=auth["secret"])
            except Error as exc:
                if "error: 403" not in str(exc):
                    raise
                raise Error(409, "credential_permission", "凭证可以读取但不能推送（HTTP 403）：GitHub 细粒度令牌需对该仓库有 "
                            "Contents 读写权限，GitLab 令牌需 write_repository 范围") from None
    return {"created": theirs is None}


def _api(method, url, secret, kind, body=None):
    headers = ({"Authorization": "Bearer " + secret, "Accept": "application/vnd.github+json"} if kind == "github_pat"
               else {"PRIVATE-TOKEN": secret})
    headers.update({"User-Agent": "kirocrew-on", **({"Content-Type": "application/json"} if body is not None else {})})
    request = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with repo_credentials.OPENER.open(request, timeout=30) as response:   # never follows a redirect
            value = json.loads(response.read(1 << 20))
        if not isinstance(value, (dict, list)):
            raise ValueError("unexpected answer")
        return value
    except urllib.error.HTTPError as exc:
        with exc:
            detail = exc.read()[:500].decode(errors="replace")
        if exc.code == 401:
            raise Error(409, "credential_rejected", "托管方拒绝了凭证（HTTP 401）") from None
        if exc.code == 403:   # a valid token without this permission (or the host's rate limit)
            raise Error(409, "credential_permission", sources.scrub(
                f"凭证没有此操作的权限（HTTP 403：{detail[:200]}）：GitHub 细粒度令牌需对该仓库有 Pull requests 读写权限，"
                "GitLab 令牌需 api 范围", secret)) from None
        raise Error(409, "publish_failed", sources.scrub(f"托管方返回 HTTP {exc.code}：{detail}", secret)) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise Error(502, "publish_failed", "无法连接托管方的 API") from None


def pull_request(kind, host, path, branch, target, title, body, secret):
    """The open draft request for ``branch``, reused, or a new one into ``target`` (default: the default branch)."""
    base = repo_credentials.api_base(kind, host)
    if kind == "github_pat":
        target = target or _api("GET", f"{base}/repos/{path}", secret, kind)["default_branch"]
        owner = path.split("/")[0]
        open_ = _api("GET", f"{base}/repos/{path}/pulls?" + urllib.parse.urlencode({"head": f"{owner}:{branch}", "state": "open"}),
                     secret, kind)
        if open_:
            return {"url": open_[0]["html_url"], "number": open_[0]["number"], "kind": "pull_request", "reused": True}
        made = _api("POST", f"{base}/repos/{path}/pulls", secret, kind,
                    {"title": title, "head": branch, "base": target, "body": body, "draft": True})
        return {"url": made["html_url"], "number": made["number"], "kind": "pull_request", "reused": False}
    project = urllib.parse.quote(path, safe="")
    target = target or _api("GET", f"{base}/projects/{project}", secret, kind)["default_branch"]
    open_ = _api("GET", f"{base}/projects/{project}/merge_requests?"
                 + urllib.parse.urlencode({"source_branch": branch, "state": "opened"}), secret, kind)
    if open_:
        return {"url": open_[0]["web_url"], "number": open_[0]["iid"], "kind": "merge_request", "reused": True}
    made = _api("POST", f"{base}/projects/{project}/merge_requests", secret, kind,
                {"source_branch": branch, "target_branch": target, "title": "Draft: " + title, "description": body})
    return {"url": made["web_url"], "number": made["iid"], "kind": "merge_request", "reused": False}
