"""D46 P6a, the Worker's side: when a native task has ended, the Attempt records what it used — Kiro credits and wall time
from the run, tokens and cost from KiroCrew's usage row for that run — read once and kept in the Attempt's folder."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client as crew_api
from octopus.worker import Worker

RID = "run-" + "1" * 32
FIRST, SECOND = "attempt-" + "a" * 32, "attempt-" + "b" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现加法并运行真实测试"}
STARTED = 1_790_000_000.0


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class UsageWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        self.calls, self.runs, self.turns, self.turns_error = [], {}, [], None

        def native(path, body=None, method=None, timeout=None, headers=None):
            self.calls.append(path)
            if path.startswith("/api/chat/slots/octopus-") and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is not None:
                self.spawned = getattr(self, "spawned", 0) + 1
                return {"id": "aaaa1111" if self.spawned == 1 else "bbbb2222"}
            if path.endswith("/continue"):
                return {"id": "bbbb2222", "conversation": "aaaa1111", "status": "spawned"}
            if path == "/api/spawn":
                return {"agents": [{"id": k, "parent": "dashboard:octopus-" + RID, "done": True} for k in self.runs]}
            if path.startswith("/api/spawn/"):
                return {"id": path.rsplit("/", 1)[1], "done": True, "result": "完成", **self.runs[path.rsplit("/", 1)[1]]}
            if path.startswith("/api/usage/turns"):
                if self.turns_error:
                    raise self.turns_error
                return {"slot": "subagent:aaaa1111", "days": 2, "turns": self.turns}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})

    def run_attempt(self, attempt=FIRST, native_id="aaaa1111", **extra):
        self.runs.setdefault(native_id, {"started": STARTED, "elapsed": 200.0, "credits": 0.0})
        self.node.dispatch({"run_id": RID, "attempt_id": attempt, "input": "需求", "spec": SPEC, "upstream": [], **extra})
        return self.node.inspect({"run_id": RID, "attempt_id": attempt})["evidence"]["usage"]

    def test_a_kiro_run_reports_credits_and_time(self):
        self.runs["aaaa1111"] = {"started": STARTED, "elapsed": 125.5, "credits": 12.4, "turns": 7}
        self.turns = [{"ts": iso(STARTED + 125), "model": "auto", "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
                       "credits": 12.4, "cost": 0.0}]
        usage = self.run_attempt()
        self.assertEqual((usage["credits"], usage["seconds"]), (12.4, 125.5))
        self.assertEqual(usage["cost_usd"], 0.0)

    def test_a_claude_run_reports_tokens_and_cost(self):
        self.turns = [{"ts": iso(STARTED + 190), "model": "claude-sonnet-x", "input": 120000, "output": 8000, "cache_read": 50000,
                       "cache_create": 2000, "credits": 0, "cost": 0.84}]
        usage = self.run_attempt()
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"], usage["cache_write_tokens"]),
                         (120000, 8000, 50000, 2000))
        self.assertEqual((usage["cost_usd"], usage["model"]), (0.84, "claude-sonnet-x"))
        self.assertTrue(any(c.startswith("/api/usage/turns?") and "subagent%3Aaaaa1111" in c for c in self.calls))

    def test_continued_runs_take_their_own_row(self):
        self.turns = [{"ts": iso(STARTED + 190), "model": "m", "input": 1000, "output": 10, "cost": 0.1},
                      {"ts": iso(STARTED + 5000 + 90), "model": "m", "input": 3000, "output": 30, "cost": 0.3}]
        self.run_attempt()
        self.runs["bbbb2222"] = {"started": STARTED + 5000, "elapsed": 100.0, "credits": 0.0}
        usage = self.run_attempt(SECOND, "bbbb2222", **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        self.assertEqual((usage["input_tokens"], usage["cost_usd"]), (3000, 0.3))
        self.assertTrue(any("subagent%3Aaaaa1111" in c for c in self.calls if c.startswith("/api/usage/turns?")))

    def test_without_a_row_tokens_are_unavailable(self):
        self.turns = [{"ts": iso(STARTED - 3600), "model": "m", "input": 5, "output": 5, "cost": 0.01}]   # another run's
        usage = self.run_attempt()
        self.assertEqual(usage["tokens"], "unavailable")
        self.assertNotIn("input_tokens", usage)
        self.assertEqual(usage["seconds"], 200.0)

    def test_usage_is_read_once(self):
        self.turns = [{"ts": iso(STARTED + 10), "model": "m", "input": 1, "output": 1, "cost": 0.0}]
        self.run_attempt()
        first = sum(c.startswith("/api/usage/turns") for c in self.calls)
        self.node.inspect({"run_id": RID, "attempt_id": FIRST})
        self.assertEqual(sum(c.startswith("/api/usage/turns") for c in self.calls), first)
        self.assertTrue((Path(self.tmp.name) / "worker" / RID / "attempts" / FIRST / "usage.json").is_file())

    def test_odd_values_are_dropped(self):
        self.runs["aaaa1111"] = {"started": STARTED, "elapsed": "soon", "credits": -3}
        self.turns = [{"ts": iso(STARTED + 10), "model": "m", "input": "many", "output": True, "cost": float("nan")}]
        usage = self.run_attempt()
        for key in ("seconds", "credits", "input_tokens", "output_tokens", "cost_usd"):
            self.assertNotIn(key, usage)

    def test_a_failing_query_never_fails_the_inspect(self):
        self.turns_error = crew_api.GatewayError(500, '{"error":"boom"}')
        usage = self.run_attempt()
        self.assertEqual(usage["tokens"], "unavailable")
        self.assertEqual(usage["seconds"], 200.0)


if __name__ == "__main__":
    unittest.main()
