"""BACKLOG (performance): every console load downloaded the whole build again (Cache-Control: no-store on all of it, ~800
KB). Vite names each asset by its content hash, so /assets/* is cached for good; index.html stays no-store, so a deploy's
new names are picked up at once."""
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from octopus.db import Database
from octopus.server import server


class StaticCacheTests(unittest.TestCase):
    def fetch(self, path):
        with tempfile.TemporaryDirectory() as root:
            ui = Path(root) / "ui"
            (ui / "dist" / "assets").mkdir(parents=True)
            (ui / "dist" / "index.html").write_text("<!doctype html><script src=/assets/index-Dx9n1sQE.js></script>")
            (ui / "dist" / "assets" / "index-Dx9n1sQE.js").write_text("console.log(1)")
            (ui / "dist" / "assets" / "plain.js").write_text("console.log(2)")
            with patch("octopus.server.UI", ui):
                app = server(Database(Path(root) / "data"), 0)
                thread = threading.Thread(target=app.serve_forever, daemon=True)
                thread.start()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{app.server_address[1]}{path}", timeout=5) as response:
                        return response.status, response.headers.get("Cache-Control")
                finally:
                    app.shutdown()
                    app.server_close()

    def test_a_hashed_asset_is_cached_for_good(self):
        self.assertEqual(self.fetch("/assets/index-Dx9n1sQE.js"), (200, "public, max-age=31536000, immutable"))

    def test_the_page_and_an_unhashed_file_are_never_cached(self):
        self.assertEqual(self.fetch("/"), (200, "no-store"))
        self.assertEqual(self.fetch("/assets/plain.js"), (200, "no-store"))


if __name__ == "__main__":
    unittest.main()
