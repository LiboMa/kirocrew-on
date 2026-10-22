"""A task's verify commands, run by the platform itself (D37 §5, G1): in the Session's project as the Worker's own user
(the execution user the Agent runs as, so no new privilege), one after another, every one of them even after a failure
(D57 §13.3: a failing advisory check must not keep a required one from running). Each command and all of them together
are time-limited; a command that overruns has its whole process group ended, and what the total limit leaves is skipped.
What remains is the exit code, the time and the output's tail with credentials hidden; ``unrunnable`` tells a failure
the environment caused (a missing command or module, no git identity) from one of what the command checks (D57 §5)."""
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid

from . import sources
from .security import Error

TAIL = 4096
GRACE = 5
KEEP = 3600   # a finished job is answered for an hour
# Commands a planned verify commonly names; which of them this host has is told to the planners (a verify that names a
# missing one fails with exit code 127 and no recovery can change the command).
COMMON = ("python3", "python", "pip3", "pip", "pytest", "uv", "node", "npm", "npx", "pnpm", "yarn", "go", "cargo",
          "java", "mvn", "gradle", "make", "ruby", "bundle", "php", "dotnet", "deno", "bun", "git", "docker")
# A check that makes a commit (git am, git commit) needs an identity the host may not have (D57 §5): the one the
# platform's own commits carry, unless the Worker's environment gives one.
_NAME, _EMAIL = (dict(setting.split("=", 1) for setting in sources.COMMITTER[1::2])[key] for key in ("user.name", "user.email"))
IDENTITY = {"GIT_AUTHOR_NAME": _NAME, "GIT_AUTHOR_EMAIL": _EMAIL, "GIT_COMMITTER_NAME": _NAME, "GIT_COMMITTER_EMAIL": _EMAIL}
# (pattern, reason) for a failure the environment caused, tried in order on the output's tail (D57 §5). A shell's own
# "not found" counts only at a line's start: what a login profile the check's shell sources prints ("~/.bashrc: line 3:
# setopt: command not found") starts with that file's path, and says nothing about the check.
UNRUNNABLE = [
    (re.compile(r"^(?:\S*/)?(?:ba|da|z)?sh: (?:line \d+: |\d+: )?([^\s:]+): (?:command )?not found\s*$", re.M), "命令不存在：{}"),
    (re.compile(r"^(?:\S*/)?zsh: command not found: ([^\s:]+)\s*$", re.M), "命令不存在：{}"),
    # a missing #! interpreter, and a command make could not start; a script of the project's own ("./x.sh") is not
    (re.compile(r"^(?:\S*/)?env: [‘'\"]?([^\s:‘’'\"]+)[’'\"]?: No such file or directory\s*$", re.M), "命令不存在：{}"),
    (re.compile(r"^make(?:\[\d+\])?: ([^\s:/]+): (?:No such file or directory|Command not found)\s*$", re.M), "命令不存在：{}"),
    # the interpreter's own ("python3: No module named pytest") or an import that failed; not a skip reason quoting one
    (re.compile(r"(?:^\S*python[\d.]*: |(?:ModuleNotFoundError|ImportError): )No module named '?([\w.]+)'?", re.M),
     "缺少 Python 模块：{}"),
    # a package, not a file of the project ('./routes', '/p/server.js') or a path alias ('@/utils', '~/lib', '#internal')
    (re.compile(r"Cannot find (?:module|package) '(?!@/|~/|#)([^'./][^']*)'"), "缺少 Node 模块：{}"),
    (re.compile(r"(?:Committer|Author) identity unknown|empty ident name|unable to auto-detect email address"),
     "检查环境没有 git 提交者身份"),
    (re.compile(r"Cannot connect to the Docker daemon"), "Docker 服务不可用"),
]


def unrunnable(result):
    """Why a check failed because of where it ran rather than what it checks (D57 §5), in words for the user; None when it
    passed, failed on its own, or never came to an exit code (a timeout, a stop, the total limit, a runner error)."""
    code = result.get("exit")
    if code in (0, None):
        return None
    tail = str(result.get("tail") or "")
    for pattern, reason in UNRUNNABLE:
        found = pattern.search(tail)
        if found:
            return reason.format(*found.groups())
    return "命令不存在（退出码 127）" if code == 127 else None


class Checks:
    def __init__(self, path_prepend=(), per_command=600, total=1200):
        self.path_prepend, self.per_command, self.total = tuple(str(p) for p in path_prepend), per_command, total
        self.jobs, self.by_attempt, self.lock = {}, {}, threading.Lock()

    def commands(self):
        """Which common commands the checks find on their search path."""
        search = os.pathsep.join([*self.path_prepend, os.environ.get("PATH", "/usr/bin:/bin")])
        found = {command: bool(shutil.which(command, path=search)) for command in COMMON}
        return {"available": [c for c in COMMON if found[c]], "missing": [c for c in COMMON if not found[c]]}

    def start(self, run_id, attempt_id, project, commands, kind="verify", around=None):
        """The job running ``commands`` for this Attempt; asking again returns the same job. ``kind`` tells an Attempt's
        jobs apart (its checks, its baseline run); ``around`` is (prepare, restore): run before the commands, and after
        them whatever happened (D37 §11.1)."""
        with self.lock:
            now = time.time()
            for job, value in list(self.jobs.items()):
                if value["state"] == "done" and now - value["ended"] > KEEP:
                    self.by_attempt.pop((value["run_id"], value["attempt_id"], value["kind"]), None)
                    del self.jobs[job]
            if (run_id, attempt_id, kind) in self.by_attempt:
                return {"job": self.by_attempt[(run_id, attempt_id, kind)]}
            job = uuid.uuid4().hex
            self.jobs[job] = {"run_id": run_id, "attempt_id": attempt_id, "kind": kind, "state": "running", "results": [],
                              "passed": None, "failed": None, "current": None, "cancelled": False, "ended": None,
                              "error": None, "artifacts": []}
            self.by_attempt[(run_id, attempt_id, kind)] = job
        threading.Thread(target=self._run, args=(job, project, list(commands), around), daemon=True,
                         name="octopus-checks").start()
        return {"job": job}

    def status(self, job):
        with self.lock:
            value = self.jobs.get(job)
            if value is None:   # unknown, or the Worker restarted since: the platform starts the checks again
                raise Error(404, "check_unknown", "检查任务不存在（Worker 可能已重启）")
            return {key: value[key] for key in ("state", "passed", "failed", "error", "artifacts")} | \
                {"results": [dict(r) for r in value["results"]]}

    def running(self, run_id, kind):
        """Whether a job of ``kind`` is running for the Session (its baseline run restores the workspace itself)."""
        with self.lock:
            return any(v["run_id"] == run_id and v["kind"] == kind and v["state"] == "running" for v in self.jobs.values())

    def cancel(self, run_id):
        """End the Session's running checks (its stop, close or delete)."""
        with self.lock:
            running = [value for value in self.jobs.values() if value["run_id"] == run_id and value["state"] == "running"]
            for value in running:
                value["cancelled"] = True
        for value in running:
            if value["current"] is not None:
                self._end(value["current"])

    def _run(self, job, project, commands, around=None):
        value, started = self.jobs[job], time.monotonic()
        # The Worker's own modules (its unit sets PYTHONPATH to the release) are not the project's (as apps.py).
        env = {key: item for key, item in os.environ.items() if key != "PYTHONPATH"}
        env["PATH"] = os.pathsep.join([*self.path_prepend, os.environ.get("PATH", "/usr/bin:/bin")])
        for key, item in IDENTITY.items():
            env.setdefault(key, item)
        # A baseline run asks only whether the commands fail before the change: its first failure answers that, and the
        # project stays on the baseline (previews, downloads and publishing wait) until the run ends.
        stop = value["kind"] == "baseline"
        passed, failed = True, None
        prepare, restore = around or (None, None)
        try:
            if prepare:
                try:
                    prepare()
                except Exception as exc:   # never judged as a failing run: the platform skips the gate
                    with self.lock:
                        value["error"] = "无法切换到基线：" + sources.scrub(exc)
                    passed, commands = False, []
            for index, command in enumerate(commands):
                left = self.total - (time.monotonic() - started)
                if value["cancelled"]:
                    break
                if left <= 0:   # never run, and said so; not passed, but not a failure of its own (``failed`` names one that ran)
                    result = {"command": command, "exit": None, "seconds": 0.0, "tail": "", "skipped": "total_timeout"}
                else:
                    try:
                        result = self._one(value, project, command, env, min(self.per_command, left))
                    except Exception as exc:   # e.g. the project is gone: a failed check, never a job left running
                        result = {"command": command, "exit": None, "seconds": 0.0,
                                  "tail": f"无法运行检查命令（{type(exc).__name__}）"}
                with self.lock:
                    value["results"].append(result)
                passed = passed and result["exit"] == 0
                if result["exit"] != 0 and "skipped" not in result:
                    failed = index if failed is None else failed
                    if stop:
                        break
        finally:
            if restore:
                try:
                    restore()
                except Exception as exc:
                    with self.lock:
                        value["error"] = (value["error"] + "；" if value["error"] else "") + "恢复失败：" + sources.scrub(exc)
            try:   # what the commands left (.coverage, reports): the platform's, not the next task's deliverables (§11.2)
                artifacts = sources.dirty_paths(project)
            except Exception:
                artifacts = []
            with self.lock:
                value["artifacts"] = artifacts
                value.update(state="done", passed=passed and not value["cancelled"] and len(value["results"]) == len(commands),
                             failed=failed, current=None, ended=time.time())

    def _one(self, value, project, command, env, limit):
        began = time.monotonic()
        proc = subprocess.Popen(["bash", "-lc", command], cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True, env=env)
        value["current"] = proc
        output = bytearray()

        def read():
            for chunk in iter(lambda: proc.stdout.read(8192), b""):
                output.extend(chunk)
                del output[:-2 * TAIL]   # enough to hide a credential cut at the edge, little to hold
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        timeout = False
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timeout = True
        # Whatever the command left behind in its process group (a server it started, "sleep &") ends with it: it would
        # hold the output pipe open and keep its port.
        self._end(proc)
        reader.join(timeout=GRACE)
        if not reader.is_alive():
            proc.stdout.close()
        value["current"] = None
        hidden = sources.SUSPICIOUS.sub(sources.HIDDEN.encode(), bytes(output))
        tail = sources.scrub(hidden[-TAIL:].decode(errors="replace"))
        result = {"command": command, "exit": None if timeout or value["cancelled"] else proc.returncode,
                  "seconds": round(time.monotonic() - began, 1), "tail": tail.encode()[-TAIL:].decode(errors="ignore")}
        if timeout:
            result["timeout"] = True
        elif value["cancelled"]:
            result["stopped"] = True
        return result

    @staticmethod
    def _end(proc):
        """SIGTERM the command's process group, SIGKILL what is left after a grace period, and reap the leader."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            deadline = time.monotonic() + GRACE
            while time.monotonic() < deadline:
                proc.poll()
                try:
                    os.killpg(proc.pid, 0)
                except (ProcessLookupError, PermissionError):
                    deadline = None
                    break
                time.sleep(0.05)
            if deadline is None:
                break
        try:
            proc.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            pass
