"""Ordered migrations never initialize existing data and always back up before changing it."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from octopus import migrations
from octopus.db import Database

# Verbatim MVP 1.0.0 schema tail used by deployed databases (octopus/db.py before migrations).
LEGACY_TAIL = "CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);\nINSERT OR IGNORE INTO schema_version VALUES(1);\n"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "octopus.sqlite3"
        self.backups = self.root / "backups"

    def tearDown(self):
        self.temporary.cleanup()

    def legacy_database(self):
        db = sqlite3.connect(self.path)
        db.executescript(migrations.BASELINE + LEGACY_TAIL)
        db.execute("INSERT INTO users(id,username,password,admin,active,created) VALUES('u1','alice','x',1,1,0)")
        db.commit()
        db.close()

    def version(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT max(version) FROM schema_version").fetchone()[0]

    def test_fresh_database_gets_baseline_without_backup(self):
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result, {"from": 0, "to": 1, "backup": None})
        self.assertEqual(self.version(), 1)

    def test_legacy_mvp_database_keeps_data(self):
        self.legacy_database()
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result, {"from": 1, "to": 1, "backup": None})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT username FROM users").fetchall(), [("alice",)])

    def test_pending_migration_backs_up_then_applies(self):
        self.legacy_database()
        steps = migrations.MIGRATIONS + [(2, "add_note", "ALTER TABLE users ADD COLUMN note TEXT;")]
        result = migrations.apply(self.path, steps, backup_dir=self.backups, clock=lambda: 1_700_000_000)
        self.assertEqual((result["from"], result["to"]), (1, 2))
        backup = Path(result["backup"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT note FROM users").fetchall(), [(None,)])
        self.assertEqual(self.version(), 2)

    def test_failed_migration_rolls_back_completely(self):
        self.legacy_database()
        broken = migrations.MIGRATIONS + [(2, "broken", "ALTER TABLE users ADD COLUMN note TEXT;\nSELECT * FROM missing_table;")]
        with self.assertRaises(sqlite3.OperationalError):
            migrations.apply(self.path, broken, backup_dir=self.backups)
        self.assertEqual(self.version(), 1)
        with sqlite3.connect(self.path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(users)")]
        self.assertNotIn("note", columns)

    def test_newer_database_is_refused(self):
        self.legacy_database()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schema_version VALUES(99)")
        with self.assertRaisesRegex(RuntimeError, "高于程序支持"):
            migrations.apply(self.path, backup_dir=self.backups)

    def test_database_class_applies_migrations(self):
        Database(self.root)
        self.assertEqual(self.version(), 1)
        Database(self.root)
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
