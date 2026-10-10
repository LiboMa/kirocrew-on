"""Composition routes poc-* templates to native factories; all paths come from Settings."""
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

    def seed(self, legacy=False):
        with patch.dict("sys.modules", {"kiro_crew.agent": self.stub}):
            return templates.seed(crew_home=self.crew, kiro_home=self.kiro, allowed_roots=[self.runs], port=6000,
                                  legacy_pipeline=legacy)

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
        self.assertEqual((self.crew / "config.json").read_text(), '{"user_config":"preserve"}\n')
        agent = json.loads((self.kiro / "agents/poc-claude.json").read_text())
        self.assertEqual((agent["model"], agent["resources"]), ("user-model", ["skill://x"]))
        self.assertIn("真实研发工作", agent["prompt"])

    def test_legacy_pipeline_templates_only_on_request(self):
        self.seed(legacy=True)
        legacy = json.loads((self.kiro / "agents/poc-pipeline-code.json").read_text())
        self.assertEqual(legacy["description"], "App PoC / claude worker")


if __name__ == "__main__":
    unittest.main()
