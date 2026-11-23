"""MVP 0.2.0 P1 Task 5 (design §5.3 #7): the Engine runs alone through a lease it is given — today a file lock on its
host, later one a warm standby cannot take while the primary holds it (P5)."""
import tempfile
import unittest
from pathlib import Path

from octopus.db import Database
from octopus.engine import Engine
from octopus.lease import FileLease, LeaseHeld


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "engine.lock"

    def test_a_second_engine_cannot_take_the_lease(self):
        first, second = FileLease(self.path), FileLease(self.path)
        first.acquire()
        self.addCleanup(first.release)
        with self.assertRaises(LeaseHeld):
            second.acquire()

    def test_the_lease_is_released_with_its_holder(self):
        first = FileLease(self.path)
        first.acquire()
        first.release()
        again = FileLease(self.path)
        again.acquire()
        again.release()

    def test_an_engine_uses_the_lease_it_is_given(self):
        calls = []

        class Recorded:
            def acquire(self):
                calls.append("acquire")

            def release(self):
                calls.append("release")
        scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # its threads may still write as it stops
        self.addCleanup(scratch.cleanup)
        engine = Engine(Database(scratch.name), lease=Recorded())
        engine.start()
        engine.drain(1)
        self.assertEqual(calls[0], "acquire")

    def test_by_default_it_is_the_file_lock_beside_the_database(self):
        db = Database(self.tmp.name)
        engine = Engine(db)
        self.assertIsInstance(engine.lease, FileLease)
        self.assertEqual(engine.lease.path, db.root / "engine.lock")


if __name__ == "__main__":
    unittest.main()
