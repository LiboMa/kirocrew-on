"""A session's application running in its execution environment for the remote preview (D36 §5.1).

How it starts comes from the project's .octopus/preview.json or what preview_spec detects. Each process runs as the
environment's OS user, inside the project, in its own process group; the entry listens on a port from 47000–47999.
The Worker owns the processes (they end with it); the platform records and reaches them (octopus/remote_preview.py).
"""
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import preview_spec
from .security import Error, require

PORTS = range(47000, 48000)
LIMIT = 3
LOG_LINES = 200
LIVE = {"installing", "starting", "running"}
STOP_GRACE = 5.0
STATIC = Path(__file__).with_name("static_site.py")
# Install again when these change (a continuation added a dependency), not only when the declaration does.
LOCKFILES = ("package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt",
             "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock")


def tail(path, lines=LOG_LINES):
    """The last ``lines`` lines of a log, read from its end only."""
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            size = log.tell()
            log.seek(max(0, size - 65536))
            text = log.read().decode(errors="replace").splitlines()
    except OSError:
        return []
    return (text[1:] if size > 65536 else text)[-lines:]


class App:
    """One Session's preview: its processes, ports and state."""

    def __init__(self, run_id, project, folder, spec, source, ports):
        self.run_id, self.project, self.folder, self.spec, self.source = run_id, project, folder, spec, source
        self.port, self.ports = ports[0], ports
        self.status = "installing" if spec["install"] else "starting"
        self.error, self.started = None, time.time()
        self.processes, self.current = [], None   # [(name, Popen)]; the install step running now
        self.stopping, self.lock = threading.Event(), threading.Lock()

    def view(self, instance):
        return {"status": self.status, "port": self.port, "path": self.spec["ready"]["path"], "error": self.error,
                "started": self.started, "source": self.source, "instance": instance}


class Apps:
    def __init__(self, path_prepend=(), limit=LIMIT, ports=PORTS, install_timeout=900, poll=1.0):
        self.path_prepend, self.limit, self.ports = tuple(str(p) for p in path_prepend), limit, ports
        self.install_timeout, self.poll = install_timeout, poll
        self.apps, self.lock = {}, threading.Lock()
        # Every answer names this process: after a Worker restart the platform knows its records are stale.
        self.instance = uuid.uuid4().hex

    def running(self):
        with self.lock:
            return [rid for rid, app in self.apps.items() if app.status in LIVE]

    def start(self, run_id, project, folder):
        with self.lock:
            app = self.apps.get(run_id)
            if app and app.status in LIVE:
                return app.view(self.instance)
            # The project as it is: dependencies an agent already installed are used, not reinstalled over (npm ci
            # would also refuse a lockfile the agent did not update). Declared install steps rerun when a lockfile
            # changes (the install marker).
            spec, source, problem = preview_spec.resolve(project)
            require(spec, problem or "未找到启动方式", 409, "app_unavailable")
            if not spec.get("static"):
                require(preview_spec.entry(spec)["port"] == "$PORT",
                        f'远程预览要求入口进程的 port 为 "$PORT"（{preview_spec.DECLARATION}）', 409, "app_entry_port")
            live = [a for a in self.apps.values() if a.status in LIVE]
            require(len(live) < self.limit, f"此执行环境已有 {self.limit} 个远程预览在运行，请先停止其中一个",
                    409, "app_limit")
            # The entry first, then one for each other process that asks for $PORT.
            wanted = 1 + sum(p["port"] == "$PORT" and not p["entry"] for p in spec["processes"])
            ports = self._free({port for a in live for port in a.ports}, wanted)
            app = self.apps[run_id] = App(run_id, project, folder, spec, source, ports)
        threading.Thread(target=self._run, args=(app,), daemon=True, name="octopus-app-" + run_id[4:12]).start()
        return app.view(self.instance)

    def stop(self, run_id):
        with self.lock:
            app = self.apps.get(run_id)
        if app:
            app.stopping.set()
            self._kill(app)
            with app.lock:
                app.status = "stopped"
        return {"status": "stopped"}

    def stop_all(self):
        for run_id in list(self.apps):
            self.stop(run_id)

    def status(self, run_id, folder):
        with self.lock:
            app = self.apps.get(run_id)
        if app and app.status == "running":
            exited = next(((name, proc.returncode) for name, proc in app.processes if proc.poll() is not None), None)
            if exited:
                self._fail(app, f"进程 {exited[0]} 已退出（代码 {exited[1]}）")
        view = app.view(self.instance) if app else {"status": "stopped", "port": None, "path": None, "error": None,
                                                    "started": None, "source": None, "instance": self.instance}
        view["log"] = tail(folder / "app.log")
        return view

    def _free(self, taken, count):
        found = []
        for port in self.ports:
            if port in taken:
                continue
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
            found.append(port)
            if len(found) == count:
                return found
        raise Error(409, "app_no_port", f"{self.ports.start}–{self.ports.stop - 1} 没有空闲端口，请先停止其他预览")

    def _env(self, port):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}   # the platform's own path is not the app's
        env.update(PATH=os.pathsep.join([*self.path_prepend, os.environ.get("PATH", "/usr/bin:/bin")]),
                   PORT=str(port), HOST="127.0.0.1", BROWSER="none")
        return env

    def _spawn(self, app, argv, cwd, port, log):
        return subprocess.Popen(argv, cwd=cwd, env=self._env(port), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)

    def _run(self, app):
        try:
            self._launch(app)
        except Exception as exc:   # nothing may leave a preview starting forever with its quota held
            self._fail(app, f"启动失败：{type(exc).__name__}: {exc}")

    def _launch(self, app):
        path = app.folder / "app.log"
        path.write_text("")
        try:
            with open(path, "ab", buffering=0) as log:
                note = lambda text: log.write((text + "\n").encode())
                if app.spec["install"] and not self._install(app, log, note):
                    return
                with app.lock:
                    if app.stopping.is_set():
                        return
                    app.status = "starting"
                spare = iter(app.ports[1:])
                launches = []
                for process in app.spec["processes"]:
                    port = app.port if process["entry"] else next(spare) if process["port"] == "$PORT" else process["port"]
                    launches.append((process["name"], f"== 启动 {process['name']}：{process['command']}（端口 {port}）",
                                     ["/bin/sh", "-c", process["command"]], app.project / process["cwd"], port))
                if app.spec.get("static"):
                    launches.append(("static", f"== 启动静态站点：{app.spec['static']}（端口 {app.port}）",
                                     [sys.executable, "-I", str(STATIC), str(app.port), str(app.project / app.spec["static"])],
                                     app.project, app.port))
                for name, line, argv, cwd, port in launches:
                    note(line)
                    with app.lock:   # a stop in between must see every process it has to end
                        if app.stopping.is_set():
                            return
                        app.processes.append((name, self._spawn(app, argv, cwd, port, log)))
        except OSError as exc:
            return self._fail(app, f"启动失败：{exc}")
        self._wait_ready(app)

    def _install(self, app, log, note):
        """The install steps, once per declaration (a marker in the run's folder); False when the preview ended."""
        marker = app.folder / "app-install.json"
        digest = hashlib.sha256(json.dumps(app.spec["install"], sort_keys=True).encode())
        for folder in sorted({step["cwd"] for step in app.spec["install"]}):
            for name in LOCKFILES:
                lock = app.project / folder / name
                if lock.is_file():
                    digest.update(f"{folder}/{name}:".encode() + hashlib.sha256(lock.read_bytes()).digest())
        digest = digest.hexdigest()
        if marker.is_file() and marker.read_text() == digest:
            return True
        for step in app.spec["install"]:
            note(f"== 安装：{step['command']}")
            with app.lock:
                if app.stopping.is_set():
                    return False
                step_process = app.current = self._spawn(app, ["/bin/sh", "-c", step["command"]],
                                                         app.project / step["cwd"], app.port, log)
            deadline = time.time() + self.install_timeout
            while step_process.poll() is None and not app.stopping.is_set() and time.time() < deadline:
                app.stopping.wait(self.poll)
            code = step_process.poll()
            if app.stopping.is_set():
                return False
            if code is None:
                self._fail(app, f"安装步骤超时（{self.install_timeout} 秒）：{step['command']}")
                return False
            if code != 0:
                self._fail(app, f"安装步骤失败：{step['command']}（代码 {code}）")
                return False
        marker.write_text(digest)
        return True

    def _wait_ready(self, app):
        ready = app.spec["ready"]
        deadline = time.time() + ready["timeout"]
        while not app.stopping.is_set():
            exited = next(((name, proc.returncode) for name, proc in app.processes if proc.poll() is not None), None)
            if exited:
                return self._fail(app, f"进程 {exited[0]} 已退出（代码 {exited[1]}）")
            if self._answers(app.port, ready["path"]):
                with app.lock:
                    if not app.stopping.is_set():
                        app.status = "running"
                return
            if time.time() > deadline:
                return self._fail(app, f"{ready['timeout']} 秒内未就绪（GET {ready['path']}）：检查日志与 "
                                       f"{preview_spec.DECLARATION} 的 ready")
            app.stopping.wait(self.poll)

    @staticmethod
    def _answers(port, path):
        """Any HTTP answer counts as ready: a 404 at / still means the server is listening."""
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", path, headers={"Host": f"localhost:{port}"})
            connection.getresponse().close()
            return True
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def _fail(self, app, error):
        with app.lock:
            if app.stopping.is_set() or app.status not in LIVE:
                return
            app.status, app.error = "failed", error
        app.stopping.set()
        self._kill(app)

    @staticmethod
    def _kill(app):
        """End every process group (SIGTERM, then SIGKILL after a grace period) and reap the leaders."""
        with app.lock:
            procs = [proc for _, proc in app.processes] + ([app.current] if app.current else [])
        for proc in procs:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.time() + STOP_GRACE
        for proc in procs:
            while True:
                proc.poll()
                try:
                    os.killpg(proc.pid, 0)
                except (ProcessLookupError, PermissionError):
                    # macOS reports a group whose leader is only a zombie as gone: reap it, or it stays defunct.
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                    break
                if time.time() > deadline:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    break
                time.sleep(0.05)
