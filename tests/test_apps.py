"""D36 §5.1: running a session's application in its execution environment for the remote preview — real processes,
one process group each, an entry port from the preview range, a ready check, at most a few per environment."""
import json
import os
from pathlib import Path
import random
import shlex
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from octopus.apps import Apps, LIMIT, PORTS
from octopus.security import Error

PY = shlex.quote(sys.executable)
SERVE = f"{PY} -m http.server $PORT --bind 127.0.0.1"
RID, OTHER, THIRD = "run-" + "a" * 32, "run-" + "b" * 32, "run-" + "c" * 32


def free_block(size=40):
    """A block of ports nothing listens on, so parallel test runs and local services do not collide."""
    while True:
        start = random.randrange(20000, 60000 - size)
        try:
            for port in (start, start + size - 1):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            return range(start, start + size)
        except OSError:
            continue


def gone(pid, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


class AppsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ports = free_block()
        self.apps = Apps(ports=self.ports, poll=0.05)
        self.addCleanup(self.apps.stop_all)

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, rid=RID, declaration=None, files=None):
        folder = self.root / rid
        project = folder / "workspace/project"
        project.mkdir(parents=True, exist_ok=True)
        if declaration is not None:
            (project / ".octopus").mkdir(exist_ok=True)
            (project / ".octopus/preview.json").write_text(json.dumps(declaration))
        for name, text in (files or {}).items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text)
        return project, folder

    def start(self, rid=RID, **kwargs):
        project, folder = self.project(rid, **kwargs)
        return self.apps.start(rid, project, folder), project, folder

    def settle(self, rid, folder, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            view = self.apps.status(rid, folder)
            if view["status"] not in {"installing", "starting"}:
                return view
            time.sleep(0.05)
        self.fail(f"{rid} did not settle: {view}")

    def get(self, port, path="/"):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.read().decode()

    def test_a_declared_process_runs_on_a_port_from_the_range(self):
        first, project, folder = self.start(declaration={"processes": [{"command": SERVE, "port": "$PORT"}]}, files={"hello.txt": "hi"})
        self.assertIn(first["status"], {"starting", "installing"})
        self.assertIn(first["port"], self.ports)
        self.assertEqual((first["path"], first["source"]), ("/", "declared"))
        view = self.settle(RID, folder)
        self.assertEqual(view["status"], "running", view)
        self.assertEqual(self.get(view["port"], "/hello.txt"), "hi")
        self.assertTrue(any(line.startswith("== 启动") for line in view["log"]), view["log"])
        self.assertEqual(self.apps.running(), [RID])
        self.assertEqual(self.apps.start(RID, project, folder)["port"], view["port"])   # running already: no second copy

    def test_a_backend_and_an_entry_frontend(self):
        backend = next(p for p in free_block(5))
        declaration = {"processes": [
            {"name": "backend", "cwd": "api", "command": f"{PY} -m http.server {backend} --bind 127.0.0.1", "port": backend},
            {"name": "frontend", "cwd": "web", "command": SERVE, "port": "$PORT", "entry": True}]}
        _, _, folder = self.start(declaration=declaration, files={"api/x.json": "{}", "web/index.html": "web"})
        view = self.settle(RID, folder)
        self.assertEqual(view["status"], "running", view)
        self.assertEqual(self.get(view["port"]), "web")
        self.assertEqual(self.get(backend, "/x.json"), "{}")

    def test_install_runs_once(self):
        declaration = {"processes": [{"command": SERVE, "port": "$PORT"}], "install": [{"command": "echo done >> installed.txt"}]}
        first, project, folder = self.start(declaration=declaration)
        self.assertEqual(first["status"], "installing")
        self.assertEqual(self.settle(RID, folder)["status"], "running")
        self.apps.stop(RID)
        self.apps.start(RID, project, folder)
        view = self.settle(RID, folder)
        self.assertEqual(view["status"], "running")
        self.assertEqual((project / "installed.txt").read_text(), "done\n")
        self.assertFalse(any(line.startswith("== 安装") for line in view["log"]), view["log"])

    def test_a_static_site_is_served(self):
        _, _, folder = self.start(files={"index.html": "<h1>clock</h1>"})
        view = self.settle(RID, folder)
        self.assertEqual((view["status"], self.get(view["port"])), ("running", "<h1>clock</h1>"))

    def test_stop_ends_the_whole_process_tree(self):
        command = f"sh -c '{PY} -m http.server $PORT --bind 127.0.0.1 & echo $! > child.pid; wait' & wait"
        _, project, folder = self.start(declaration={"processes": [{"command": command, "port": "$PORT"}]})
        view = self.settle(RID, folder)
        self.assertEqual(view["status"], "running", view)
        child = int((project / "child.pid").read_text())
        self.assertEqual(self.apps.stop(RID), {"status": "stopped"})
        self.assertTrue(gone(child), "the grandchild still runs")
        with socket.socket() as probe:   # released: nothing listens (SO_REUSEADDR only passes TIME_WAIT, as servers do)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", view["port"]))
            probe.listen()
        self.assertEqual(self.apps.status(RID, folder)["status"], "stopped")
        self.assertEqual(self.apps.running(), [])

    def test_failures_leave_nothing_running(self):
        apps = Apps(ports=self.ports, poll=0.05, limit=1)
        self.addCleanup(apps.stop_all)
        for declaration, code in (({"processes": [{"command": SERVE, "port": 3000}]}, "app_entry_port"),
                                  ({"processes": [{"command": SERVE, "cwd": "../x", "port": "$PORT"}]}, "app_unavailable")):
            project, folder = self.project(declaration=declaration)
            with self.assertRaises(Error) as caught:
                apps.start(RID, project, folder)
            self.assertEqual((caught.exception.status, caught.exception.code), (409, code))
            self.assertIn(".octopus/preview.json", str(caught.exception))
        for rid, declaration, words in (
                (RID, {"processes": [{"command": "exit 3", "port": "$PORT"}]}, "已退出（代码 3）"),
                (OTHER, {"processes": [{"command": "echo $$ > sleeper.pid; exec sleep 30", "port": "$PORT"}], "ready": {"timeout": 1}}, "未就绪"),
                (THIRD, {"processes": [{"command": SERVE, "port": "$PORT"}], "install": [{"command": "false"}]}, "安装步骤失败")):
            project, folder = self.project(rid, declaration=declaration)
            apps.start(rid, project, folder)   # limit=1: a failed preview does not hold the quota
            view = self.settle_with(apps, rid, folder)
            self.assertEqual(view["status"], "failed", view)
            self.assertIn(words, view["error"])
            if (project / "sleeper.pid").exists():
                self.assertTrue(gone(int((project / "sleeper.pid").read_text())), "the process that never got ready still runs")
        self.assertEqual(apps.running(), [])

    def settle_with(self, apps, rid, folder):
        deadline = time.time() + 15
        while (view := apps.status(rid, folder))["status"] in {"installing", "starting"}:
            self.assertLess(time.time(), deadline)
            time.sleep(0.05)
        return view

    def test_an_environment_runs_a_limited_number(self):
        self.assertEqual((LIMIT, PORTS), (3, range(47000, 48000)))
        apps = Apps(ports=self.ports, poll=0.05, limit=2)
        self.addCleanup(apps.stop_all)
        for rid in (RID, OTHER):
            project, folder = self.project(rid, declaration={"processes": [{"command": SERVE, "port": "$PORT"}]})
            apps.start(rid, project, folder)
        project, folder = self.project(THIRD, declaration={"processes": [{"command": SERVE, "port": "$PORT"}]})
        with self.assertRaises(Error) as caught:
            apps.start(THIRD, project, folder)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "app_limit"))
        apps.stop(RID)
        apps.start(THIRD, project, folder)
        self.assertEqual(self.settle_with(apps, THIRD, folder)["status"], "running")

    def test_a_process_that_exits_while_running_fails_the_preview(self):
        command = f"{PY} -m http.server $PORT --bind 127.0.0.1 & echo $! > server.pid; wait"
        _, project, folder = self.start(declaration={"processes": [{"name": "web", "command": command, "port": "$PORT"}]})
        self.assertEqual(self.settle(RID, folder)["status"], "running")
        os.kill(int((project / "server.pid").read_text()), 9)
        deadline = time.time() + 10
        while (view := self.apps.status(RID, folder))["status"] == "running":
            self.assertLess(time.time(), deadline)
            time.sleep(0.05)
        self.assertEqual(view["status"], "failed")
        self.assertIn("进程 web 已退出", view["error"])
        self.assertEqual(self.apps.running(), [])

    def test_after_a_worker_restart_a_preview_reads_as_stopped(self):
        _, _, folder = self.start(declaration={"processes": [{"command": SERVE, "port": "$PORT"}]})
        self.settle(RID, folder)
        view = Apps(ports=self.ports).status(RID, folder)
        self.assertEqual(view["status"], "stopped")
        self.assertTrue(view["log"])   # the log of the last run stays readable


class ReviewFixTests(AppsTests):
    """The final review's findings on the Worker side (D36 phase 2)."""

    def test_answers_carry_this_workers_instance(self):
        started, _, folder = self.start(declaration={"processes": [{"command": SERVE, "port": "$PORT"}]})
        self.assertRegex(started["instance"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.apps.status(RID, folder)["instance"], started["instance"])
        self.assertNotEqual(Apps(ports=self.ports).status(RID, folder)["instance"], started["instance"])

    def test_the_static_server_hides_dotfiles_and_links_outside_the_project(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        project, folder = self.project(files={"index.html": "<h1>ok</h1>", ".env": "SECRET=abc", ".git/config": "[x]",
                                              "assets/app.js": "js", "assets/.hidden": "h"})
        (project / "link.txt").symlink_to(outside)
        (project / "inner.txt").symlink_to(project / "assets/app.js")
        self.apps.start(RID, project, folder)
        view = self.settle(RID, folder)
        self.assertEqual(view["status"], "running", view)
        self.assertEqual(self.get(view["port"]), "<h1>ok</h1>")
        self.assertEqual(self.get(view["port"], "/assets/app.js"), "js")
        self.assertEqual(self.get(view["port"], "/inner.txt"), "js")
        for path in ("/.env", "/.git/config", "/assets/.hidden", "/link.txt", "/%2e%65nv", "/assets/../.env"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(view["port"], path)
            self.assertEqual(caught.exception.code, 404, path)

    def test_an_unusual_ready_path_is_refused_before_anything_starts(self):
        project, folder = self.project(declaration={"processes": [{"command": SERVE, "port": "$PORT"}],
                                                    "ready": {"path": "/首页"}})
        with self.assertRaises(Error) as caught:
            self.apps.start(RID, project, folder)
        self.assertEqual(caught.exception.code, "app_unavailable")
        self.assertIn("ready.path", str(caught.exception))
        self.assertEqual(self.apps.running(), [])

    def test_an_unexpected_error_still_fails_the_preview(self):
        project, _ = self.project(declaration={"processes": [{"command": SERVE, "port": "$PORT"}]})
        gone_folder = self.root / "no-such-folder"
        self.apps.start(RID, project, gone_folder)
        view = self.settle(RID, gone_folder)
        self.assertEqual(view["status"], "failed", view)
        self.assertEqual(self.apps.running(), [])

    def test_install_runs_again_when_the_lockfile_changes(self):
        declaration = {"processes": [{"command": SERVE, "port": "$PORT"}], "install": [{"command": "echo done >> installed.txt"}]}
        _, project, folder = self.start(declaration=declaration, files={"package-lock.json": "{\"v\": 1}"})
        self.assertEqual(self.settle(RID, folder)["status"], "running")
        self.apps.stop(RID)
        self.apps.start(RID, project, folder)
        self.assertEqual(self.settle(RID, folder)["status"], "running")
        self.assertEqual((project / "installed.txt").read_text(), "done\n")   # unchanged: not again
        self.apps.stop(RID)
        (project / "package-lock.json").write_text("{\"v\": 2}")   # a continuation added a dependency
        self.apps.start(RID, project, folder)
        self.assertEqual(self.settle(RID, folder)["status"], "running")
        self.assertEqual((project / "installed.txt").read_text(), "done\ndone\n")


class WorkerAppTests(unittest.TestCase):
    def setUp(self):
        from tests.test_archive import make_worker
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.node, self.project = make_worker(self.root)
        self.node.apps = Apps(ports=free_block(), poll=0.05)
        self.addCleanup(self.node.apps.stop_all)
        (self.project / "index.html").write_text("<h1>hi</h1>")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, body, token=None):
        from octopus.worker import serve
        from tests.test_archive import TOKEN
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/app", method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + (token or TOKEN),
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_start_status_and_stop_through_the_worker(self):
        from tests.test_archive import RID as WORKER_RID
        status, started = self.call({"run_id": WORKER_RID, "action": "start"})
        self.assertEqual((status, started["source"], started["via"]), (200, "detected", None))
        deadline = time.time() + 10
        while (view := self.call({"run_id": WORKER_RID, "action": "status"})[1])["status"] == "starting":
            self.assertLess(time.time(), deadline)
            time.sleep(0.05)
        self.assertEqual(view["status"], "running", view)
        self.assertEqual(self.call({"run_id": WORKER_RID, "action": "stop"}), (200, {"status": "stopped"}))
        self.assertEqual(self.call({"run_id": WORKER_RID, "action": "start"}, token="y" * 40)[0], 401)
        self.assertEqual(self.call({"run_id": WORKER_RID, "action": "launch"})[0], 400)
        self.assertTrue(self.node.health()["app_preview"])

    def test_a_worker_host_says_how_the_platform_reaches_it(self):
        from tests.test_archive import RID as WORKER_RID
        self.node.preview_tls_port, self.node.preview_tls_name = 8945, "w1.worker.kirocrew-on"
        status, started = self.call({"run_id": WORKER_RID, "action": "start"})
        self.assertEqual((status, started["via"]), (200, {"port": 8945, "name": "w1.worker.kirocrew-on"}))

    def test_a_session_without_source_cannot_start(self):
        status, body = self.call({"run_id": "run-" + "9" * 32, "action": "start"})
        self.assertEqual((status, body["code"]), (409, "app_unavailable"))


class ConfigTests(unittest.TestCase):
    def test_a_boundary_can_name_its_preview_port(self):
        from octopus.config import ConfigError, _layer
        values = _layer({"preview_tls_port": 8945, "preview_tls_name": "w1.worker.kirocrew-on"}, Path("/"))
        self.assertEqual(values, {"preview_tls_port": 8945, "preview_tls_name": "w1.worker.kirocrew-on"})
        with self.assertRaises(ConfigError):
            _layer({"preview_tls_port": 0}, Path("/"))


if __name__ == "__main__":
    unittest.main()
