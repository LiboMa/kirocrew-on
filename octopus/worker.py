"""A worker colocated with ONE trusted execution boundary and its native Crew Gateway."""
import hashlib
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
import urllib.error
from urllib.parse import quote, urlencode, urlsplit
import zipfile

from . import __version__
from . import gateway_client as crew_api
from . import sources as workspace
from .db import dumps
from .domain import CONTRACT, steps
from .security import Error, require


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        stream.write(dumps(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


ARCHIVE_LIMIT = 200 * 1024 * 1024   # D36 §4.2: before compression
ARCHIVE_SKIP = workspace.SKIP_DIRS
ENV_KEPT = workspace.ENV_KEPT
PUBLISH_LIMIT = 100 * 1024 * 1024   # D35 §5.2: one bundle (as publish.LIMIT on the platform)


def briefing(requirement, contract):
    """The requirement KiroCrew completed and this task's contract, ahead of the task itself (D34 §6.2)."""
    text = ""
    if requirement and (requirement.get("intent") or requirement.get("summary")):
        text += "\n\n## 需求与验收标准（KiroCrew 规划补全）\n"
        if requirement.get("intent"):
            text += "意图：" + requirement["intent"] + "\n"
        text += requirement.get("summary", "")
    if contract:
        labels = (("deliverables", "交付物"), ("acceptance", "验收项"), ("verify", "验证命令"), ("paths", "可改动路径"))
        text += "\n\n## 本任务契约\n" + (f"类别：{contract['kind']}\n" if contract.get("kind") else "")
        for key, label in labels:
            if contract.get(key):
                text += label + "：\n" + "".join(f"- {item}\n" for item in contract[key])
        if contract.get("acceptance"):
            text += "逐项完成并核对上述验收项；审查与测试以它们为判定依据。\n"
    return text


# A cloned repository's config may carry commands git runs when the copy is opened (core.fsmonitor, core.pager,
# aliases, filters, credential helpers…): a download keeps only the format, remote and branch settings.
SAFE_GIT = re.compile(r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode"
                      r"|symlinks)|extensions\.[a-z0-9]+|remote\..+\.(url|fetch|pushurl)|branch\..+\.(remote|merge|rebase)")


def safe_git_config(path):
    try:
        listed = subprocess.run(["git", "config", "--file", str(path), "--list", "-z"], capture_output=True,
                                timeout=10, stdin=subprocess.DEVNULL)
        entries = listed.stdout.decode(errors="replace").split("\0") if listed.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        entries = []
    sections = {}
    for entry in filter(None, entries):
        key, newline, value = entry.partition("\n")
        if SAFE_GIT.fullmatch(key):
            section, _, rest = key.partition(".")
            subsection, _, name = rest.rpartition(".")
            sections.setdefault((section, subsection), []).append((name, value if newline else None))
    if not sections:
        sections[("core", "")] = [("repositoryformatversion", "0"), ("bare", "false")]
    quote = lambda text: text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    lines = []
    for (section, subsection), items in sections.items():
        lines.append(f'[{section} "{quote(subsection)}"]' if subsection else f"[{section}]")
        lines += [f"\t{name}" if value is None else f'\t{name} = "{quote(value)}"' for name, value in items]
    return "\n".join(lines) + "\n"


def pack_file(archive, relative, path):
    """Add one regular file, opened without following a link and without blocking on a FIFO swapped in since the
    plan; False when it can no longer be read (removed, unreadable, no longer a regular file)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    with os.fdopen(fd, "rb") as source:
        status = os.fstat(source.fileno())
        if not stat.S_ISREG(status.st_mode):
            return False
        info = zipfile.ZipInfo(relative, time.localtime(max(status.st_mtime, 315619200))[:6])   # zip dates start in 1980
        info.external_attr, info.compress_type = (status.st_mode & 0xFFFF) << 16, zipfile.ZIP_DEFLATED
        info.file_size = status.st_size
        with archive.open(info, "w") as target:
            shutil.copyfileobj(source, target, 1 << 20)
    return True


class Worker:
    def __init__(self, root, token, gateway_port=5476, crew_home=None, isolation="trusted-local", routing_log=None,
                 crew_url=None, approval_mode="", crew_entry_ttl="8h", kiro_cli=None, path_prepend=(),
                 preview_tls_port=0, preview_tls_name=""):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.token, self.port = token, gateway_port
        self.home = Path(crew_home or Path.home() / ".kiro/crew").resolve()
        self.isolation = isolation
        self.routing_log = Path(routing_log).resolve() if routing_log else None
        from .crew_entry import browser_origin
        self.crew_origin = browser_origin(crew_url or f"http://localhost:{gateway_port}")
        from .config import ENTRY_TTL
        require(isinstance(crew_entry_ttl, str) and ENTRY_TTL.fullmatch(crew_entry_ttl), "crew_entry_ttl 只能是 1–480m 或 1–8h")
        self.crew_entry_ttl = crew_entry_ttl
        self.lock = threading.RLock()
        require(len(token) >= 32, "Worker Token 至少 32 字符")
        require(isolation in {"dedicated", "trusted-local"}, "Worker 隔离模式无效")
        require(approval_mode in {"", "auto", "interactive"}, "Worker 批准模式无效")
        # "auto": this unattended boundary asks KiroCrew for native auto approval on platform spawns (D14).
        self.approval_mode = approval_mode
        self.kiro_cli, self._login = kiro_cli, (0.0, None)
        from .apps import Apps
        from .checks import Checks
        # Remote preview (D36 §5): on a Worker host the platform reaches the apps through this host's nginx preview
        # port (TLS, preview_tls_name in its certificate); without one, on the platform host's loopback.
        self.apps = Apps(path_prepend)
        self.checks = Checks(path_prepend)   # a task's verify commands (D37 §5)
        self.preview_tls_port, self.preview_tls_name = preview_tls_port, preview_tls_name

    @staticmethod
    def contract(spec, value):
        """The task contract sent beside the spec, validated like the task itself (D34 §6.2)."""
        if not value:
            return {}
        require(isinstance(value, dict) and set(value) <= set(CONTRACT), "任务契约字段无效")
        checked = steps([{**spec, **value}], known=value.get("depends_on") or [])[0]
        return {key: checked[key] for key in CONTRACT if key in checked}

    def tool_auth(self, tool="kiro"):
        """kiro-cli's login on this boundary (D34 §5.2): ok, expired, or unknown when it cannot be checked; cached for
        a minute so health checks and dispatches stay fast."""
        now = time.monotonic()
        if self._login[1] is not None and now - self._login[0] < 60:
            return self._login[1]
        binary = self.kiro_cli or shutil.which("kiro-cli") or shutil.which("kiro-cli", path=str(Path.home() / ".local/bin"))
        state = "unknown"
        if binary:
            try:
                done = subprocess.run([str(binary), "whoami"], capture_output=True, timeout=3, stdin=subprocess.DEVNULL)
                text = (done.stdout + done.stderr).decode(errors="replace")
                # Only a recognisable answer counts: anything else (another CLI version, a crash) must not hold every
                # Kiro dispatch, the KiroCrew planning task included.
                if done.returncode == 0 and text.lstrip().startswith("Logged in"):
                    state = "ok"
                elif done.returncode != 0 and re.search(r"not logged in|expired|kiro-cli login|log in", text, re.I):
                    state = "expired"
            except (OSError, subprocess.TimeoutExpired):
                state = "unknown"
        self._login = (now, state)
        return state

    def native(self, path, body=None, method=None):
        return crew_api.request(path, body, method, port=self.port, home=self.home)

    def path(self, rid):
        require(isinstance(rid, str) and re.fullmatch(r"run-[a-f0-9]{32}", rid), "Run ID 无效")
        path = self.root / rid
        require(not path.is_symlink(), "Workspace 路径不可为符号链接")
        return path

    OBSERVE_TEXT = 20_000

    def observe(self, body):
        """Read-only view of the run's own native Session for the console and MCP: no native login is handed out (D30)."""
        run, parent = self.binding(body["run_id"])
        slot = parent["slot"]
        require(body.get("native_parent") == slot and parent["parent"] == "dashboard:" + slot,
                "原生 Session 绑定不一致", 409, "entry_mismatch")
        lifecycle = run / "lifecycle.json"
        require(not lifecycle.exists() or json.loads(lifecycle.read_text()).get("status") != "deleted",
                "原生 Session 已删除", 409, "session_deleted")
        limit, before = body.get("limit", 50), body.get("before")
        require(type(limit) is int and 1 <= limit <= 100, "limit 须为 1–100 的整数")
        require(before is None or type(before) is int and before >= 0, "before 须为非负整数")
        query = urlencode({"limit": limit, **({"before": before} if before is not None else {})})
        try:
            native = self.native("/api/chat/slots/" + quote(slot, safe="") + "?" + query)
        except crew_api.GatewayError as exc:
            if exc.status == 404:
                raise Error(409, "native_unavailable", "原生 Session 当前不可用，请在工作台重新打开或检查 Gateway") from None
            raise
        require(native.get("key", native.get("slot")) == slot, "Gateway 返回的 Session 与绑定不一致", 409, "entry_mismatch")
        messages = []
        for message in native.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content") if isinstance(message.get("content"), str) else ""
            messages.append({"role": str(message.get("role") or ""), "content": content[:self.OBSERVE_TEXT],
                             "truncated": len(content) > self.OBSERVE_TEXT,
                             "ts": message["ts"] if isinstance(message.get("ts"), str) else None})
        number = lambda value: value if type(value) in (int, float) else None
        return {"native_parent": slot, "title": native["title"] if isinstance(native.get("title"), str) else None,
                "running": native.get("running") is True, "total": number(native.get("total")),
                "has_more": native.get("has_more") is True, "next_before": number(native.get("next_before")),
                "context_pct": number(native.get("context_pct")), "messages": messages}

    PREVIEW_ENTRIES = ("index.html", "dist/index.html", "build/index.html", "public/index.html", "web/index.html",
                       "out/index.html", "site/index.html")

    def files(self, body):
        """Read-only project files for the console preview: never outside the project, never hidden files (.env, .git)."""
        project = workspace.project_dir(self.path(body.get("run_id"))).resolve()
        relative = body.get("path") or ""
        require(isinstance(relative, str) and len(relative) <= 500 and "\x00" not in relative, "路径无效")
        if not relative:
            entry = next((e for e in self.PREVIEW_ENTRIES if (project / e).is_file()), None)
            entry = entry or next((p.name for p in sorted(project.glob("*.html"))), None)
            require(entry is not None, "未找到可预览的 index.html；需要开发服务器的项目请在本机执行 "
                    "deploy/aws/deploy.sh space " + body["run_id"], 404, "no_preview")
            return {"entry": entry}
        target = (project / relative).resolve()
        require(target.is_relative_to(project) and target.is_file(), "文件不存在", 404, "not_found")
        require(not any(part.startswith(".") for part in target.relative_to(project).parts), "文件不存在", 404, "not_found")
        size = target.stat().st_size
        require(size <= 5_000_000, "文件超过 5 MB，不在浏览器中预览", 413, "too_large")
        import base64
        return {"content": base64.b64encode(target.read_bytes()).decode(), "size": size}

    def archive_plan(self, run_id):
        """What a download of the run's project contains (D36 §4.2): every file and directory and .git, without
        dependency and cache directories, .env files, symlinks (never followed) or git hooks; .git config files are
        packed cleaned (safe_git_config). 413 above the size limit, naming the largest parts."""
        project = workspace.project_dir(self.path(run_id))
        files, folders, configs, links, skipped, excluded, sizes, total = [], [], [], [], [], set(), {}, 0
        for current, dirs, names in os.walk(project, followlinks=False):
            here = Path(current)
            base = here.relative_to(project)
            keep = []
            for name in sorted(dirs):
                relative = (base / name).as_posix()
                if (here / name).is_symlink():
                    links.append(relative)
                elif name in ARCHIVE_SKIP or (name == "cache" and here.name == ".next"):
                    excluded.add(".next/cache" if name == "cache" else name)
                else:
                    keep.append(name)
                    folders.append(relative)
            dirs[:] = keep
            inside_git = ".git" in base.parts
            for name in sorted(names):
                path, relative = here / name, (base / name).as_posix()
                try:
                    mode = path.lstat().st_mode
                except OSError:   # removed while walking
                    skipped.append(relative)
                    continue
                if stat.S_ISLNK(mode):
                    links.append(relative)
                elif (name == ".env" or name.startswith(".env.")) and name not in ENV_KEPT:
                    excluded.add(".env")
                elif inside_git and ("hooks" in base.parts[base.parts.index(".git"):] and not name.endswith(".sample")
                                     or name == "config.worktree"):
                    # Run by git on the machine the repository is opened on (D36 review).
                    excluded.add(".git/hooks" if name != "config.worktree" else ".git/config.worktree")
                elif not stat.S_ISREG(mode):   # FIFOs, sockets, devices
                    skipped.append(relative)
                else:
                    size = path.lstat().st_size
                    total += size
                    top = relative.split("/")[0]
                    sizes[top] = sizes.get(top, 0) + size
                    files.append((relative, path))
                    if inside_git and name == "config" and (here / "HEAD").is_file():
                        configs.append(relative)
        if total > ARCHIVE_LIMIT:
            largest = "、".join(f"{name} {size / 1048576:.0f} MB" for name, size in sorted(sizes.items(), key=lambda x: -x[1])[:3])
            raise Error(413, "archive_too_large", f"项目超过 {ARCHIVE_LIMIT // 1048576} MB（最大的部分：{largest}）："
                        "请在交付任务中清理产物，或改用远程预览")
        return {"files": files, "dirs": folders, "configs": configs, "bytes": total, "excluded": sorted(excluded),
                "links": sorted(links), "skipped": skipped}

    def verify_start(self, body):
        """Run a task's verify commands in its Session's project (D37 §5); the same Attempt gets the same job."""
        commands = body.get("commands")
        require(isinstance(commands, list) and 0 < len(commands) <= 10 and
                all(isinstance(c, str) and c.strip() and len(c) <= 500 for c in commands), "检查命令须为 1–10 条、每条 ≤ 500 字符")
        require(isinstance(body.get("attempt_id"), str) and body["attempt_id"], "需要 attempt_id")
        project = workspace.project_dir(self.path(body["run_id"]))
        return self.checks.start(body["run_id"], body["attempt_id"], project, commands)

    def verify_status(self, body):
        return self.checks.status(str(body.get("job", "")))

    def candidate(self, body):
        """A task's candidate commit (D37 §3, ``mode: commit``), or a review's check that the workspace is still the
        candidate it reviewed (§6, ``mode: check``)."""
        run = self.path(body["run_id"])
        project, record = workspace.project_dir(run), workspace.read_record(run)
        base, branch = record.get("commit"), record.get("branch")
        require(base and branch, "此会话的项目没有 Git 基线，无法生成候选", 409, "candidate_unsupported")
        mode = body.get("mode")
        require(mode in {"commit", "check"}, "mode 只能是 commit 或 check")
        for key in ("parent", "expected"):
            require(body.get(key) is None or (isinstance(body[key], str) and workspace.SHA.fullmatch(body[key])),
                    f"{key} 须为完整的提交 ID")
        try:
            if mode == "check":
                require(body.get("expected"), "check 需要 expected")
                return workspace.clean_against(project, body["expected"])
            message = str(body.get("message") or "KiroCrew ON：任务候选")[:300]
            return workspace.task_candidate(project, message, branch, run, body.get("parent"), base)
        except ValueError as exc:
            raise Error(409, "candidate_failed", workspace.scrub(exc)) from None

    def publish_plan(self, body):
        """What a publish exports (D35 §5.2): the Session's branch, with the workspace's pending changes committed (without
        what a download leaves out). The bundle carries every commit, so everything new since the clone's baseline is
        checked first, and the branch moves only when nothing in it looks like a credential or a .env file."""
        run = self.path(body["run_id"])
        project, record = workspace.project_dir(run), workspace.read_record(run)
        base, branch = record.get("commit"), record.get("branch")
        require(base and branch, "此会话的项目没有 Git 基线，无法发布", 409, "nothing_to_publish")
        message = str(body.get("message") or "KiroCrew ON：发布会话成果")[:300]
        commit = body.get("commit")
        if commit is not None:   # D37 §7: the verified candidate, exactly, or nothing
            require(isinstance(commit, str) and workspace.SHA.fullmatch(commit), "commit 须为完整的提交 ID")
            try:
                state = workspace.clean_against(project, commit)
                found = workspace.unpublishable(project, base, commit) if state["clean"] else []
            except ValueError as exc:
                raise Error(409, "publish_failed", workspace.scrub(exc)) from None
            require(state["clean"], "这些改动尚未经过门禁与审查，未发布：" + (
                "、".join(state["changed_files"][:20]) if state["changed_files"] else f"会话分支已不在已验证的候选 {commit[:12]}")
                + "。请续作会话，让改动经过门禁与审查后再发布", 409, "publish_unverified")
            require(not found, "未发布：已提交的历史中有疑似凭证或 .env 文件：" + "、".join(found[:20]), 409, "publish_secret_found")
            return {"project": project, "base": base, "head": commit, "branch": branch, "run": run}
        try:
            old = workspace._git(project, "rev-parse", "--verify", "HEAD^{commit}")
            head = workspace.publish_candidate(project, message, branch, run)
            require(head != base, "没有可发布的改动：工作区与克隆时相同", 409, "nothing_to_publish")
            committed = workspace.unpublishable(project, base, old) if old != base else []
            pending = workspace.unpublishable(project, old, head) if head != old else []
            if committed or pending:
                raise Error(409, "publish_secret_found", "未发布：" + "；".join(
                    ([f"工作区的这些文件中有疑似凭证（移除后再发布）：{'、'.join(pending[:20])}"] if pending else [])
                    + ([f"已提交的历史中有疑似凭证或 .env 文件：{'、'.join(committed[:20])}（需改写这些提交后再发布，例如让 Agent "
                        f"执行 git reset --soft {base[:12]} 后去掉它们重新提交）"] if committed else [])))
            workspace.advance(project, branch, head, old)
        except ValueError as exc:
            raise Error(409, "publish_failed", workspace.scrub(exc)) from None
        return {"project": project, "base": base, "head": head, "branch": branch, "run": run}

    def publish_bundle(self, plan):
        """The bundle file of ``plan``: the branch with its whole history — the platform holds no copy of the repository
        to satisfy a baseline prerequisite; it checks the baseline is an ancestor, and the push sends only what is new —
        and exactly the commit that was checked. The caller streams and removes it."""
        bundle = plan["run"] / ".publish.bundle"
        bundle.unlink(missing_ok=True)
        try:
            ref = "refs/heads/" + plan["branch"]
            workspace._git(plan["project"], "bundle", "create", "-q", str(bundle), ref)
            require(workspace._git(plan["project"], "bundle", "list-heads", str(bundle)).split() == [plan["head"], ref],
                    "发布期间工作区的分支被改动，未发布，请重试", 409, "publish_failed")
            require(bundle.stat().st_size <= PUBLISH_LIMIT, f"待发布的内容超过 {PUBLISH_LIMIT // 1048576} MB", 413,
                    "publish_too_large")
        except ValueError as exc:
            bundle.unlink(missing_ok=True)
            raise Error(409, "publish_failed", workspace.scrub(exc)) from None
        except BaseException:
            bundle.unlink(missing_ok=True)
            raise
        return bundle

    def archive_summary(self, body):
        """A dry run before a download link is issued: size and refusals reach the console as an answer, not as a
        page the browser navigates to."""
        plan = self.archive_plan(body["run_id"])
        return {"bytes": plan["bytes"], "files": len(plan["files"]), "excluded": plan["excluded"], "links": plan["links"]}

    def archive_readme(self, run_id, meta, plan):
        """OCTOPUS-ARCHIVE.txt, built before the response starts: nothing in it may fail a download."""
        from . import preview_spec
        project = workspace.project_dir(self.path(run_id))
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True,
                                  timeout=10, stdin=subprocess.DEVNULL).stdout.strip() or "无"
        except (OSError, subprocess.TimeoutExpired):
            head = "无"
        try:
            # The steps are for the downloaded copy, which has none of the dependency directories left behind.
            spec, source, problem = preview_spec.resolve(project, packaged=True)
            steps = preview_spec.local_steps(spec) if spec else [problem]
        except Exception:   # a declaration nothing anticipated (a cwd looping through links…)
            source, steps = None, [f"启动方式无法识别：请检查 {preview_spec.DECLARATION}"]
        text = lambda key: str((meta or {}).get(key) or "")[:200]
        return "\n".join([
            "KiroCrew ON 会话源码", "", f"会话：{text('name')}（{run_id}）", f"创建者：{text('creator')}",
            f"打包时间：{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}", f"HEAD：{head}", "",
            "未打包：" + ("、".join(plan["excluded"]) or "无") + "（依赖与缓存目录请在本机重新安装；.env 不随包下载）",
            "符号链接（未打包）：" + ("、".join(plan["links"]) or "无"),
            *(["已移除：.git/config 中会执行命令的设置与 .git/hooks（打开下载的仓库不会运行会话中的命令）"]
              if plan["configs"] else []), "",
            "本机启动" + {"declared": "（来自 .octopus/preview.json）", "detected": "（自动识别）"}.get(source, "") + "：",
            *[f"  {step}" for step in steps], ""])

    def write_archive(self, stream, run_id, meta, plan=None, readme=None):
        """Write the zip to ``stream`` as it is built (no temporary file), with OCTOPUS-ARCHIVE.txt last. A file
        that can no longer be read is listed there; any other failure leaves the zip without its end record, so the
        platform never completes or records a partial download."""
        plan = plan or self.archive_plan(run_id)
        readme = readme or self.archive_readme(run_id, meta, plan)
        archive, skipped = zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED), list(plan["skipped"])
        try:
            for relative in plan["dirs"]:   # empty ones included: git needs .git/refs even when it is empty
                info = zipfile.ZipInfo(relative + "/")
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, b"")
            for relative, path in plan["files"]:
                if relative in plan["configs"]:
                    info = zipfile.ZipInfo(relative, time.localtime()[:6])
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, safe_git_config(path), zipfile.ZIP_DEFLATED)
                elif not pack_file(archive, relative, path):
                    skipped.append(relative)
            if skipped:
                readme += "未能读取（未打包）：" + "、".join(skipped) + "\n"
            archive.writestr("OCTOPUS-ARCHIVE.txt", readme)
            archive.close()
        except BaseException:
            archive.fp = None   # never write the central directory of a partial archive
            raise

    def app(self, body):
        """Start, stop or report the run's application for the remote preview (D36 §5.1)."""
        action, rid = body.get("action"), body["run_id"]
        require(action in {"start", "stop", "status"}, "操作无效")
        run = self.path(rid)
        if action == "stop":
            return self.apps.stop(rid)
        if action == "start":
            try:
                project = workspace.project_dir(run)
            except ValueError as exc:
                raise Error(409, "app_unavailable", f"此会话的源码不可用：{exc}") from None
            result = self.apps.start(rid, project, run)
        else:
            result = self.apps.status(rid, run)
        result["via"] = {"port": self.preview_tls_port, "name": self.preview_tls_name} if self.preview_tls_port else None
        return result

    def health(self):
        # Descriptive boundary, not cryptographic proof of OS isolation.
        boundary = f"{socket.gethostname()}:{os.getuid()}:{self.home}"
        cache = self.home / "provider_models.json"
        models = json.loads(cache.read_text()) if cache.is_file() else {}
        namespace = {"kiro": "acp", "claude": "claude_code", "codex": "codex", "opencode": "opencode"}
        advertised = {tool: [m for m in models.get(ns, []) if isinstance(m, str)] for tool, ns in namespace.items()}
        return {"version": __version__, "isolation": self.isolation, "workflow_design": True,
                "spawn_approval": "auto" if self.approval_mode == "auto" else "interactive",
                "crew_entry": True, "crew_origin": self.crew_origin, "observe": True, "app_preview": True,
                "tool_auth": {"kiro": self.tool_auth()},
                "coordinator_recovery": True, "quality_outcome": True,
                "boundary": hashlib.sha256(boundary.encode()).hexdigest(),
                "host_principal": hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest(),
                "routing_evidence": bool(self.routing_log and self.routing_log.is_file()),
                "model_source": "KiroCrew provider_models.json; ACP session/new advertised cache",
                "advertised_models": advertised,
                "models": {tool: list(dict.fromkeys(["auto"] + [m for m in ids if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", m)]))
                           for tool, ids in advertised.items()},
                "limitations": ["当前原生 spawn 模型格式不支持 /、:、[]；这些已公布的 ID 不列为可直接派发模型。",
                                "OpenCode Effort 覆盖未验证。实际模型与实际 Effort 仅按原生证据报告。"]}

    def prepare(self, body):
        rid = body["run_id"]
        run = self.path(rid)
        with self.lock:
            run.mkdir(exist_ok=True)
            # Compatibility projection for the existing, tested workspace preparation module.
            if not (run / "workflow-run.json").exists():
                write(run / "workflow-run.json", {"id": rid})
            source = body["source"]
            require(source.get("type") in {"new", "git"}, "源码类型无效")
            if source["type"] == "git":
                # Multi-user input cannot ask the worker to copy arbitrary host directories.
                url = str(source.get("url", ""))
                require(url.startswith(("https://", "ssh://")) or re.fullmatch(r"[\w.-]+@[\w.-]+:[^\s]+", url),
                        "API 仅接受远程 Git 来源；不接受任意宿主机路径")
            auth = body.get("git_auth")
            if auth is not None:
                # Only for this clone, and only for the repository's own host (D35 §5.1).
                require(isinstance(auth, dict) and set(auth) == {"kind", "host", "username", "secret"}
                        and all(isinstance(v, str) and v for v in auth.values()), "git_auth 无效")
                require(source["type"] == "git" and urlsplit(str(source.get("url", ""))).netloc.lower() == auth["host"],
                        "凭证的主机与仓库地址不符")
            try:
                workspace.prepare_project(run, source=source.get("url"), ref=source.get("ref"),
                                          new=source["type"] == "new", auth=auth)
            except ValueError as exc:   # the reason as sources cleaned it, instead of 「Worker 操作未完成」
                raise Error(409, "source_failed", str(exc)) from None
            slot = "octopus-" + rid
            parent_file = run / "parent.json"
            if not parent_file.exists():
                try:
                    present = self.native("/api/chat/slots/" + slot)
                except crew_api.GatewayError as exc:
                    if exc.status != 404:
                        raise
                    present = self.native("/api/chat/slots", {
                        "name": slot, "agent": "poc-coordinator", "agent_kind": "template", "model": "auto"})
                    require(present.get("key", present.get("slot")) == slot, "原生父 Session 身份不一致")
                self.native("/api/chat/slots/" + slot + "/context", {
                    "content": "你是此 Octopus 研发运行的用户交互协调者，负责解释计划、进度、错误与修复决定。"
                               "平台 Engine 统一派发已采纳的任务；失败诊断通过原生 poc-diagnose 子任务执行（不能再派生任务）。"
                               "保持同一 Session 与工作区，使用平台给出的真实交接和纠错记录。"
                               "不要额外自行派发重复子任务、执行旧 next/accept 或修改平台数据库。"
                               "需要用户补充项目来源或权限时具体说明；不要把未知原因说成已解决。",
                    "source": "octopus-mvp-1.0.0", "ephemeral": True})
                write(parent_file, {"slot": slot, "parent": "dashboard:" + slot})
            if not (run / "parent-note.json").exists():
                result = self.native("/api/chat/slots/" + slot + "/note", {
                    "content": f"Octopus MVP 1.0.0 已建立研发运行 {rid}。源码工作区：{workspace.project_dir(run)}。"
                               "任务由平台 API 串行调度，进度与交接会回写此对话。",
                    "source": "octopus-run", "ephemeral": True})
                require(result.get("ok"), "原生会话记录未确认", 409, "native_unavailable")
                write(run / "parent-note.json", {"recorded": True})
            return {"native_parent": slot, "workspace": str(workspace.project_dir(run)),
                    "project": workspace.read_record(run)}

    def binding(self, rid):
        run = self.path(rid)
        require((run / "parent.json").is_file(), "Worker 尚未登记此 Session", 409, "not_prepared")
        return run, json.loads((run / "parent.json").read_text())

    def crew_entry(self, body):
        run, parent = self.binding(body["run_id"])
        slot = parent["slot"]
        require(body.get("native_parent") == slot and parent["parent"] == "dashboard:" + slot,
                "原生 Session 绑定不一致", 409, "entry_mismatch")
        lifecycle = run / "lifecycle.json"
        require(not lifecycle.exists() or json.loads(lifecycle.read_text()).get("status") not in {"closed", "deleted"},
                "原生 Session 已关闭，请先重新打开", 409, "session_closed")
        try:
            native = self.native("/api/chat/slots/" + quote(slot, safe=""))
        except crew_api.GatewayError as exc:
            if exc.status == 404:
                raise Error(409, "native_unavailable", "原生 Session 当前不可用，请在工作台重新打开或检查 Gateway") from None
            raise
        require(native.get("key", native.get("slot")) == slot,
                "Gateway 返回的 Session 与绑定不一致", 409, "entry_mismatch")
        return {"native_parent": slot,
                "url": crew_api.chat_entry_url(slot, self.port, home=self.home, origin=self.crew_origin,
                                               ttl=self.crew_entry_ttl)}

    def dispatch(self, body):
        run, parent = self.binding(body["run_id"])
        attempt = body["attempt_id"]
        require(isinstance(attempt, str) and re.fullmatch(r"attempt-[a-f0-9]{32}", attempt), "Attempt ID 无效")
        spec = steps([body["spec"]])[0]
        explicit = spec["model"] not in {"auto", "default"}
        if explicit:
            require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", spec["model"]),
                    "当前 KiroCrew spawn 接口不接受此模型 ID 格式（/、:、[]）；不能保证按此配置派发",
                    409, "model_transport_unsupported")
            require(spec["model"] in self.health()["models"][spec["tool"]],
                    "所选模型未由此 Worker 后端公布", 409, "model_unavailable")
        folder = run / "attempts" / attempt
        # Checked before the lock: a slow kiro-cli must not hold every other request of this Worker.
        login = self.tool_auth() if spec["tool"] == "kiro" or body.get("coordination") or body.get("design") else "ok"
        with self.lock:
            record = folder / "dispatch.json"
            if record.exists():
                value = json.loads(record.read_text())
                require(value["request_hash"] == hashlib.sha256(dumps(body).encode()).hexdigest(),
                        "Attempt ID 对应不同请求", 409, "conflict")
                if value.get("native_id") or value.get("status") == "blocked":
                    return value
                return self.reconcile(run, parent, attempt, value)
            if login == "expired":
                # Refused before anything is recorded or spawned: the platform dispatches this Attempt again later.
                raise Error(503, "tool_auth_expired", "Worker 上 kiro-cli 登录已过期：请管理员执行 deploy.sh kiro-login")
            folder.mkdir(parents=True, exist_ok=True)
            handoff = folder / "handoff.md"
            context = {"input": body["input"], "task": spec, "upstream": body.get("upstream", []),
                       "project": workspace.read_record(run)}
            contract, requirement = self.contract(spec, body.get("contract")), body.get("requirement")
            require(requirement is None or (isinstance(requirement, dict) and
                    all(isinstance(requirement.get(k, ""), str) for k in ("intent", "summary"))), "需求补全格式无效")
            if contract:
                context["contract"] = contract
            if requirement:
                context["requirement"] = requirement
            design_prompt = ""
            extra_prompt = ""
            if body.get("coordination"):
                from .recovery import coordinator_instructions
                require(spec["tool"] == "kiro", "协调者须使用 Kiro 后端")
                context["coordination"] = body["coordination"]
                extra_prompt += coordinator_instructions(body["coordination"], folder)
            elif body.get("quality"):
                from .recovery import quality_instructions
                context["quality_contract"] = 2
                extra_prompt += quality_instructions(folder, contract)
            if body.get("candidate"):   # a review bound to the candidate it reviews (D37 §6)
                target = body["candidate"]
                require(isinstance(target, dict) and all(isinstance(target.get(k), str) and workspace.SHA.fullmatch(target[k])
                                                         for k in ("sha", "parent")), "审查对象须为完整的提交 ID")
                context["candidate"] = {"sha": target["sha"], "parent": target["parent"]}
                extra_prompt += (f"\n\n## 审查对象\n审查提交 {target['sha']}（本轮改动：git diff {target['parent']} "
                                 f"{target['sha']}）。审查不修改代码，修改交给实现任务；在 outcome.json 中写 "
                                 f"\"reviewed_commit\": \"{target['sha']}\"。")
            if body.get("repair"):
                context["repair"] = body["repair"]
                extra_prompt += ("\n\n## 本轮协调者纠错与复审要求\n" + dumps(body["repair"]) +
                                 "\n完整阅读失败证据，优先执行本轮具体修复要求；再次运行相关验证，不能沿用旧通过结论。")
            if body.get("design"):
                from .designs import instructions
                require(spec["tool"] == "kiro", "工作流设计须使用 Kiro 后端")
                context["design"] = body["design"]
                design_prompt = instructions(body["input"], body["design"], workspace.project_dir(run),
                                             self.health()["models"])
            instructions = (
                "# 本次研发任务\n\n" + body["input"] + briefing(requirement, contract) + "\n\n## 任务\n" + spec["prompt"] +
                "\n\n## 前序交接\n" + dumps(body.get("upstream", [])) +
                f"\n\n源码目录：{workspace.project_dir(run)}\n交接文件：{handoff}\n"
                "依据需求进行开发和真实验证，按项目既有依赖与规范工作。记录具体修改、测试命令、结果和限制。"
                "若不能完成，最终回答单独一行写 BLOCKED 并说明恢复条件。"
                "只执行当前任务，不派生其他子任务。不读取平台或其他项目的凭据。"
                "不要编造测试结果或实际模型。完成后写交接文件。" + extra_prompt +
                ("\n\n" + design_prompt if design_prompt else ""))
            (folder / "instructions.md").write_text(instructions)
            write(folder / "context.json", context)
            context_hash = hashlib.sha256(dumps(context).encode()).hexdigest()
            marker = "[OCTOPUS-ATTEMPT:" + attempt + "]"
            prompt = f"{marker}\n完整阅读 {folder / 'instructions.md'}，在指定源码目录完成本次任务。"
            value = {"request_hash": hashlib.sha256(dumps(body).encode()).hexdigest(),
                     "status": "dispatching", "attempt_id": attempt, "context_hash": context_hash}
            write(record, value)  # Persist before the native side effect.
            try:
                spawn = {
                    "task": prompt, "parent_session": parent["parent"],
                    "agent": "poc-diagnose" if body.get("coordination") else "poc-" + spec["tool"],
                    "model": spec["model"], "reasoning_effort": spec["effort"],
                    "cwd": str(workspace.project_dir(run)), "keep": True, "include_memory": False,
                    "include_lessons": False, "include_project": False,
                    "solo_reason": "user_requested",
                    "solo_details": "用户通过 Octopus API 选择此任务、工具与执行配置。",
                    "silent": True}
                if self.approval_mode == "auto":
                    # Owner-authenticated request for native per-spawn auto approval (D14, K19).
                    spawn["approval_mode"] = "auto"
                result = self.native("/api/spawn", spawn)
                require(re.fullmatch(r"[a-f0-9]{8,64}", str(result.get("id", ""))), "原生 Task ID 无效")
                value.update(status="running", native_id=result["id"], receipt=result)
            except crew_api.GatewayError as exc:
                try:
                    failure = json.loads(str(exc).partition(": ")[2])
                except ValueError:
                    failure = {}
                # Current api_spawn contract marks every post-submission error
                # counted:true. Only a recognized JSON client refusal is definitive.
                refused = exc.status in {400, 401, 403, 404, 409, 422} and "error" in failure and not failure.get("counted")
                value.update(status="blocked" if refused else "dispatch_unknown",
                             error=("原生拒绝：" + str(failure["error"])[:1000]) if refused
                             else f"Native HTTP {exc.status}; inspect before retry")
            except Exception:
                value.update(status="dispatch_unknown", error="原生派发回执未确认")
            write(record, value)
            return value

    def reconcile(self, run, parent, attempt, value):
        tasks = self.native("/api/spawn").get("agents", [])
        matches = [t for t in tasks if t.get("parent") == parent["parent"]
                   and "[OCTOPUS-ATTEMPT:" + attempt + "]" in t.get("task", "")]
        if len(matches) == 1:
            value.update(status="running", native_id=matches[0]["id"], reconciled=True)
            write(run / "attempts" / attempt / "dispatch.json", value)
        else:
            value.update(status="dispatch_unknown", error="尚不能唯一确认原生派发；没有重发")
        return value

    def inspect(self, body):
        run, parent = self.binding(body["run_id"])
        attempt = body["attempt_id"]
        require(re.fullmatch(r"attempt-[a-f0-9]{32}", str(attempt)), "Attempt ID 无效")
        folder = run / "attempts" / attempt
        require((folder / "dispatch.json").is_file(), "Attempt 未派发", 404, "not_found")
        dispatch = json.loads((folder / "dispatch.json").read_text())
        if not dispatch.get("native_id"):
            return self.reconcile(run, parent, attempt, dispatch)
        rows = self.native("/api/spawn").get("agents", [])
        native = next((t for t in rows if t.get("id") == dispatch["native_id"] and t.get("parent") == parent["parent"]), None)
        statefile = self.home / "subagents" / dispatch["native_id"] / "state.json"
        state = {}
        if statefile.is_file() and not statefile.is_symlink():
            saved = json.loads(statefile.read_text())
            if saved.get("id") == dispatch["native_id"] and saved.get("parent_session") == parent["parent"]:
                state = saved
        require(native is not None or state, "原生任务归属当前不可核实", 409, "native_unavailable")
        if native is None or native.get("done"):
            # Native GET loads persisted task results after a Gateway restart.
            # Disk state proves ownership, but does not itself prove completion.
            native = {**(native or {}), **self.native("/api/spawn/" + dispatch["native_id"])}
        require("done" in native, "原生执行状态暂不可核实", 409, "native_unavailable")
        evidence = {"native_task_id": dispatch["native_id"], "parent": parent["parent"],
                    "actual_backend": None, "actual_model": None, "actual_effort": None}
        if state:
            evidence["native_session_id"] = state.get("session_id")
            evidence["actual_model"] = state.get("resolved_model")
            effort = re.search(r"\[(low|medium|high|xhigh|max)\]$", state.get("resolved_model") or "")
            if effort:
                evidence["actual_effort"] = effort.group(1)
        if self.routing_log and self.routing_log.is_file():
            for line in reversed(self.routing_log.read_text().splitlines()):
                try:
                    route = json.loads(line)
                except ValueError:
                    continue
                if route.get("session_key") == "subagent:" + dispatch["native_id"]:
                    evidence.update(actual_backend=route.get("actual_backend"), routing=route)
                    break
        # Only use native returned model observations, never the requested config.
        evidence["actual_model"] = evidence.get("actual_model") or native.get("resolved_model")
        handoff = folder / "handoff.md"
        handoff_text = ""
        if handoff.is_file() and not handoff.is_symlink() and handoff.stat().st_size <= 2_000_000:
            handoff_text = handoff.read_text()
            evidence["handoff_sha256"] = hashlib.sha256(handoff_text.encode()).hexdigest()
            evidence["handoff_path"] = str(handoff)
        if native.get("done") and not (folder / "result-note.json").exists():
            # Native busy-turn notes are limited to MAX_DEFERRED_NOTE_CHARS=4000.
            # Keep the complete native response below and in SQL; only the
            # declarative parent-chat projection is shortened.
            header = (f"Octopus 执行 Attempt {attempt} 已返回。原生 Task：{dispatch['native_id']}。"
                      f"实际后端：{evidence.get('actual_backend') or '未核实'}。交接验证由平台进行。\n\n")
            raw = str(native.get("result", ""))
            footer = "\n\n（父对话仅显示摘要；完整结果保留在原生子任务和 Octopus 执行记录中。）"
            note = header + raw
            if len(note) > 3800:
                note = header + raw[:max(0, 3800 - len(header) - len(footer))] + footer
            receipt = self.native("/api/chat/slots/" + parent["slot"] + "/note", {
                "content": note,
                "source": "octopus-result", "ephemeral": True})
            require(receipt.get("ok"), "原生结果记录未确认", 409, "native_unavailable")
            write(folder / "result-note.json", {"recorded": True})
        reports = {}
        for key in ("outcome", "decision"):
            path = folder / (key + ".json")
            reports[key] = path.read_text() if path.is_file() and not path.is_symlink() and path.stat().st_size <= 64000 else ""
        return {**dispatch, "native": native, "evidence": evidence, "handoff": handoff_text, **reports}

    def control(self, body):
        with self.lock:
            return self._control(body)

    def _control(self, body):
        action = body["action"]
        require(action in {"stop", "close", "delete", "reopen"}, "操作无效")
        if action != "reopen":
            self.checks.cancel(body["run_id"])   # its verify commands end with it (D37 §5)
        run = self.path(body["run_id"])
        if not (run / "parent.json").exists():
            slot = "octopus-" + body["run_id"]
            try:
                self.native("/api/chat/slots/" + slot)
            except crew_api.GatewayError as exc:
                if exc.status != 404:
                    raise
                return {"status": "stopped", "lifecycle":
                        {"close": "closed", "delete": "deleted"}.get(body["action"], "open")}
            record = run / "workflow-run.json"
            require(record.is_file() and json.loads(record.read_text()).get("id") == body["run_id"],
                    "此原生父 Session 的 Worker 归属未确认", 409, "native_unavailable")
            write(run / "parent.json", {"slot": slot, "parent": "dashboard:" + slot})
        run, parent = self.binding(body["run_id"])
        lifecycle = run / "lifecycle.json"
        current = json.loads(lifecycle.read_text()) if lifecycle.exists() else {"status": "open"}
        if action == "reopen":
            if current["status"] == "open":
                return {"status": "stopped", "lifecycle": "open"}
            require(current["status"] == "closed", "Session 未关闭", 409, "invalid_state")
            receipt = self.native("/api/chat/slots/" + parent["slot"] + "/resume", {"key": parent["parent"]})
            require(receipt.get("ok") and receipt.get("key") == parent["slot"],
                    "原生重新打开未确认", 409, "native_unavailable")
            write(lifecycle, {"status": "open"})
            return {"status": "stopped", "lifecycle": "open"}
        if current["status"] == "deleted":
            return {"status": "stopped", "lifecycle": "deleted"}
        if current["status"] == "closed":
            try:
                self.native("/api/chat/slots/" + parent["slot"])
                current["status"] = "open"  # Reopened through native App since our last observation.
            except crew_api.GatewayError as exc:
                if exc.status != 404:
                    raise
        if current["status"] != "closed":
            self.native("/api/chat/slots/" + parent["slot"] + "/stop?force=true", {})
            self.native("/api/spawn/stop-all", {"slot": parent["slot"]})
            own = [t for t in self.native("/api/spawn").get("agents", []) if t.get("parent") == parent["parent"]]
            slot = self.native("/api/chat/slots/" + parent["slot"])
            if slot.get("running") or slot.get("queue") or slot.get("queue_depth") or any(not t.get("done") for t in own):
                return {"status": "stopping", "lifecycle": "open"}
            if action in {"close", "delete"}:
                current["backup_kind"] = self.backup(run, parent, slot)
                receipt = self.native("/api/chat/slots/" + parent["slot"], method="DELETE")
                require(receipt.get("ok"), "原生关闭未确认", 409, "native_unavailable")
                current["status"] = "closed"
                write(lifecycle, current)
        if action == "delete":
            require((run / "native-session-backup.gz").is_file() or
                    current.get("backup_kind") == "native_empty_observation" and
                    (run / "empty-session-observation.json").is_file(), "缺少原生备份")
            receipt = self.native("/api/sessions/" + quote(parent["parent"], safe=""), method="DELETE")
            require(receipt.get("ok"), "原生删除未确认", 409, "native_unavailable")
            current["status"] = "deleted"
            write(lifecycle, current)
        return {"status": "stopped", "lifecycle": current["status"], "backup_kind": current.get("backup_kind")}

    def backup(self, run, parent, slot):
        import urllib.error
        import urllib.request
        path = "/api/chat/slots/" + parent["slot"] + "/export"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}" + path,
                                     headers={"Cookie": f"mc_token_{self.port}={crew_api.token(self.port, self.home)}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read(32 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            with exc:
                failure = json.loads(exc.read(4096))
            if exc.code != 400 or failure.get("code") != "export_bundle_empty":
                raise Error(409, "backup_unavailable", "原生备份未成功；会话尚未关闭") from None
            # Native notes alone are deliberately excluded from its export bundle.
            # Preserve an explicitly labelled observation; never forge a kcsession bundle.
            write(run / "empty-session-observation.json",
                  {"kind": "native_empty_observation", "native_export_code": "export_bundle_empty",
                   "slot": parent["slot"], "observation": slot, "at": time.time()})
            return "native_empty_observation"
        require(len(content) <= 32 * 1024 * 1024, "原生备份过大")
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as bundle:
            decoded = bundle.read(64 * 1024 * 1024 + 1)
        require(len(decoded) <= 64 * 1024 * 1024 and isinstance(json.loads(decoded), dict),
                "原生会话导出不是有效的 gzip JSON 备份")
        path = run / "native-session-backup.gz"
        with path.open("wb") as backup:
            os.chmod(path, 0o600)
            backup.write(content)
            backup.flush()
            os.fsync(backup.fileno())
        descriptor = os.open(run, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return "native_bundle"


def serve(worker, port=8928, host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_call(self):
            try:
                require(hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + worker.token),
                        "Worker authentication required", 401, "unauthorized")
                if self.command == "POST" and self.path == "/archive":
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 < size <= 100_000, "请求体无效")
                    body = json.loads(self.rfile.read(size))
                    plan = worker.archive_plan(body.get("run_id"))   # refusals still answer as JSON errors
                    readme = worker.archive_readme(body["run_id"], body.get("meta"), plan)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    try:   # streamed as it is built; a failure from here on can only end the connection
                        worker.write_archive(self.wfile, body["run_id"], body.get("meta"), plan, readme)
                    except Exception:   # the zip has no end record: the platform sees an incomplete download
                        pass
                    return
                if self.command == "POST" and self.path == "/publish/bundle":
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 < size <= 100_000, "请求体无效")
                    plan = worker.publish_plan(json.loads(self.rfile.read(size)))   # refusals answer as JSON errors
                    bundle = worker.publish_bundle(plan)
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-git-bundle")
                        self.send_header("Content-Length", str(bundle.stat().st_size))
                        self.send_header("X-Publish-Commit", plan["head"])
                        self.send_header("X-Publish-Base", plan["base"])
                        self.send_header("X-Publish-Branch", plan["branch"])
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        with open(bundle, "rb") as source:
                            shutil.copyfileobj(source, self.wfile, 1 << 20)
                    except Exception:   # after the 200 header only the connection can end: the platform sees a short bundle
                        pass
                    finally:
                        bundle.unlink(missing_ok=True)
                    return
                if self.command == "GET" and self.path == "/health":
                    result = worker.health()
                else:
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 < size <= 2_000_000, "请求体无效")
                    body = json.loads(self.rfile.read(size))
                    methods = {"/prepare": worker.prepare, "/dispatch": worker.dispatch,
                               "/inspect": worker.inspect, "/control": worker.control,
                               "/crew-entry": worker.crew_entry, "/files": worker.files, "/observe": worker.observe,
                               "/archive/plan": worker.archive_summary, "/app": worker.app,
                               "/candidate": worker.candidate, "/verify/start": worker.verify_start,
                               "/verify/status": worker.verify_status}
                    require(self.path in methods and self.command == "POST", "接口不存在", 404, "not_found")
                    result = methods[self.path](body)
                status = 200
            except (Error, crew_api.GatewayError, ValueError, OSError, KeyError, TypeError) as exc:
                status = getattr(exc, "status", 400)
                message = str(exc) if isinstance(exc, Error) else f"Worker 操作未完成 ({type(exc).__name__})"
                result = {"error": message, "code": getattr(exc, "code", "worker_error")}
                if isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError)) \
                        and not isinstance(exc, urllib.error.HTTPError):
                    # The Gateway is restarting or not yet up: temporary, so the platform waits and retries.
                    status = 503
                    result = {"error": "KiroCrew Gateway 暂不可用，稍后自动重试", "code": "native_unavailable"}
            data = dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = handle_call
    return ThreadingHTTPServer((host, port), Handler)
