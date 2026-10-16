"""Write poc-* agent templates beside existing agents; never reset user fields or sessions."""
import json

from octopus import prompts as development

from .registry import BACKENDS, LEGACY_BACKENDS

LEGACY_COMMON = ("这是用户授权的本地多 ACP PoC。只操作明确指定的演示目录。"
                 "不读取凭据，不联网安装依赖，不发布、不提交。报告实际执行结果，不编造。")


def agent_prompt(name):
    """A template's system prompt (D46): the parent Session's Owner observer, the platform Coordinator's diagnosis agent,
    or the executor every Coding tool runs a task as."""
    if name == "poc-diagnose":
        return development.COMMON + development.DIAGNOSE
    if name in {"poc-coordinator", "poc-pipeline-coordinator"}:
        return development.COMMON + development.COORDINATOR
    return development.COMMON + development.WORKER


def seed(*, crew_home, kiro_home, allowed_roots, port, legacy_pipeline, yolo=True):
    from kiro_crew.agent import managed_mcp_spec_entry
    crew_home.mkdir(parents=True, exist_ok=True)
    (kiro_home / "agents").mkdir(parents=True, exist_ok=True)
    config = {
        "agent": {
            "provider": "acp", "acp_backend": "", "default_agent": "poc-coordinator",
            # Global tool approval stays interactive even for auto boundaries: only platform-dispatched spawns
            # request native auto approval (D14). The standing YOLO below (crew_yolo) overrides it while on.
            "model": "auto", "sandbox": "auto", "approval_mode": "interactive",
            "session_sharing": False, "max_subagents": 3, "log_level": "INFO",
            "subagent_cwd_allowed_roots": [str(root) for root in allowed_roots],
            "subagent_result_ttl_secs": 86400,
        },
        "session": {"pool_size": 0},
        "memory": {"enabled": False},
        "dashboard": {"host": "127.0.0.1", "port": port, "onboarded": True},
        "workspaces": {"default": {"path": str(allowed_roots[0])}},
        "default_workspace": "default",
    }
    path = crew_home / "config.json"
    if not path.exists():
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    # The boundary's standing YOLO (crew_yolo): the one key written into an existing config, so it holds across
    # Gateway restarts (a Dashboard grant ends at shutdown) and the setting can turn it off again.
    current = json.loads(path.read_text())
    agent = current.setdefault("agent", {})
    if agent.get("dangerously_skip_permissions") is not yolo:
        agent["dangerously_skip_permissions"] = bool(yolo)
        staged = path.with_name(path.name + ".tmp")
        staged.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
        staged.replace(path)
    routes = {**BACKENDS, **(LEGACY_BACKENDS if legacy_pipeline else {})}
    written = []
    for name, backend in routes.items():
        coordinator = name in {"poc-coordinator", "poc-pipeline-coordinator"}
        spec = {
            "name": name,
            "description": f"Octopus development / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker"),
            "model": "auto",
            "prompt": agent_prompt(name),
            "tools": ["read", "write", "shell"] + (["@kirocrew-core"] if coordinator else []),
            "allowedTools": [], "resources": [],
            # Derive the managed invocation from the installed release so AcpRuntime can attach identity.
            "mcpServers": {"kirocrew-core": managed_mcp_spec_entry("kirocrew-core")} if coordinator else {},
            "includeMcpJson": False, "includeCrewContext": True,
        }
        if name == "poc-diagnose":
            spec["description"] = "Octopus diagnosis / Kiro CLI worker (no delegation)"
        if coordinator:
            spec["availableAgents"] = (
                [n for n in LEGACY_BACKENDS if n != name] if name == "poc-pipeline-coordinator"
                else ["poc-kiro", "poc-claude", "poc-codex", "poc-opencode"])
        if name.startswith("poc-pipeline-"):
            from pipeline_spec import COORDINATOR_PROMPT, WORKER_PROMPTS
            spec["description"] = f"App PoC / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker")
            spec["prompt"] = LEGACY_COMMON + (COORDINATOR_PROMPT if coordinator else WORKER_PROMPTS[name.rsplit("-", 1)[1]])
        agent_path = kiro_home / "agents" / f"{name}.json"
        if agent_path.exists() and not name.startswith("poc-pipeline-"):
            # Refresh the development contract without resetting user skills, MCP, models or permissions.
            previous = json.loads(agent_path.read_text())
            spec = {**spec, **previous, "prompt": spec["prompt"], "description": spec["description"]}
        agent_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
        written.append(agent_path)
    return written
