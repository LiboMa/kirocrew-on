"""The React console is served from octopus/ui/dist when built; the classic console stays available at /classic/."""
import http.client
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from octopus import server as server_module
from octopus.db import Database


class StaticConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.ui = root / "ui"
        (self.ui / "dist" / "assets").mkdir(parents=True)
        (self.ui / "index.html").write_text("<title>classic</title>")
        (self.ui / "app.js").write_text("// classic")
        (self.ui / "crew-entry.html").write_text("<title>entry</title>")
        (self.ui / "dist" / "index.html").write_text("<title>react</title>")
        (self.ui / "dist" / "assets" / "index-Ab12_c.js").write_text("// react bundle")
        (self.ui / "dist" / "assets" / "index-Ab12_c.css").write_text("body{}")
        (root / "secret.txt").write_text("outside")
        patcher = patch.object(server_module, "UI", self.ui)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = server_module.server(Database(root / "data"), 0, host="127.0.0.1")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        port = self.httpd.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, response.headers, body

    def test_root_serves_the_react_build_when_present(self):
        status, headers, body = self.get("/")
        self.assertEqual((status, body), (200, b"<title>react</title>"))
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])

    def test_root_falls_back_to_the_classic_console_without_a_build(self):
        (self.ui / "dist" / "index.html").unlink()
        self.assertEqual(self.get("/")[2], b"<title>classic</title>")

    def test_classic_console_stays_available(self):
        for path in ("/classic/", "/classic"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[:1] + (self.get(path)[2],), (200, b"<title>classic</title>"))
        self.assertEqual(self.get("/app.js")[2], b"// classic")
        self.assertEqual(self.get("/crew-entry.html")[2], b"<title>entry</title>")

    def test_built_assets_are_typed_and_cannot_escape_the_assets_directory(self):
        status, headers, body = self.get("/assets/index-Ab12_c.js")
        self.assertEqual((status, body), (200, b"// react bundle"))
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertTrue(self.get("/assets/index-Ab12_c.css")[1]["Content-Type"].startswith("text/css"))
        for path in ("/assets/../index.html", "/assets/%2e%2e/%2e%2e/secret.txt", "/assets/sub/x.js",
                     "/assets/missing.js", "/assets/", "/assets/index-Ab12_c.exe"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
