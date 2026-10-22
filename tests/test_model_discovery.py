"""D51: each environment publishes the models its Coding tools can really use, with no one asking: the Worker probes every
available tool KiroCrew has not yet seen a session of, once, with a fixed one-turn prompt (owner 2026-10-20)."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from octopus import gateway_client as crew_api
from octopus.worker import DISCOVERY_PROMPT, Worker, discovery_loop

TOOLS = {"kiro": {"available": True, "login": "ok"}, "claude": {"available": True}, "codex": {"available": True},
         "opencode": {"available": False, "reason": "已停用"}}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew", crew_yolo=True)
        self.calls, self.running, self.tools, self.failure = [], [], json.loads(json.dumps(TOOLS)), None

        def native(path, body=None, method=None, timeout=None, headers=None):
            self.calls.append((path, body))
            if path == "/api/chat/slots/octopus-models" and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is None:
                return {"agents": self.running}
            if path == "/api/spawn":
                if self.failure:
                    raise self.failure
                return {"id": "probe" + str(len(self.spawns()))}
            return {"ok": True}
        self.node.native = native
        patcher = patch.object(Worker, "tools", lambda node: self.tools)
        patcher.start()
        self.addCleanup(patcher.stop)

    def spawns(self):
        return [body for path, body in self.calls if path == "/api/spawn" and body is not None]

    def cache(self, **namespaces):
        (self.root / "crew").mkdir(parents=True, exist_ok=True)
        (self.root / "crew/provider_models.json").write_text(json.dumps(namespaces))

    def test_each_cold_available_tool_is_probed_once(self):
        self.assertEqual(self.node.discover_models(), ["kiro", "claude"])   # codex never publishes; opencode is disabled
        # kiro's list is KiroCrew's /api/models catalog (kiro-cli --list-models), never a session's (review 2026-10-20)
        self.assertIn(("/api/models", None), self.calls)
        probes = self.spawns()
        self.assertEqual([p["agent"] for p in probes], ["poc-claude"])
        for probe in probes:
            self.assertEqual(probe["task"], DISCOVERY_PROMPT)
            self.assertEqual((probe["max_turns"], probe["keep"], probe["parent_session"]), (1, False, "dashboard:octopus-models"))
            self.assertFalse(probe["include_memory"] or probe["include_lessons"] or probe["include_project"])
        record = json.loads((self.root / "worker/model-discovery.json").read_text())
        self.assertEqual(record["claude"]["result"], "spawned")

    def test_a_tool_that_published_its_models_is_left_alone(self):
        self.cache(claude_code=["us.anthropic.claude-opus-5-5"], acp=["claude-opus-5.5"])
        self.assertEqual(self.node.discover_models(), [])
        self.assertEqual(self.spawns(), [])

    def test_kiro_waits_for_its_login(self):
        self.tools["kiro"]["login"] = "expired"
        self.assertEqual(self.node.discover_models(), ["claude"])

    def test_a_busy_gateway_is_not_probed(self):
        self.running = [{"id": "real-task", "done": False}]
        self.assertEqual(self.node.discover_models(), ["kiro"])   # the catalog call takes no sub-agent slot
        self.assertEqual(self.spawns(), [])

    def test_a_probe_is_not_repeated_within_two_hours(self):
        self.node.discover_models()
        self.assertEqual(self.node.discover_models(), [])   # still cold: the first probe may not have started yet
        with patch("octopus.worker.time.time", return_value=__import__("time").time() + 2 * 3600 + 1):
            self.assertEqual(self.node.discover_models(), ["kiro", "claude"])

    def test_a_failed_probe_is_noted_not_raised(self):
        self.failure = crew_api.GatewayError(503, '{"error":"starting"}')
        self.assertEqual(self.node.discover_models(), ["kiro", "claude"])
        record = json.loads((self.root / "worker/model-discovery.json").read_text())
        self.assertEqual((record["claude"]["result"], record["claude"]["error"]), ("failed", "GatewayError"))

    def test_a_tool_that_stays_cold_is_given_up_after_three_tries(self):   # review: no end, a paid turn every 2 hours
        later = __import__("time").time()
        for round_ in range(4):
            with patch("octopus.worker.time.time", return_value=later + round_ * (2 * 3600 + 1)):
                tried = self.node.discover_models()
        self.assertEqual(tried, [])
        self.assertEqual(len(self.spawns()), 3)
        with patch.object(Worker, "tool_auth", lambda node: "ok"):
            state = self.node.health()["model_discovery"]
        self.assertEqual((state["claude"]["result"], state["claude"]["attempts"]), ("gave_up", 3))

    def test_a_list_that_appears_ends_discovery(self):
        self.node.discover_models()
        self.cache(claude_code=["us.anthropic.claude-opus-5-5"], acp=["claude-opus-5.5"])
        self.node.discover_models()
        record = json.loads((self.root / "worker/model-discovery.json").read_text())
        self.assertEqual((record["claude"]["result"], record["kiro"]["result"]), ("found", "found"))

    def test_without_auto_approval_no_probe_session_is_opened(self):   # review: it would wait on an approval
        self.node.crew_yolo, self.node.approval_mode = False, "interactive"
        self.assertEqual(self.node.discover_models(), ["kiro"])   # the catalog call needs no session
        self.assertEqual(self.spawns(), [])

    def test_a_deploy_drain_is_left_alone(self):
        (self.root / "worker/.maintenance").write_text("")
        self.assertEqual(self.node.discover_models(), [])
        self.assertEqual(self.calls, [])

    def test_one_probe_session_per_round(self):   # review: two at once could queue a Session's dispatch behind them
        self.tools["opencode"] = {"available": True}
        self.assertEqual(self.node.discover_models(), ["kiro", "claude"])
        self.assertEqual([p["agent"] for p in self.spawns()], ["poc-claude"])
        self.assertEqual(self.node.discover_models(), ["opencode"])

    def test_the_loop_runs_until_stopped(self):
        stop, seen = threading.Event(), []

        class Node:
            def discover_models(self):
                seen.append(True)
                stop.set()
        discovery_loop(Node(), stop, first=0, every=0)
        self.assertEqual(seen, [True])

    def test_a_failing_round_does_not_end_the_loop(self):
        stop, rounds = threading.Event(), []

        class Node:
            def discover_models(self):
                rounds.append(True)
                if len(rounds) == 2:
                    stop.set()
                raise crew_api.GatewayError(503, '{"error":"starting"}')
        discovery_loop(Node(), stop, first=0, every=0)
        self.assertEqual(len(rounds), 2)

    def test_the_worker_command_starts_it(self):   # nobody has to ask (owner 2026-10-20)
        text = (Path(__file__).resolve().parents[1] / "octopus/__main__.py").read_text()
        worker = text[text.index('elif args.command == "worker":'):text.index('elif args.command == "wait-ready":')]
        self.assertIn("target=discovery_loop", worker)
        self.assertIn("stop_discovery.set()", worker)

    def test_codex_says_which_model_auto_is(self):
        home = self.root / "home"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex/config.toml").write_text('model_provider = "amazon-bedrock"\nmodel = "openai.gpt-6-astra"\n')
        with patch("octopus.worker.Path.home", return_value=home), patch.object(Worker, "tool_auth", lambda node: "ok"):
            self.assertEqual(self.node.health()["model_defaults"], {"codex": "openai.gpt-6-astra"})


if __name__ == "__main__":
    unittest.main()
