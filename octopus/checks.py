"""A task's verify commands, run by the platform itself (D37 §5, G1): in the Session's project as the Worker's own user
(the execution user the Agent runs as, so no new privilege), one after another, every one of them even after a failure
(D57 §13.3: a failing advisory check must not keep a required one from running). Each command and all of them together
are time-limited; a command that overruns has its whole process group ended, and what the total limit leaves is skipped.
What remains is the exit code, the time and the output's tail with credentials masked (D57 §3.3: what was masked is
listed in the result's ``findings``, never its text); ``unrunnable`` tells a failure the environment caused (a missing
command or module, no git identity) from one of what the command checks, and the Worker records its answer in each
failed result (D57 §5), judged on the output as the command printed it, so that masking never changes it. A commit a
check makes has the platform's identity only where git finds none of its own."""
import os
import re
import shutil
import signal
import subprocess
import tempfile
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
# platform's own commits carry, as a default (see _git_defaults).
_NAME, _EMAIL = (dict(setting.split("=", 1) for setting in sources.COMMITTER[1::2])[key] for key in ("user.name", "user.email"))
PYTHON_MODULE, NODE_MODULE = "缺少 Python 模块：{}", "缺少 Node 模块：{}"
# A path the check's own shell cannot find ("./run_tests.sh", "scripts/lint.sh": a script never written) is the project's
# error, never the environment's, whatever else the output says. With a PATH set (a check always has one) bash reports a
# PATH miss as "command not found", never as "No such file or directory": that, even for a bare name, is a file argument,
# a "source" target or a redirect that is not there ("bash run_tests.sh"). dash's bare "sh: 1: X: not found" is a PATH
# miss. And a 127 is a missing command only where no line says a path is missing (a script of the project's calling a
# helper it lacks: "scripts/run.sh: line 2: ./helper.sh: …"); a bare name there would also be a wrapper's PATH miss
# ("timeout: failed to run command 'pytest'").
MISSING_PATH = re.compile(r"^(?:\S*/)?(?:(?:ba|da)?sh: (?:line \d+: |\d+: )?(?:[^\s:]+: No such file or directory|"
                          r"[^\s:]*/[^\s:]*: (?:command )?not found)"
                          r"|zsh: (?:no such file or directory|command not found): \S*/\S*)\s*$", re.M)
SAYS_MISSING_PATH = re.compile(r"[^\s:]*/[^\s:]*: (?:No such file or directory|(?:command )?not found)\s*$", re.M)
# (pattern, reason) for a failure the environment caused, tried in order on the output's tail (D57 §5). A shell's own
# "not found" counts only at a line's start and for a bare command name: what a login profile the check's shell sources
# prints ("~/.bashrc: line 3: setopt: command not found") starts with that file's path, and says nothing about the check.
UNRUNNABLE = [
    (re.compile(r"^(?:\S*/)?(?:ba|da|z)?sh: (?:line \d+: |\d+: )?([^\s:/]+): (?:command )?not found\s*$", re.M), "命令不存在：{}"),
    (re.compile(r"^(?:\S*/)?zsh: command not found: ([^\s:/]+)\s*$", re.M), "命令不存在：{}"),
    # a missing #! interpreter, and a command make could not start; a script of the project's own ("./x.sh") is not
    (re.compile(r"^(?:\S*/)?env: [‘'\"]?([^\s:/‘’'\"]+)[’'\"]?: No such file or directory\s*$", re.M), "命令不存在：{}"),
    (re.compile(r"^make(?:\[\d+\])?: ([^\s:/]+): (?:No such file or directory|Command not found)\s*$", re.M), "命令不存在：{}"),
    # the interpreter's own ("python3: No module named pytest") or an import that failed; not a skip reason quoting one
    (re.compile(r"(?:^\S*python[\d.]*: |(?:ModuleNotFoundError|ImportError): )No module named '?([\w.]+)'?", re.M),
     PYTHON_MODULE),
    # a package, not a file of the project ('./routes', '/p/server.js') or a path alias ('@/utils', '~/lib', '#internal')
    (re.compile(r"Cannot find (?:module|package) '(?!@/|~/|#)([^'./][^']*)'"), NODE_MODULE),
    (re.compile(r"(?:Committer|Author) identity unknown|empty ident name|unable to auto-detect email address"),
     "检查环境没有 git 提交者身份"),
    (re.compile(r"Cannot connect to the Docker daemon"), "Docker 服务不可用"),
]
# A missing module the project has itself, at its root or under src/ (an Agent's wrong import, a file it did not write),
# is the project's error, not the environment's: what such a module's file may end in.
OWN_MODULES = {PYTHON_MODULE: (".py",), NODE_MODULE: (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".json")}


def unrunnable(result, project=None, output=None):
    """Why a check failed because of where it ran rather than what it checks (D57 §5), in words for the user; None when it
    passed, failed on its own, or never came to an exit code (a timeout, a stop, the total limit, a runner error). A
    script path that is not there is the project's error; with ``project``, so is a module the project has itself;
    without it, what the Worker recorded with the project at hand is the answer (results of an older Worker are judged
    from their stored tail). ``output``: the tail of the output as the command printed it, which the Worker judges (the
    stored tail is masked, and masking must never change the answer, D57 §3.3)."""
    if "unrunnable" in result:
        return result["unrunnable"]
    code = result.get("exit")
    if code in (0, None):
        return None
    tail = str(result.get("tail") or "") if output is None else output
    if MISSING_PATH.search(tail):
        return None
    for pattern, reason in UNRUNNABLE:
        names = [found.groups() for found in pattern.finditer(tail)]
        if not names:
            continue
        if project is not None and reason in OWN_MODULES and any(_own(project, reason, name) for name, in names):
            return None
        return reason.format(*names[0])
    return "命令不存在（退出码 127）" if code == 127 and not SAYS_MISSING_PATH.search(tail) else None


def _own(project, reason, name):
    """Whether the project has the top level of the module ``name`` (myapp of myapp.models, lodash of lodash/fp, @scope/x
    of @scope/x/y) at its root or under src/, as a directory or a module file."""
    parts = name.split(".") if reason == PYTHON_MODULE else name.split("/")
    top = os.path.join(*parts[:2]) if name.startswith("@") and len(parts) > 1 else parts[0]
    return any(os.path.isdir(os.path.join(base, top)) or any(os.path.isfile(os.path.join(base, top + end)) for end in OWN_MODULES[reason])
               for base in (str(project), os.path.join(str(project), "src")))


def _quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _git_defaults(env):
    """The checks' global git config (D57 §5): the platform's identity first, then the user's own global config as git
    finds it ($XDG_CONFIG_HOME/git/config or ~/.config/git/config, then ~/.gitconfig), so the user's, the repository's and
    a ``-c`` identity all win over the default (GIT_AUTHOR_NAME and the like would win over every one of them). A file of
    its own outside the project, removed when the job ends; None when the caller chose a global config or it cannot be
    written. (A git older than 2.32 ignores it: no default, as before.)"""
    if "GIT_CONFIG_GLOBAL" in env:
        return None
    home = env.get("HOME") or None
    xdg = env.get("XDG_CONFIG_HOME") or (home and os.path.join(home, ".config"))
    included = [path for path in (xdg and os.path.join(xdg, "git", "config"), home and os.path.join(home, ".gitconfig"))
                if path and "\n" not in path]
    lines = ["[user]", f"\tname = {_quoted(_NAME)}", f"\temail = {_quoted(_EMAIL)}",
             *(["[include]", *(f"\tpath = {_quoted(path)}" for path in included)] if included else [])]
    try:
        handle, path = tempfile.mkstemp(prefix="octopus-checks-", suffix=".gitconfig")
    except OSError:
        return None
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write("\n".join(lines) + "\n")
    except OSError:
        _remove(path)
        return None
    return path


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def masked_tail(raw, dropped=None):
    """The last TAIL bytes of a command's output (``raw``: the bytes kept of it) as they are shown and stored, every
    credential masked (D57 §3.3), and what was masked: ``[{kind, line, masked, hash}]``, ``line`` counted in the tail.
    Masked before it is cut, so a credential across the cut is masked whole; and nothing from before the cut is shown,
    so the masks' shorter text never makes room for the head of ``raw``, which may be the rest of a credential whose
    start was already dropped and so is not recognised. A private key whose BEGIN is out of view — dropped with the
    output's head (``dropped``: whether any was; by default, whether ``raw`` is as long as what is kept of it) or before
    the cut and not yet ended — is masked from the cut through its END."""
    cut = max(len(raw) - TAIL, 0)
    dropped = len(raw) >= 2 * TAIL if dropped is None else dropped
    before = raw.count(b"\n", 0, cut)
    pieces, at, found = [], cut, []
    for hit in sources.find_secrets(raw, cut=cut, dropped=dropped):
        if hit["end"] <= cut:
            continue   # in the part kept only so that one across the cut is still recognised
        pieces += [raw[at:hit["start"]], hit["masked"].encode()]   # nothing between for the one across the cut
        at = hit["end"]
        found.append({"kind": hit["kind"], "line": max(hit["line"] - before, 1), "masked": hit["masked"],
                      "hash": hit["hash"]})
    tail = sources.scrub(b"".join(pieces + [raw[at:]]).decode(errors="replace"))
    return tail.encode()[-TAIL:].decode(errors="ignore"), found


def _shown(reason):
    """An ``unrunnable`` reason as it is stored and shown: judged on the output as printed, so masked and scrubbed as the
    tail is (a module name may be a credential)."""
    return None if reason is None else sources.scrub(sources.mask_text(reason)[0])


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
        gitconfig = _git_defaults(env)
        if gitconfig:
            env["GIT_CONFIG_GLOBAL"] = gitconfig
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
                        result, output = self._one(value, project, command, env, min(self.per_command, left))
                    except Exception as exc:   # e.g. the project is gone: a failed check, never a job left running
                        result = {"command": command, "exit": None, "seconds": 0.0,
                                  "tail": f"无法运行检查命令（{type(exc).__name__}）"}
                    else:   # judged here, where the project is at hand, and on the output before it was masked
                        if result["exit"] not in (0, None):
                            result["unrunnable"] = _shown(unrunnable(result, project, output))
                with self.lock:
                    value["results"].append(result)
                passed = passed and result["exit"] == 0
                if result["exit"] != 0 and "skipped" not in result:
                    failed = index if failed is None else failed
                    if stop:
                        break
        finally:
            if gitconfig:
                _remove(gitconfig)
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
        """The command's result, and the tail of its output as it printed it (never stored: the Worker judges it)."""
        began = time.monotonic()
        proc = subprocess.Popen(["bash", "-lc", command], cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True, env=env)
        value["current"] = proc
        output, dropped = bytearray(), False

        def read():
            nonlocal dropped
            for chunk in iter(lambda: proc.stdout.read(8192), b""):
                output.extend(chunk)
                if len(output) > 2 * TAIL:   # enough to mask a credential across the tail's edge, little to hold
                    del output[:-2 * TAIL]
                    dropped = True
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
        raw = bytes(output)
        tail, findings = masked_tail(raw, dropped)
        result = {"command": command, "exit": None if timeout or value["cancelled"] else proc.returncode,
                  "seconds": round(time.monotonic() - began, 1), "tail": tail}
        if findings:
            result["findings"] = findings
        if timeout:
            result["timeout"] = True
        elif value["cancelled"]:
            result["stopped"] = True
        return result, raw[-TAIL:].decode(errors="replace")

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
