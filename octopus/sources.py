"""Prepare a pinned project checkout before native workflow dispatch.

Invoked by the KiroCrew coordinator through its existing Shell tool. No model,
Coding CLI, new Gateway endpoint, or credential-copying mechanism is introduced.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
import uuid


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


def _git(cwd, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
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


def prepare_project(run, *, source=None, ref=None, new=False):
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
                staging.mkdir()
                commit, branch = None, None
            else:
                if kind == "local-git":
                    source_path = Path(source)
                    _git(source_path, "rev-parse", "--show-toplevel")
                    if not ref and _git(source_path, "status", "--porcelain", "--untracked-files=normal"):
                        raise ValueError("本地来源有未提交改动；本操作检出 Git 版本，不会隐式丢弃或提交改动。"
                                         "请用 --ref 明确选择已提交基线，或先保存所需源码版本。")
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
            state.update(status="failed", error=str(exc), at=time.time())
            if staging.exists():
                state["retained_attempt"] = str(staging.relative_to(run))
            _write(run, state)
            raise ValueError("源码准备失败：" + str(exc)) from exc
