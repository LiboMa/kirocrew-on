"""`octopus connect <client>`: let Claude Code or Kiro CLI drive KiroCrew ON through MCP, with a usage guide for the agent.

Claude Code: MCP server at user scope (`claude mcp add-json`) and a Skill in ~/.claude/skills/kirocrew-on/SKILL.md.
Kiro CLI: MCP server at workspace scope by default (`kiro-cli mcp add --scope workspace`, run in the project directory) and
steering in <project>/.kiro/steering/kirocrew-on.md. Workspace scope keeps KiroCrew's own kiro agents (which share
~/.kiro on this machine) from loading the server. Existing files are backed up before they are replaced.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

NAME = "kirocrew-on"
ROOT = Path(__file__).resolve().parent.parent

GUIDE = """# KiroCrew ON：把开发任务交给后台、查看进度、迭代

当用户要把开发任务交给 KiroCrew ON、查看任务进度或在已有任务上继续迭代时，使用 MCP 服务器 `kirocrew-on` 的工具。
控制台：{console}（会话链接：{console}?run=<run_id>）。

## 提交任务：`octopus_start`
- `request_id`：每个新需求生成新的 UUID；同一请求重试时沿用，后台只执行一次。
- `input`：完整需求——要做什么、约束、验收标准。用户给的仓库地址、分支与限制原样写进去。
- `source`：新项目 `{{"type": "new"}}`；已有仓库 `{{"type": "git", "url": "<仓库地址>", "ref": "<分支，可省略>"}}`。
- 不指定 `steps` / `workflow_id` / `template_id` / `document` 时，由 KiroCrew 的 Kiro Agent 识别意图、补全需求、
  拆分任务并分配 Coding 工具（Session 第 1 个任务“需求分析与任务规划”，完成后自动追加并执行生成的任务）。
- 已经写好了工作流（JSON / YAML / 含一个代码块的 Markdown）时，放进 `document: {{"content": "..."}}`，按提交内容运行；
  可先用 `octopus_parse_workflow` 预览规范化结果。
- `worker_id` 可省略：空间内只有一个可用 Worker 时自动使用。

## 查看进度：`octopus_status`
- 传 `run_id`。读取 `session.status`、各任务 `status`、`planning`（意图与补全后的需求）、`attention`（暂停原因与下一步）和最近事件。
- 任务通常需要几分钟到几十分钟；每 30–60 秒查询一次即可，向用户汇报真实状态。`completed` 之前不要宣称已完成。
- `octopus_runs` 列出用户可见的全部会话。

## 迭代：`octopus_continue`
- 在原 Session 与原 Workspace 上修改需求或某个任务后重跑该任务及其后续任务：
  `run_id`、新的 `request_id`、`expected_version`（取自最近一次 `octopus_status` 的 `session.version`）、`task_id`、
  更新后的 `input`；可选 `execution`（`name`、`prompt`、`tool`、`model`、`effort`）。
- 任务暂停（`blocked` / `failed` / `stopped`）时，也可用 `octopus_recover` 请协调者诊断并在原工作区安排修复。

## 预览产出
- `octopus_preview` 返回 Web 预览链接（静态页面在浏览器中直接打开）。
- 需要运行开发服务器的项目：在仓库目录执行 `deploy/aws/deploy.sh space <run_id>`，在本机 `localhost:<远端端口+10000>` 预览。

## 原则
- 停止、关闭、删除会话前先征得用户同意（`octopus_control`）。
- 只报告工具返回的真实状态与证据，不编造执行结果。
"""


def skill(console):
    return ("---\nname: kirocrew-on\ndescription: Submit development tasks to the KiroCrew ON backend, track their progress "
            "and iterate on them through the kirocrew-on MCP server.\n---\n\n" + GUIDE.format(console=console))


def server(url, token):
    return {"command": sys.executable, "args": ["-m", "octopus", "mcp", "--url", url],
            "env": {"OCTOPUS_API_TOKEN": token, "PYTHONPATH": str(ROOT)}}


def write(path, content, actions, dry_run):
    path = Path(path)
    if path.exists() and path.read_text() != content:
        backup = path.with_name(path.name + time.strftime(".bak-%Y%m%d%H%M%S"))
        actions.append(f"备份 {path} → {backup}")
        if not dry_run:
            shutil.copy2(path, backup)
    actions.append(f"写入 {path}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def connect(client, url, token, *, home=None, project=None, scope=None, dry_run=False, run=subprocess.run):
    """Configure ``client`` ("claude" or "kiro"); returns the actions taken (or that would be taken)."""
    home = Path(home or Path.home())
    console = url.rstrip("/") + "/"
    config = server(url, token)
    masked = json.dumps({**config, "env": {**config["env"], "OCTOPUS_API_TOKEN": "****"}}, ensure_ascii=False)
    actions = []
    if client == "claude":
        scope = scope or "user"
        actions.append(f"claude mcp add-json --scope {scope} {NAME} {masked}")
        if not dry_run:
            run(["claude", "mcp", "remove", "--scope", scope, NAME], capture_output=True, text=True)
            run(["claude", "mcp", "add-json", "--scope", scope, NAME, json.dumps({"type": "stdio", **config})],
                check=True, capture_output=True, text=True)
        write(home / ".claude/skills" / NAME / "SKILL.md", skill(console), actions, dry_run)
    elif client == "kiro":
        scope = scope or "workspace"
        project = Path(project or Path.cwd())
        command = ["kiro-cli", "mcp", "add", "--name", NAME, "--scope", scope, "--command", config["command"],
                   "--args", json.dumps(config["args"]), "--force"]
        for key, value in config["env"].items():
            command += ["--env", f"{key}={value}"]
        actions.append(" ".join(command).replace(token, "****") + (f"（工作目录 {project}）" if scope == "workspace" else ""))
        if not dry_run:
            run(command, check=True, capture_output=True, text=True, cwd=project)
        steering = (project if scope == "workspace" else home) / ".kiro/steering" / f"{NAME}.md"
        write(steering, GUIDE.format(console=console), actions, dry_run)
    else:
        raise ValueError("支持的客户端：claude、kiro")
    return actions
