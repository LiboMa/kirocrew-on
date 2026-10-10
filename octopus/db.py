"""Transactional product metadata. Native Crew data remains owned by Crew."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .migrations import BASELINE as SCHEMA, apply as migrate  # noqa: F401  (SCHEMA kept for callers)


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "octopus.sqlite3"
        with self.read() as db:
            db.execute("PRAGMA journal_mode=WAL")
        migrate(self.path, backup_dir=self.root / "backups")
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def event(db, run, kind, payload):
        db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                   (run, kind, dumps(payload), time.time()))
