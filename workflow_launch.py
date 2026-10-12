"""Open Workflow Studio, preserving Gateway, user sessions and existing runs."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

import launch
import workflow as w
from build_workflow_ui import build

ROOT = Path(__file__).resolve().parent


def start_server():
    build(ROOT)
    marker = w.read_json(ROOT / "state/workflow-server.json", {})
    pid = marker.get("pid")
    if pid:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True).stdout
        if str(ROOT / "workflow.py") + " serve" in command:
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                if subprocess.run(["lsof", "-tiTCP:8917", "-sTCP:LISTEN"], capture_output=True).returncode:
                    break
                time.sleep(.1)
    if not subprocess.run(["lsof", "-tiTCP:8917", "-sTCP:LISTEN"], capture_output=True).returncode:
        raise RuntimeError("8917 已被其他服务占用；没有停止该服务。")
    log = (ROOT / "state/workflow-server.log").open("a")
    proc = subprocess.Popen([sys.executable, str(ROOT / "workflow.py"), "serve"], cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    log.close()
    for _ in range(30):
        if proc.poll() is not None:
            raise RuntimeError("Workflow 看板启动失败；查看 state/workflow-server.log。")
        try:
            for path in ("/api/definitions", "/", "/workflow.js", "/workflow-view.js"):
                with urllib.request.urlopen("http://127.0.0.1:8917" + path, timeout=1) as response:
                    if not response.read(1):
                        raise OSError("工作台资源为空：" + path)
            return proc.pid
        except OSError:
            time.sleep(.1)
    raise RuntimeError("Workflow 看板尚未就绪；请查看日志。")


def main():
    pids = launch.listener()
    gateway = w.read_json(ROOT / "state/gateway-runtime.json", {})
    if pids and str(gateway.get("pid", "")) not in pids:
        raise SystemExit("5476 不是当前 PoC 的 Gateway；没有停止或替换它。")
    if not pids:
        launch.main()
    elif "codex_effort_pair_v1" not in gateway.get("composition_features", []):
        from reload_gateway import reload_when_idle
        try:
            reload_when_idle()
        except RuntimeError as exc:
            print(str(exc))
            print("可以先编辑配置；Gateway 空闲后重新打开启动器以加载 Effort 桥接。")
    for path in w.DEFINITIONS.glob("*.json"):
        value = w.read_json(path)
        if not (w.VERSIONS / value["id"]).exists():
            w.save(value, 0)
    pid = start_server()
    webbrowser.open("http://127.0.0.1:8917/")
    print(f"Workflow Studio 已打开（观察器 PID {pid}）。原 Gateway 与所有 Sessions 保留。")


if __name__ == "__main__":
    main()
