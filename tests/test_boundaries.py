"""MVP 0.2.0 P1 Task 3 (S9): every new boundary's identity and OS user id from the control plane — UID = GID = 100000 +
a sequence number that is never reused, so a volume's files keep their owner on whichever host it is mounted; boundaries
of before keep the id their host gave them."""
import re
import threading

from octopus import boundaries
from tests.test_access import AccessCase


class AllocationTests(AccessCase):
    def allocate(self, user=None, name=None):
        with self.db.transaction() as db:
            return boundaries.allocate(db, user or self.b["id"], name)

    def test_the_first_boundary_is_uid_100001(self):
        first = self.allocate()
        self.assertEqual((first["seq"], first["uid"], first["name"]), (1, 100001, "u1"))
        self.assertRegex(first["id"], r"^bnd-[0-9a-f]{32}$")
        self.assertEqual(self.allocate(name="alice")["name"], "alice")   # an admin's boundary keeps its name

    def test_numbers_are_never_reused(self):
        first = self.allocate()
        with self.db.transaction() as db:
            boundaries.release(db, first["id"])
        again = self.allocate()
        self.assertEqual((again["seq"], again["uid"]), (2, 100002))
        self.assertEqual(self.allocate(name="u1")["name"], "u1")   # a released name may be used again, never its number

    def test_concurrent_allocations_never_share_a_number(self):
        found, lock = [], threading.Lock()

        def work():
            for _ in range(25):
                row = self.allocate()
                with lock:
                    found.append(row["uid"])
        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(found), 100)
        self.assertEqual(len(set(found)), 100)

    def test_only_an_admin_allocates_and_it_is_audited(self):
        with self.http() as call:
            self.assertEqual(call("POST", "/v1/admin/boundaries", self.tokens["bob"], {"owner": "bob", "name": "bob"})[0], 403)
            status, made = call("POST", "/v1/admin/boundaries", self.tokens["alice"], {"owner": "bob", "name": "bob"})
            self.assertEqual(status, 201)
            self.assertEqual(made["name"], "bob")
            self.assertEqual(call("POST", "/v1/admin/boundaries", self.tokens["alice"], {"owner": "nobody", "name": "x"})[0], 404)
            self.assertEqual(call("POST", "/v1/admin/boundaries", self.tokens["alice"], {"owner": "bob", "name": "Bad Name"})[0], 400)
        with self.db.read() as db:
            self.assertTrue(db.execute("SELECT 1 FROM audit WHERE action='boundary.allocate'").fetchone())

    def test_boundary_add_uses_the_platform_uid_for_a_new_boundary_only(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "deploy/install/boundary.sh").read_text()
        create = text[text.index('if ! id "$CREW"'):text.index("chown \"$CREW:$CREW\" \"$BASE\"")]
        self.assertIn("platform --allocate", create)
        self.assertIn('groupadd -g "$B_UID" "$CREW"', create)
        self.assertIn('useradd -u "$B_UID" -g "$B_UID"', create)
        self.assertIn('getent passwd "$B_UID"', create)   # a host id in use by another name stops it
        self.assertRegex(text, re.compile(r'boundary_id = \\"\$B_ID\\"'))
