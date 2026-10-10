"""Contract checks against the installed KiroCrew factory, without starting LLMs."""
import copy
import tempfile
import unittest
from pathlib import Path

import poc
poc.prepare_environment()
from kiro_crew._ssl_compat import _ensure_ssl_certs
_ensure_ssl_certs()
from kiro_crew.config import KiroCrewConfig
from kiro_crew.platform.defaults import DefaultProviderRegistry
from kiro_crew.acp.session_mcp import kiro_control_plane_servers


class RoutingContractTests(unittest.TestCase):
    def setUp(self):
        self.cfg = KiroCrewConfig.load()
        self.cfg.agent.session_sharing = False
        self.cfg.session.pool_size = 0
        self.registry = poc.PocProviderRegistry(DefaultProviderRegistry())

    def test_actual_backend_and_config_isolation(self):
        original = copy.deepcopy(self.cfg)
        factory = self.registry.create_factory(self.cfg)
        with tempfile.TemporaryDirectory() as directory:
            for name, expected in poc.BACKENDS.items():
                with self.subTest(agent=name):
                    provider = factory(session_key=f"contract:{name}", agent=name,
                                       cwd=directory, model_override="auto")
                    self.assertEqual(provider.client.backend, expected)
            # Same factory selects Kiro again after creating other backends.
            provider = factory(session_key="contract:kiro-again", agent="poc-kiro",
                               cwd=directory, model_override="auto")
            self.assertEqual(provider.client.backend, "")
            ordinary = factory(session_key="contract:ordinary", agent="kirocrew",
                               cwd=directory, model_override="auto")
            self.assertEqual(ordinary.client.backend, original.agent.acp_backend)
        self.assertEqual(self.cfg, original)

    def test_unknown_poc_template_is_not_silently_kiro(self):
        factory = self.registry.create_factory(self.cfg)
        with self.assertRaisesRegex(ValueError, "Unknown PoC"):
            factory(session_key="contract:unknown", agent="poc-gemini")

    def test_native_control_plane_can_receive_identity(self):
        mount = kiro_control_plane_servers("poc-coordinator", work_dir=poc.CREW/"workspace")
        # 0.8.0 reports both mounted elements and native withholding verdicts.
        # Older supported releases returned the element list directly.
        entries = getattr(mount, "elements", mount)
        self.assertNotIn("kirocrew-core", getattr(mount, "withheld", {}))
        self.assertIn("kirocrew-core", [e["name"] for e in entries])

    def test_incompatible_pooling_refused(self):
        self.cfg.agent.session_sharing = True
        with self.assertRaises(RuntimeError):
            self.registry.create_factory(self.cfg)

    def test_workflow_parameters_reach_native_factory_and_mcp_schema(self):
        from kiro_crew.mcp_tools.spawn import schemas
        schema = next(s for s in schemas() if s["name"] == "spawn_run")["inputSchema"]["properties"]
        self.assertIn("model", schema)
        self.assertIn("reasoning_effort", schema)
        factory = self.registry.create_factory(self.cfg)
        for agent, backend, model, effort in (
            ("poc-claude", "claude", "global.anthropic.claude-sonnet-4-6", "medium"),
            ("poc-codex", "codex", "openai.gpt-6-astra", "high"),
            ("poc-codex", "codex", "openai.gpt-6-astra[low]", "low"),
        ):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as directory:
                provider = factory(session_key=f"contract:workflow:{agent}", agent=agent,
                                   cwd=directory, model_override=model,
                                   reasoning_effort_override=effort)
                self.assertEqual(provider.client.backend, backend)
                expected_wire = model.split("[", 1)[0] + f"[{effort}]" if backend == "codex" else model
                self.assertEqual(provider.client._model, expected_wire)
                self.assertEqual(provider._effort_per_model[expected_wire], effort)

    def test_public_spawn_validation_and_internal_wire_id_are_distinct(self):
        from kiro_crew.validation import SPAWN_RUN_SCHEMA, ValidationError, validate_tool_args
        payload = {"task": "Read the real diff and report findings.", "agent": "poc-codex",
                   "model": "openai.gpt-6-astra", "reasoning_effort": "low"}
        accepted = validate_tool_args(payload, SPAWN_RUN_SCHEMA)
        self.assertEqual(accepted["reasoning_effort"], "low")
        with self.assertRaises(ValidationError):
            validate_tool_args({**payload, "model": "openai.gpt-6-astra[low]"}, SPAWN_RUN_SCHEMA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
