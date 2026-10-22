"""`octopus connect <client>`: let Claude Code or Kiro CLI drive KiroCrew ON through MCP, with a usage guide for the agent.

Both clients get the same Skill, rendered from skills/kirocrew-on/SKILL.md (connection steps and usage guide).
Claude Code: MCP server at user scope (`claude mcp add-json`) and the Skill in ~/.claude/skills/kirocrew-on/.
Kiro CLI: MCP server at workspace scope by default (`kiro-cli mcp add --scope workspace`, run in the project directory) and
the Skill in <project>/.kiro/skills/kirocrew-on/. Workspace scope keeps KiroCrew's own kiro agents (which share ~/.kiro
on this machine) from loading the server. Codex: the server merged into ~/.codex/config.toml and the Skill in
~/.codex/skills/kirocrew-on/. Kiro IDE: the server merged into <project>/.kiro/settings/mcp.json (no kiro-cli needed) and
the Skill in <project>/.kiro/skills/kirocrew-on/ (M3-1, 2026-10-21). Other clients are configured by hand
(docs/CONNECT.md); `install_skill` writes the Skill into any skills directory. Existing files are backed up before they are
replaced; what they hold besides this server is kept.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib

NAME = "kirocrew-on"
CLIENTS = ("claude", "kiro", "codex", "kiro-ide")
ROOT = Path(__file__).resolve().parent.parent

SKILL = ROOT / "skills" / NAME / "SKILL.md"


def skill(url, repo=ROOT):
    """The portable Skill (Agent Skills format) rendered with this platform's address and this checkout's path."""
    return SKILL.read_text().replace("{{OCTOPUS_URL}}", url.rstrip("/")).replace("{{KIROCREW_ON_REPO}}", str(repo))


def issue_token(client_factory, url, client, username, password, admin=False, expires=None):
    """Sign in as ``username`` and issue the Coding Agent's own token (spec §7.1): read, execute and manage for 30 days;
    with ``admin`` (an administrator only) also admin, for 7 days. ``expires`` (D62: 7d / 30d / 365d / never; admin at most
    30d) chooses otherwise; ttl still goes along for a platform from before D62. Returns {"token", "id"} and, when signing
    out failed, a "warning"; the sign-in token is logged out in every case it can be."""
    if admin and expires not in (None, "7d", "30d"):
        raise ValueError("带 admin 权限的 Token 最长 30 天：--expires 用 7d 或 30d")
    login = client_factory(url, "").request("POST", "/v1/login", {"username": username, "password": password})
    session = client_factory(url, login["token"])
    issued = None
    try:
        if admin and not session.request("GET", "/v1/me").get("admin"):
            raise ValueError("--admin 需要管理员账号")
        issued = session.request("POST", "/v1/tokens", {
            "label": f"{client} MCP" + (" admin" if admin else ""),
            "scopes": ["read", "execute", "manage"] + (["admin"] if admin else []),
            "ttl": (7 if admin else 30) * 86400, **({"expires_in": expires} if expires else {})})
        return {"token": issued["token"], "id": issued.get("id")}
    finally:
        try:
            session.request("POST", "/v1/logout", {})
        except (RuntimeError, OSError) as exc:   # best effort: the issued token (or the real error) must still reach the user
            if issued is not None:
                return {"token": issued["token"], "id": issued.get("id"),
                        "warning": f"临时登录未能登出（{exc}），可在控制台撤销名为 Web login 的 Token"}


def revoke(client_factory, url, issued):
    """Revoke a token this command issued (the client configuration could not be written)."""
    client_factory(url, issued["token"]).request("DELETE", "/v1/tokens/" + issued["id"])


def server(url, token):
    return {"command": sys.executable, "args": ["-m", "octopus", "mcp", "--url", url],
            "env": {"OCTOPUS_API_TOKEN": token, "PYTHONPATH": str(ROOT)}}


def write(path, content, actions, dry_run, private=False):
    """``private``: the file holds the token — readable by its owner only."""
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
        if private:
            os.chmod(path, 0o600)


TABLE = re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*(?:kirocrew-on|\"kirocrew-on\")\s*(?:\.[^\]]*)?\]\s*$")


def codex_config(text, config):
    """Codex's config.toml with this server's tables, every other line as it was; checked by parsing it back."""
    kept, inside = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            inside = bool(TABLE.match(line))
        if not inside:
            kept.append(line)
    body = "\n".join(kept).rstrip()
    string = lambda value: json.dumps(value, ensure_ascii=False)
    table = [f"[mcp_servers.{NAME}]", f"command = {string(config['command'])}", f"args = {string(config['args'])}", "",
             f"[mcp_servers.{NAME}.env]"] + [f"{key} = {string(value)}" for key, value in config["env"].items()]
    merged = (body + "\n\n" if body else "") + "\n".join(table) + "\n"
    try:
        written = tomllib.loads(merged)["mcp_servers"][NAME]
    except (tomllib.TOMLDecodeError, KeyError):
        written = None
    if written != config:
        raise ValueError("无法把 MCP 服务器合并进 ~/.codex/config.toml：请按 docs/CONNECT.md「其他客户端」手动配置")
    return merged


def install_skill(url, dest, dry_run=False):
    """Write the rendered Skill to <dest>/kirocrew-on/SKILL.md; returns the actions taken."""
    actions = []
    write(Path(dest).expanduser() / NAME / "SKILL.md", skill(url), actions, dry_run)
    return actions


def connect(client, url, token, *, home=None, project=None, scope=None, dry_run=False, run=subprocess.run):
    """Configure ``client`` (one of CLIENTS); returns the actions taken (or that would be taken)."""
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
    elif client == "codex":
        path = home / ".codex/config.toml"
        actions.append(f"合并 MCP 服务器 {NAME} 到 {path}：{masked}")
        write(path, codex_config(path.read_text() if path.exists() else "", config), actions, dry_run, private=True)
        write(home / ".codex/skills" / NAME / "SKILL.md", skill(url), actions, dry_run)
    elif client == "kiro-ide":
        project = Path(project or Path.cwd())
        path = project / ".kiro/settings/mcp.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(current, dict) or not isinstance(current.get("mcpServers", {}), dict):
            raise ValueError(f"{path} 不是 Kiro 的 MCP 配置（mcpServers）：请检查后重试")
        current = {**current, "mcpServers": {**current.get("mcpServers", {}), NAME: config}}
        actions.append(f"合并 MCP 服务器 {NAME} 到 {path}：{masked}")
        write(path, json.dumps(current, ensure_ascii=False, indent=2) + "\n", actions, dry_run, private=True)
        write(project / ".kiro/skills" / NAME / "SKILL.md", skill(url), actions, dry_run)
        actions.append(f"提示：{path} 含 Token，请加入项目的 .gitignore，不要提交")
    else:
        raise ValueError("支持的客户端：" + "、".join(CLIENTS))
    return actions
