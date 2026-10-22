"""Ordered migrations never initialize existing data and always back up before changing it."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from octopus import migrations
from octopus.db import Database

# Verbatim MVP 1.0.0 schema tail used by deployed databases (octopus/db.py before migrations).
LATEST = max(number for number, _, _ in migrations.MIGRATIONS)
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
        self.assertEqual(result, {"from": 0, "to": LATEST, "backup": None})
        self.assertEqual(self.version(), LATEST)

    def test_legacy_mvp_database_keeps_data(self):
        self.legacy_database()
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual((result["from"], result["to"]), (1, LATEST))
        self.assertIsNotNone(result["backup"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT username FROM users").fetchall(), [("alice",)])

    def test_pending_migration_backs_up_then_applies(self):
        self.legacy_database()
        steps = migrations.MIGRATIONS + [(LATEST + 1, "add_note", "ALTER TABLE users ADD COLUMN note TEXT;")]
        result = migrations.apply(self.path, steps, backup_dir=self.backups, clock=lambda: 1_700_000_000)
        self.assertEqual((result["from"], result["to"]), (1, LATEST + 1))
        backup = Path(result["backup"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT note FROM users").fetchall(), [(None,)])
        self.assertEqual(self.version(), LATEST + 1)

    def test_failed_migration_rolls_back_completely(self):
        self.legacy_database()
        broken = migrations.MIGRATIONS + [(LATEST + 1, "broken", "ALTER TABLE users ADD COLUMN note TEXT;\nSELECT * FROM missing_table;")]
        with self.assertRaises(sqlite3.OperationalError):
            migrations.apply(self.path, broken, backup_dir=self.backups)
        self.assertEqual(self.version(), LATEST)
        with sqlite3.connect(self.path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(users)")]
        self.assertNotIn("note", columns)

    def test_environments_migration_keeps_workers_dedicated(self):
        self.legacy_database()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w1','personal','u1','{}')")
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result["to"], LATEST)
        self.assertIsNotNone(result["backup"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT scope FROM workers WHERE id='w1'").fetchone()[0], "dedicated")
            db.execute("INSERT INTO teams(id,name,created) VALUES('t1','A',0)")
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('w1','t1')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO worker_spaces(worker_id,team_id,user_id) VALUES('w1','t1','u1')")
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w2','other','u1','{}')")
            with self.assertRaises(sqlite3.IntegrityError):  # a Team has one environment
                db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('w2','t1')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE workers SET scope='bogus' WHERE id='w1'")

    def test_access_migration_adds_audit_and_activity_columns(self):
        self.legacy_database()
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result["to"], LATEST)
        with sqlite3.connect(self.path) as db:
            self.assertIn("last_login", [c[1] for c in db.execute("PRAGMA table_info(users)")])
            self.assertIn("last_used", [c[1] for c in db.execute("PRAGMA table_info(tokens)")])
            db.execute("INSERT INTO audit(at,actor_id,action,kind,target) VALUES(1,'u1','user.deactivate','user','u2')")
            self.assertEqual(db.execute("SELECT detail FROM audit").fetchone()[0], "{}")

    def test_apps_migration_records_remote_previews(self):
        self.legacy_database()
        self.assertEqual(migrations.apply(self.path, backup_dir=self.backups)["to"], LATEST)
        with sqlite3.connect(self.path) as db:
            columns = [c[1] for c in db.execute("PRAGMA table_info(apps)")]
        self.assertEqual(columns, ["run_id", "worker_id", "status", "port", "path", "upstream", "error", "started_by",
                                   "started", "last_access", "stopped"])

    def test_teams_workers_migration_adds_archive_and_retire_columns(self):
        self.legacy_database()
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result["to"], LATEST)
        with sqlite3.connect(self.path) as db:
            self.assertIn("archived", [c[1] for c in db.execute("PRAGMA table_info(teams)")])
            columns = [c[1] for c in db.execute("PRAGMA table_info(workers)")]
            self.assertIn("retired", columns)
            self.assertIn("last_team", columns)
            db.execute("INSERT INTO teams(id,name,created) VALUES('t9','新 Team',0)")
            self.assertEqual(db.execute("SELECT archived FROM teams WHERE id='t9'").fetchone()[0], 0)

    def test_knowledge_migration_keeps_rows_and_takes_kept(self):   # D52 R4: items kept for the repository only
        self.legacy_database()
        migrations.apply(self.path, [m for m in migrations.MIGRATIONS if m[0] <= 7], backup_dir=self.backups)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w1','w','u1','{}')")
            db.execute("INSERT INTO runs(id,owner_id,snapshot,input,source,status,created,updated) "
                       "VALUES('r1','u1','{}','x','{}','completed',0,0)")
            db.execute("INSERT INTO knowledge(id,run_id,task_id,worker_id,repo,rule,result,created) "
                       "VALUES('k1','r1','knowledge','w1','repo','依赖用 pnpm','written',0)")
        self.assertEqual(migrations.apply(self.path, backup_dir=self.backups)["to"], LATEST)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT rule, result FROM knowledge").fetchall(), [("依赖用 pnpm", "written")])
            db.execute("INSERT INTO knowledge(id,run_id,task_id,worker_id,repo,rule,result,created) "
                       "VALUES('k2','r1','knowledge','w1','repo','接口测试','kept',0)")
            indexes = {row[1] for row in db.execute("PRAGMA index_list(knowledge)")}
        self.assertTrue({"knowledge_run", "knowledge_repo"} <= indexes)

    def test_newer_database_is_refused(self):
        self.legacy_database()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO schema_version VALUES(99)")
        with self.assertRaisesRegex(RuntimeError, "高于程序支持"):
            migrations.apply(self.path, backup_dir=self.backups)

    def test_database_class_applies_migrations(self):
        Database(self.root)
        self.assertEqual(self.version(), LATEST)
        Database(self.root)
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
