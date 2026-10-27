"""MVP 0.2.0 P1 Task 1 (CE9): a boundary registered once is never registered again — not even when its Worker is
disabled or retired, or a volume's identity could be handed to another person. One function holds the rules that every
way of registering applies (the console's form, and the enrollment codes of Task 6)."""
from unittest import mock

from octopus import workers
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase

HEALTH = {"isolation": "dedicated", "boundary": "b-old", "host_principal": "hp-new"}


class RegistrationIdentityTests(AccessCase):
    def known(self, wid, enabled=1, retired=0):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope,enabled,retired) VALUES(?,?,?,?,'dedicated',?,?)",
                       (wid, wid, self.e["id"], dumps({"url": "https://10.0.0.1:8943", "token": "x" * 40,
                                                         "capabilities": {"boundary": "b-old", "host_principal": "hp-old"}}),
                        enabled, retired))

    def register(self):
        body = {"name": "again", "owner_id": self.b["id"], "connection": {"url": "https://10.0.0.9:8943", "token": "t" * 40}}
        with mock.patch("octopus.server.validate_worker", return_value=HEALTH), self.http() as call:
            return call("POST", "/v1/workers", self.tokens["alice"], body)

    def test_a_disabled_workers_boundary_is_not_registered_again(self):
        self.known("w-off", enabled=0)
        status, answer = self.register()
        self.assertEqual((status, answer.get("code")), (409, "worker_conflict"))

    def test_a_retired_workers_boundary_is_not_registered_again(self):
        self.known("w-gone", enabled=0, retired=1)
        status, answer = self.register()
        self.assertEqual((status, answer.get("code")), (409, "worker_conflict"))

    def test_the_rules_are_one_function(self):
        self.known("w-off", enabled=0)
        with self.db.read() as db, self.assertRaises(Error) as caught:
            workers.registration_conflicts(db, HEALTH, team=None, user=self.b["id"], spare=False)
        self.assertEqual(caught.exception.code, "worker_conflict")
        with self.db.read() as db:   # a new boundary passes
            workers.registration_conflicts(db, {**HEALTH, "boundary": "b-new"}, team=None, user=self.b["id"], spare=False)
