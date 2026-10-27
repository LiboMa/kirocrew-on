"""MVP 0.2.0 P1 Task 2 (S8, CE3): a boundary's identity from the control plane, in the Worker's rendered configuration
— never from the user's volume. With a boundary_id the boundary is the same on any host (a volume moves, its identity
with it); without one, nothing changes for the boundaries of today."""
import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from octopus.config import ConfigError, load
from octopus.worker import Worker

BID = "bnd-" + "a" * 32


class BoundaryIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def worker(self, **extra):
        return Worker(self.root / "worker", "t" * 40, crew_home=self.root / "crew", **extra)

    def test_without_a_boundary_id_the_identity_is_as_before(self):
        node = self.worker()
        health = node.health()
        before = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}:{node.home}".encode()).hexdigest()
        self.assertEqual(health["boundary"], before)
        self.assertNotIn("boundary_id", health)

    def test_a_boundary_id_names_the_boundary_on_any_host(self):
        node = self.worker(boundary_id=BID)
        with mock.patch("socket.gethostname", return_value="host-a"):
            first = node.health()
        with mock.patch("socket.gethostname", return_value="host-b"):
            second = node.health()
        self.assertEqual(first["boundary_id"], BID)
        self.assertEqual(first["boundary"], second["boundary"])
        self.assertEqual(first["boundary"], hashlib.sha256(f"{BID}:{os.getuid()}".encode()).hexdigest())
        self.assertNotEqual(first["host_principal"], second["host_principal"])   # the host still says which host

    def test_a_malformed_boundary_id_is_refused(self):
        path = self.root / "boundary.toml"
        for value in ("bnd-x", "../etc", "BND-" + "a" * 32):
            path.write_text(f'boundary_id = "{value}"\n')
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load(path, environ={})
        path.write_text(f'boundary_id = "{BID}"\n')
        self.assertEqual(load(path, environ={}).boundary_id, BID)


if __name__ == "__main__":
    unittest.main()
