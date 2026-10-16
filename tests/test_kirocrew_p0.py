"""D46 P0: the KiroCrew boundary as KiroCrew 0.10.0 reads it — the platform's parent Sessions keep out of the boundary's
global memory (a shared environment serves several spaces), the coordinator's spawn allowlist sits where KiroCrew reads it,
it cannot arm its own monitor loops, and no ignored fields are sent."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composition import templates
from octopus import gateway_client as crew_api
from octopus.worker import Worker

MONITORS = {"@kirocrew-core/monitor_watch", "@kirocrew-core/monitor_start", "@kirocrew-core/monitor_update",
            "@kirocrew-core/monitor_inspect", "@kirocrew-core/monitor_stop"}


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.crew, self.kiro, self.runs = root / "crew", root / "kiro", root / "worker"
        with patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
            templates.seed(crew_home=self.crew, kiro_home=self.kiro, allowed_roots=[self.runs], port=6000, legacy_pipeline=False)

    def spec(self, name):
        return json.loads((self.kiro / "agents" / f"{name}.json").read_text())

    def test_a_fresh_config_writes_no_key_kirocrew_ignores(self):
        config = json.loads((self.crew / "config.json").read_text())
        self.assertNotIn("enabled", config.get("memory", {}))   # not a 0.10.0 memory key: it switched nothing off

    def test_a_fresh_config_pins_the_concurrency_cap(self):
        config = json.loads((self.crew / "config.json").read_text())
        self.assertEqual(config["agent"]["adaptive_concurrency_mode"], "fixed")   # max_subagents is what it says

    def test_the_coordinators_spawn_allowlist_is_where_kirocrew_reads_it(self):
        spec = self.spec("poc-coordinator")
        self.assertNotIn("availableAgents", spec)   # top level is read as "allow all"
        allowed = set(spec["toolsSettings"]["subagent"]["availableAgents"])
        self.assertTrue({"poc-kiro", "poc-claude", "poc-codex", "poc-diagnose"} <= allowed)   # the platform dispatches these

    def test_the_coordinator_cannot_arm_its_own_monitor_loops(self):
        self.assertTrue(MONITORS <= set(self.spec("poc-coordinator")["excludedTools"]))


class MigrationTests(unittest.TestCase):
    def test_an_existing_coordinator_spec_moves_the_allowlist_and_keeps_user_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "kiro/agents").mkdir(parents=True)
            (root / "kiro/agents/poc-coordinator.json").write_text(json.dumps({
                "name": "poc-coordinator", "availableAgents": ["poc-kiro"], "excludedTools": ["@user/tool"],
                "toolsSettings": {"shell": {"user": "keep"}}}))
            with patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
                templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"], port=6000,
                               legacy_pipeline=False)
            spec = json.loads((root / "kiro/agents/poc-coordinator.json").read_text())
        self.assertNotIn("availableAgents", spec)
        self.assertIn("poc-diagnose", spec["toolsSettings"]["subagent"]["availableAgents"])
        self.assertEqual(spec["toolsSettings"]["shell"], {"user": "keep"})
        self.assertTrue({"@user/tool"} | MONITORS <= set(spec["excludedTools"]))


class ParentSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        self.rid, self.calls = "run-" + "1" * 32, []

        def native(path, body=None, method=None):
            self.calls.append((path, body))
            if path == "/api/chat/slots/octopus-" + self.rid and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + self.rid}
            return {"ok": True}
        self.node.native = native

    def test_a_new_parent_session_keeps_out_of_the_global_memory(self):
        self.node.prepare({"run_id": self.rid, "source": {"type": "new"}})
        created = [body for path, body in self.calls if path == "/api/chat/slots"]
        self.assertEqual(created[0]["memory_mode"], "incognito")


class SpawnTests(unittest.TestCase):
    def test_no_ignored_spawn_fields_are_sent(self):
        source = Path(__file__).resolve().parents[1].joinpath("octopus", "worker.py").read_text()
        self.assertNotIn('"solo_reason"', source)   # KiroCrew 0.10.0: unadvertised and ignored
        self.assertNotIn('"solo_details"', source)


if __name__ == "__main__":
    unittest.main()
