"""Unattended boundaries ask KiroCrew (owner-authenticated) for native auto approval on platform spawns."""
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.security import Error
from octopus.worker import Worker

SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现并测试"}


class WorkerApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def worker(self, mode):
        node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew", approval_mode=mode)
        rid = "run-" + "1" * 32

        def native(path, body=None, method=None):
            if path == "/api/chat/slots/octopus-" + rid and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + rid}
            return {"ok": True}

        node.native = native
        node.prepare({"run_id": rid, "source": {"type": "new"}})
        return node, {"run_id": rid, "attempt_id": "attempt-" + "2" * 32, "input": "需求", "spec": SPEC, "upstream": []}

    def test_auto_boundary_requests_native_auto_approval_on_platform_spawn(self):
        node, body = self.worker("auto")
        calls = []

        def native(path, payload=None, method=None):
            calls.append((path, payload))
            return {"id": "aabb1234"}

        node.native = native
        self.assertEqual(node.dispatch(body)["status"], "running")
        path, payload = calls[0]
        self.assertEqual(path, "/api/spawn")
        self.assertEqual(payload["approval_mode"], "auto")

    def test_default_boundary_keeps_native_interactive_spawn(self):
        node, body = self.worker("")
        calls = []

        def native(path, payload=None, method=None):
            calls.append((path, payload))
            return {"id": "aabb1234"}

        node.native = native
        node.dispatch(body)
        self.assertNotIn("approval_mode", calls[0][1])

    def test_health_reports_spawn_approval(self):
        self.assertEqual(self.worker("auto")[0].health()["spawn_approval"], "auto")
        self.assertEqual(Worker(self.root / "w2", "x" * 40, crew_home=self.root / "crew").health()["spawn_approval"],
                         "interactive")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(Error):
            Worker(self.root / "w3", "x" * 40, crew_home=self.root / "crew", approval_mode="yolo")


class DiagnosisDispatchTests(WorkerApprovalTests):
    def test_coordination_dispatch_uses_the_non_delegating_diagnosis_template(self):
        node, body = self.worker("auto")
        calls = []

        def native(path, payload=None, method=None):
            calls.append(payload)
            return {"id": "aabb1234"}

        node.native = native
        node.dispatch({**body, "spec": {**body["spec"], "tool": "kiro"}, "coordination": {"input": "x", "failure": {}}})
        self.assertEqual(calls[0]["agent"], "poc-diagnose")
        self.assertEqual(calls[0]["approval_mode"], "auto")

if __name__ == "__main__":
    unittest.main()
