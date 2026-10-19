"""A deploy that restarts the KiroCrew Gateway first drains it (BACKLOG, postmortem #8: EC2 run-f7670bfb… was created during
a deploy and its first task was killed): while the Worker's maintenance mark is there it takes no new work — the platform
retries on its own (native_unavailable) — and the installer restarts the Gateway once the running native tasks end."""
from pathlib import Path
import re
import tempfile
import unittest

from octopus import gateway_client as crew_api
from octopus.security import Error
from octopus.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        self.calls = []

        def native(path, body=None, method=None):
            self.calls.append(path)
            if path.startswith("/api/chat/slots/") and body is None and method is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            return {"key": body["name"]} if path == "/api/chat/slots" else {"ok": True}
        self.node.native = native

    def test_under_maintenance_no_new_work_is_taken(self):
        (self.node.root / ".maintenance").write_text("deploy")
        for call, body in ((self.node.prepare, {"run_id": "run-" + "1" * 32, "source": {"type": "new"}}),
                           (self.node.dispatch, {"run_id": "run-" + "1" * 32, "attempt_id": "attempt-1"})):
            with self.subTest(call.__name__), self.assertRaises(Error) as caught:
                call(body)
            self.assertEqual((caught.exception.status, caught.exception.code), (503, "native_unavailable"))
            self.assertIn("维护", str(caught.exception))
        self.assertEqual(self.calls, [])   # nothing reached the Gateway

    def test_without_the_mark_work_is_taken(self):
        self.node.prepare({"run_id": "run-" + "2" * 32, "source": {"type": "new"}})
        self.assertTrue(self.calls)


class InstallerTests(unittest.TestCase):
    def test_a_gateway_restart_is_drained_first(self):
        text = (ROOT / "deploy/install/boundary-lib.sh").read_text()   # D49: boundary_restart, for every boundary
        block = re.search(r"# gateway:begin(.*?)# gateway:end", text, re.S).group(1)
        mark, restart = block.index(".maintenance"), block.index('systemctl restart "octopus-gateway@$name"')
        self.assertLess(mark, restart)   # marked before the restart
        self.assertIn("DRAIN_MINUTES", block)   # and waits, with a limit, for the running native tasks
        after = text[text.index("# gateway:end"):]
        self.assertLess(after.index('systemctl restart "octopus-worker@$name"'), after.index('rm -f "$maintenance"'))


if __name__ == "__main__":
    unittest.main()
