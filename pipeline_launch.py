"""Open the requirements entry. Existing App sessions and runs remain intact."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

import launch
import pipeline
import poc

ROOT = Path(__file__).resolve().parent


def main():
    launch.main()
    # Refresh only this PoC's templates when reusing an already running Gateway.
    subprocess.run([str(poc.PYTHON), "-c", "import poc; poc.prepare_environment(); poc.seed()"],
                   cwd=ROOT, check=True)
    # Only replace our observer. This never stops the Gateway or a model task.
    marker = pipeline.read_json(ROOT / "state/pipeline-server.json", {})
    pid = marker.get("pid")
    if pid:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 text=True, capture_output=True).stdout
        if str(ROOT / "pipeline.py") + " serve" in command:
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                listeners = subprocess.run(["lsof", "-tiTCP:8916", "-sTCP:LISTEN"],
                                           text=True, capture_output=True)
                if listeners.returncode != 0:
                    break
                time.sleep(0.1)
    if subprocess.run(["lsof", "-tiTCP:8916", "-sTCP:LISTEN"], capture_output=True).returncode == 0:
        raise SystemExit("8916 已被其他服务使用。请选择空闲端口执行 pipeline.py serve --port <端口>。")
    log = (ROOT / "state/pipeline-server.log").open("a")
    child = subprocess.Popen([sys.executable, str(ROOT / "pipeline.py"), "serve"],
                             cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
    log.close()
    for _ in range(30):
        if child.poll() is not None:
            raise SystemExit("看板启动失败；查看 state/pipeline-server.log。已有运行和 App 会话保留。")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8916/state.json", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    webbrowser.open("http://127.0.0.1:8916/#new")
    print("填写需求后发送到 KiroCrew，或点击“在 App 中输入需求”。无需编辑计划或任务文件。")
    print(f"实时看板：http://127.0.0.1:8916/  ·  观察进程 {child.pid}")
    print("先前运行与 Sessions 均保留。")


if __name__ == "__main__":
    main()
