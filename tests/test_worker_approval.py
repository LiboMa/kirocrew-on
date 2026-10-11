"""Unattended boundaries ask KiroCrew for native auto approval per platform spawn, over internal auth only."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_auto_boundary_spawns_with_native_auto_approval_over_internal_auth(self):
        node, body = self.worker("auto")
        calls = []

        def native(path, payload=None, method=None, internal=False):
            calls.append((path, payload, internal))
            return {"id": "aabb1234"}

        node.native = native
        self.assertEqual(node.dispatch(body)["status"], "running")
        path, payload, internal = calls[0]
        self.assertEqual(path, "/api/spawn")
        self.assertEqual(payload["approval_mode"], "auto")
        self.assertTrue(internal)

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


class InternalAuthTests(unittest.TestCase):
    def test_internal_request_uses_local_secret_header_and_mints_no_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".local_secret").write_text("local-secret-value\n")
            seen = []

            def urlopen(request, timeout=30):
                seen.append(request)
                return io.BytesIO(json.dumps({"id": "aabb1234"}).encode())

            with patch("urllib.request.urlopen", side_effect=urlopen), \
                    patch.object(gateway_client, "token", side_effect=AssertionError("no token minting")):
                result = gateway_client.request("/api/spawn", {"task": "x"}, "POST", port=6476, home=home, internal=True)
            self.assertEqual(result, {"id": "aabb1234"})
            headers = {k.lower(): v for k, v in seen[0].header_items()}
            self.assertEqual(headers["x-internal-secret"], "local-secret-value")
            self.assertNotIn("cookie", headers)
            self.assertEqual(seen[0].full_url, "http://127.0.0.1:6476/api/spawn")


if __name__ == "__main__":
    unittest.main()
