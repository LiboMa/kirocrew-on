"""API client errors stay readable when CloudFront answers for an unavailable origin (observed on EC2 reboot)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

import io
import json

from octopus import mcp
from octopus.client import ApiError, Client


class GatewayTimeout(BaseHTTPRequestHandler):
    status = 504

    def do_GET(self):
        body = b"<html><body><h1>CloudFront ERROR</h1></body></html>"
        self.send_response(self.status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ClientErrorTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayTimeout)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def client(self):
        return Client(f"http://127.0.0.1:{self.server.server_port}", "token")

    def test_non_json_error_body_reports_the_status_instead_of_a_parse_error(self):
        with self.assertRaisesRegex(RuntimeError, r"^504 Gateway Timeout: API 暂不可用"):
            self.client().request("GET", "/v1/runs/run-x")

    def test_event_stream_reports_the_status_too(self):
        with self.assertRaisesRegex(RuntimeError, r"^504 Gateway Timeout: API 暂不可用"):
            next(self.client().events("run-x"))

    def test_non_json_client_error_is_not_called_temporary(self):
        GatewayTimeout.status = 413
        self.addCleanup(setattr, GatewayTimeout, "status", 504)
        with self.assertRaises(RuntimeError) as caught:
            self.client().request("GET", "/v1/runs/run-x")
        self.assertTrue(str(caught.exception).startswith("413 "))
        self.assertNotIn("暂不可用", str(caught.exception))

    def test_cli_prints_the_error_without_a_traceback(self):
        import subprocess
        import sys
        from pathlib import Path
        result = subprocess.run([sys.executable, "-m", "octopus", "request", "--url",
                                 f"http://127.0.0.1:{self.server.server_port}", "GET", "/v1/runs/run-x"],
                                cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True,
                                env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "OCTOPUS_API_TOKEN": "token"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("504 Gateway Timeout", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class JsonError(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"error": "Token 缺少 admin scope", "code": "forbidden"}).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class StructuredErrorTests(unittest.TestCase):
    def serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return Client(f"http://127.0.0.1:{server.server_port}", "token")

    def call(self, client, name="octopus_runs"):
        out = io.StringIO()
        mcp.serve(client, io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                  "params": {"name": name, "arguments": {}}}) + "\n"), out)
        return json.loads(out.getvalue())["result"]

    def test_api_errors_carry_status_code_and_message(self):
        with self.assertRaises(ApiError) as caught:
            self.serve(JsonError).request("GET", "/v1/runs")
        error = caught.exception
        self.assertEqual((error.status, error.code, error.error), (403, "forbidden", "Token 缺少 admin scope"))
        self.assertEqual(str(error), "403 forbidden: Token 缺少 admin scope")
        self.assertIsInstance(error, RuntimeError)

    def test_mcp_returns_the_error_as_structured_content(self):
        result = self.call(self.serve(JsonError))
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"], {"status": 403, "code": "forbidden", "error": "Token 缺少 admin scope"})
        self.assertEqual(result["content"][0]["text"], "403 forbidden: Token 缺少 admin scope")

    def test_proxy_errors_keep_their_status(self):
        result = self.call(self.serve(GatewayTimeout))
        self.assertEqual((result["structuredContent"]["status"], result["structuredContent"]["code"]), (504, "http_error"))

    def test_other_failures_are_client_errors(self):
        class Broken:
            def request(self, *args, **kwargs):
                raise ValueError("Use a /v1/ API path")
        result = self.call(Broken())
        self.assertEqual(result["structuredContent"], {"status": 0, "code": "client_error", "error": "Use a /v1/ API path"})


if __name__ == "__main__":
    unittest.main()
