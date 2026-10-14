"""Composition routes poc-* templates to native factories; all paths come from Settings."""
import dataclasses
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composition import environment, registry, templates
from octopus.config import load


class FakeConfig:
    def __init__(self, sharing=False, pool=0):
        self.agent = SimpleNamespace(session_sharing=sharing, acp_backend="", member_acp_backend="kas")
        self.session = SimpleNamespace(pool_size=pool)

    def __deepcopy__(self, memo):
        clone = FakeConfig(self.agent.session_sharing, self.session.pool_size)
        clone.agent.acp_backend, clone.agent.member_acp_backend = self.agent.acp_backend, self.agent.member_acp_backend
        return clone

    def create_provider_factory(self):
        backend = self.agent.acp_backend

        def factory(session_key=None, agent=None, **kwargs):
            return SimpleNamespace(client=SimpleNamespace(backend=backend, _model=kwargs.get("model_override", "")),
                                   cwd=kwargs.get("cwd", ""))
        return factory


class FakeOriginal:
    def register_acp_backends(self):
        return "registered"

    def create_factory(self, cfg):
        return lambda session_key=None, agent=None, **kwargs: SimpleNamespace(client=SimpleNamespace(backend="ordinary"))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.log = Path(self.temporary.name) / "routing.jsonl"
        self.registry = registry.PocProviderRegistry(FakeOriginal(), routing_log=self.log)

    def tearDown(self):
        self.temporary.cleanup()

    def test_every_template_reaches_its_backend_and_is_logged(self):
        factory = self.registry.create_factory(FakeConfig())
        for name, backend in registry.ROUTES.items():
            self.assertEqual(factory(session_key="k:" + name, agent=name).client.backend, backend)
        lines = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual([l["agent"] for l in lines], list(registry.ROUTES))
        self.assertEqual(lines[0]["actual_backend"], "kiro")

    def test_ordinary_agents_use_the_original_factory(self):
        factory = self.registry.create_factory(FakeConfig())
        self.assertEqual(factory(agent="kirocrew").client.backend, "ordinary")
        self.assertEqual(self.registry.register_acp_backends(), "registered")

    def test_unknown_poc_template_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown PoC template"):
            self.registry.create_factory(FakeConfig())(agent="poc-gemini")

    def test_pooling_or_sharing_is_refused(self):
        for cfg in (FakeConfig(sharing=True), FakeConfig(pool=2)):
            with self.subTest(), self.assertRaises(RuntimeError):
                self.registry.create_factory(cfg)

    def test_codex_effort_is_paired_at_the_factory_seam(self):
        effort = SimpleNamespace(is_valid_effort=lambda e: e == "high", model_supports_effort=lambda m: True)
        names = SimpleNamespace(split_effort_suffix=lambda m: (m, ""))
        with patch.dict("sys.modules", {"kiro_crew.effort": effort, "kiro_crew.model_registry": names}):
            provider = self.registry.create_factory(FakeConfig())(
                agent="poc-codex", model_override="gpt-x", reasoning_effort_override="high")
        self.assertEqual(provider.client._model, "gpt-x[high]")

    def test_config_is_not_mutated(self):
        cfg = FakeConfig()
        before = copy.deepcopy(cfg)
        self.registry.create_factory(cfg)
        self.assertEqual((cfg.agent.acp_backend, cfg.agent.member_acp_backend),
                         (before.agent.acp_backend, before.agent.member_acp_backend))


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.adapters = root / "node_modules/.bin"
        self.adapters.mkdir(parents=True)
        for name in ("claude-agent-acp", "codex-acp"):
            (self.adapters / name).write_text("#!/bin/sh\n")
        self.settings = load(environ={"HOME": str(root)}, cwd=root, overrides={
            "adapters_dir": str(self.adapters), "kirocrew_python": str(root / "kc/bin/python3.12"),
            "tools": {"claude": str(root / "t/claude"), "codex": str(root / "t/codex")}, "gateway_port": 6000})

    def tearDown(self):
        self.temporary.cleanup()

    def test_environment_comes_from_settings_without_mac_paths(self):
        env = {"PATH": "/usr/bin"}
        environment.prepare(self.settings, env, which=lambda name: None)
        self.assertEqual(env["KIROCREW_HOME"], str(self.settings.crew_home))
        self.assertEqual(env["KIRO_HOME"], str(self.settings.kiro_home))
        self.assertEqual(env["KIROCREW_PORT"], "6000")
        self.assertEqual(env["CLAUDE_AGENT_ACP_BIN"], str(self.adapters / "claude-agent-acp"))
        self.assertEqual(env["CODEX_PATH"], str(self.settings.tools["codex"]))
        self.assertEqual(env["CLAUDE_CODE_EXECUTABLE"], str(self.settings.tools["claude"]))
        parts = env["PATH"].split(":")
        self.assertEqual(parts[0], str(self.settings.kirocrew_python.parent))
        self.assertEqual(parts[-1], "/usr/bin")
        self.assertNotIn("/Applications", json.dumps(env))
        self.assertNotIn("/opt/homebrew", env["PATH"])
        self.assertTrue(self.settings.crew_home.is_dir())

    def test_browser_entry_origin_is_trusted_by_the_gateway(self):
        # The console opens the native chat at crew_url: behind CloudFront that origin must pass KiroCrew's Host and
        # CSRF checks; through an SSM tunnel the local port must be an explicitly trusted loopback port.
        cases = {"https://dnative.cloudfront.net": ("https://dnative.cloudfront.net", None),
                 "http://localhost:15476": (None, "15476"), "": (None, None)}
        for crew_url, (origins, ports) in cases.items():
            with self.subTest(crew_url=crew_url):
                settings = dataclasses.replace(self.settings, crew_url=crew_url)
                env = {"PATH": "/usr/bin"}
                environment.prepare(settings, env, which=lambda name: None)
                self.assertEqual(env.get("KIROCREW_CORS_ORIGINS"), origins)
                self.assertEqual(env.get("KIROCREW_ALLOWED_LOOPBACK_PORTS"), ports)

    def test_missing_adapter_is_reported(self):
        (self.adapters / "codex-acp").unlink()
        with self.assertRaisesRegex(RuntimeError, "codex-acp"):
            environment.prepare(self.settings, {"PATH": ""}, which=lambda name: None)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.crew, self.kiro, self.runs = root / "crew", root / "kiro", root / "worker"
        self.stub = SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "native-managed-entry"})

    def tearDown(self):
        self.temporary.cleanup()

    def seed(self, legacy=False, **kwargs):
        with patch.dict("sys.modules", {"kiro_crew.agent": self.stub}):
            return templates.seed(crew_home=self.crew, kiro_home=self.kiro, allowed_roots=[self.runs], port=6000,
                                  legacy_pipeline=legacy, **kwargs)

    def test_fresh_config_allows_only_worker_roots(self):
        self.seed()
        config = json.loads((self.crew / "config.json").read_text())
        self.assertEqual(config["agent"]["subagent_cwd_allowed_roots"], [str(self.runs)])
        self.assertFalse(config["agent"]["session_sharing"])
        self.assertEqual(config["session"]["pool_size"], 0)
        self.assertEqual(config["dashboard"]["port"], 6000)
        names = sorted(p.stem for p in (self.kiro / "agents").glob("*.json"))
        self.assertEqual(names, sorted(registry.BACKENDS))
        coordinator = json.loads((self.kiro / "agents/poc-coordinator.json").read_text())
        self.assertEqual(coordinator["mcpServers"]["kirocrew-core"], {"command": "native-managed-entry"})

    def test_existing_config_and_user_agent_fields_are_preserved(self):
        self.crew.mkdir(parents=True)
        (self.crew / "config.json").write_text('{"user_config":"preserve"}\n')
        (self.kiro / "agents").mkdir(parents=True)
        (self.kiro / "agents/poc-claude.json").write_text(json.dumps({"name": "poc-claude", "model": "user-model",
                                                                       "prompt": "old", "resources": ["skill://x"]}))
        self.seed()
        # only the standing approval switch is written (crew_yolo); everything else the user set stays
        self.assertEqual(json.loads((self.crew / "config.json").read_text()),
                         {"user_config": "preserve", "agent": {"dangerously_skip_permissions": True}})
        agent = json.loads((self.kiro / "agents/poc-claude.json").read_text())
        self.assertEqual((agent["model"], agent["resources"]), ("user-model", ["skill://x"]))
        self.assertIn("真实研发工作", agent["prompt"])

    def test_yolo_is_kept_on_by_the_boundary_setting_and_can_be_turned_off(self):
        self.seed()
        config = json.loads((self.crew / "config.json").read_text())
        self.assertIs(config["agent"]["dangerously_skip_permissions"], True)   # a real bool: KiroCrew ignores strings
        self.seed(yolo=False)
        config = json.loads((self.crew / "config.json").read_text())
        self.assertIs(config["agent"]["dangerously_skip_permissions"], False)
        self.assertEqual(config["agent"]["approval_mode"], "interactive")   # the rest of the agent section stays
        text = (self.crew / "config.json").read_text()
        self.seed(yolo=False)
        self.assertEqual((self.crew / "config.json").read_text(), text)   # already as set: not rewritten

    def test_legacy_pipeline_templates_only_on_request(self):
        self.seed(legacy=True)
        legacy = json.loads((self.kiro / "agents/poc-pipeline-code.json").read_text())
        self.assertEqual(legacy["description"], "App PoC / claude worker")


class ApprovalModeTests(unittest.TestCase):
    """D14 (narrowed after the local run): only platform-dispatched spawns get native auto approval.
    The boundary's global KiroCrew tool approval stays interactive, so owner and parent chats keep prompting."""

    def test_seed_keeps_global_tool_approval_interactive(self):
        stub = SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict("sys.modules", {"kiro_crew.agent": stub}):
                templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"],
                               port=6000, legacy_pipeline=False)
            config = json.loads((root / "crew/config.json").read_text())
            self.assertEqual(config["agent"]["approval_mode"], "interactive")


class CompositionMarkerTests(unittest.TestCase):
    """I4: only the process that actually serves with the composed registry can mark itself ready."""

    def test_factory_marker_is_written_by_the_composed_registry(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "gateway-factory.json"
            registry.PocProviderRegistry(FakeOriginal(), factory_marker=marker).create_factory(FakeConfig())
            value = json.loads(marker.read_text())
            self.assertEqual(value["pid"], os.getpid())
            self.assertIn("built_at", value)

    def test_live_target_reexec_is_disabled_with_the_native_marker(self):
        # Observed on EC2 (0.10.0): the Gateway itself writes live_target.json ({"checkout": null}) at startup, so the
        # file's presence is not a re-exec. The composition instead sets KiroCrew's own exec-chain marker, which makes
        # maybe_reexec() return before any execve could drop PocProviderRegistry.
        from composition.gateway import disable_live_target_reexec
        stub = SimpleNamespace(EXEC_MARKER="KIROCREW_LIVE_EXECED")
        env = {}
        with patch.dict("sys.modules", {"kiro_crew.service.live_target": stub}):
            self.assertTrue(disable_live_target_reexec(env))
        self.assertEqual(env, {"KIROCREW_LIVE_EXECED": "1"})
        env = {}
        with patch.dict("sys.modules", {"kiro_crew.service.live_target": None}):
            self.assertFalse(disable_live_target_reexec(env))
        self.assertEqual(env, {})


class DiagnosisTemplateTests(unittest.TestCase):
    """Focused review I1: diagnosis runs with native auto approval, so it must not be able to delegate."""

    def test_diagnosis_template_has_no_spawn_capability_and_routes_to_kiro(self):
        self.assertEqual(registry.BACKENDS["poc-diagnose"], "")
        stub = SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict("sys.modules", {"kiro_crew.agent": stub}):
                templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"],
                               port=6000, legacy_pipeline=False)
            spec = json.loads((root / "kiro/agents/poc-diagnose.json").read_text())
        self.assertEqual(spec["tools"], ["read", "write", "shell"])
        self.assertEqual(spec["mcpServers"], {})
        self.assertNotIn("availableAgents", spec)
        self.assertNotIn("@kirocrew-core", json.dumps(spec))

class VersionGateTests(unittest.TestCase):
    """A KiroCrew release outside the verified set serves only after its contract check is accepted in config."""

    def test_verified_and_explicitly_accepted_versions(self):
        from composition.gateway import version_accepted
        self.assertTrue(version_accepted("0.8.0", ()))
        self.assertFalse(version_accepted("0.10.0", ()))
        self.assertTrue(version_accepted("0.10.0", ("0.10.0",)))

if __name__ == "__main__":
    unittest.main()
