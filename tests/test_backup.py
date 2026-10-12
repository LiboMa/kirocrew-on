"""Backups are consistent under concurrent writes and restore into a separate directory only."""
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import time
import unittest

from octopus import backup, migrations
from octopus.db import Database


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = Database(self.root / "platform")
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u','u','x',1,1,0)")

    def tearDown(self):
        self.temporary.cleanup()

    def test_backup_during_writes_is_consistent_and_restores_elsewhere(self):
        stop = threading.Event()

        def writer():
            n = 0
            while not stop.is_set():
                with self.db.transaction() as c:
                    c.execute("INSERT INTO teams(id,name,created) VALUES(?,?,?)", (f"t{n}", f"team{n}", time.time()))
                n += 1

        thread = threading.Thread(target=writer)
        thread.start()
        time.sleep(0.1)
        manifest = backup.backup_database(self.db.path, self.root / "backups")
        stop.set()
        thread.join()
        self.assertEqual(manifest["integrity"], "ok")
        self.assertEqual(manifest["schema_version"], max(number for number, _, _ in migrations.MIGRATIONS))
        path = self.root / "backups" / manifest["file"]
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        report = backup.restore_verify(path.with_suffix(".json"), self.root / "restore")
        self.assertTrue(report["ok"])
        self.assertEqual(report["tables"]["users"], 1)
        self.assertEqual(report["tables"]["teams"], manifest["tables"]["teams"])

    def test_restore_refuses_non_empty_target_and_tampered_file(self):
        manifest = backup.backup_database(self.db.path, self.root / "backups")
        target = self.root / "busy"
        target.mkdir()
        (target / "keep").write_text("x")
        with self.assertRaisesRegex(ValueError, "非空"):
            backup.restore_verify(self.root / "backups" / (Path(manifest["file"]).stem + ".json"), target)
        with (self.root / "backups" / manifest["file"]).open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "sha256"):
            backup.restore_verify(self.root / "backups" / (Path(manifest["file"]).stem + ".json"), self.root / "new")

    def test_archive_directory_excludes_patterns(self):
        source = self.root / "boundary"
        (source / "worker/run-1").mkdir(parents=True)
        (source / "worker/run-1/handoff.md").write_text("h")
        (source / "home/.cache").mkdir(parents=True)
        (source / "home/.cache/big").write_text("c")
        manifest = backup.archive_directory(source, self.root / "backups", "boundary-a", exclude=("home/.cache/*",))
        archive = self.root / "backups" / manifest["file"]
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        self.assertIn("worker/run-1/handoff.md", names)
        self.assertNotIn("home/.cache/big", names)
        self.assertEqual(manifest["files"], 1)


if __name__ == "__main__":
    unittest.main()
