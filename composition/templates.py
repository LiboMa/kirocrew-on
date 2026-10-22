"""Write poc-* agent templates beside existing agents; never reset user fields or sessions."""
import json

from octopus import prompts as development

from .registry import BACKENDS, LEGACY_BACKENDS

LEGACY_COMMON = ("这是用户授权的本地多 ACP PoC。只操作明确指定的演示目录。"
                 "不读取凭据，不联网安装依赖，不发布、不提交。报告实际执行结果，不编造。")


MONITORS = ("@kirocrew-core/monitor_watch", "@kirocrew-core/monitor_start", "@kirocrew-core/monitor_update",
            "@kirocrew-core/monitor_inspect", "@kirocrew-core/monitor_stop")
LEARN = ("@kirocrew-core/learn_add", "@kirocrew-core/learn_remove")


def agent_prompt(name):
    """A template's system prompt (D46): the parent Session's Owner observer, the platform Coordinator's diagnosis agent,
    or the executor every Coding tool runs a task as."""
    if name == "poc-diagnose":
        return development.COMMON + development.DIAGNOSE
    if name in {"poc-coordinator", "poc-pipeline-coordinator"}:
        return development.COMMON + development.COORDINATOR
    return development.COMMON + development.WORKER


def _forget_ignored(config):
    """Remove what earlier releases of the platform wrote that KiroCrew 0.10.0 never read — memory.enabled,
    dashboard.host / port and workspaces.default.path — and keep everything else. Returns whether anything went."""
    changed = False
    memory = config.get("memory")
    if isinstance(memory, dict) and "enabled" in memory:
        memory.pop("enabled")
        changed = True
        if not memory:
            config.pop("memory")
    dashboard = config.get("dashboard")
    if isinstance(dashboard, dict):
        for key in ("host", "port"):
            if key in dashboard:
                dashboard.pop(key)
                changed = True
    workspaces = config.get("workspaces")
    if isinstance(workspaces, dict) and isinstance(workspaces.get("default"), dict) and "path" in workspaces["default"]:
        workspaces["default"].pop("path")
        changed = True
        if not workspaces["default"]:
            workspaces.pop("default")
        if not workspaces:
            config.pop("workspaces")
    return changed


def seed(*, crew_home, kiro_home, allowed_roots, legacy_pipeline, yolo=True):
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
            # D46 P0: max_subagents is the cap — the adaptive controller would otherwise lower it under host pressure
            "adaptive_concurrency_mode": "fixed",
        },
        # Only keys KiroCrew reads (tests/kirocrew_contract.json): its port comes from --port / KIROCREW_PORT, and the
        # default workspace stays its own base directory (a workspace entry names it with `dir`, not `path`).
        # D63: KiroCrew pre-creates a session for every chat slot (eager_spawn defaults on); the platform's slot is only a
        # parent and never claimed, so that session holds a process and, evicted under memory pressure, takes the
        # slot's running sub-agent down with it (parent-end teardown, EC2 2026-10-22).
        "session": {"pool_size": 0, "eager_spawn": False},
        "dashboard": {"onboarded": True},
        "default_workspace": "default",
    }
    path = crew_home / "config.json"
    if not path.exists():
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    # The boundary's standing YOLO (crew_yolo): written into an existing config (as eager_spawn is, above), so it holds across
    # Gateway restarts (a Dashboard grant ends at shutdown) and the setting can turn it off again. The keys the platform
    # once wrote that KiroCrew never read go at the same time (tests/kirocrew_contract.json).
    current = json.loads(path.read_text())
    changed = _forget_ignored(current)
    session = current.setdefault("session", {})
    if isinstance(session, dict) and "eager_spawn" not in session:   # D63, into an existing config; one set on stays on
        session["eager_spawn"] = False
        changed = True
    agent = current.setdefault("agent", {})
    if agent.get("dangerously_skip_permissions") is not yolo:
        agent["dangerously_skip_permissions"] = bool(yolo)
        changed = True
    if changed:
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
            # Where KiroCrew 0.10.0 reads the spawn allowlist (a top-level key is "allow all"); the platform dispatches
            # every task and the diagnosis under this parent, so they are all listed (D46 P0).
            spec["toolsSettings"] = {"subagent": {"availableAgents": (
                [n for n in LEGACY_BACKENDS if n != name] if name == "poc-pipeline-coordinator"
                else ["poc-kiro", "poc-claude", "poc-codex", "poc-opencode", "poc-diagnose"])}}
            # The observer does not arm loops that add turns of their own, and neither adds nor removes KiroCrew lessons:
            # the platform is their one writer (D46 P4).
            spec["excludedTools"] = sorted(MONITORS + LEARN)
            # kiro-cli applies excludedTools; KiroCrew's own kirocrew-core filters by managedToolPolicy (both, so neither alone)
            spec["managedToolPolicy"] = {"exclude": sorted(tool.rsplit("/", 1)[1] for tool in MONITORS + LEARN)}
        if name.startswith("poc-pipeline-"):
            from pipeline_spec import COORDINATOR_PROMPT, WORKER_PROMPTS
            spec["description"] = f"App PoC / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker")
            spec["prompt"] = LEGACY_COMMON + (COORDINATOR_PROMPT if coordinator else WORKER_PROMPTS[name.rsplit("-", 1)[1]])
        agent_path = kiro_home / "agents" / f"{name}.json"
        if agent_path.exists() and not name.startswith("poc-pipeline-"):
            # Refresh the development contract without resetting user skills, MCP, models or permissions.
            previous = json.loads(agent_path.read_text())
            previous.pop("availableAgents", None)   # the platform's own earlier key, which KiroCrew never read (D46 P0)
            merged = {**spec, **previous, "prompt": spec["prompt"], "description": spec["description"]}
            if coordinator:   # the platform's allowlist and exclusions hold; the user's other tool settings stay
                settings = dict(previous.get("toolsSettings") or {})
                settings["subagent"] = {**(settings.get("subagent") or {}), **spec["toolsSettings"]["subagent"]}
                merged["toolsSettings"] = settings
                merged["excludedTools"] = sorted(set(previous.get("excludedTools") or []) | set(spec["excludedTools"]))
                policy = previous.get("managedToolPolicy") if isinstance(previous.get("managedToolPolicy"), dict) else {}
                merged["managedToolPolicy"] = {**policy, "exclude": sorted(set(policy.get("exclude") or [])
                                                                         | set(spec["managedToolPolicy"]["exclude"]))}
            spec = merged
        agent_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
        written.append(agent_path)
    return written
