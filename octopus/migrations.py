"""Ordered, transactional schema migrations; a consistent backup precedes any change to existing data."""
import os
from pathlib import Path
import sqlite3
import time

BASELINE = """
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
"""

MIGRATIONS = [(1, "baseline", BASELINE)]


def _statements(script):
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                yield buffer.strip()
            buffer = ""
    if buffer.strip() and not buffer.strip().startswith("--"):
        raise ValueError("迁移脚本包含未结束的语句")


def current(db):
    db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY)")
    return db.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]


def _backup(db, backup_dir, version, clock):
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(clock()))
    target = backup_dir / f"octopus-v{version}-{stamp}.sqlite3"
    copy = sqlite3.connect(target)
    try:
        db.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("迁移前备份完整性检查失败；未执行迁移")
    finally:
        copy.close()
    os.chmod(target, 0o600)
    return target


def apply(path, migrations=MIGRATIONS, *, backup_dir, clock=time.time):
    path, backup_dir = Path(path), Path(backup_dir)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=10000")
        found = current(db)
        latest = max(number for number, _, _ in migrations)
        if found > latest:
            raise RuntimeError(f"数据库 schema 版本 {found} 高于程序支持的 {latest}；请使用更新的版本或从备份恢复")
        pending = sorted(m for m in migrations if m[0] > found)
        backup = _backup(db, backup_dir, found, clock) if pending and found > 0 else None
        for number, name, script in pending:
            db.execute("BEGIN IMMEDIATE")
            try:
                for statement in _statements(script):
                    db.execute(statement)
                db.execute("INSERT INTO schema_version VALUES(?)", (number,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return {"from": found, "to": max(found, latest), "backup": str(backup) if backup else None}
    finally:
        db.close()
