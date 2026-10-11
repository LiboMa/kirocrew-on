"""API client errors stay readable when CloudFront answers for an unavailable origin (observed on EC2 reboot)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from octopus.client import Client


class GatewayTimeout(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body><h1>504 Gateway Timeout ERROR</h1></body></html>"
        self.send_response(504)
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

    def test_non_json_error_body_reports_the_status_instead_of_a_parse_error(self):
        client = Client(f"http://127.0.0.1:{self.server.server_port}", "token")
        with self.assertRaisesRegex(RuntimeError, r"^504 .*Gateway Timeout"):
            client.request("GET", "/v1/runs/run-x")


if __name__ == "__main__":
    unittest.main()
