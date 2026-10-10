"""Review regressions: retries must not overlap native work or escape a run."""
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import test_workflow as fixtures
import workflow as w


class RetryReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.home = patch.object(Path, "home", return_value=self.fixture.root)
        self.home.start()
        self.addCleanup(self.home.stop)
        self.native = patch.object(w.api, "request", side_effect=AssertionError("unexpected native write"))
        self.native_mock = self.native.start()
        self.addCleanup(self.native.stop)

    def assert_retry_rejected(self, run, request_id):
        handoff = run / "workspace/plan-handoff.md"
        before = handoff.read_bytes()
        with self.assertRaises(ValueError, msg="retry accepted while native work is still running"):
            w.retry_stage(run, "plan", request_id)
        self.assertEqual(handoff.read_bytes(), before)
        self.assertFalse((run / "retry-state.json").exists())
        self.native_mock.assert_not_called()

    def test_running_task_remains_non_retryable_after_early_checkpoint(self):
        manifest, run = self.fixture.prepare()
        _, receipts, tasks = self.fixture.seed_retry(
            manifest, run, ("running", "pending", "pending", "pending"))
        with patch.object(w, "dispatch_receipts", return_value=receipts), \
                patch.object(w, "route_for", return_value={"actual_backend": "kiro"}):
            with self.assertRaisesRegex(ValueError, "原生任务尚未成功结束"):
                w.accept(run, tasks[0]["id"])
            snapshot = w.project(run, tasks, {"running": False}, [])
            w.write_json(run / "workflow-live.json", snapshot)
            self.assertFalse(snapshot["steps"][0]["task"]["done"])
            self.assert_retry_rejected(run, "review-early-checkpoint")

    def test_running_older_attempt_prevents_retry_of_latest_failure(self):
        manifest, run = self.fixture.prepare()
        _, receipts, tasks = self.fixture.seed_retry(
            manifest, run, ("failed", "pending", "pending", "pending"))
        older = {**tasks[0], "id": "def00000", "done": False, "error": None,
                 "started": tasks[0]["started"] - 1}
        with patch.object(w, "dispatch_receipts", return_value=receipts), \
                patch.object(w, "route_for", return_value={"actual_backend": "kiro"}):
            snapshot = w.project(run, [older, *tasks], {"running": False}, [])
            w.write_json(run / "workflow-live.json", snapshot)
            self.assertTrue(any(not task["done"] for task in snapshot["steps"][0]["attempts"]))
            self.assert_retry_rejected(run, "review-older-attempt")

    def test_http_retry_rejects_untrusted_origins_and_malformed_identifiers(self):
        manifest, run = self.fixture.prepare()
        _, receipts, _ = self.fixture.seed_retry(manifest, run)
        (self.fixture.root / "state").mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        ready, servers = threading.Event(), []

        def create(address, handler):
            server = ThreadingHTTPServer(address, handler)
            servers.append(server)
            ready.set()
            return server

        def call(body, override=None):
            headers = {"Origin": f"http://127.0.0.1:{port}",
                       "Content-Type": "application/json", "X-Workflow-MVP": "1"}
            headers.update(override or {})
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/retry",
                data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code

        def artifacts():
            return {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for directory in ("accepted", "workspace") for p in (run / directory).rglob("*")
                    if p.is_file()}

        valid = {"run": manifest["id"], "stage": "code", "request_id": "review-http-input"}
        before = artifacts()
        outside = self.fixture.root / "outside"
        outside.mkdir()
        (outside / "workflow-run.json").write_text("{}")
        (w.RUNS / "workflow-escape").symlink_to(outside, target_is_directory=True)
        bad_bodies = [None, [], 3, "bad", {}, *[
            {**valid, "run": value} for value in
            (None, [], "../outside", str(outside), "workflow-missing", "workflow-escape", "\x00")
        ], *[
            {**valid, "stage": value} for value in
            (None, [], "../plan", "absent", "code;touch marker", "$(touch marker)")
        ], *[
            {**valid, "request_id": value} for value in
            (None, [], "../request", "x" * 81, "$(touch marker)")
        ]]
        bad_headers = [{"Origin": ""}, {"Origin": "http://unrelated.test"},
                       {"Host": f"localhost:{port}"}, {"X-Workflow-MVP": ""},
                       {"Content-Type": "text/plain"}]
        with patch.object(w, "ROOT", self.fixture.root), \
                patch.object(w, "observe", return_value=None), \
                patch.object(w, "ThreadingHTTPServer", side_effect=create), \
                patch.object(w, "dispatch_receipts", return_value=receipts):
            thread = threading.Thread(target=w.serve, args=(port,), daemon=True)
            thread.start()
            self.assertTrue(ready.wait(3))
            try:
                for body in bad_bodies:
                    with self.subTest(body=body):
                        self.assertEqual(call(body), 400)
                for headers in bad_headers:
                    with self.subTest(headers=headers):
                        self.assertEqual(call(valid, headers), 403)
                self.assertEqual(artifacts(), before)
                self.assertFalse((run / "retry-state.json").exists())
                self.native_mock.assert_not_called()
                print(f"HTTP retry rejection cases: {len(bad_bodies) + len(bad_headers)}; native writes: 0")
            finally:
                servers[0].shutdown()
                servers[0].server_close()
                thread.join(3)


if __name__ == "__main__":
    unittest.main()
