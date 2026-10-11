"""`octopus connect <client>`: let Claude Code or Kiro CLI drive KiroCrew ON through MCP, with a usage guide for the agent.

Both clients get the same Skill, rendered from skills/kirocrew-on/SKILL.md (connection steps and usage guide).
Claude Code: MCP server at user scope (`claude mcp add-json`) and the Skill in ~/.claude/skills/kirocrew-on/.
Kiro CLI: MCP server at workspace scope by default (`kiro-cli mcp add --scope workspace`, run in the project directory) and
the Skill in <project>/.kiro/skills/kirocrew-on/. Workspace scope keeps KiroCrew's own kiro agents (which share ~/.kiro
on this machine) from loading the server. Other clients are configured by hand (docs/CONNECT.md); `install_skill` writes
the Skill into any skills directory. Existing files are backed up before they are replaced.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

NAME = "kirocrew-on"
ROOT = Path(__file__).resolve().parent.parent

SKILL = ROOT / "skills" / NAME / "SKILL.md"


def skill(url, repo=ROOT):
    """The portable Skill (Agent Skills format) rendered with this platform's address and this checkout's path."""
    return SKILL.read_text().replace("{{OCTOPUS_URL}}", url.rstrip("/")).replace("{{KIROCREW_ON_REPO}}", str(repo))


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


def install_skill(url, dest, dry_run=False):
    """Write the rendered Skill to <dest>/kirocrew-on/SKILL.md; returns the actions taken."""
    actions = []
    write(Path(dest).expanduser() / NAME / "SKILL.md", skill(url), actions, dry_run)
    return actions


def connect(client, url, token, *, home=None, project=None, scope=None, dry_run=False, run=subprocess.run):
    """Configure ``client`` ("claude" or "kiro"); returns the actions taken (or that would be taken)."""
    home = Path(home or Path.home())
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
        write(home / ".claude/skills" / NAME / "SKILL.md", skill(url), actions, dry_run)
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
        write((project if scope == "workspace" else home) / ".kiro/skills" / NAME / "SKILL.md", skill(url), actions, dry_run)
        if scope == "workspace":
            actions.append(f"提示：{project / '.kiro/settings/mcp.json'} 含 Token，请加入项目的 .gitignore，不要提交")
    else:
        raise ValueError("支持的客户端：claude、kiro")
    return actions
