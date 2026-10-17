"""How a session's project starts (D36 §3): the declaration ``.octopus/preview.json`` a delivery task writes, or, without
one, what the project's own files say. Used for the local start steps in a download and for the remote preview."""
import json
from pathlib import Path
import re

DECLARATION = ".octopus/preview.json"
READY = {"path": "/", "timeout": 120}


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} 须为 1–{limit} 个字符")
    return value.strip()


def _cwd(value, project):
    cwd = value if value is not None else "."
    if not isinstance(cwd, str) or not cwd or Path(cwd).is_absolute():
        raise ValueError("cwd 须为项目内的相对路径")
    root = Path(project).resolve()
    if not (root / cwd).resolve().is_relative_to(root):
        raise ValueError(f"cwd {cwd} 越出了项目目录")
    return cwd


def _port(value):
    if value == "$PORT":
        return value
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise ValueError("port 须为 1024–65535 的整数或 \"$PORT\"")
    return value


def load(project):
    """The validated declaration, None without one; ValueError says which part is wrong."""
    path = Path(project) / DECLARATION
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (ValueError, UnicodeDecodeError):
        raise ValueError("不是有效的 JSON") from None
    if not isinstance(value, dict) or not isinstance(value.get("processes"), list) or not 1 <= len(value["processes"]) <= 4:
        raise ValueError("processes 须为 1–4 个进程")
    processes = []
    for index, item in enumerate(value["processes"], 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个进程不是对象")
        processes.append({"name": _text(item.get("name", f"process-{index}"), "name", 40),
                          "cwd": _cwd(item.get("cwd"), project), "command": _text(item.get("command"), "command", 500),
                          "port": _port(item.get("port")), "entry": item.get("entry") is True})
    if len(processes) == 1:
        processes[0]["entry"] = True
    if sum(p["entry"] for p in processes) != 1:
        raise ValueError("须恰好有一个入口进程（entry: true）")
    install = value.get("install", [])
    if not isinstance(install, list) or len(install) > 4:
        raise ValueError("install 最多 4 条")
    install = [{"cwd": _cwd(step.get("cwd"), project), "command": _text(step.get("command"), "install.command", 500)}
               for step in install if isinstance(step, dict)]
    ready = value.get("ready", {})
    if not isinstance(ready, dict):
        raise ValueError("ready 须为对象")
    path_ = ready.get("path", READY["path"])
    timeout = ready.get("timeout", READY["timeout"])
    if not isinstance(path_, str) or not re.fullmatch(r"/[\x21-\x7e]{0,500}", path_) or isinstance(timeout, bool) \
            or not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise ValueError("ready.path 须以 / 开头、只含可见 ASCII 字符（中文路径请 URL 编码），ready.timeout 为 1–600 秒")
    return {"processes": processes, "install": install, "ready": {"path": path_, "timeout": timeout}}


def detect(project, packaged=False):
    """How a project without a declaration starts: its package.json scripts, or a static site; None otherwise.
    ``packaged``: steps for the downloaded copy, where the dependency directories left behind count as absent."""
    project = Path(project)
    package = project / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text()).get("scripts")
        except (ValueError, UnicodeDecodeError, AttributeError):
            scripts = None
        scripts = scripts if isinstance(scripts, dict) else {}
        command = None
        if isinstance(scripts.get("dev"), str):
            command = ("npm run dev -- --host 127.0.0.1 --port $PORT --strictPort" if scripts["dev"].strip().startswith("vite")
                       else "npm run dev")
        elif isinstance(scripts.get("start"), str):
            command = "npm start"
        if command:
            # npm ci installs exactly the lockfile and refuses to run without one.
            install = [] if (project / "node_modules").is_dir() and not packaged else [
                {"cwd": ".", "command": "npm ci" if (project / "package-lock.json").is_file() else "npm install"}]
            return {"processes": [{"name": "app", "cwd": ".", "command": command, "port": "$PORT", "entry": True}],
                    "install": install, "ready": dict(READY)}
    if (project / "index.html").is_file():
        return {"static": ".", "processes": [], "install": [], "ready": dict(READY)}
    return None


def resolve(project, packaged=False):
    """(spec, "declared" | "detected" | None, error): a broken declaration is reported, never silently replaced."""
    try:
        spec = load(project)
    except ValueError as exc:
        return None, None, f"{DECLARATION}：{exc}"
    if spec:
        return spec, "declared", None
    spec = detect(project, packaged)
    return (spec, "detected", None) if spec else (None, None, f"未找到启动方式：请在交付任务中写 {DECLARATION}")


def entry(spec):
    return next(p for p in spec["processes"] if p["entry"])


def _local_port(process):
    if process["port"] != "$PORT":
        return process["port"]
    return 5173 if "npm run dev" in process["command"] or "vite" in process["command"] else 3000


def local_steps(spec):
    """The steps to run the project on one's own machine, as shell lines and a closing「打开 …」."""
    if spec.get("static"):
        return ["python3 -m http.server 8000", "打开 http://localhost:8000" + spec["ready"]["path"]]
    line = lambda cwd, command: command if cwd == "." else f"cd {cwd} && {command}"
    steps = [line(step["cwd"], step["command"]) for step in spec["install"]]
    steps += [line(p["cwd"], p["command"].replace("$PORT", str(_local_port(p)))) for p in spec["processes"]]
    return steps + [f"打开 http://localhost:{_local_port(entry(spec))}{spec['ready']['path']}"]
