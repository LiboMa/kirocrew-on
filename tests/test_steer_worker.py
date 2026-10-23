"""D46 P5b, the Worker's side: a running task of this Session is given a note through KiroCrew's steer; deleting the
Session releases its continuable native conversations."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client as crew_api
from octopus.security import Error
from octopus.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
RID, OTHER = "run-" + "1" * 32, "run-" + "9" * 32
FIRST, SECOND, THIRD = ("attempt-" + c * 32 for c in "abc")
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现加法并运行真实测试"}


class SteerWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        self.calls, self.answers, self.ids = [], {}, iter(["aaaa1111", "cccc3333", "dddd4444"])

        def native(path, body=None, method=None, timeout=None, headers=None):
            self.calls.append((path, body, method))
            if path in self.answers:
                answer = self.answers[path]
                if isinstance(answer, Exception):
                    raise answer
                return answer
            if path.startswith("/api/chat/slots/") and body is None or path.startswith("/api/sessions/"):
                raise crew_api.GatewayError(404, '{"error":"not found","code":"slot_not_found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is not None:
                return {"id": next(self.ids)}
            if path.endswith("/continue"):
                return {"id": "bbbb2222", "conversation": "aaaa1111", "status": "spawned"}
            if path == "/api/spawn":
                return {"agents": []}
            if path.endswith("/steer"):
                return {"id": path.split("/")[3], "status": "steered" if (body or {}).get("mode", "interrupt") == "interrupt"
                        else "follow_up_queued"}
            return {"ok": True}
        self.node.native = native
        for rid in (RID, OTHER):
            self.node.prepare({"run_id": rid, "source": {"type": "new"}})

    def dispatch(self, attempt, rid=RID, **extra):
        return self.node.dispatch({"run_id": rid, "attempt_id": attempt, "input": "需求", "spec": SPEC, "upstream": [], **extra})

    def steer(self, attempt=FIRST, rid=RID, **body):
        return self.node.steer({"run_id": rid, "attempt_id": attempt, "message": "先只改 README，不动测试", **body})

    def test_a_running_task_is_steered(self):
        self.dispatch(FIRST)
        self.assertEqual(self.steer(), {"ok": True, "status": "steered"})
        path, body, method = self.calls[-1]
        self.assertEqual((path, body, method), ("/api/spawn/aaaa1111/steer", {"message": "先只改 README，不动测试", "mode": "interrupt"},
                                                "POST"))

    def test_follow_up_is_refused(self):
        # KiroCrew delivers a follow-up as a new run of the conversation the platform would never track (review 2026-10-19)
        self.dispatch(FIRST)
        with self.assertRaises(Error):
            self.steer(mode="follow_up")
        self.assertFalse(any(p.endswith("/steer") for p, _, _ in self.calls))

    def test_another_sessions_attempt_is_refused(self):
        self.dispatch(FIRST, rid=OTHER)
        with self.assertRaises(Error) as caught:
            self.steer(FIRST)   # the attempt was dispatched in the other Session
        self.assertEqual(caught.exception.status, 404)
        self.dispatch(SECOND)
        for body in ({"message": ""}, {"message": "x" * 4001}, {"mode": "now"}):
            with self.subTest(body=str(body)[:30]), self.assertRaises(Error):
                self.steer(SECOND, **body)
        self.assertFalse(any(p.endswith("/steer") for p, _, _ in self.calls))

    def test_refusals_are_said_as_they_are(self):
        self.dispatch(FIRST)
        for status, error, code, expected in (
                (409, "not_running", "not_running", (409, "not_running")),
                (503, "session_starting", "session_starting", (503, "native_unavailable")),
                (404, "not_found", "not_found", (409, "not_running")),
                (502, "steer_unsupported: the codex backend cannot steer; use mode='follow_up'", "steer_failed",
                 (409, "steer_unsupported")),
                (502, "shutting_down", "steer_failed", (503, "native_unavailable"))):
            with self.subTest(error[:20]):
                self.answers["/api/spawn/aaaa1111/steer"] = crew_api.GatewayError(status, json.dumps({"error": error, "code": code}))
                with self.assertRaises(Error) as caught:
                    self.steer()
                self.assertEqual((caught.exception.status, caught.exception.code), expected)

    def test_an_error_is_masked_before_it_is_cut(self):
        # D57 §3.3: KiroCrew's error is cut to 200 characters for the platform, after masking (its own token too)
        self.dispatch(FIRST)
        token = self.node.token
        error = json.dumps({"error": "y" * 160 + " Bearer " + token, "code": "steer_failed"})
        self.answers["/api/spawn/aaaa1111/steer"] = crew_api.GatewayError(502, error)
        with self.assertRaises(Error) as caught:
            self.steer()
        masked = "502: " + error.replace(token, f"{token[:4]}…{token[-2:]}")
        self.assertEqual((caught.exception.status, str(caught.exception)), (502, "KiroCrew 未接受补充说明：" + masked[:200]))
        self.assertNotIn(token[4:-2], str(caught.exception))

    def test_delete_releases_the_sessions_conversations(self):
        self.dispatch(FIRST)
        self.dispatch(SECOND, **{"continue": {"conversation": "aaaa1111", "from": FIRST}})   # bbbb2222 in aaaa1111's
        self.dispatch(THIRD)   # a conversation of its own: cccc3333
        self.node.control({"run_id": RID, "action": "stop"})
        self.node.control({"run_id": RID, "action": "close"})
        self.assertFalse(any(p.endswith("/release") for p, _, _ in self.calls))   # closing keeps them for a reopen
        deleted = self.node.control({"run_id": RID, "action": "delete"})
        self.assertEqual(deleted["lifecycle"], "deleted")
        released = sorted(p for p, _, _ in self.calls if p.endswith("/release"))
        self.assertEqual(released, ["/api/spawn/aaaa1111/release", "/api/spawn/cccc3333/release"])
        lifecycle = json.loads((self.node.path(RID) / "lifecycle.json").read_text())
        self.assertEqual(lifecycle["released"], 2)

    def test_a_failing_release_never_stops_the_delete(self):
        self.dispatch(FIRST)
        self.answers["/api/spawn/aaaa1111/release"] = crew_api.GatewayError(409, '{"error":"busy","code":"conversation_busy"}')
        self.node.control({"run_id": RID, "action": "stop"})
        self.node.control({"run_id": RID, "action": "close"})
        self.assertEqual(self.node.control({"run_id": RID, "action": "delete"})["lifecycle"], "deleted")

    def test_the_worker_says_it_steers(self):
        from unittest.mock import patch
        with patch.object(Worker, "tools", lambda self: {}), patch.object(Worker, "tool_auth", lambda self: "ok"):
            self.assertIs(self.node.health()["steer"], True)


class ContractTests(unittest.TestCase):
    def test_the_contract_lists_steer_and_release(self):
        http = json.loads((ROOT / "tests/kirocrew_contract.json").read_text())["http"]
        self.assertEqual(http["POST /api/spawn/{}/steer"], {"module": "dashboard/messaging_api/run_control.py",
                                                           "reads": ["message", "mode"]})
        self.assertEqual(http["POST /api/spawn/{}/release"]["module"], "dashboard/messaging_api/run_control.py")


if __name__ == "__main__":
    unittest.main()
