"""First-time bootstrap registers the boundary Worker for the admin exactly once."""
from pathlib import Path
import tempfile
import threading
import unittest

from octopus.bootstrap import register_boundary_worker
from octopus.db import Database
from octopus.security import create_user
from octopus.server import server
from octopus.worker import Worker, serve

PASSWORD = "bootstrap-password-123"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        db = Database(root / "platform")
        with db.transaction() as c:
            create_user(c, "admin", PASSWORD, admin=True)
        self.api = server(db, 0)
        self.token = "w" * 40
        self.worker = serve(Worker(root / "worker", self.token, crew_home=root / "crew", isolation="trusted-local"), 0)
        for httpd in (self.api, self.worker):
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.api_url = f"http://127.0.0.1:{self.api.server_address[1]}"
        self.worker_url = f"http://127.0.0.1:{self.worker.server_address[1]}"

    def tearDown(self):
        for httpd in (self.api, self.worker):
            httpd.shutdown()
            httpd.server_close()
        self.temporary.cleanup()

    def test_registers_once_and_is_idempotent(self):
        first = register_boundary_worker(self.api_url, "admin", PASSWORD, self.worker_url, self.token, "personal")
        second = register_boundary_worker(self.api_url, "admin", PASSWORD, self.worker_url, self.token, "personal")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])

    def test_wrong_password_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            register_boundary_worker(self.api_url, "admin", "wrong-password-xx", self.worker_url, self.token, "personal")


if __name__ == "__main__":
    unittest.main()
