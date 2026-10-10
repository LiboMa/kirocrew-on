"""Start the existing-data Gateway with the PoC composition, then the real App."""
from pathlib import Path
import json
import os
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def listener():
    result = subprocess.run(["lsof", "-tiTCP:5476", "-sTCP:LISTEN"],
                            text=True, capture_output=True)
    return result.stdout.split()


def main():
    os.chdir(ROOT)
    (ROOT / "state").mkdir(exist_ok=True)
    pids = listener()
    marker_path = ROOT / "state/gateway-runtime.json"
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else {}
    if pids and str(marker.get("pid", "")) not in pids:
        raise SystemExit(
            "5476 已由普通 Gateway 使用。为保留正在运行的工作，本启动器不会强制结束它。\n"
            "请先在 KiroCrew 菜单中退出 App；确认没有其他 Gateway 服务运行，再双击本启动器。"
        )
    if not pids:
        config_path = Path.home() / ".kiro/crew/config.json"
        if not config_path.exists():
            raise SystemExit("请先安装并启动已验证的 KiroCrew 0.7.1 / 0.7.2 / 0.8.0，完成自己的 Coding Tool 登录。")
        config = json.loads(config_path.read_text())
        if config.get("agent", {}).get("session_sharing", True) or config.get("session", {}).get("pool_size", 1):
            raise SystemExit("首次准备请在本目录运行 ./setup.sh；它会备份配置并配置原生会话分配策略。")
        log = (ROOT / "state/gateway-original.log").open("a")
        child = subprocess.Popen([str(ROOT / "start.sh")], cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
        log.close()
        for _ in range(50):
            if child.poll() is not None:
                raise SystemExit("PoC Gateway 启动失败，请查看 state/gateway-original.log。")
            try:
                with urllib.request.urlopen("http://127.0.0.1:5476/api/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("Gateway 仍在启动，请稍后再运行；日志位于 state/gateway-original.log。")
    env = {**os.environ, "KIROCREW_HOME": str(Path.home()/".kiro/crew"),
           "KIROCREW_PORT": "5476", "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.Popen(["/Applications/KiroCrew.app/Contents/MacOS/KiroCrew"],
                     env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    print("KiroCrew App PoC 已打开：5476，使用原有 Sessions。")


if __name__ == "__main__":
    main()
