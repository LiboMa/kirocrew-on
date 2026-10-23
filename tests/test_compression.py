"""Performance (EC2 2026-10-23): the console's Session list was 199 KB for 50 rows and the console's script 731 KB, both
sent as they are (CloudFront's behaviours cache nothing, so it compresses nothing). A GET whose answer is text of 1 KB or
more goes gzipped to a client that accepts gzip; a POST never does, as a login's or a new token's answer carries a secret
(a response's length is what BREACH-style attacks read)."""
import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from octopus.db import Database
from octopus.server import accepts_gzip, server
from tests.test_access import AccessCase


class AcceptTests(unittest.TestCase):
    def test_what_counts_as_accepting_gzip(self):
        for header, expected in [("gzip, deflate, br", True), ("br;q=1.0, gzip;q=0.8", True), ("*", True),
                                 ("GZIP", True), ("gzip;q=0", False), ("gzip; q=0.000", False), ("br", False),
                                 ("identity", False), ("", False), (None, False), ("*;q=0", False)]:
            with self.subTest(header=header):
                self.assertIs(accepts_gzip(header), expected)


class ApiTests(AccessCase):
    def setUp(self):
        super().setUp()
        self.shared_env(users=("bob",))
        for n in range(12):
            self.start(self.b, f"start-{n:010d}")

    def fetch(self, base, method, path, encoding, body=None):
        request = urllib.request.Request(base + path, method=method, headers={
            "Authorization": "Bearer " + self.tokens["bob"], **({"Accept-Encoding": encoding} if encoding else {}),
            **({"Content-Type": "application/json"} if body is not None else {})},
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.headers, response.read()

    def test_a_large_answer_is_gzipped_for_a_client_that_accepts_it(self):
        with self.http() as call:
            plain_headers, plain = self.fetch(call.base, "GET", "/v1/runs?page=1&view=list", None)
            headers, data = self.fetch(call.base, "GET", "/v1/runs?page=1&view=list", "gzip, br")
        self.assertGreaterEqual(len(plain), 1024)
        self.assertIsNone(plain_headers.get("Content-Encoding"))
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(int(headers["Content-Length"]), len(data))
        self.assertLess(len(data), len(plain) / 2)
        self.assertEqual(json.loads(gzip.decompress(data)), json.loads(plain))
        self.assertIn("Accept-Encoding", headers.get("Vary", ""))
        self.assertIn("Accept-Encoding", plain_headers.get("Vary", ""))   # a cache keeps the two apart

    def test_a_small_answer_and_a_post_go_as_they_are(self):
        with self.http() as call:
            headers, data = self.fetch(call.base, "GET", "/v1/me", "gzip")
            self.assertLess(len(data), 1024)
            self.assertIsNone(headers.get("Content-Encoding"))
            headers, _ = self.fetch(call.base, "POST", "/v1/tokens", "gzip", {"name": "t", "scopes": ["read"]})
            self.assertIsNone(headers.get("Content-Encoding"))


class AssetTests(unittest.TestCase):
    def test_the_console_script_is_gzipped_and_still_cached_for_good(self):
        with tempfile.TemporaryDirectory() as root:
            ui = Path(root) / "ui"
            (ui / "dist" / "assets").mkdir(parents=True)
            (ui / "dist" / "index.html").write_text("<!doctype html>")
            script = "console.log('kirocrew');\n" * 400
            (ui / "dist" / "assets" / "index-Dx9n1sQE.js").write_text(script)
            with patch("octopus.server.UI", ui):
                app = server(Database(Path(root) / "data"), 0)
                thread = threading.Thread(target=app.serve_forever, daemon=True)
                thread.start()
                try:
                    request = urllib.request.Request(f"http://127.0.0.1:{app.server_address[1]}/assets/index-Dx9n1sQE.js",
                                                     headers={"Accept-Encoding": "gzip"})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        headers, data = response.headers, response.read()
                finally:
                    app.shutdown()
                    app.server_close()
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(data).decode(), script)
        self.assertEqual(headers.get("Cache-Control"), "public, max-age=31536000, immutable")


if __name__ == "__main__":
    unittest.main()
