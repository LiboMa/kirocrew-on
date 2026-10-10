"""Local composition root for explicitly verified, unmodified KiroCrew releases."""
from __future__ import annotations

import copy
import dataclasses
import json
import os
from pathlib import Path
import shutil
import sys
import time
import development_prompts as development

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
CREW = Path.home() / ".kiro/crew"
KIRO = Path.home() / ".kiro"
PORT = 5476
SUPPORTED_VERSIONS = {"0.7.1", "0.7.2", "0.8.0"}
BACKENDS = {
    "poc-coordinator": "",
    "poc-kiro": "",
    "poc-claude": "claude",
    "poc-codex": "codex",
    "poc-opencode": "opencode",
    "poc-pipeline-coordinator": "",
    "poc-pipeline-plan": "",
    "poc-pipeline-code": "claude",
    "poc-pipeline-review": "codex",
    "poc-pipeline-deliver": "opencode",
}
RESOURCES = Path("/Applications/KiroCrew.app/Contents/Resources")
BIN = RESOURCES / "backend-dist/kirocrew-backend-arm64/bin"
PYTHON = BIN / "python3.12"


def prepare_environment():
    # Tool authentication stays in each tool's existing home.
    native_codex = shutil.which("codex")
    native_claude = shutil.which("claude")
    adapters = ROOT / "node_modules/.bin"
    if not adapters.exists():
        adapters = ROOT.parent / "multi-acp-poc/node_modules/.bin"
    for directory in (CREW, KIRO / "agents", STATE / "runs", ROOT / "evidence"):
        directory.mkdir(parents=True, exist_ok=True)
    os.environ["KIROCREW_HOME"] = str(CREW)
    os.environ["KIRO_HOME"] = str(KIRO)
    os.environ["KIROCREW_PORT"] = str(PORT)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PATH"] = os.pathsep.join(
        [str(BIN), str(adapters), str(Path.home() / ".local/bin"),
         "/opt/homebrew/bin", os.environ.get("PATH", "")]
    )
    os.environ.setdefault("CODEX_PATH", native_codex or "/Applications/ChatGPT.app/Contents/Resources/codex")
    os.environ.setdefault("CLAUDE_CODE_EXECUTABLE", native_claude or str(Path.home() / ".local/bin/claude"))
    for variable, executable in (
        ("CLAUDE_AGENT_ACP_BIN", "claude-agent-acp"),
        ("CODEX_ACP_BIN", "codex-acp"),
    ):
        adapter = adapters / executable
        if not adapter.exists():
            raise RuntimeError(f"Required adapter missing: {adapter}")
        os.environ[variable] = str(adapter)


def seed():
    """Add PoC templates alongside existing agents; keep all existing sessions."""
    from kiro_crew.agent import managed_mcp_spec_entry
    config = {
        "agent": {
            "provider": "acp", "acp_backend": "", "default_agent": "poc-coordinator",
            "model": "auto", "sandbox": "auto", "approval_mode": "interactive",
            "session_sharing": False, "max_subagents": 3, "log_level": "INFO",
            "subagent_cwd_allowed_roots": [str(STATE / "runs")],
            "subagent_result_ttl_secs": 86400,
        },
        "session": {"pool_size": 0},
        "memory": {"enabled": False},
        "dashboard": {"host": "127.0.0.1", "port": PORT, "onboarded": True},
        "workspaces": {"default": {"path": str(STATE / "runs")}},
        "default_workspace": "default",
    }
    path = CREW / "config.json"
    if not path.exists():
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    legacy_common = (
        "这是用户授权的本地多 ACP PoC。只操作明确指定的演示目录。"
        "不读取凭据，不联网安装依赖，不发布、不提交。报告实际执行结果，不编造。"
    )
    for name, backend in BACKENDS.items():
        coordinator = name in {"poc-coordinator", "poc-pipeline-coordinator"}
        spec = {
            "name": name,
            "description": f"Octopus development / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker"),
            "model": "auto",
            "prompt": development.COMMON + (development.COORDINATOR if coordinator else development.WORKER),
            "tools": ["read", "write", "shell"] + (["@kirocrew-core"] if coordinator else []),
            "allowedTools": [],
            "resources": [],
            "mcpServers": ({
                # Derive the managed invocation from this installed release so
                # AcpRuntime can attach its signed per-session identity.
                "kirocrew-core": managed_mcp_spec_entry("kirocrew-core")
            } if coordinator else {}),
            "includeMcpJson": False,
            "includeCrewContext": True,
        }
        if coordinator:
            spec["availableAgents"] = (
                [n for n in BACKENDS if n.startswith("poc-pipeline-") and n != name]
                if name == "poc-pipeline-coordinator"
                else ["poc-kiro", "poc-claude", "poc-codex", "poc-opencode"]
            )
        if name.startswith("poc-pipeline-"):
            from pipeline_spec import COORDINATOR_PROMPT, WORKER_PROMPTS
            spec["description"] = f"App PoC / {backend or 'Kiro CLI'}" + (" coordinator" if coordinator else " worker")
            spec["prompt"] = legacy_common + (
                COORDINATOR_PROMPT if coordinator else WORKER_PROMPTS[name.rsplit("-", 1)[1]]
            )
        agent_path = KIRO / "agents" / f"{name}.json"
        if agent_path.exists() and not name.startswith("poc-pipeline-"):
            # Update the development contract without resetting user skills,
            # MCP entries, model selections, or permission configuration.
            previous = json.loads(agent_path.read_text())
            spec = {**spec, **previous, "prompt": spec["prompt"], "description": spec["description"]}
        agent_path.write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
        )


class PocProviderRegistry:
    """Compose existing ACP factories; all lifecycle/permissions remain Crew's."""

    def __init__(self, original):
        self.original = original

    def register_acp_backends(self):
        return self.original.register_acp_backends()

    def create_factory(self, cfg):
        if cfg.agent.session_sharing or cfg.session.pool_size:
            raise RuntimeError("PoC routing requires session_sharing=false and pool_size=0")
        factories = {}
        for backend in set(BACKENDS.values()):
            scoped = copy.deepcopy(cfg)
            scoped.agent.acp_backend = backend
            scoped.agent.member_acp_backend = backend
            factories[backend] = scoped.create_provider_factory()
        ordinary = self.original.create_factory(cfg)

        def create(session_key=None, agent=None, **kwargs):
            if agent not in BACKENDS:
                if agent and agent.startswith("poc-"):
                    raise ValueError(f"Unknown PoC template: {agent}")
                return ordinary(session_key=session_key, agent=agent, **kwargs)
            backend = BACKENDS[agent]
            requested_model = kwargs.get("model_override")
            requested_effort = kwargs.get("reasoning_effort_override", "")
            if backend == "codex" and requested_model and requested_model != "auto" and requested_effort:
                from kiro_crew.effort import is_valid_effort, model_supports_effort
                from kiro_crew.model_registry import split_effort_suffix
                base_model, _ = split_effort_suffix(requested_model)
                if is_valid_effort(requested_effort) and model_supports_effort(base_model):
                    # Keep the public spawn_run model field bare (its schema
                    # rejects brackets). At the existing factory seam, encode
                    # the pair Codex advertises. The native ACP client applies
                    # it through its model/effort config-option split path.
                    kwargs = {**kwargs, "model_override": f"{base_model}[{requested_effort}]"}
            provider = factories[backend](session_key=session_key, agent=agent, **kwargs)
            actual = provider.client.backend
            if actual != backend:
                raise RuntimeError(f"ACP routing mismatch: {agent}: {actual!r} != {backend!r}")
            event = {
                "time": time.time(), "gateway_pid": os.getpid(),
                "session_key": session_key, "agent": agent, "backend": backend or "kiro",
                "cwd": str(getattr(provider, "cwd", "") or kwargs.get("cwd", "")),
                "actual_backend": actual or "kiro",
                "provider_type": type(provider).__name__,
                "requested_model": requested_model,
                "requested_effort": requested_effort,
                "wire_model": getattr(provider.client, "_model", ""),
            }
            with (ROOT / "evidence/provider-routing.jsonl").open("a") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            return provider

        return create


def main():
    prepare_environment()
    from kiro_crew._ssl_compat import _ensure_ssl_certs
    _ensure_ssl_certs()
    from kiro_crew import __version__
    from kiro_crew.config import KiroCrewConfig
    from kiro_crew.platform import boot_platform, set_context, assert_security_floor
    cfg = KiroCrewConfig.load()
    # Runtime-only resource settings avoid handing a Kiro warm session to a
    # different backend. Persist the same two settings before launch so native
    # config reloads cannot undo the invariant; existing session data is retained.
    if __version__ not in SUPPORTED_VERSIONS:
        raise RuntimeError(f"Unsupported KiroCrew version: {__version__}; run contract checks before extending support")
    if cfg.agent.session_sharing or cfg.session.pool_size:
        raise RuntimeError("PoC requires session_sharing=false and pool_size=0")
    seed()
    ctx = boot_platform(cfg)
    composed = dataclasses.replace(ctx, providers=PocProviderRegistry(ctx.providers))
    assert_security_floor(composed.security)
    set_context(composed)
    if "--check" in sys.argv:
        factory = composed.providers.create_factory(cfg)
        for name in BACKENDS:
            p = factory(session_key=f"poc-check:{name}", agent=name)
            print(name, type(p).__name__)
        return
    (STATE / "gateway-runtime.json").write_text(json.dumps({
        "pid": os.getpid(), "home": str(CREW), "port": PORT,
        "kirocrew_version": __version__, "composition": "PocProviderRegistry",
        "composition_features": ["codex_effort_pair_v1"],
        "started_at": time.time(),
    }, indent=2) + "\n")
    from kiro_crew.cli import main as crew_main
    sys.argv = [str(ROOT / "poc.py"), "gateway", "--no-open", "--port", str(PORT)]
    crew_main()


if __name__ == "__main__":
    main()
