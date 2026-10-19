"""D49: a boundary on the platform host registered as one person's dedicated Worker (bootstrap-worker --owner), and that
person's Sessions going to it without being asked, even when a shared environment also serves them."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from octopus.bootstrap import register_boundary_worker
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.security import authenticate, create_user, issue_token
from octopus.server import server
from octopus.worker import Worker, serve

PASSWORD = "bootstrap-password-123"
HEALTH = {"isolation": "dedicated", "boundary": "b-alice", "host_principal": "crew-alice"}
STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["完成"]}]


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.db = Database(root / "platform")
        with self.db.transaction() as c:
            create_user(c, "admin", PASSWORD, admin=True)
            self.alice = create_user(c, "alice", "alice-password-123", admin=False)
        self.api = server(self.db, 0)
        self.token = "w" * 40
        self.worker = serve(Worker(root / "worker", self.token, crew_home=root / "crew", isolation="dedicated"), 0)
        for httpd in (self.api, self.worker):
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
        self.api_url = f"http://127.0.0.1:{self.api.server_address[1]}"
        self.worker_url = f"http://127.0.0.1:{self.worker.server_address[1]}"

    def register(self, name="alice", owner="alice", health=HEALTH):
        with patch("octopus.server.validate_worker", return_value=health):
            return register_boundary_worker(self.api_url, "admin", PASSWORD, self.worker_url, self.token, name, owner=owner)

    def worker_row(self, wid):
        with self.db.read() as db:
            return dict(db.execute("SELECT * FROM workers WHERE id=?", (wid,)).fetchone())

    def test_registering_for_a_user_makes_it_their_dedicated_worker(self):
        created = self.register()
        self.assertTrue(created["created"])
        row = self.worker_row(created["id"])
        self.assertEqual((row["owner_id"], row["team_id"], row["scope"], row["name"]), (self.alice["id"], None, "dedicated", "alice"))

    def test_registering_again_refreshes_the_connection(self):
        first = self.register()
        second = self.register()
        self.assertEqual((second["id"], second["created"]), (first["id"], False))

    def test_registering_for_an_unknown_user_is_refused(self):
        with self.assertRaises(Exception) as caught:
            self.register(owner="nobody")
        self.assertIn("nobody", str(caught.exception))
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workers").fetchone()[0], 0)

    def test_the_owner_can_be_checked_before_anything_is_provisioned(self):
        self.assertEqual(register_boundary_worker(self.api_url, "admin", PASSWORD, None, None, "alice", owner="alice",
                                                  check_only=True), {"ok": True, "owner": "alice"})
        with self.assertRaises(Exception) as caught:
            register_boundary_worker(self.api_url, "admin", PASSWORD, None, None, "alice", owner="nobody", check_only=True)
        self.assertIn("nobody", str(caught.exception))
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workers").fetchone()[0], 0)

    def test_a_worker_host_never_takes_over_a_persons_boundary(self):
        own = self.register()
        with patch("octopus.server.validate_worker", return_value={**HEALTH, "boundary": "b-host", "host_principal": "host"}):
            host = register_boundary_worker(self.api_url, "admin", PASSWORD, "https://10.0.0.9:8943", "h" * 40, "alice",
                                            spare=True)
        self.assertNotEqual(host["id"], own["id"])   # a spare of its own, as for the platform's own Worker of that name
        self.assertEqual(self.worker_row(own["id"])["config"].count(self.worker_url), 1)   # still its own connection
        self.assertEqual(self.worker_row(own["id"])["owner_id"], self.alice["id"])

    def test_a_second_own_environment_is_refused(self):
        self.register()
        with self.assertRaises(Exception) as caught:
            self.register(name="alice-2", health={**HEALTH, "boundary": "b-alice-2"})
        self.assertIn("alice", str(caught.exception))


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Database(self.temporary.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            admin = create_user(db, "admin", PASSWORD, admin=True)
            alice = create_user(db, "alice", "alice-password-123", admin=False)
            self.alice = authenticate(db, issue_token(db, alice["id"])["token"])
            self.alice_id = alice["id"]
            config = dumps({"url": "fixture", "token": "fixture", "capabilities": {}})
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('shared','personal',?,?,'shared')",
                       (admin["id"], config))
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('shared',?)", (alice["id"],))
            self.config = config
        self.requests = 0

    def own(self, wid, name):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'dedicated')",
                       (wid, name, self.alice_id, self.config))

    def start(self, **body):
        self.requests += 1
        created = self.service.start(self.alice, {"request_id": f"choice-fixture-{self.requests}", "input": "实现登录功能",
                                                  "source": {"type": "new"}, "steps": STEPS, **body})
        return self.service.run(self.alice, created["id"])

    def test_their_own_worker_is_chosen_over_a_shared_one(self):
        self.own("mine", "alice")
        self.assertEqual(self.start()["worker_id"], "mine")

    def test_with_only_the_shared_one_it_is_chosen_as_before(self):
        self.assertEqual(self.start()["worker_id"], "shared")

    def test_two_dedicated_workers_wait_for_a_choice(self):
        self.own("mine", "alice")
        self.own("mine-2", "alice-2")
        run = self.start()
        self.assertIsNone(run["worker_id"])
        self.assertEqual(run["status"], "waiting_worker")

    def test_a_disabled_own_worker_is_not_chosen(self):
        self.own("mine", "alice")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='mine'")
        self.assertEqual(self.start()["worker_id"], "shared")

    def test_a_named_worker_is_kept(self):
        self.own("mine", "alice")
        self.assertEqual(self.start(worker_id="shared")["worker_id"], "shared")


if __name__ == "__main__":
    unittest.main()
