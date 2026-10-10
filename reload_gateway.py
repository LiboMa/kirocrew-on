"""Reload only this PoC Gateway when chats, tasks, queues and approvals are idle."""
import argparse
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import time

import api
from pipeline import read_json, write_json

ROOT = Path(__file__).resolve().parent


def reload_when_idle(wait_seconds=0):
    marker = read_json(ROOT / "state/gateway-runtime.json", {})
    old_pid = marker.get("pid")
    deadline = time.monotonic() + wait_seconds
    while True:
        slots = api.request("/api/chat/slots")
        tasks = api.request("/api/spawn")["agents"]
        summary = api.request("/api/tasks/summary")
        if not summary.get("available"):
            raise RuntimeError("无法核实原生队列，未重载 Gateway。")
        depth = summary["depth"]
        idle = (not any(s.get("running") or s.get("queue") for s in slots)
                and not any(not t.get("done") for t in tasks)
                and not any(depth.get(k, 0) for k in ("running", "queued", "waiting", "recovering"))
                and not api.request("/api/approvals"))
        if idle:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Gateway 仍有任务或待处理授权，未重载。")
        time.sleep(2)
    listeners = subprocess.check_output(["lsof", "-tiTCP:5476", "-sTCP:LISTEN"], text=True).split()
    command = subprocess.check_output(["ps", "-p", str(old_pid), "-o", "command="], text=True)
    if str(old_pid) not in listeners or "poc.py" not in command:
        raise RuntimeError("5476 不是该 PoC 的 Gateway，未重载。")
    cfg = Path.home() / ".kiro/crew/config.json"
    before = {"pid": old_pid, "slots": sorted(s["key"] for s in slots),
              "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(), "idle": True, "at": time.time()}
    write_json(ROOT / "evidence/workflow-gateway-before.json", before)
    os.kill(old_pid, signal.SIGTERM)
    print(f"正在优雅停止空闲 PoC Gateway {old_pid}；保留数据目录。", flush=True)
    for _ in range(120):
        if subprocess.run(["lsof", "-tiTCP:5476", "-sTCP:LISTEN"], capture_output=True).returncode:
            break
        time.sleep(.25)
    else:
        raise RuntimeError("Gateway 尚未退出；未强制结束，也未启动第二个进程。")
    log = (ROOT / "state/gateway-original.log").open("a")
    child = subprocess.Popen([str(ROOT / "start.sh")], cwd=ROOT, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True)
    log.close()
    for _ in range(60):
        if child.poll() is not None:
            raise RuntimeError("Gateway 启动失败，请查看 state/gateway-original.log。数据目录保留。")
        try:
            new_slots = api.request("/api/chat/slots")
            if set(before["slots"]) <= {s["key"] for s in new_slots}:
                break
            # The route is available before the asynchronous slot hydration
            # finishes; a first response is not the full persisted inventory.
            time.sleep(.3)
        except Exception:
            time.sleep(.3)
    else:
        raise RuntimeError("Gateway 还未就绪，请查看日志。")
    after = {"pid": child.pid, "slots": sorted(s["key"] for s in new_slots),
             "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(), "at": time.time()}
    after["all_previous_slots_retained"] = set(before["slots"]) <= set(after["slots"])
    after["config_unchanged"] = before["config_sha256"] == after["config_sha256"]
    write_json(ROOT / "evidence/workflow-gateway-after.json", after)
    if not after["all_previous_slots_retained"]:
        raise RuntimeError("部分历史 slot 暂未返回；停止新任务并检查持久化记录。")
    print(f"Gateway 已重载为 {child.pid}；之前的 {len(before['slots'])} 个会话均保留。", flush=True)
    return after


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=int, default=0, help="最多等待空闲的秒数")
    args = parser.parse_args()
    try:
        reload_when_idle(args.wait)
    except RuntimeError as exc:
        parser.exit(1, str(exc) + "\n")
