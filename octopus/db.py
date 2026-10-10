"""Transactional product metadata. Native Crew data remains owned by Crew."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
 admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(
 hash TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 scopes TEXT NOT NULL, expires REAL NOT NULL, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS teams(
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS members(
 team_id TEXT NOT NULL REFERENCES teams(id), user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','member')), PRIMARY KEY(team_id,user_id));
CREATE TABLE IF NOT EXISTS workflows(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
 name_key TEXT NOT NULL, version INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, UNIQUE(owner_id,name_key));
CREATE TABLE IF NOT EXISTS workflow_versions(
 workflow_id TEXT NOT NULL REFERENCES workflows(id), version INTEGER NOT NULL,
 spec TEXT NOT NULL, author TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(workflow_id,version));
CREATE TABLE IF NOT EXISTS grants(
 workflow_id TEXT NOT NULL REFERENCES workflows(id), kind TEXT NOT NULL CHECK(kind IN ('user','team')),
 principal TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('viewer','runner','editor')),
 PRIMARY KEY(workflow_id,kind,principal));
CREATE TABLE IF NOT EXISTS workers(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT REFERENCES users(id),
 team_id TEXT REFERENCES teams(id), config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 CHECK((owner_id IS NULL) != (team_id IS NULL)));
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), team_id TEXT REFERENCES teams(id),
 worker_id TEXT REFERENCES workers(id), workflow_id TEXT REFERENCES workflows(id),
 snapshot TEXT NOT NULL, input TEXT NOT NULL, source TEXT NOT NULL,
 status TEXT NOT NULL, desired TEXT NOT NULL DEFAULT 'active', lifecycle TEXT NOT NULL DEFAULT 'open',
 version INTEGER NOT NULL DEFAULT 1, native_parent TEXT, workspace TEXT, error TEXT,
 created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 spec TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', current_attempt TEXT,
 PRIMARY KEY(run_id,id), UNIQUE(run_id,ordinal));
CREATE TABLE IF NOT EXISTS attempts(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), task_id TEXT NOT NULL,
 number INTEGER NOT NULL, spec TEXT NOT NULL, status TEXT NOT NULL,
 native_id TEXT, context_hash TEXT, evidence TEXT NOT NULL DEFAULT '{}', result TEXT,
 error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(run_id,task_id,number));
CREATE TABLE IF NOT EXISTS revisions(
 run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
 content TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(run_id,version));
CREATE TABLE IF NOT EXISTS commands(
 actor TEXT NOT NULL REFERENCES users(id), key TEXT NOT NULL, hash TEXT NOT NULL,
 response TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(actor,key));
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS run_events ON events(run_id,id);
CREATE INDEX IF NOT EXISTS run_owner ON runs(owner_id,team_id);
CREATE TABLE IF NOT EXISTS recoveries(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), number INTEGER NOT NULL,
 failed_task_id TEXT NOT NULL, failed_attempt_id TEXT NOT NULL REFERENCES attempts(id),
 attempt_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, context TEXT NOT NULL,
 native_id TEXT, context_hash TEXT, result TEXT, evidence TEXT NOT NULL DEFAULT '{}',
 decision TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(run_id,number));
CREATE INDEX IF NOT EXISTS recovery_run ON recoveries(run_id,number);
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
"""


class Database:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "octopus.sqlite3"
        with self.read() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
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
