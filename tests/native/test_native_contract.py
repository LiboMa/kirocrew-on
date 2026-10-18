"""Native contract against an installed KiroCrew release, using an isolated boundary config (never ~/.kiro).

Run under the KiroCrew interpreter, from the repo root:
  OCTOPUS_CONFIG=<boundary.toml> PYTHONPATH=. <kirocrew python> -s -m unittest discover -s tests/native -v
Not part of tools/regress.sh: it needs a real KiroCrew install and ACP adapters, but starts no models.
"""
import copy
import dataclasses
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from composition import environment, templates
from composition.registry import BACKENDS, PocProviderRegistry
from octopus.config import load

SETTINGS = load(os.environ.get("OCTOPUS_CONFIG"))
environment.prepare(SETTINGS, os.environ)

from kiro_crew import __version__  # noqa: E402
from kiro_crew._ssl_compat import _ensure_ssl_certs  # noqa: E402

_ensure_ssl_certs()
from kiro_crew.config import KiroCrewConfig  # noqa: E402
from kiro_crew.platform.defaults import DefaultProviderRegistry  # noqa: E402


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.cfg = KiroCrewConfig.load()
        self.cfg.agent.session_sharing = False
        self.cfg.session.pool_size = 0
        self.registry = PocProviderRegistry(DefaultProviderRegistry())

    def test_release_and_isolated_home(self):
        print(f"\nKiroCrew {__version__}; KIROCREW_HOME={os.environ['KIROCREW_HOME']}")
        self.assertEqual(os.environ["KIROCREW_HOME"], str(SETTINGS.crew_home))
        self.assertNotEqual(SETTINGS.crew_home, SETTINGS.crew_home.home() / ".kiro/crew")

    def test_every_template_reaches_its_backend_and_config_is_untouched(self):
        original = copy.deepcopy(self.cfg)
        factory = self.registry.create_factory(self.cfg)
        with tempfile.TemporaryDirectory() as directory:
            for name, expected in BACKENDS.items():
                with self.subTest(agent=name):
                    provider = factory(session_key=f"contract:{name}", agent=name, cwd=directory, model_override="auto")
                    self.assertEqual(provider.client.backend, expected)
            ordinary = factory(session_key="contract:ordinary", agent="kirocrew", cwd=directory, model_override="auto")
            self.assertEqual(ordinary.client.backend, original.agent.acp_backend)
        self.assertEqual(self.cfg, original)

    def test_unknown_template_and_pooling_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown PoC"):
            self.registry.create_factory(self.cfg)(session_key="contract:unknown", agent="poc-gemini")
        self.cfg.agent.session_sharing = True
        with self.assertRaises(RuntimeError):
            self.registry.create_factory(self.cfg)

    def test_coordinator_control_plane_mount(self):
        from kiro_crew.acp.session_mcp import kiro_control_plane_servers
        mount = kiro_control_plane_servers("poc-coordinator", work_dir=SETTINGS.crew_home / "workspace")
        entries = getattr(mount, "elements", mount)
        self.assertNotIn("kirocrew-core", getattr(mount, "withheld", {}))
        self.assertIn("kirocrew-core", [e["name"] for e in entries])

    def test_model_and_effort_reach_the_factory_and_spawn_schema(self):
        from kiro_crew.mcp_tools.spawn import schemas
        schema = next(s for s in schemas() if s["name"] == "spawn_run")["inputSchema"]["properties"]
        self.assertIn("model", schema)
        self.assertIn("reasoning_effort", schema)
        factory = self.registry.create_factory(self.cfg)
        for agent, backend, model, effort in (("poc-claude", "claude", "global.anthropic.claude-sonnet-4-6", "medium"),
                                              ("poc-codex", "codex", "openai.gpt-6-astra", "high")):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as directory:
                provider = factory(session_key=f"contract:workflow:{agent}", agent=agent, cwd=directory,
                                   model_override=model, reasoning_effort_override=effort)
                self.assertEqual(provider.client.backend, backend)
                expected = f"{model}[{effort}]" if backend == "codex" else model
                self.assertEqual(provider.client._model, expected)

    def test_public_spawn_schema_model_id_rules(self):
        # 0.8.0 rejects any bracket; 0.10.0 accepts one bounded "[qualifier]" suffix. Every verified release
        # rejects provider-qualified ids with "/" or ":" (K02), so the Worker keeps filtering those.
        from kiro_crew.validation import SPAWN_RUN_SCHEMA, ValidationError, validate_tool_args
        payload = {"task": "Read the real diff and report findings.", "agent": "poc-codex",
                   "model": "openai.gpt-6-astra", "reasoning_effort": "low"}
        self.assertEqual(validate_tool_args(payload, SPAWN_RUN_SCHEMA)["reasoning_effort"], "low")
        for model in ("opencode/big-pickle", "provider:model"):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                validate_tool_args({**payload, "model": model}, SPAWN_RUN_SCHEMA)
        try:
            validate_tool_args({**payload, "model": "openai.gpt-6-astra[low]"}, SPAWN_RUN_SCHEMA)
            print(f"\nKiroCrew {__version__}: bracket qualifier accepted by spawn_run")
        except ValidationError:
            print(f"\nKiroCrew {__version__}: bracket qualifier rejected by spawn_run")


CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "kirocrew_contract.json").read_text())


class ConfigContractTests(unittest.TestCase):
    """The keys the platform writes into config.json, against the KiroCrew being installed (tests/kirocrew_contract.json)."""

    def test_the_contract_is_for_this_kirocrew(self):
        self.assertEqual(CONTRACT["kirocrew_version"], __version__)

    def test_every_listed_key_is_a_field_kirocrew_reads(self):
        cfg = KiroCrewConfig()
        names = lambda value: {f.name for f in dataclasses.fields(value)}
        self.assertEqual(sorted(set(CONTRACT["config"][""]) - names(KiroCrewConfig)), [])
        for section, keys in CONTRACT["config"].items():
            if section:
                self.assertEqual(sorted(set(keys) - names(getattr(cfg, section))), [], section)

    def test_the_seeded_config_reads_back_with_no_key_left_over(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"], legacy_pipeline=False)
            with patch.dict(os.environ, {"KIROCREW_HOME": str(root / "crew")}):
                cfg = KiroCrewConfig.load()
            self.assertEqual((cfg._extra_keys, cfg._extra_sections), ({}, {}))
            self.assertEqual(cfg.agent.adaptive_concurrency_mode, "fixed")   # and what was written is what it reads



class RuntimeErrorContractTests(unittest.TestCase):
    """The installed KiroCrew still words its runtime failures the way the platform's L0 retry reads them
    (tests/kirocrew_contract.json runtime_errors)."""

    def test_every_runtime_failure_phrase_is_in_the_installed_kirocrew(self):
        import kiro_crew
        sources = "\n".join(path.read_text(errors="replace") for path in Path(kiro_crew.__file__).parent.rglob("*.py"))
        missing = [item["phrase"] for item in CONTRACT["runtime_errors"] if item["phrase"] not in sources]
        self.assertEqual(missing, [], "KiroCrew no longer says these: update octopus/engine.py transient and the contract")



class HttpContractTests(unittest.TestCase):
    """Each handler module the platform's request bodies go to still reads every key the contract lists for it
    (tests/kirocrew_contract.json http): a renamed or removed field fails the deploy instead of being ignored."""

    def test_every_listed_key_is_read_by_its_handler_module(self):
        import kiro_crew
        package = Path(kiro_crew.__file__).parent
        for endpoint, entry in CONTRACT["http"].items():
            with self.subTest(endpoint):
                module = package / entry["module"]
                self.assertTrue(module.is_file(), f"{entry['module']} is no longer in KiroCrew")
                text = module.read_text()
                self.assertEqual([k for k in entry["reads"] if f'"{k}"' not in text and f"'{k}'" not in text], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
