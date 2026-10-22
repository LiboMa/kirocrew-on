"""D46 P0: the KiroCrew boundary as KiroCrew 0.10.0 reads it — the platform's parent Sessions keep out of the boundary's
global memory (a shared environment serves several spaces), the coordinator's spawn allowlist sits where KiroCrew reads it,
it cannot arm its own monitor loops, and no ignored fields are sent."""
import gzip
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
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
            templates.seed(crew_home=self.crew, kiro_home=self.kiro, allowed_roots=[self.runs], legacy_pipeline=False)

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

    def test_kirocrews_own_tools_also_exclude_the_monitors(self):
        # kiro-cli applies excludedTools; KiroCrew's kirocrew-core filters by managedToolPolicy.exclude (sessions.py:4925)
        names = {tool.rsplit("/", 1)[1] for tool in MONITORS}
        self.assertTrue(names <= set(self.spec("poc-coordinator")["managedToolPolicy"]["exclude"]))


class LegacyConfigTests(unittest.TestCase):
    def test_an_existing_config_loses_the_keys_kirocrew_never_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "crew").mkdir()
            (root / "crew/config.json").write_text(json.dumps({
                "memory": {"enabled": False}, "dashboard": {"host": "127.0.0.1", "port": 5476, "onboarded": True},
                "workspaces": {"default": {"path": "/w"}}, "default_workspace": "default", "user_config": "keep",
                "agent": {"model": "user-model"}}))
            with patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
                templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"], legacy_pipeline=False)
            config = json.loads((root / "crew/config.json").read_text())
        self.assertNotIn("memory", config)
        self.assertEqual(config["dashboard"], {"onboarded": True})
        self.assertNotIn("workspaces", config)
        self.assertEqual((config["user_config"], config["agent"]["model"], config["default_workspace"]), ("keep", "user-model", "default"))


class MigrationTests(unittest.TestCase):
    def test_an_existing_coordinator_spec_moves_the_allowlist_and_keeps_user_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "kiro/agents").mkdir(parents=True)
            (root / "kiro/agents/poc-coordinator.json").write_text(json.dumps({
                "name": "poc-coordinator", "availableAgents": ["poc-kiro"], "excludedTools": ["@user/tool"],
                "toolsSettings": {"shell": {"user": "keep"}}}))
            with patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
                templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"],
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

        self.modes = {}   # what KiroCrew keeps of each slot: its memory mode (chat_handlers.py:1997, default persistent)

        def native(path, body=None, method=None):
            self.calls.append((path, body))
            if path == "/api/chat/slots/octopus-" + self.rid and body is None and method is None and not self.modes:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                self.modes["octopus-" + self.rid] = body.get("memory_mode", "persistent")
                return {"key": "octopus-" + self.rid}
            if path == "/api/spawn":
                return {"agents": []}
            return {"ok": True, "running": False, "queue_depth": 0}
        self.node.native = native

    def export(self, request, timeout=None):
        """KiroCrew 0.10.0's export (session_export.py:380): an incognito or temporary slot is refused, never exported."""
        slot = request.full_url.split("/api/chat/slots/")[1].split("/")[0]
        if self.modes.get(slot) != "persistent":
            raise urllib.error.HTTPError(request.full_url, 400, "restricted", {},
                                         io.BytesIO(b'{"code":"export_slot_not_persistent"}'))
        return io.BytesIO(gzip.compress(json.dumps({"kind": "kcsession", "slot": slot}).encode()))

    def test_a_parent_session_the_platform_creates_can_be_backed_up_and_closed(self):
        # EC2 2026-10-17 (3ec713b): parents made incognito could not be exported, so no new Session could be closed or deleted
        self.node.prepare({"run_id": self.rid, "source": {"type": "new"}})
        with patch("octopus.gateway_client.token", return_value="test-only"), patch("urllib.request.urlopen", side_effect=self.export):
            result = self.node.control({"run_id": self.rid, "action": "close"})
        self.assertEqual(result["lifecycle"], "closed")
        self.assertTrue((self.node.path(self.rid) / "native-session-backup.gz").is_file())


class SpawnTests(unittest.TestCase):
    def test_no_ignored_spawn_fields_are_sent(self):
        source = Path(__file__).resolve().parents[1].joinpath("octopus", "worker.py").read_text()
        self.assertNotIn('"solo_reason"', source)   # KiroCrew 0.10.0: unadvertised and ignored
        self.assertNotIn('"solo_details"', source)


if __name__ == "__main__":
    unittest.main()
