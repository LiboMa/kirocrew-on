"""A worker colocated with ONE trusted execution boundary and its native Crew Gateway."""
from datetime import datetime
import hashlib
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
from urllib.parse import quote, urlencode, urlsplit
import zipfile

from . import __version__
from . import gateway_client as crew_api
from . import skills as pack
from . import sources as workspace
from .db import dumps
from .domain import CONTRACT, runs_checks, steps
from .security import Error, require


# D51: KiroCrew learns a backend's models only from a real session (model_registry.seed_available_models), so the Worker
# opens one per tool that has none yet, with no one asking (owner 2026-10-20); codex never publishes its models.
DISCOVERY_PROMPT = "这是 KiroCrew ON 平台的模型发现探测：不要读取或修改任何文件，不要调用任何工具，只回复 OK。"
DISCOVERY_TOOLS = ("kiro", "claude", "opencode")
DISCOVERY_RETRY = 2 * 3600   # a try that left no models is repeated after this
DISCOVERY_TRIES = 3          # then the tool is given up on (reported in health) until its list appears
NAMESPACES = {"kiro": "acp", "claude": "claude_code", "codex": "codex", "opencode": "opencode"}


def discovery_loop(worker, stop, first=60, every=900):
    """The Worker's own background check (D51 R1): soon after it starts, then every quarter hour, until it stops."""
    if stop.wait(first):
        return
    while True:
        try:
            worker.discover_models()
        except Exception as exc:   # the Gateway may be restarting: the next round tries again
            print(f"KiroCrew ON Worker: 模型发现未完成（{type(exc).__name__}）", file=sys.stderr, flush=True)
        if stop.wait(every):
            return


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
KNOWLEDGE_FILE = ".octopus/knowledge.md"   # the repository's knowledge (D52)
ENV_KEPT = workspace.ENV_KEPT
PUBLISH_LIMIT = 100 * 1024 * 1024   # D35 §5.2: one bundle (as publish.LIMIT on the platform)
SYNC_LIMIT = 20 * 1024 * 1024   # D58: the local commits one sync brings in


# What each kind of work is told at the end of its instructions (D46): planning, design and diagnosis are not development.
TAILS = {
    "planning": "只做分析与规划，不修改业务代码；按本任务要求输出 JSON。",
    "workflow_design": "只做分析与规划，不修改业务代码；按本任务要求输出工作流 JSON。",
    "coordination": "只诊断，不修改源码；按要求写 decision.json。",
    "design": "只做分析，不修改业务代码；在交接中给出结论、依据与后续任务所需的输入。",
    "review": "只读审查，不修改代码；结论绑定审查对象，缺陷用 changes_requested。",
    "test": "只写测试与测试报告，不修改实现；按项目既有依赖与规范工作。",
    "implement": "依据需求修改源码并真实验证，按项目既有依赖与规范工作；需求要改代码时，改动应留在源码目录中；"
                 "只产出不提交的补丁或报告也可以。",
    "deliver": "依据需求完成交付并真实验证，按项目既有依赖与规范工作；交付涉及代码时，改动应留在源码目录中；"
               "只产出不提交的补丁或报告也可以。",
}
DEFAULT_TAIL = "依据需求进行开发和真实验证，按项目既有依赖与规范工作。"
COMMON_TAIL = ("记录具体修改、测试命令、结果和限制。若不能完成，最终回答单独一行写 BLOCKED 并说明恢复条件"
               "（BLOCKED 只用于缺输入、认证或权限；缺陷用 changes_requested）。只执行当前任务，不派生其他子任务。"
               "不读取平台或其他项目的凭据。不要编造测试结果或实际模型。")
# Planning and diagnosis report their conclusion, not changes (D46): the Coordinator's way to ask is wait_for_user.
STAGE_TAILS = {
    "planning": "只执行当前任务，不派生其他子任务。不读取平台或其他项目的凭据。需求缺少必要信息、无法规划时，最终回答单独一行写 BLOCKED 并说明缺少什么。",
    "workflow_design": "只执行当前任务，不派生其他子任务。不读取平台或其他项目的凭据。不要编造结论。",
    "coordination": "只执行当前诊断，不派生其他子任务。不读取平台或其他项目的凭据。需要用户补充或决定时在 decision.json 中用 wait_for_user。",
}
# The parent Session's agent (D46): the Owner observer — it explains and points the user to a continue; it does not act.
OBSERVER_CONTEXT = ("你是此 Octopus 研发会话的 Owner 观察员：向用户解释计划、进度、错误与修复决定。"
                    "平台 Engine 统一派发已采纳的任务；失败诊断通过原生 poc-diagnose 子任务执行（不能再派生任务）。"
                    "保持同一 Session 与工作区，使用平台给出的真实交接和纠错记录。"
                    "不要自行派发子任务、执行旧 next/accept、修改源码或平台数据库。"
                    "用户要修改需求或任务时，请其在控制台「续作」或经 octopus_continue 提交；需要用户补充项目来源或权限时具体说明；"
                    "不要把未知原因说成已解决。")
PREVIEW = ("\n\n## 可运行产出的声明\n交付物是可以运行的应用时，在项目根目录写 .octopus/preview.json："
           '{"processes":[{"name":"web","command":"npm run dev -- --port $PORT","cwd":".","port":"$PORT","entry":true}],'
           '"install":["npm ci"],"ready":{"path":"/","timeout":120}}。1–4 个进程（前后端分开时各一个），恰有一个 entry；'
           'install 至多 4 条；远程预览要求入口进程的 port 为 "$PORT"。')


def envelope(task_input, requirement, contract, prompt, upstream, project, handoff, stage=None, extra=""):
    """The instructions an Attempt reads (D46): the requirement, the contract and the task, the upstream handoffs, then
    what this kind of work does — planning, design and diagnosis are not told to develop — and the handoff it writes."""
    from . import roles
    from .prompts import HANDOFF
    tail = TAILS.get(stage or (contract or {}).get("kind"), DEFAULT_TAIL)
    common = STAGE_TAILS.get(stage, COMMON_TAIL)
    role = (contract or {}).get("role")   # D46 P2: the role card comes first
    card = f"# 你的角色：{roles.LABELS[role]}\n{roles.card(role)}\n\n" if role in roles.KINDS else ""
    return (card + "# 本次研发任务\n\n" + task_input + briefing(requirement, contract) + "\n\n## 任务\n" + prompt +
            "\n\n## 前序交接\n前序任务的结论、发现、候选与检查；handoff 是其完整交接文件的路径，需要细节时读取；"
            "执行者没有写交接时，handoff_text 是平台根据其结果生成的交接。\n" +
            dumps(upstream) + f"\n\n源码目录：{project}\n交接文件：{handoff}\n" + tail + common + HANDOFF + extra)


def briefing(requirement, contract):
    """The requirement KiroCrew completed and this task's contract, ahead of the task itself (D34 §6.2)."""
    text = ""
    if requirement and (requirement.get("intent") or requirement.get("summary")):
        text += "\n\n## 需求与验收标准（KiroCrew 规划补全）\n"
        if requirement.get("outdated"):   # D46: a continue changed the requirement after it was planned
            text += "（以下为首次规划时的补全；用户已在续作中修改需求，以「本次研发任务」为准。）\n"
        if requirement.get("intent"):
            text += "意图：" + requirement["intent"] + "\n"
        text += requirement.get("summary", "")
    if contract:
        checks = ("建议的检查命令（平台不运行）" if not runs_checks(contract)
                  else "检查命令（任务结束后平台运行，须通过）")   # D46: say whether the platform runs them
        labels = (("deliverables", "交付物"), ("acceptance", "验收项"), ("verify", checks), ("paths", "可改动路径"))
        from . import roles
        text += "\n\n## 本任务契约\n" + (f"类别：{contract['kind']}\n" if contract.get("kind") else "") + (
            f"角色：{roles.LABELS[contract['role']]}\n" if contract.get("role") in roles.KINDS else "")
        for key, label in labels:
            if contract.get(key):
                text += label + "：\n" + "".join(f"- {item}\n" for item in contract[key])
        if contract.get("acceptance"):
            text += "逐项完成并核对上述验收项；审查与测试以它们为判定依据。\n"
        if contract.get("kind") == "deliver":
            text += PREVIEW.lstrip("\n") + "\n"
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


# The command each Coding tool is started with, and its [tools] setting (composition/environment.py starts them the same way).
DISABLED = {"opencode": "已停用（K01：OpenCode 在此执行环境写文件时被 KiroCrew 工具门禁拒绝、返回空结果；配置 disabled_tools 可恢复）"}
TOOL_COMMANDS = {"kiro": ("kiro_cli", "kiro-cli"), "claude": ("claude", "claude"), "codex": ("codex", "codex"),
                 "opencode": ("opencode", "opencode")}


# What a close / delete records instead of a backup when KiroCrew has nothing to export (backup kind -> observation file),
# and the export refusals that mean so (KiroCrew's export code -> backup kind). Any other refusal keeps the Session open.
OBSERVATIONS = {"native_empty_observation": "empty-session-observation.json",
                "native_restricted_observation": "restricted-session-observation.json",
                "native_missing_observation": "missing-session-observation.json"}
REFUSED_EXPORTS = {"export_bundle_empty": "native_empty_observation", "export_slot_not_persistent": "native_restricted_observation"}


def rework_note(kind, repair):
    """What a resumed conversation is told (D46 P5a), by the kind of work: a re-check never changes code. A fresh spawn
    (a refused continuation included) is never told any of it."""
    if kind in ("review", "test") or (repair or {}).get("recheck"):
        return ("\n这是同一任务的复验：你上一轮的审查与对话都在；只针对本轮的源码与候选重新核对，不修改代码；"
                "结论与交接写到本轮 instructions.md 指定的文件，不沿用上一轮的文件。")
    if kind == "design":
        return ("\n这是同一任务的重新分析：你上一轮的分析与对话都在；按本轮 instructions.md 的要求补充或修正结论，"
                "不修改业务代码，结论写到本轮指定的文件。")
    return ("\n这是同一任务的返工：你上一轮的工作与对话都在，先核对本轮 instructions.md 的要求，在已有改动上修复，"
            "不要从头重写；交接与结论写到本轮指定的文件。")

class Worker:
    def __init__(self, root, token, gateway_port=5476, crew_home=None, isolation="trusted-local", routing_log=None,
                 crew_url=None, approval_mode="", crew_entry_ttl="8h", kiro_cli=None, path_prepend=(),
                 tool_paths=None, adapters_dir=None, preview_tls_port=0, preview_tls_name="", disabled_tools=("opencode",),
                 crew_yolo=True):
        self.root = Path(root).resolve()
        self.disabled_tools = frozenset(disabled_tools)   # reported unavailable, so nothing plans, switches to or runs them
        self.crew_yolo = crew_yolo   # the boundary's standing YOLO: without it a continuation cannot ask for auto approval
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.token, self.port = token, gateway_port
        self._sync_guard, self._sync_locks = threading.Lock(), {}   # D58: one sync at a time per Session
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
        self.tool_paths = {**(tool_paths or {}), **({"kiro_cli": kiro_cli} if kiro_cli else {})}
        self.search = [*([adapters_dir] if adapters_dir else []), *path_prepend]
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

    def tools(self):
        """Whether each Coding tool can be started on this boundary (D38 §3.1): its configured path, else its command on
        the search path the Gateway uses. Kiro's login is reported beside it: an expired login waits for an
        administrator and is not a reason to switch tools."""
        search = os.pathsep.join([str(p) for p in self.search] + [os.environ.get("PATH", ""), str(Path.home() / ".local/bin")])
        result = {}
        for tool, (setting, command) in TOOL_COMMANDS.items():
            configured = self.tool_paths.get(setting)
            if tool in self.disabled_tools:
                result[tool] = {"available": False, "reason": DISABLED.get(tool, "已在此执行环境停用（disabled_tools）")}
                continue
            if configured:
                found = os.access(configured, os.X_OK)
                reason = f"配置的 {configured} 不存在或不可执行"
            else:
                found, reason = bool(shutil.which(command, path=search)), f"{command} 命令不存在"
            result[tool] = {"available": found, **({} if found else {"reason": reason})}
        result["kiro"]["login"] = self.tool_auth() if result["kiro"]["available"] else "unknown"
        return result

    APPROVAL_INPUT, APPROVAL_PURPOSE = 4000, 1000

    def _pending(self, slot):
        """The approvals pending on this slot (D38 §4.2): its native tool request and the state-level records it owns."""
        clipped = []
        def clip(text, limit):
            text = str(text or "")
            if len(text) <= limit:
                return text
            clipped.append(True)
            return text[:limit] + "…"
        def read(path):   # a Gateway refusal must not reach the console as a 401 (it signs the user out) or a 404
            try:
                return self.native(path) or []
            except crew_api.GatewayError as exc:
                raise Error(502, "native_error", "读取 KiroCrew 待批准项失败：" + str(exc).splitlines()[0][:200]) from None
        items = []
        for view in read("/api/chat/slots"):
            info = view.get("pending_approval_info") if isinstance(view, dict) and view.get("key") == slot else None
            if isinstance(info, dict) and info.get("origin") == "native" and info.get("request_mid"):
                items.append({"origin": "native", "request_id": str(info.get("request_id") or ""),
                              "request_mid": str(info["request_mid"]), "tool": clip(info.get("tool"), 200),
                              "tool_input": clip(info.get("tool_input"), self.APPROVAL_INPUT),
                              "tool_kind": clip(info.get("tool_kind"), 40),
                              "tool_purpose": clip(info.get("tool_purpose"), self.APPROVAL_PURPOSE)})
                if clipped:   # what is shown is not all of it: the console and the Skill say so
                    items[-1]["truncated"], clipped[:] = True, []
        for record in read("/api/approvals"):
            if isinstance(record, dict) and record.get("slot") == slot and record.get("instance"):
                items.append({"origin": "coordinator", "request_id": str(record.get("id") or ""),
                              "instance": str(record["instance"]), "source": clip(record.get("source"), 40),
                              "tool": clip(record.get("tool"), 200),
                              "tool_input": clip(record.get("tool_input"), self.APPROVAL_INPUT),
                              "tool_purpose": clip(record.get("tool_purpose"), self.APPROVAL_PURPOSE),
                              "ts": record.get("ts")})
                if clipped:
                    items[-1]["truncated"], clipped[:] = True, []
        return items

    def approvals(self, body):
        _, parent = self.binding(body["run_id"])
        return {"approvals": self._pending(parent["slot"])}

    def approvals_answer(self, body):
        """Answer one approval of this Session (D38 §4.2): it must still be pending on the Session's own slot, with the
        same identity the caller was shown, so a replaced or another Session's request is never answered."""
        _, parent = self.binding(body["run_id"])
        slot, origin, decision = parent["slot"], body.get("origin"), body.get("decision")
        require(decision in {"approve", "reject"}, "decision 只能是 approve 或 reject")
        require(origin in {"native", "coordinator"}, "origin 只能是 native 或 coordinator")
        key = "request_mid" if origin == "native" else "instance"
        wanted = (origin, str(body.get("request_id") or ""), str(body.get(key) or ""))
        require(any((i["origin"], i["request_id"], i.get(key, "")) == wanted for i in self._pending(slot)),
                "该批准请求已不在等待中（已被答复、过期或不属于此会话）", 409, "approval_not_pending")
        try:
            if origin == "native":
                self.native("/api/chat/slots/" + quote(slot, safe="") + "/approve",
                            {"origin": "native", "request_id": wanted[1], "request_mid": wanted[2],
                             "action": "approved" if decision == "approve" else "rejected"})
            else:
                query = urlencode({"origin": "coordinator", "slot": slot, "instance": wanted[2]})
                self.native(f"/api/approvals/{quote(wanted[1], safe='')}/{decision}?{query}", {}, "POST")
        except crew_api.GatewayError as exc:   # the platform reads a Worker 404 as "not supported": never pass one on
            if exc.status == 404:
                raise Error(409, "approval_not_pending", "该批准请求已不在等待中（已被答复或过期）") from None
            raise Error(502, "native_error", "KiroCrew 未接受答复：" + str(exc).splitlines()[0][:200]) from None
        return {"ok": True, "origin": origin, "request_id": wanted[1], "decision": decision}

    def native(self, path, body=None, method=None, timeout=None, headers=None):
        extra = {k: v for k, v in (("timeout", timeout), ("headers", headers)) if v is not None}
        return crew_api.request(path, body, method, port=self.port, home=self.home, **extra)

    # KiroCrew trusts the dashboard's own key for lesson writes (handlers/_shared.py); without a silo they go to the
    # Gateway's global store, and the repository mark keeps each lesson to its repository (D46 P4).
    LESSONS = {"X-Session-Key": "dashboard:ui"}

    @staticmethod
    def _knowledge_scope(value):
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", value), "知识范围标识无效")
        return ".octopus-kb/" + value

    def knowledge_write(self, body):
        """Write the platform-checked items as lessons of this repository: always injected into its later tasks."""
        self.binding(body["run_id"])
        scope = self._knowledge_scope(body.get("scope"))
        items = body.get("items")
        require(isinstance(items, list) and 0 < len(items) <= 5, "知识条目须为 1–5 条")
        for item in items:
            require(isinstance(item, dict) and isinstance(item.get("rule"), str) and 0 < len(item["rule"]) <= 500
                    and isinstance(item.get("avoid", ""), str) and len(item.get("avoid", "")) <= 500, "知识条目无效")
        results = []
        for item in items:
            lesson = {"rule": item["rule"], "category": "knowledge", "applies": "always", "repo_scope": scope}
            if item.get("avoid"):
                lesson["negative"] = item["avoid"]
            try:
                answer = self.native("/api/lessons", lesson, "POST", headers=self.LESSONS)
            except crew_api.GatewayError as exc:
                text = str(exc).partition(": ")[2]
                try:
                    code = json.loads(text).get("code")
                except (ValueError, AttributeError):
                    code = None
                results.append({"rule": item["rule"], "result": "refused", "reason": f"HTTP {exc.status} " + (code or text[:200])})
                continue
            if not answer.get("ok"):
                results.append({"rule": item["rule"], "result": "refused",
                                "reason": str(answer.get("reason") or answer.get("outcome") or "未写入")[:200]})
            elif answer.get("superseded"):
                results.append({"rule": item["rule"], "result": "superseded",
                                "reason": "替代了：" + "；".join(str(r)[:100] for r in answer["superseded"][:3])})
            else:
                results.append({"rule": item["rule"], "result": "written"})
        return {"results": results}

    def knowledge_forget(self, body):
        self.binding(body["run_id"])
        scope = self._knowledge_scope(body.get("scope"))
        rule = body.get("rule")
        require(isinstance(rule, str) and 0 < len(rule) <= 500, "知识条目无效")
        try:
            answer = self.native("/api/lessons", {"rule": rule, "exact": True, "repo_scope": scope}, "DELETE", headers=self.LESSONS)
        except crew_api.GatewayError as exc:   # the platform reads a Worker 404 as "not supported": never pass one on
            if exc.status == 404:
                return {"ok": True, "absent": True}   # KiroCrew holds no such lesson (deleted there already)
            raise Error(502, "native_error", "KiroCrew 未删除：" + str(exc).splitlines()[0][:200]) from None
        if isinstance(answer, dict) and answer.get("ok") is False:
            raise Error(409, "forget_refused", "KiroCrew 未删除：" + str(answer.get("reason") or "原因未报告")[:200])
        return {"ok": True}

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
    # Directories whose HTML is a dependency's, never the Session's output (EC2 run-968d6e37: a cloned repository's .venv).
    NOT_PAGES = {"node_modules", "venv", "site-packages", "__pycache__", "bower_components"}
    PAGES = 30

    def pages(self, project):
        """The HTML the Session produced, best first: a web project's own entry (PREVIEW_ENTRIES), then the newest. EC2
        2026-10-22: reports written to report/ or artifacts/<name>/ were 「未找到可预览的 index.html」."""
        found, seen = [], 0
        for root, directories, files in os.walk(project):
            directories[:] = sorted(d for d in directories if not d.startswith(".") and d not in self.NOT_PAGES)
            for name in files:
                seen += 1
                if name.lower().endswith((".html", ".htm")) and not name.startswith("."):
                    path = Path(root) / name
                    found.append((path.relative_to(project).as_posix(), path.stat().st_mtime))
            if seen > 20000:   # a very large project: what was found so far
                break
        entries = [e for e in self.PREVIEW_ENTRIES if any(f == e for f, _ in found)]
        rest = [f for f, _ in sorted(found, key=lambda item: (-item[1], item[0])) if f not in entries]
        return (entries + rest)[:self.PAGES]

    def files(self, body):
        """Read-only project files for the console preview: never outside the project, never hidden files (.env, .git)."""
        self._not_on_baseline(body.get("run_id"))
        self._undo_baseline(self.path(body.get("run_id")))
        project = workspace.project_dir(self.path(body.get("run_id"))).resolve()
        relative = body.get("path") or ""
        require(isinstance(relative, str) and len(relative) <= 500 and "\x00" not in relative, "路径无效")
        if not relative:
            pages = self.pages(project)
            require(bool(pages), "此会话还没有产出 HTML 页面；需要开发服务器的项目请在会话页「远程预览」里启动，或「下载源码」在本机运行",
                    404, "no_preview")
            return {"entry": pages[0], "pages": pages}
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
        self._not_on_baseline(run_id)
        self._undo_baseline(self.path(run_id))
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
        run = self.path(body["run_id"])
        self._undo_baseline(run)
        project = workspace.project_dir(run)
        baseline = body.get("baseline")
        if baseline is None:
            return self.checks.start(body["run_id"], body["attempt_id"], project, commands)
        # tests_fail_on_baseline (D37 §11.1): the same commands, in place, on the Session's baseline with the test task's
        # changes laid on top; the workspace is put back on the candidate whatever happens.
        require(isinstance(baseline, dict) and all(isinstance(baseline.get(k), str) and workspace.SHA.fullmatch(baseline[k])
                                                   for k in ("candidate", "start")), "基线检查须给出完整的提交 ID")
        base = workspace.read_record(run).get("commit")
        require(base, "此会话的项目没有 Git 基线", 409, "candidate_unsupported")
        marker = run / "baseline.json"

        def prepare():
            write(marker, {"candidate": baseline["candidate"]})
            workspace.baseline_apply(project, base, baseline["start"], baseline["candidate"])

        def restore():
            workspace.baseline_restore(project, baseline["candidate"])
            marker.unlink(missing_ok=True)
        started = self.checks.start(body["run_id"], body["attempt_id"], project, commands, kind="baseline",
                                    around=(prepare, restore))
        return {**started, "baseline": True, "base": base}

    def verify_status(self, body):
        return self.checks.status(str(body.get("job", "")))

    def _not_on_baseline(self, rid):
        """While a baseline run has the project on the base (D37 §11.1), what reads or serves its files waits: a download,
        a preview start or a publish would otherwise take the baseline for the Session's work."""
        require(not self.checks.running(rid, "baseline"), "正在会话基线上运行测试，稍后再试", 409, "baseline_running")

    def _undo_baseline(self, run):
        """A baseline run the Worker did not finish (it restarted): the workspace back on the candidate first (D37 §11.1)."""
        marker = run / "baseline.json"
        if marker.is_file() and not self.checks.running(run.name, "baseline"):   # a live run puts it back itself
            candidate = json.loads(marker.read_text()).get("candidate")
            if isinstance(candidate, str) and workspace.SHA.fullmatch(candidate):
                workspace.baseline_restore(workspace.project_dir(run), candidate)
            marker.unlink(missing_ok=True)

    @staticmethod
    def _untracked_at_start(run, folder):
        """The untracked files a review starts with (D57 §4), kept in its Attempt's folder, outside the project: what it
        creates beside them is moved out when its changes are put back. None when they cannot be listed: the review runs
        without them."""
        try:
            return workspace.untracked(workspace.project_dir(run))
        except Exception as exc:   # never holds the review up
            print(f"KiroCrew ON Worker: 未记录审查开始时的未跟踪文件（{folder.name}：{workspace.scrub(exc)[:200]}）",
                  file=sys.stderr, flush=True)
            return None

    @staticmethod
    def _review_snapshot(run, attempt):
        """The untracked files a review Attempt started with, or None: not a review, or dispatched before D57 §4."""
        if not (isinstance(attempt, str) and re.fullmatch(r"attempt-[a-f0-9]{32}", attempt)):
            return None
        path = run / "attempts" / attempt / "untracked.json"
        try:
            paths = None if path.is_symlink() or not path.is_file() else json.loads(path.read_text()).get("paths")
        except (OSError, ValueError, AttributeError):
            return None
        return paths if isinstance(paths, list) and all(isinstance(name, str) for name in paths) else None

    @staticmethod
    def _created(state, project, keep):
        """A check's or a rewind's state with the untracked files the review created (``created``, D57 §4);
        ``tracked_only`` when only its tracked files could be told: the Attempt has no snapshot, or listing failed."""
        if keep is not None:
            try:
                return {**state, "created": sorted(set(workspace.untracked(project)) - set(keep)), "tracked_only": False}
            except ValueError:   # never a failure of its own: told as by a Worker without snapshots
                pass
        return {**state, "created": [], "tracked_only": True}

    def candidate(self, body):
        """A task's candidate commit (D37 §3, ``mode: commit``), a review's check that the workspace is still the
        candidate it reviewed (§6, ``mode: check``), or putting back what a read-only platform task changed in tracked
        files after that candidate (D46 P4, ``mode: restore``; never moves the branch), or what a review changed — its
        commits on top of the candidate too, so the branch is back on it, and the files it created, moved into its
        Attempt's ``review-removed`` folder (D57 §4, ``mode: rewind``) — or what a candidate changed (D56, ``mode: diff``;
        read only). check and rewind name the files the review created (``created``) when its dispatch listed the
        untracked files it started with."""
        run = self.path(body["run_id"])
        self._undo_baseline(run)
        try:   # a Session whose workspace is gone (cleaned up long after it ended) says so, not a 500
            project, record = workspace.project_dir(run), workspace.read_record(run) or {}
        except ValueError as exc:
            raise Error(409, "workspace_missing", workspace.scrub(exc)) from None
        base, branch = record.get("commit"), record.get("branch")
        require(base and branch, "此会话的项目没有 Git 基线，无法生成候选", 409, "candidate_unsupported")
        mode = body.get("mode")
        require(mode in {"commit", "check", "restore", "rewind", "diff"}, "mode 只能是 commit、check、restore、rewind 或 diff")
        for key in ("parent", "expected", "since"):
            require(body.get(key) is None or (isinstance(body[key], str) and workspace.SHA.fullmatch(body[key])),
                    f"{key} 须为完整的提交 ID")
        try:
            if mode == "diff":
                require(body.get("expected"), "diff 需要 expected")
                return workspace.candidate_diff(project, body["expected"], body.get("parent") or base)
            keep = self._review_snapshot(run, body.get("attempt_id")) if mode in {"check", "rewind"} else None
            if mode == "check":
                require(body.get("expected"), "check 需要 expected")
                return self._created(workspace.clean_against(project, body["expected"]), project, keep)
            if mode == "restore":
                require(body.get("expected"), "restore 需要 expected")
                state = workspace.clean_against(project, body["expected"])
                require(state["head"] == body["expected"], "会话分支已移动，不能还原", 409, "conflict")
                if state["changed_files"]:
                    workspace.restore(project, state["changed_files"])
                return workspace.clean_against(project, body["expected"])
            if mode == "rewind":
                require(body.get("expected"), "rewind 需要 expected")
                state = workspace.clean_against(project, body["expected"])
                if state["head"] != body["expected"]:   # only commits made on top of the candidate are dropped
                    require(workspace.ancestor(project, body["expected"], state["head"], env=workspace.SAFE_GIT_ENV),
                            f"会话分支不在候选 {body['expected'][:7]} 之上，不能撤回", 409, "conflict")
                    current = workspace._git(project, "rev-parse", "--abbrev-ref", "HEAD")
                    require(current == branch, f"工作区不在会话分支 {branch}（当前 {current}），不能撤回", 409, "conflict")
                into = None if keep is None else run / "attempts" / body["attempt_id"] / "review-removed"
                return self._created(workspace.rewind(project, body["expected"], keep=keep, into=into), project, keep)
            message = str(body.get("message") or "KiroCrew ON：任务候选")[:300]
            return workspace.task_candidate(project, message, branch, run, body.get("parent"), base, body.get("since"))
        except ValueError as exc:
            raise Error(409, "candidate_failed", workspace.scrub(exc)) from None

    def publish_plan(self, body):
        """What a publish exports (D35 §5.2): the Session's branch, with the workspace's pending changes committed (without
        what a download leaves out). The bundle carries every commit, so everything new since the clone's baseline is
        checked first, and the branch moves only when nothing in it looks like a credential or a .env file."""
        run = self.path(body["run_id"])
        self._not_on_baseline(body["run_id"])
        self._undo_baseline(run)
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

    def _session_project(self, rid):
        run = self.path(rid)
        self._undo_baseline(run)
        try:   # a Session whose workspace is gone says so, not a 500
            project, record = workspace.project_dir(run), workspace.read_record(run) or {}
        except ValueError as exc:
            raise Error(409, "workspace_missing", workspace.scrub(exc)) from None
        return run, project, record.get("commit"), record.get("branch")

    def pull_plan(self, body):
        """What a local pull brings back (D58 §5): the Session's branch at its head, checked like a publish — nothing that
        looks like a credential or a .env file leaves the execution environment. Read only."""
        run, project, base, branch = self._session_project(body["run_id"])
        require(base and branch, "此会话的项目没有 Git 基线，无法拉回", 409, "nothing_to_pull")
        try:
            head = workspace._git(project, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
            found = workspace.unpublishable(project, base, head)
        except ValueError as exc:
            raise Error(409, "pull_failed", workspace.scrub(exc)) from None
        require(not found, "这个会话的分支中有疑似凭证或 .env 文件，不能拉回：" + "、".join(found[:20]), 409, "bundle_secret_found")
        return {"project": project, "base": base, "head": head, "branch": branch, "run": run,
                "full": bool(body.get("full")) or head == base}

    def pull_bundle(self, plan):
        """The bundle file of a pull: the branch since the baseline (a local clone of the repository has it), or its
        whole history (``full``: a new project, or a clone without the baseline). The caller streams and removes it."""
        bundle = plan["run"] / ".pull.bundle"
        bundle.unlink(missing_ok=True)
        ref = "refs/heads/" + plan["branch"]
        try:
            workspace._git(plan["project"], "bundle", "create", "-q", str(bundle), ref, *([] if plan["full"] else ["^" + plan["base"]]))
            require(workspace._git(plan["project"], "bundle", "list-heads", str(bundle)).split() == [plan["head"], ref],
                    "拉回期间会话分支被改动，请重试", 409, "pull_failed")
            require(bundle.stat().st_size <= PUBLISH_LIMIT, f"要拉回的内容超过 {PUBLISH_LIMIT // 1048576} MB", 413, "pull_too_large")
        except ValueError as exc:
            bundle.unlink(missing_ok=True)
            raise Error(409, "pull_failed", workspace.scrub(exc)) from None
        except Error:
            bundle.unlink(missing_ok=True)
            raise
        return bundle

    def sync(self, body, data):
        """A local fast-forward of the Session's branch, brought in from a bundle (D58 §6). The bundle is untrusted: its
        header must name exactly one head (``head``), and its objects are unpacked into a quarantine directory first —
        nothing of a refused sync stays in the repository. The branch and workspace move (a fast-forward merge, which never
        overwrites an untracked file) only when the branch is still at ``old``, the workspace holds nothing a move would
        lose, ``head`` descends from ``old`` and nothing new looks like a credential or a .env file. The same sync again,
        once the branch is at ``head``, answers the same (a retry after the platform failed to record it)."""
        require(len(data) <= SYNC_LIMIT, f"同步的内容超过 {SYNC_LIMIT // 1048576} MB", 413, "sync_too_large")
        for key in ("old", "head"):
            require(isinstance(body.get(key), str) and workspace.SHA.fullmatch(body[key]), f"{key} 须为完整的提交 ID")
        run, project, base, branch = self._session_project(body["run_id"])
        require(base and branch, "此会话的项目没有 Git 基线，无法同步", 409, "sync_unsupported")
        old, new = body["old"], body["head"]
        with self._sync_lock(body["run_id"]):
            quarantine = run / f".sync-{secrets.token_hex(8)}"
            try:
                tip = workspace._git(project, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
                if tip == new and new != old and workspace.ancestor(project, old, new):   # already applied: a retry
                    return self._synced(project, old, new)
                require(tip == old and workspace._git(project, "rev-parse", "--abbrev-ref", "HEAD") == branch,
                        "会话分支已有新的提交（另一次同步或续作）：请先拉回（pull），在其上修改后再同步", 409, "sync_not_fast_forward")
                state = workspace.clean_against(project, old)
                require(state["clean"], "会话的工作区有尚未纳入候选的改动：" + "、".join(state["changed_files"][:20])
                        + "。请等会话结束、或续作纳入这些改动后再同步", 409, "sync_dirty")
                objects = quarantine / "objects"
                (objects / "pack").mkdir(parents=True)
                bundle = quarantine / "sync.bundle"
                bundle.write_bytes(data)
                heads = workspace._git(project, "bundle", "list-heads", str(bundle)).splitlines()
                sha, _, ref = heads[0].partition(" ") if len(heads) == 1 else ("", "", "")
                require(sha == new and (ref == "HEAD" or (ref.startswith("refs/heads/") and subprocess.run(
                    ["git", "check-ref-format", ref], capture_output=True).returncode == 0)),
                        "上传的内容须只含一个分支提交（声明的 head）", 409, "sync_invalid")
                store = Path(workspace._git(project, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
                env = {"GIT_OBJECT_DIRECTORY": str(objects), "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(store)}
                workspace._git(project, "bundle", "unbundle", str(bundle), env=env)   # refs are not written; objects stay here
                require(workspace.ancestor(project, old, new, env), "本地提交不是基于会话分支的当前提交：请先拉回（pull），"
                        "在其上修改后再同步", 409, "sync_not_fast_forward")
                found = workspace.unpublishable(project, old, new, env)
                require(not found, "同步被拒绝：本地提交中有疑似凭证或 .env 文件：" + "、".join(found[:20]), 409, "sync_secret_found")
                for item in objects.rglob("*"):   # checked: the objects join the repository's store
                    if item.is_file():
                        target = store / item.relative_to(objects)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(item, target)
                moved = subprocess.run(["git", "-C", str(project), "merge", "-q", "--ff-only", new], capture_output=True, text=True)
                require(moved.returncode == 0, "会话的工作区有会被覆盖的文件，未同步：" + workspace.scrub(moved.stderr.strip())[-300:],
                        409, "sync_dirty")
                return self._synced(project, old, new)
            except ValueError as exc:
                raise Error(409, "sync_failed", workspace.scrub(exc)) from None
            finally:
                shutil.rmtree(quarantine, ignore_errors=True)

    @staticmethod
    def _synced(project, old, new):
        return {"sha": new, "parent": old, "changed": True, "secrets": [],
                "commits": int(workspace._git(project, "rev-list", "--count", f"{old}..{new}")), **workspace.change_stats(project, old, new)}

    def _sync_lock(self, rid):
        with self._sync_guard:
            return self._sync_locks.setdefault(rid, threading.Lock())

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
        if action == "start":
            self._not_on_baseline(rid)
        self._undo_baseline(run)
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

    def _continued_model(self, conversation, model):
        """The model a continued rework asks for: the task's own; for "auto" the one its conversation first ran on, since
        KiroCrew resolves "auto" on a continue to its global default (EC2 2026-10-20: a claude "default" run went on as
        Opus). Only a plain id a spawn takes; otherwise "auto"."""
        if model != "auto":
            return model
        state = self.home / "subagents" / conversation / "state.json"
        try:
            first = json.loads(state.read_text()).get("resolved_model") if state.is_file() and not state.is_symlink() else None
        except (OSError, ValueError):
            first = None
        return first if isinstance(first, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", first) else "auto"

    def advertised(self):
        """The models KiroCrew recorded each tool's backend as publishing (its provider_models.json)."""
        cache = self.home / "provider_models.json"
        models = json.loads(cache.read_text()) if cache.is_file() else {}
        return {tool: [m for m in models.get(ns, []) if isinstance(m, str)] for tool, ns in NAMESPACES.items()}

    @staticmethod
    def model_defaults():
        """The configured model each tool's "auto" stands for, where the tool publishes no list (D51 R5): codex's."""
        try:
            import tomllib
            model = tomllib.loads((Path.home() / ".codex/config.toml").read_text()).get("model")
        except (OSError, ValueError):
            return {}
        return {"codex": model} if isinstance(model, str) and model else {}

    def discover_models(self):
        """Fill KiroCrew's model lists for the tools that have none yet (D51 R2–R4), at most one probe session a round and
        three tries per tool: kiro's list is KiroCrew's catalog (GET /api/models, kiro-cli --list-models; no model turn),
        the others publish theirs when a session starts. Never during a deploy drain, while real work runs, or where a
        session would wait for an approval. Returns the tools tried."""
        if (self.root / ".maintenance").exists():
            return []
        record_file = self.root / "model-discovery.json"
        record = json.loads(record_file.read_text()) if record_file.is_file() else {}
        published, tools, now = self.advertised(), self.tools(), time.time()
        for tool, entry in record.items():   # a list that appeared ends that tool's discovery
            if published.get(tool):
                entry["result"] = "found"

        def due(tool):
            entry = record.get(tool) or {}
            if not (tools.get(tool) or {}).get("available") or published.get(tool) or entry.get("result") == "gave_up":
                return False
            if tool == "kiro" and tools["kiro"].get("login") != "ok":   # never a login flow (R3)
                return False
            if now - entry.get("at", 0) < DISCOVERY_RETRY:
                return False
            if entry.get("attempts", 0) >= DISCOVERY_TRIES:
                entry["result"] = "gave_up"
                return False
            return True

        def tried(tool, **result):
            entry = record.setdefault(tool, {})
            entry.update(at=now, attempts=entry.get("attempts", 0) + 1, **result)
            write(record_file, record)

        done = []
        if due("kiro"):
            try:
                self.native("/api/models")
                tried("kiro", result="asked")
            except Exception as exc:
                tried("kiro", result="failed", error=type(exc).__name__)
            done.append("kiro")
        session = next((tool for tool in DISCOVERY_TOOLS if tool != "kiro" and due(tool)), None)
        if session and (self.crew_yolo or self.approval_mode == "auto") and \
                not any(not row.get("done") for row in self.native("/api/spawn").get("agents", [])):
            try:
                slot = self._discovery_slot()
                spawn = {"task": DISCOVERY_PROMPT, "parent_session": "dashboard:" + slot, "agent": "poc-" + session,
                         "model": "auto", "max_turns": 1, "cwd": str(self.root / "model-discovery"), "keep": False,
                         "include_memory": False, "include_lessons": False, "include_project": False, "silent": True}
                if self.approval_mode == "auto":
                    spawn["approval_mode"] = "auto"
                tried(session, result="spawned", native_id=self.native("/api/spawn", spawn).get("id"))
            except Exception as exc:   # recorded, not raised: the next round tries again
                tried(session, result="failed", error=type(exc).__name__)
            done.append(session)
        write(record_file, record)
        return done

    def _discovery_slot(self):
        """The platform's own parent session for probes (D51), kept apart from every Session's octopus-run-* slot."""
        slot = "octopus-models"
        try:
            self.native("/api/chat/slots/" + slot)
        except crew_api.GatewayError as exc:
            if exc.status != 404:
                raise
            present = self.native("/api/chat/slots", {"name": slot, "agent": "poc-coordinator", "agent_kind": "template",
                                                      "model": "auto", "memory_mode": "temporary"})
            require(present.get("key", present.get("slot")) == slot, "原生父 Session 身份不一致")
        (self.root / "model-discovery").mkdir(exist_ok=True)
        return slot

    def discovery_state(self):
        """What model discovery has done on this boundary (D51), for the health report."""
        record_file = self.root / "model-discovery.json"
        return json.loads(record_file.read_text()) if record_file.is_file() else {}

    def health(self):
        # Descriptive boundary, not cryptographic proof of OS isolation.
        boundary = f"{socket.gethostname()}:{os.getuid()}:{self.home}"
        advertised = self.advertised()
        return {"version": __version__, "isolation": self.isolation, "workflow_design": True,
                "spawn_approval": "auto" if self.approval_mode == "auto" else "interactive",
                "crew_entry": True, "crew_origin": self.crew_origin, "observe": True, "app_preview": True,
                "tool_auth": {"kiro": self.tool_auth()}, "tools": self.tools(), "commands": self.checks.commands(),
                "coordinator_recovery": True, "quality_outcome": True, "baseline_checks": True, "attempt_stop": True,
                "role_cards": True,   # D46 P2: a contract may carry a role
                "knowledge": True,    # D46 P4: repository mark, lessons on request, /knowledge/write and /forget
                "continue": True,     # D46 P5a: a rework may continue its task's native conversation
                "steer": True,        # D46 P5b: a running task may be given a note
                "skills": list(pack.CATALOG),   # D50: the skill pack it carries
                "boundary": hashlib.sha256(boundary.encode()).hexdigest(),
                "host_principal": hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest(),
                "routing_evidence": bool(self.routing_log and self.routing_log.is_file()),
                "model_source": "KiroCrew provider_models.json; ACP session/new advertised cache",
                "advertised_models": advertised,
                "model_defaults": self.model_defaults(),   # D51 R5: what "auto" runs where the tool publishes no list
                "model_discovery": self.discovery_state(),
                "models": {tool: list(dict.fromkeys(["auto"] + [m for m in ids if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", m)]))
                           for tool, ids in advertised.items()},
                "limitations": ["当前原生 spawn 模型格式不支持 /、:、[]；这些已公布的 ID 不列为可直接派发模型。",
                                "OpenCode Effort 覆盖未验证。实际模型与实际 Effort 仅按原生证据报告。",
                                "已知问题 K01：OpenCode 在 EC2 执行边界上以 shell 写文件被 KiroCrew 工具门禁拒绝、返回空结果"
                                + ("，已停用。" if "opencode" in self.disabled_tools else "，实现类任务可能失败。")]}

    def taking_work(self):
        """A deploy that restarts the Gateway marks the Worker while it drains (deploy/install/al2023.sh): no new work until
        it is done — the platform waits and retries on its own (native_unavailable)."""
        require(not (self.root / ".maintenance").exists(), "执行环境维护中（部署），稍后自动继续", 503, "native_unavailable")

    def prepare(self, body):
        self.taking_work()
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
            if body.get("knowledge_scope") is not None:
                self._mark(workspace.project_dir(run), body["knowledge_scope"])
            slot = "octopus-" + rid
            parent_file = run / "parent.json"
            if not parent_file.exists():
                try:
                    present = self.native("/api/chat/slots/" + slot)
                except crew_api.GatewayError as exc:
                    if exc.status != 404:
                        raise
                    present = self.native("/api/chat/slots", {
                        "name": slot, "agent": "poc-coordinator", "agent_kind": "template", "model": "auto",
                        # KiroCrew exports only a persistent slot (session_export.py:380), and close / delete need that
                        # backup; incognito still reads memory, and a mode is only ever tightened. Memory is kept apart
                        # by giving each person or Team its own Worker (owner, 2026-10-17), not per Session.
                        "memory_mode": "persistent"})
                    require(present.get("key", present.get("slot")) == slot, "原生父 Session 身份不一致")
                self.native("/api/chat/slots/" + slot + "/context", {
                    "content": OBSERVER_CONTEXT,
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

    def _repository_knowledge(self, run):
        """The repository's knowledge file, as the cloned baseline holds it (D52 R5): never on a new project, never one an
        agent added or changed in this Session (final review 2026-10-20)."""
        record, project = workspace.read_record(run), workspace.project_dir(run)
        path = project / KNOWLEDGE_FILE
        if record.get("kind") not in {"git", "local-git"} or path.is_symlink() or not path.is_file():
            return False
        try:   # the baseline's blob from the object store, compared here: no work-tree git (fsmonitor, diff drivers)
            baseline = workspace._git_bytes(project, "cat-file", "blob", f"{record['commit']}:{KNOWLEDGE_FILE}")
            return baseline == path.read_bytes()
        except (OSError, ValueError):
            return False

    def _mark(self, project, key):
        """The repository mark KiroCrew scopes this repository's lessons to: a file only on this host, never committed."""
        mark = project / self._knowledge_scope(key)
        exclude = project / ".git/info/exclude"
        require(not any(p.is_symlink() for p in (mark.parent, mark, exclude.parent, exclude)), "知识标记路径不可为符号链接")
        mark.parent.mkdir(exist_ok=True)
        mark.touch()
        exclude.parent.mkdir(exist_ok=True)
        lines = exclude.read_text().splitlines() if exclude.is_file() else []
        if "/.octopus-kb/" not in lines:
            exclude.write_text("".join(line + "\n" for line in lines + ["/.octopus-kb/"]))

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
        self.taking_work()
        run, parent = self.binding(body["run_id"])
        self._undo_baseline(run)   # the next task's Agent must see the candidate, never a baseline left by a restart
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
        resume = body.get("continue")
        if resume is not None:   # D46 P5a: a rework resumes one of this Session's own native conversations, no other
            require(isinstance(resume, dict) and set(resume) == {"conversation", "from"}
                    and re.fullmatch(r"[a-f0-9]{8,64}", str(resume.get("conversation", "")))
                    and re.fullmatch(r"attempt-[a-f0-9]{32}", str(resume.get("from", ""))), "续接对象无效")
        # Why this rework spawns afresh instead (D46 P5a): a conversation this Session's Worker never started (the Session was
        # moved to another environment), or approvals the conversation cannot be asked to take automatically.
        unresumable = None if resume is None else (
            "not_this_session" if resume["conversation"] not in self._conversations(run) else
            "approval_mode" if self.approval_mode == "auto" and not self.crew_yolo else None)
        folder = run / "attempts" / attempt
        # Checked before the lock: a slow kiro-cli must not hold every other request of this Worker.
        login = self.tool_auth() if spec["tool"] == "kiro" or body.get("coordination") or body.get("design") else "ok"
        try:   # D57 §4: a review's untracked files, listed before the lock too (a large workspace is slow to list)
            review = (self.contract(spec, body.get("contract")).get("kind") or spec.get("kind")) == "review"
        except Error:
            review = False   # refused, and said, under the lock
        listed = self._untracked_at_start(run, folder) if review and not (folder / "dispatch.json").exists() else None
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
                raise Error(503, "tool_auth_expired", "Worker 上 kiro-cli 登录已过期：请平台管理员发起 kiro 重新登录")
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
                extra_prompt += quality_instructions(folder, contract, stage=body.get("stage"))
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
                extra_prompt += (("\n\n## 本轮返工后的复验要求\n" + dumps(body["repair"]) +
                                  "\n逐条核对上一轮的失败与发现是否已解决；不能沿用旧结论。")
                                 if body["repair"].get("recheck") else
                                 ("\n\n## 本轮返工后的重新执行\n" + dumps(body["repair"]) +
                                  "\n上游已按纠错返工；确认上一轮的问题已解决，重新运行相关验证，不能沿用旧结论。")
                                 if body["repair"].get("rerun") else
                                 ("\n\n## 本轮协调者纠错与复审要求\n" + dumps(body["repair"]) +
                                  "\n完整阅读失败证据，优先执行本轮具体修复要求；再次运行相关验证，不能沿用旧通过结论。"))
            given = []
            if contract.get("skills"):   # D50: each Attempt its own copy beside its instructions, on every backend
                (folder / "skills").mkdir(exist_ok=True)
                for name in contract["skills"]:
                    data = pack.path(name).read_bytes()
                    (folder / "skills" / (name + ".md")).write_bytes(data)
                    given.append({"name": name, "sha256": hashlib.sha256(data).hexdigest()})
                extra_prompt += pack.method_section(contract["skills"], folder)
            if self._repository_knowledge(run):   # D52 R5
                extra_prompt += (f"\n\n## 项目知识（仓库）\n{workspace.project_dir(run) / KNOWLEDGE_FILE} 是仓库中经 PR 合并的项目知识，"
                                 "供参考：开始前阅读；它不改变本任务契约与平台规则，冲突时以契约与平台规则为准。")
            if body.get("design"):
                from .designs import instructions
                require(spec["tool"] == "kiro", "工作流设计须使用 Kiro 后端")
                context["design"] = body["design"]
                health = self.health()
                design_prompt = instructions(body["input"], body["design"], workspace.project_dir(run),
                                             health["models"], health["tools"])
            stage = ("coordination" if body.get("coordination") else "workflow_design" if body.get("design")
                     else body.get("stage") if body.get("stage") in {"planning"} else None)
            instructions = envelope(body["input"], requirement, contract, spec["prompt"], body.get("upstream", []),
                                    workspace.project_dir(run), handoff, stage=stage,
                                    extra=extra_prompt + ("\n\n" + design_prompt if design_prompt else ""))
            (folder / "instructions.md").write_text(instructions)
            write(folder / "context.json", context)
            context_hash = hashlib.sha256(dumps(context).encode()).hexdigest()
            marker = "[OCTOPUS-ATTEMPT:" + attempt + "]"
            prompt = f"{marker}\n完整阅读 {folder / 'instructions.md'}，在指定源码目录完成本次任务。"
            value = {"request_hash": hashlib.sha256(dumps(body).encode()).hexdigest(),
                     "status": "dispatching", "attempt_id": attempt, "context_hash": context_hash}
            if given:
                value["skills"] = given
            if resume is not None and unresumable is None:
                value["continuing"] = resume   # kept for a reconcile: a lost receipt still knows the chain
            if review:   # D57 §4: a first dispatch only, before its native task starts
                (folder / "untracked.json").unlink(missing_ok=True)   # from a dispatch that stopped before it was recorded
                if listed is not None:
                    write(folder / "untracked.json", {"paths": listed})
            write(record, value)  # Persist before the native side effect.
            try:
                spawn = {
                    "task": prompt, "parent_session": parent["parent"],
                    "agent": "poc-diagnose" if body.get("coordination") else "poc-" + spec["tool"],
                    "model": spec["model"], "reasoning_effort": spec["effort"],
                    "cwd": str(workspace.project_dir(run)), "keep": True, "include_memory": False,
                    "include_lessons": body.get("lessons") is True, "include_project": False,
                    "silent": True}
                if self.approval_mode == "auto":
                    # Owner-authenticated request for native per-spawn auto approval (D14, K19).
                    spawn["approval_mode"] = "auto"
                result = None
                if unresumable:
                    value["continue_refused"] = unresumable
                elif resume is not None:
                    # A new run in the conversation: its context and cwd; /continue takes no effort or approval mode.
                    # The model is always sent, "auto" too: without one KiroCrew continues on its global default model
                    # (EC2 2026-10-20: a claude "default" run became Opus; a codex one was asked for a Claude model).
                    again = {"task": prompt + rework_note((contract or {}).get("kind"), body.get("repair")),
                             "parent_session": parent["parent"], "agent": spawn["agent"],
                             "model": self._continued_model(resume["conversation"], spec["model"])}
                    try:
                        result = self.native("/api/spawn/" + quote(resume["conversation"], safe="") + "/continue", again)
                        value["continued"] = {"conversation": resume["conversation"], "from": resume["from"]}
                        value.pop("continuing", None)
                    except crew_api.GatewayError as exc:
                        if exc.status not in {400, 404, 409}:
                            raise   # capacity or the Gateway itself: as for a spawn, inspected before any retry
                        try:
                            code = json.loads(str(exc).partition(": ")[2]).get("code")
                        except (ValueError, AttributeError):
                            code = None
                        value.pop("continuing", None)
                        value["continue_refused"] = str(code or f"HTTP {exc.status}")[:80]   # gone, busy: a fresh spawn
                if result is None:
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

    USAGE_ROW = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_tokens",
                 "cache_create": "cache_write_tokens"}

    usage_wait = 1.5   # seconds between looks for a usage row that KiroCrew writes just after a run ends

    def _usage(self, folder, dispatch, native, state):
        """What a finished run used (D46 P6a): Kiro credits, wall time and turns from the run; tokens, cost and the model
        from KiroCrew's usage row for it (one per run, written when it ends, on the conversation's slot). Read once and
        kept in the Attempt's folder; anything unreadable is left out and never changes the verdict."""
        saved = folder / "usage.json"
        if saved.is_file() and not saved.is_symlink():
            try:
                return json.loads(saved.read_text())
            except ValueError:
                pass
        try:
            usage = self._read_usage(dispatch, native, state)
        except Exception:   # best effort: never what decides an Attempt
            usage = {"tokens": "unavailable"}
        try:
            write(saved, usage)
        except OSError:
            pass
        return usage

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            value = float(value)
        except OverflowError:
            return None
        return value if math.isfinite(value) and value >= 0 else None

    def _read_usage(self, dispatch, native, state):
        number, usage = self._number, {}
        for key, name in (("credits", "credits"), ("elapsed", "seconds")):
            if number(native.get(key)) is not None:
                usage[name] = round(number(native[key]), 4)
        turns = native.get("turns", state.get("turns"))
        if isinstance(turns, int) and not isinstance(turns, bool) and 0 <= turns < 10 ** 9:
            usage["turns"] = turns
        started = number(native.get("started")) or number(state.get("started"))
        conversation = (dispatch.get("continued") or {}).get("conversation") or dispatch["native_id"]
        end = started + usage.get("seconds", 0) + 300 if started else None
        # KiroCrew writes the row in the background as the run ends, so it can trail the run's `done` by a moment.
        recent = started is not None and time.time() - (started + usage.get("seconds", 0)) < 20
        row = None
        for look in range(3 if recent else 1):
            if look:
                time.sleep(self.usage_wait)
            try:
                rows = self.native("/api/usage/turns?" + urlencode({"slot": "subagent:" + conversation, "days": 2})).get("turns")
            except Exception:   # an older Gateway or a refusal: the run's own numbers still stand
                break
            mine = []
            for item in rows if isinstance(rows, list) else []:
                try:
                    at = datetime.fromisoformat(str(item.get("ts"))).timestamp()
                except (ValueError, AttributeError, OverflowError):
                    continue
                if started is None or started - 5 <= at <= end:
                    mine.append((at, item))
            if mine:
                row = max(mine, key=lambda pair: pair[0])[1]
                break
        if row is None:
            usage["tokens"] = "unavailable"
            return usage
        for key, name in self.USAGE_ROW.items():
            value = row.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 15:
                usage[name] = value
        if number(row.get("cost")) is not None:
            usage["cost_usd"] = round(number(row["cost"]), 6)
        if isinstance(row.get("model"), str) and row["model"]:
            usage["model"] = row["model"][:120]
        return usage

    @staticmethod
    def _conversations(run):
        """The native conversations this Session's Attempts started or continued (D46 P5a)."""
        found = set()
        for record in (run / "attempts").glob("attempt-*/dispatch.json"):
            try:
                value = json.loads(record.read_text())
            except (OSError, ValueError):
                continue
            found |= {str(v) for v in (value.get("native_id"), (value.get("continued") or {}).get("conversation")) if v}
        return found

    def reconcile(self, run, parent, attempt, value):
        tasks = self.native("/api/spawn").get("agents", [])
        matches = [t for t in tasks if t.get("parent") == parent["parent"]
                   and "[OCTOPUS-ATTEMPT:" + attempt + "]" in t.get("task", "")]
        if len(matches) == 1:
            value.update(status="running", native_id=matches[0]["id"], reconciled=True)
            if value.get("continuing"):   # D46 P5a: the run it found is the continuation that was asked for
                value["continued"] = value.pop("continuing")
            write(run / "attempts" / attempt / "dispatch.json", value)
        else:
            value.update(status="dispatch_unknown", error="尚不能唯一确认原生派发；没有重发")
        return value

    def _queued(self, native_id, attempt, owned=False):
        """A deferred run of this Attempt, as KiroCrew's GET /api/spawn/{id} reports it (0.10: status queued, reason,
        reason_detail, elapsed, resuming); its task text carries the Attempt's mark, which proves it is ours — or, for one
        that began and waits again, its state file did (``owned``). None else."""
        try:
            row = self.native("/api/spawn/" + native_id)
        except crew_api.GatewayError:
            return None
        if not isinstance(row, dict) or not (row.get("queued") or row.get("status") == "queued"):
            return None
        if not owned and "[OCTOPUS-ATTEMPT:" + attempt + "]" not in str(row.get("task") or ""):
            return None
        elapsed = row.get("elapsed")
        return {"reason": str(row.get("reason") or "")[:40], "detail": str(row.get("reason_detail") or "")[:300],
                "elapsed": elapsed if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) else None,
                "resuming": bool(row.get("resuming"))}

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
        if native is None:   # D60: a run KiroCrew deferred, or one that began and waits again (resuming), is not listed
            queued = self._queued(dispatch["native_id"], attempt, owned=bool(state))
            if queued is not None:
                return {**dispatch, "native": {"done": False, "queued": True}, "queued": queued, "evidence": {
                    "native_task_id": dispatch["native_id"], "parent": parent["parent"]}, "handoff": "", "outcome": "", "decision": ""}
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
            # KiroCrew writes one line per run, at its start, under the conversation's key: a continued run (D46 P5a)
            # shares its first run's, so this run's line is the first from its start on (state.json's, this host's clock).
            key = state.get("conversation_key") or "subagent:" + ((dispatch.get("continued") or {}).get("conversation")
                                                                   or dispatch["native_id"])
            started = state.get("started")
            routes = []
            for line in self.routing_log.read_text().splitlines():
                try:
                    route = json.loads(line)
                except ValueError:
                    continue
                if isinstance(route, dict) and route.get("session_key") == key:
                    routes.append(route)
            if isinstance(started, (int, float)) and not isinstance(started, bool):
                routes = [r for r in routes if isinstance(r.get("time"), (int, float)) and r["time"] >= started - 1][:1]
            if routes:   # without a start time, the latest as before
                evidence.update(actual_backend=routes[-1].get("actual_backend"), routing=routes[-1])
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
        progress = {}
        if not native.get("done") and (state or native.get("turns") is not None):
            # What the watchdog judges a running task by (D39 §3.1): state.json's last write, its age on this host's clock
            # (the platform's may differ), and the /api/spawn row's turns and redacted last tool (state.json's is raw).
            updated = state.get("updated_at") if state else None
            signal = {"turns": native.get("turns", state.get("turns") if state else None),
                      "last_tool": str(native.get("last_tool") or "")[:200], "status": state.get("status") if state else None}
            if isinstance(updated, (int, float)) and not isinstance(updated, bool):
                signal.update(updated_at=updated, idle_seconds=max(0.0, time.time() - updated))
            progress = {"progress": signal}
        if dispatch.get("skills"):
            evidence["skills"] = dispatch["skills"]   # D50: the methods it was given, by content
        if native.get("done"):
            evidence["usage"] = self._usage(folder, dispatch, native, state)   # D46 P6a
        return {**dispatch, "native": native, "evidence": evidence, "handoff": handoff_text, **reports, **progress}

    def attempt_stop(self, body):
        """Stop one Attempt's native task (D39 §3.1, the watchdog): KiroCrew's own stop of a subagent. A task already gone
        (404) is stopped as far as the platform is concerned."""
        run, _ = self.binding(body["run_id"])
        attempt = body.get("attempt_id")
        require(isinstance(attempt, str) and re.fullmatch(r"attempt-[a-f0-9]{32}", attempt), "Attempt ID 无效")
        record = run / "attempts" / attempt / "dispatch.json"
        native_id = json.loads(record.read_text()).get("native_id") if record.is_file() else None
        if native_id:
            try:
                # KiroCrew answers once the process tree is reaped: a graceful reset of up to 30 s, then SIGKILL.
                answer = self.native("/api/spawn/" + quote(native_id, safe=""), None, "DELETE", timeout=150)
            except crew_api.GatewayError as exc:
                if exc.status != 404:
                    raise
                answer = {}
            if isinstance(answer, dict) and answer.get("cancelled") is False:   # it had finished on its own: keep its result
                return {"stopped": False, "finished": True}
        return {"stopped": True}

    def steer(self, body):
        """A note for a running task of this Session (D46 P5b), through KiroCrew's steer into the running turn. Only
        ``interrupt``: KiroCrew delivers a ``follow_up`` as a new run of the conversation that the platform would not track."""
        run, _ = self.binding(body["run_id"])
        attempt = body.get("attempt_id")
        require(isinstance(attempt, str) and re.fullmatch(r"attempt-[a-f0-9]{32}", attempt), "Attempt ID 无效")
        message, mode = body.get("message"), body.get("mode", "interrupt")
        require(isinstance(message, str) and 0 < len(message.strip()) and len(message) <= 4000, "补充说明须为 1–4000 字符")
        require(mode == "interrupt", "只支持 interrupt：补充说明送入正在运行的这一轮")
        record = run / "attempts" / attempt / "dispatch.json"
        require(record.is_file() and not record.is_symlink(), "该任务不在此会话中运行", 404, "not_found")
        native_id = json.loads(record.read_text()).get("native_id")
        require(native_id, "该任务尚未开始运行", 409, "not_running")
        try:
            answer = self.native("/api/spawn/" + quote(str(native_id), safe="") + "/steer", {"message": message, "mode": mode},
                                 "POST")
        except crew_api.GatewayError as exc:
            try:
                detail = str(json.loads(str(exc).partition(": ")[2]).get("error") or "")
            except (ValueError, AttributeError):
                detail = ""
            if exc.status == 503 or detail.startswith("shutting_down"):
                raise Error(503, "native_unavailable", "原生任务正在启动或结束，请稍后再补充说明") from None
            if exc.status in {404, 409}:
                raise Error(409, "not_running", "任务已不在运行，补充说明未送达") from None
            if detail.startswith("steer_unsupported"):   # a backend KiroCrew cannot steer (codex)
                raise Error(409, "steer_unsupported", "该任务的后端不支持补充说明") from None
            raise Error(502, "native_error", "KiroCrew 未接受补充说明：" + str(exc).splitlines()[0][:200]) from None
        return {"ok": True, "status": str(answer.get("status") or "steered")}

    def _release(self, run):
        """The Session's continuable native conversations released when it is deleted (D46 P5b): each continuation chain's
        first run; one that is busy or already gone is left as it is. Returns how many were released."""
        roots = set()
        for record in (run / "attempts").glob("attempt-*/dispatch.json"):
            try:
                value = json.loads(record.read_text())
            except (OSError, ValueError):
                continue
            chain = (value.get("continued") or {}).get("conversation")
            if chain or value.get("native_id"):
                roots.add(str(chain or value["native_id"]))
        released = 0
        for conversation in sorted(roots):
            try:
                self.native("/api/spawn/" + quote(conversation, safe="") + "/release", {}, "POST")
                released += 1
            except Exception:   # busy, gone or the Gateway away: the delete goes on
                continue
        return released

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
            try:
                self.native("/api/chat/slots/" + parent["slot"])
                gone = False
            except crew_api.GatewayError as exc:
                if exc.status != 404:
                    raise
                # KiroCrew no longer has the live slot (an incognito one is not restored after a Gateway restart, EC2
                # run-6d7c132b…): nothing runs under it and nothing can be exported; recorded as such, never forged.
                gone = True
            if gone:
                if action in {"close", "delete"}:
                    write(run / OBSERVATIONS["native_missing_observation"],
                          {"kind": "native_missing_observation", "native_code": "slot_not_found", "slot": parent["slot"],
                           "at": time.time()})
                    current.update(backup_kind="native_missing_observation", status="closed")
                    write(lifecycle, current)
            else:
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
            observed = OBSERVATIONS.get(current.get("backup_kind"))
            require((run / "native-session-backup.gz").is_file() or observed and (run / observed).is_file(), "缺少原生备份")
            try:
                receipt = self.native("/api/sessions/" + quote(parent["parent"], safe=""), method="DELETE")
            except crew_api.GatewayError as exc:
                if exc.status != 404:
                    raise
                receipt = {"ok": True}   # KiroCrew holds no history for it: already gone
            require(receipt.get("ok"), "原生删除未确认", 409, "native_unavailable")
            current["released"] = self._release(run)   # D46 P5b: its continuable conversations go with it
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
            code = failure.get("code")
            if exc.code != 400 or code not in REFUSED_EXPORTS:
                raise Error(409, "backup_unavailable", "原生备份未成功；会话尚未关闭") from None
            # Native notes alone are excluded from its export bundle, and an incognito / temporary Session is never
            # exported (KiroCrew produces nothing from it; owner 2026-10-17). Preserve an explicitly labelled observation;
            # never forge a kcsession bundle.
            kind = REFUSED_EXPORTS[code]
            write(run / OBSERVATIONS[kind], {"kind": kind, "native_export_code": code, "slot": parent["slot"],
                               **({"memory_mode": slot["memory_mode"]} if isinstance(slot, dict) and slot.get("memory_mode") else {}),
                               "observation": slot, "at": time.time()})
            return kind
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
                if self.command == "POST" and self.path == "/pull/bundle":   # D58: a local pull
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 < size <= 100_000, "请求体无效")
                    plan = worker.pull_plan(json.loads(self.rfile.read(size)))   # refusals answer as JSON errors
                    bundle = worker.pull_bundle(plan)
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-git-bundle")
                        self.send_header("Content-Length", str(bundle.stat().st_size))
                        for name in ("head", "base", "branch"):
                            self.send_header("X-Pull-" + name.title(), plan[name])
                        self.send_header("X-Pull-Full", "1" if plan["full"] else "0")
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
                if self.command == "POST" and self.path == "/sync":   # D58: local commits, as a git bundle
                    size = int(self.headers.get("Content-Length", "0"))
                    require(0 < size <= SYNC_LIMIT, f"同步的内容须为 1 字节到 {SYNC_LIMIT // 1048576} MB", 413, "sync_too_large")
                    body = {"run_id": self.headers.get("X-Octopus-Run"), "old": self.headers.get("X-Octopus-Old"),
                            "head": self.headers.get("X-Octopus-Head")}
                    result = worker.sync(body, self.rfile.read(size))
                elif self.command == "GET" and self.path == "/health":
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
                               "/verify/status": worker.verify_status, "/approvals": worker.approvals,
                               "/approvals/answer": worker.approvals_answer, "/attempt/stop": worker.attempt_stop,
                               "/knowledge/write": worker.knowledge_write, "/knowledge/forget": worker.knowledge_forget,
                               "/steer": worker.steer}
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
