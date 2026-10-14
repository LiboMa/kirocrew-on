"""A task's verify commands, run by the platform itself (D37 §5, G1): in the Session's project as the Worker's own user
(the execution user the Agent runs as, so no new privilege), one after another, stopping at the first failure. Each
command and all of them together are time-limited; a command that overruns has its whole process group ended. What
remains is the exit code, the time and the output's tail with credentials hidden."""
import os
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


class Checks:
    def __init__(self, path_prepend=(), per_command=600, total=1200):
        self.path_prepend, self.per_command, self.total = tuple(str(p) for p in path_prepend), per_command, total
        self.jobs, self.by_attempt, self.lock = {}, {}, threading.Lock()

    def start(self, run_id, attempt_id, project, commands):
        """The job running ``commands`` for this Attempt; asking again returns the same job."""
        with self.lock:
            now = time.time()
            for job, value in list(self.jobs.items()):
                if value["state"] == "done" and now - value["ended"] > KEEP:
                    self.by_attempt.pop((value["run_id"], value["attempt_id"]), None)
                    del self.jobs[job]
            if (run_id, attempt_id) in self.by_attempt:
                return {"job": self.by_attempt[(run_id, attempt_id)]}
            job = uuid.uuid4().hex
            self.jobs[job] = {"run_id": run_id, "attempt_id": attempt_id, "state": "running", "results": [],
                              "passed": None, "failed": None, "current": None, "cancelled": False, "ended": None}
            self.by_attempt[(run_id, attempt_id)] = job
        threading.Thread(target=self._run, args=(job, project, list(commands)), daemon=True,
                         name="octopus-checks").start()
        return {"job": job}

    def status(self, job):
        with self.lock:
            value = self.jobs.get(job)
            if value is None:   # unknown, or the Worker restarted since: the platform starts the checks again
                raise Error(404, "check_unknown", "检查任务不存在（Worker 可能已重启）")
            return {key: value[key] for key in ("state", "passed", "failed")} | {"results": [dict(r) for r in value["results"]]}

    def cancel(self, run_id):
        """End the Session's running checks (its stop, close or delete)."""
        with self.lock:
            running = [value for value in self.jobs.values() if value["run_id"] == run_id and value["state"] == "running"]
            for value in running:
                value["cancelled"] = True
        for value in running:
            if value["current"] is not None:
                self._end(value["current"])

    def _run(self, job, project, commands):
        value, started = self.jobs[job], time.monotonic()
        env = {**os.environ, "PATH": os.pathsep.join([*self.path_prepend, os.environ.get("PATH", "/usr/bin:/bin")])}
        passed, failed = True, None
        for index, command in enumerate(commands):
            left = self.total - (time.monotonic() - started)
            if value["cancelled"] or left <= 0:
                break
            result = self._one(value, project, command, env, min(self.per_command, left))
            with self.lock:
                value["results"].append(result)
            if result["exit"] != 0:
                passed, failed = False, index
                break
        with self.lock:
            if value["cancelled"] and passed:
                passed = False
            value.update(state="done", passed=passed and len(value["results"]) == len(commands), failed=failed,
                         current=None, ended=time.time())

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
            self._end(proc)
        reader.join(timeout=GRACE)
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
            try:
                proc.wait(timeout=GRACE)
                os.killpg(proc.pid, 0)   # the leader is gone; its children may not be
            except subprocess.TimeoutExpired:
                continue
            except (ProcessLookupError, PermissionError):
                break
