"""D46 P6a, the platform's side: a Session totals what its Attempts used; the task cards, the report and the MCP status
show it."""
import json
import unittest

from octopus import mcp, report, usage
from octopus.db import dumps
from tests.test_confirmation import ConfirmationCase

KIRO = {"credits": 12.4, "seconds": 125.5, "turns": 7, "tokens": "unavailable"}
CLAUDE = {"credits": 0.0, "seconds": 200.0, "input_tokens": 120000, "output_tokens": 8000, "cache_read_tokens": 50000,
          "cost_usd": 0.84, "model": "claude-sonnet-x"}


class TotalTests(unittest.TestCase):
    def test_the_total_adds_every_attempt(self):
        total = usage.total([{"usage": KIRO}, {"usage": CLAUDE}, {"usage": CLAUDE}])
        self.assertEqual(total, {"credits": 12.4, "input_tokens": 240000, "output_tokens": 16000, "cost_usd": 1.68,
                                 "seconds": 525.5, "attempts": 3, "tokens_unavailable": 1})

    def test_older_attempts_without_usage_are_skipped(self):
        self.assertIsNone(usage.total([{}, {"outcome": {}}]))
        self.assertEqual(usage.total([{}, {"usage": KIRO}])["attempts"], 1)

    def test_a_line_says_what_was_used(self):
        self.assertEqual(usage.line(KIRO), "12.4 credits · 2 分 6 秒")
        self.assertEqual(usage.line(CLAUDE), "输入 120k / 输出 8k token · $0.84 · 3 分 20 秒")
        self.assertEqual(usage.line({"seconds": 42.0}), "42 秒")
        self.assertEqual(usage.line({}), "—")


class SessionTests(ConfirmationCase):
    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        if path == "/inspect":
            spec = self.bodies[body["attempt_id"]]["spec"]
            result["evidence"] = {**result["evidence"], "usage": KIRO if spec["tool"] == "kiro" else CLAUDE}
        return result

    def finished(self):
        rid = self.start()
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.last = run
        return run

    def test_the_session_totals_every_attempt(self):
        run = self.finished()   # code (claude) and review (kiro)
        self.assertEqual(run["usage"]["attempts"], 2)
        self.assertEqual(run["usage"]["credits"], 12.4)
        self.assertEqual(run["usage"]["input_tokens"], 120000)
        self.assertEqual(run["usage"]["cost_usd"], 0.84)

    def test_the_report_has_a_usage_section(self):
        text = report.markdown(self.finished())
        self.assertIn("## 用量", text)
        self.assertIn("输入 120k / 输出 8k token · $0.84 · 3 分 20 秒", text)
        self.assertIn("合计", text)
        page = report.html(self.last)
        self.assertIn("<h2>用量</h2>", page)
        self.assertIn("$0.84", page)

    def test_the_compact_status_carries_usage(self):
        view = mcp.compact_status(self.finished(), [])["session"]
        self.assertEqual(view["usage"]["credits"], 12.4)
        code = next(t for t in view["tasks"] if t["id"] == "code")
        self.assertEqual(code["latest"]["usage"]["cost_usd"], 0.84)



class OlderWorkerTests(ConfirmationCase):
    def test_a_session_before_usage_has_none(self):   # a Worker before P6a: no usage in its evidence
        run = self.tick(self.start())
        self.assertEqual(run["status"], "completed")
        self.assertIsNone(run.get("usage"))
        self.assertNotIn("## 用量", report.markdown(run))

if __name__ == "__main__":
    unittest.main()
