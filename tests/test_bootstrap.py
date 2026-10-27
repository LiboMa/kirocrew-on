"""First-time bootstrap registers the boundary Worker for the admin exactly once."""
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from octopus.bootstrap import issue_enrollment, register_boundary_worker
from octopus.client import Client
from octopus.db import Database
from octopus.security import create_user
from octopus.server import server
from octopus.worker import Worker, serve

PASSWORD = "bootstrap-password-123"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        db = self.db = Database(root / "platform")
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

    def test_spare_registration_is_idempotent(self):
        # A Worker host elsewhere (D28): dedicated isolation and a host account other than the platform's.
        health = {"isolation": "dedicated", "boundary": "b-spare", "host_principal": "elsewhere"}
        url = "https://10.0.0.9:8943"
        with patch("octopus.server.validate_worker", return_value=health):
            first = register_boundary_worker(self.api_url, "admin", PASSWORD, url, self.token, "team-host-1", spare=True)
            second = register_boundary_worker(self.api_url, "admin", PASSWORD, url, self.token, "team-host-1", spare=True)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])

    def test_re_registering_a_replaced_worker_host_updates_its_connection(self):
        first_health = {"isolation": "dedicated", "boundary": "b-old", "host_principal": "old-host"}
        new_health = {"isolation": "dedicated", "boundary": "b-new", "host_principal": "new-host"}
        with patch("octopus.server.validate_worker", return_value=first_health):
            first = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.9:8943", self.token,
                                             "team-host-1", spare=True)
        with patch("octopus.server.validate_worker", return_value=new_health):
            again = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.7:8943", "n" * 40,
                                             "team-host-1", spare=True)
        self.assertEqual((again["id"], again["created"], again["updated"]), (first["id"], False, True))
        with self.db.read() as c:
            config = json.loads(c.execute("SELECT config FROM workers WHERE id=?", (first["id"],)).fetchone()[0])
        from octopus import vault   # D59: the token is stored sealed
        self.assertEqual((config["url"], vault.open_(vault.key_file(None, self.db.root), config["token_sealed"]),
                          config["capabilities"]["boundary"]), ("https://10.0.0.7:8943", "n" * 40, "b-new"))
        self.assertNotIn("token", config)

    def test_re_registering_a_worker_host_an_admin_has_shared_updates_it(self):
        # Observed on EC2: w1 had been shared from the console, and worker-host register then tried to create it (409).
        health = {"isolation": "dedicated", "boundary": "b-spare", "host_principal": "elsewhere"}
        with patch("octopus.server.validate_worker", return_value=health):
            first = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.9:8943", self.token, "w1",
                                             spare=True)
            with self.db.transaction() as c:
                c.execute("UPDATE workers SET scope='shared' WHERE id=?", (first["id"],))
            again = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.9:8943", self.token, "w1",
                                             spare=True)
        self.assertEqual((again["id"], again["created"], again["updated"]), (first["id"], False, True))

    def test_spare_registration_never_rewires_a_platform_worker_of_the_same_name(self):
        local = register_boundary_worker(self.api_url, "admin", PASSWORD, self.worker_url, self.token, "personal")
        health = {"isolation": "dedicated", "boundary": "b-host", "host_principal": "elsewhere"}
        with patch("octopus.server.validate_worker", return_value=health):
            host = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.9:8943", "h" * 40,
                                            "personal", spare=True)
        self.assertTrue(host["created"])
        self.assertNotEqual(host["id"], local["id"])
        with self.db.read() as c:
            self.assertEqual(json.loads(c.execute("SELECT config FROM workers WHERE id=?", (local["id"],)).fetchone()[0])
                             ["url"], self.worker_url)

    def test_a_new_worker_enrolls_itself_with_a_code_and_a_registered_one_is_refreshed_instead(self):
        # MVP 0.2.0 (N §3): the scripts issue a code and the Worker registers itself, its token read from its own file;
        # a Worker already registered under that name keeps the bootstrap path (its connection refreshed).
        issued = issue_enrollment(self.api_url, "admin", PASSWORD, "personal")
        self.assertTrue(issued["code"].startswith("kce_"))
        self.assertEqual(issued["platform"], self.api_url)
        made = Client(self.api_url, "").request("POST", "/v1/workers/enroll",   # the real Worker's /health, no mock
                                                {"code": issued["code"], "url": self.worker_url, "token": self.token})
        self.assertEqual((made["name"], made["scope"]), ("personal", "dedicated"))
        self.assertEqual(issue_enrollment(self.api_url, "admin", PASSWORD, "personal"), {"registered": made["id"]})
        with patch("octopus.server.validate_worker", return_value={"isolation": "dedicated", "boundary": "b-s",
                                                                   "host_principal": "elsewhere"}):
            spare = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.9:8943", "s" * 40, "w1",
                                             spare=True)
        self.assertEqual(issue_enrollment(self.api_url, "admin", PASSWORD, "w1", spare=True), {"registered": spare["id"]})
        self.assertIn("code", issue_enrollment(self.api_url, "admin", PASSWORD, "w2", spare=True))

    def test_wrong_password_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            register_boundary_worker(self.api_url, "admin", "wrong-password-xx", self.worker_url, self.token, "personal")


if __name__ == "__main__":
    unittest.main()
