"""D37 phase 2: octopus_report gives a Coding Agent the Session's Feature report as Markdown."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from octopus import mcp
from octopus.client import Client

RID = "run-" + "6" * 32


class Recording:
    def __init__(self):
        self.calls = []

    def text(self, method, path, timeout=40):
        self.calls.append((method, path))
        return "# 登录功能\n"

    def request(self, method, path, body=None, timeout=40):
        raise AssertionError("the report is text, not JSON")


class ReportToolTests(unittest.TestCase):
    def test_the_tool_returns_the_markdown_report(self):
        client = Recording()
        self.assertEqual(mcp.call(client, "octopus_report", {"run_id": RID}), {"markdown": "# 登录功能\n"})
        self.assertEqual(client.calls, [("GET", f"/v1/runs/{RID}/report?format=md")])

    def test_the_tool_is_listed_read_only(self):
        tool = next(t for t in mcp.TOOLS if t["name"] == "octopus_report")
        self.assertEqual(tool["inputSchema"]["required"], ["run_id"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertNotIn("octopus_report", mcp.ADMIN)

    def test_the_client_reads_text_with_its_token(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                data = "# 报告\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = Client(f"http://127.0.0.1:{server.server_address[1]}", "oct_x" * 8)
        self.assertEqual(client.text("GET", f"/v1/runs/{RID}/report?format=md"), "# 报告\n")
        self.assertEqual(seen, [(f"/v1/runs/{RID}/report?format=md", "Bearer " + "oct_x" * 8)])
        with self.assertRaises(ValueError):
            client.text("GET", "https://elsewhere.example/x")


if __name__ == "__main__":
    unittest.main()
