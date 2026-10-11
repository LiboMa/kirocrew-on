"""Compose existing ACP factories; all lifecycle and permissions remain KiroCrew's."""
import copy
import json
import os
from pathlib import Path
import time

SUPPORTED_VERSIONS = {"0.7.1", "0.7.2", "0.8.0"}
FEATURES = ["codex_effort_pair_v1"]
BACKENDS = {
    "poc-coordinator": "", "poc-kiro": "", "poc-claude": "claude", "poc-codex": "codex", "poc-opencode": "opencode",
}
LEGACY_BACKENDS = {
    "poc-pipeline-coordinator": "", "poc-pipeline-plan": "", "poc-pipeline-code": "claude",
    "poc-pipeline-review": "codex", "poc-pipeline-deliver": "opencode",
}
ROUTES = {**BACKENDS, **LEGACY_BACKENDS}


class PocProviderRegistry:
    def __init__(self, original, routing_log=None, factory_marker=None):
        self.original = original
        self.routing_log = Path(routing_log) if routing_log else None
        self.factory_marker = Path(factory_marker) if factory_marker else None

    def register_acp_backends(self):
        return self.original.register_acp_backends()

    def create_factory(self, cfg):
        if cfg.agent.session_sharing or cfg.session.pool_size:
            raise RuntimeError("PoC routing requires session_sharing=false and pool_size=0")
        factories = {}
        for backend in set(ROUTES.values()):
            scoped = copy.deepcopy(cfg)
            scoped.agent.acp_backend = backend
            scoped.agent.member_acp_backend = backend
            factories[backend] = scoped.create_provider_factory()
        ordinary = self.original.create_factory(cfg)
        if self.factory_marker:
            # Written from inside the serving process: a re-exec'd Gateway runs without this registry
            # and never refreshes it, so readiness can tell composition was lost.
            self.factory_marker.parent.mkdir(parents=True, exist_ok=True)
            self.factory_marker.write_text(json.dumps({"pid": os.getpid(), "built_at": time.time()}) + "\n")

        def create(session_key=None, agent=None, **kwargs):
            if agent not in ROUTES:
                if agent and agent.startswith("poc-"):
                    raise ValueError(f"Unknown PoC template: {agent}")
                return ordinary(session_key=session_key, agent=agent, **kwargs)
            backend = ROUTES[agent]
            requested_model = kwargs.get("model_override")
            requested_effort = kwargs.get("reasoning_effort_override", "")
            if backend == "codex" and requested_model and requested_model != "auto" and requested_effort:
                from kiro_crew.effort import is_valid_effort, model_supports_effort
                from kiro_crew.model_registry import split_effort_suffix
                base_model, _ = split_effort_suffix(requested_model)
                if is_valid_effort(requested_effort) and model_supports_effort(base_model):
                    # spawn_run's schema rejects brackets; encode the pair Codex advertises here.
                    kwargs = {**kwargs, "model_override": f"{base_model}[{requested_effort}]"}
            provider = factories[backend](session_key=session_key, agent=agent, **kwargs)
            actual = provider.client.backend
            if actual != backend:
                raise RuntimeError(f"ACP routing mismatch: {agent}: {actual!r} != {backend!r}")
            if self.routing_log:
                event = {
                    "time": time.time(), "gateway_pid": os.getpid(), "session_key": session_key, "agent": agent,
                    "backend": backend or "kiro", "cwd": str(getattr(provider, "cwd", "") or kwargs.get("cwd", "")),
                    "actual_backend": actual or "kiro", "provider_type": type(provider).__name__,
                    "requested_model": requested_model, "requested_effort": requested_effort,
                    "wire_model": getattr(provider.client, "_model", ""),
                }
                self.routing_log.parent.mkdir(parents=True, exist_ok=True)
                with self.routing_log.open("a") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            return provider

        return create
