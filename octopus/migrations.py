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

# Execution environments (D28): a Worker is dedicated to one space, shared by the spaces in worker_spaces, or a
# spare waiting to be assigned to a Team. Added as a column so workers (referenced by runs) is not rebuilt.
ENVIRONMENTS = """
ALTER TABLE workers ADD COLUMN scope TEXT NOT NULL DEFAULT 'dedicated'
 CHECK(scope IN ('dedicated','shared','spare'));
CREATE TABLE IF NOT EXISTS worker_spaces(
 worker_id TEXT NOT NULL REFERENCES workers(id), team_id TEXT REFERENCES teams(id), user_id TEXT REFERENCES users(id),
 CHECK((team_id IS NULL) != (user_id IS NULL)), UNIQUE(worker_id, team_id), UNIQUE(worker_id, user_id));
CREATE UNIQUE INDEX IF NOT EXISTS worker_spaces_team ON worker_spaces(team_id) WHERE team_id IS NOT NULL;
"""

ACCESS = """
ALTER TABLE users ADD COLUMN last_login REAL;
ALTER TABLE tokens ADD COLUMN last_used REAL;
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
 action TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
"""

# Access management phase 2: an archived Team takes no new Sessions or members; a retired Worker leaves every listing;
# last_team is the Team a spare Worker host last served (its code and memories may remain there, P2).
TEAMS_WORKERS = """
ALTER TABLE teams ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workers ADD COLUMN retired INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workers ADD COLUMN last_team TEXT REFERENCES teams(id);
"""

# Remote preview (D36 §5): the app each Session runs in its execution environment, how the platform reaches it, and
# the last access its idle stop counts from. The Worker owns the processes; this is the platform's record of them.
APPS = """
CREATE TABLE IF NOT EXISTS apps(
 run_id TEXT PRIMARY KEY REFERENCES runs(id), worker_id TEXT NOT NULL REFERENCES workers(id), status TEXT NOT NULL,
 port INTEGER, path TEXT NOT NULL DEFAULT '/', upstream TEXT NOT NULL DEFAULT '{}', error TEXT,
 started_by TEXT REFERENCES users(id), started REAL, last_access REAL, stopped REAL);
CREATE INDEX IF NOT EXISTS apps_worker ON apps(worker_id,status);
"""

# Repository credentials (D35 §3): sealed secrets (octopus/vault.py) of a person, a Team or the platform, grants of
# platform credentials, and the default a workflow version names for its Sessions' source.
CREDENTIALS = """
CREATE TABLE IF NOT EXISTS credentials(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('github_pat','gitlab_token','github_app')),
 host TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('user','team','platform')), owner_id TEXT,
 repos TEXT NOT NULL DEFAULT '[]', access TEXT NOT NULL DEFAULT 'read' CHECK(access IN ('read','write')),
 inject INTEGER NOT NULL DEFAULT 0, hint TEXT NOT NULL, secret TEXT NOT NULL, expires REAL,
 created_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL, updated REAL NOT NULL, last_used REAL,
 disabled INTEGER NOT NULL DEFAULT 0, CHECK((scope = 'platform') = (owner_id IS NULL)));
CREATE INDEX IF NOT EXISTS credentials_owner ON credentials(scope,owner_id);
CREATE TABLE IF NOT EXISTS credential_grants(
 credential_id TEXT NOT NULL REFERENCES credentials(id), team_id TEXT REFERENCES teams(id), user_id TEXT REFERENCES users(id),
 CHECK((team_id IS NULL) != (user_id IS NULL)), UNIQUE(credential_id,team_id), UNIQUE(credential_id,user_id));
ALTER TABLE workflow_versions ADD COLUMN credentials TEXT NOT NULL DEFAULT '{}';
"""

# Knowledge (D46 P4): each item a Session's Knowledge task listed, what became of it in the execution environment's
# KiroCrew lessons (written / refused / superseded, or skipped by the platform's checks) and who removed it.
KNOWLEDGE = """
CREATE TABLE IF NOT EXISTS knowledge(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), task_id TEXT NOT NULL,
 worker_id TEXT NOT NULL REFERENCES workers(id), repo TEXT NOT NULL, rule TEXT NOT NULL, avoid TEXT,
 result TEXT NOT NULL CHECK(result IN ('written','refused','superseded','skipped')), reason TEXT, created REAL NOT NULL,
 removed REAL, removed_by TEXT REFERENCES users(id));
CREATE INDEX IF NOT EXISTS knowledge_run ON knowledge(run_id);
CREATE INDEX IF NOT EXISTS knowledge_repo ON knowledge(worker_id,repo);
"""

# D52 R4: an item gathered on a shared environment is kept for the repository's reviewed knowledge only ('kept'); SQLite
# cannot widen a CHECK in place, so the table is rebuilt with its rows and indexes.
KNOWLEDGE_KEPT = """
CREATE TABLE knowledge_next(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), task_id TEXT NOT NULL,
 worker_id TEXT NOT NULL REFERENCES workers(id), repo TEXT NOT NULL, rule TEXT NOT NULL, avoid TEXT,
 result TEXT NOT NULL CHECK(result IN ('written','refused','superseded','skipped','kept')), reason TEXT, created REAL NOT NULL,
 removed REAL, removed_by TEXT REFERENCES users(id));
INSERT INTO knowledge_next SELECT id,run_id,task_id,worker_id,repo,rule,avoid,result,reason,created,removed,removed_by FROM knowledge;
DROP TABLE knowledge;
ALTER TABLE knowledge_next RENAME TO knowledge;
CREATE INDEX IF NOT EXISTS knowledge_run ON knowledge(run_id);
CREATE INDEX IF NOT EXISTS knowledge_repo ON knowledge(worker_id,repo);
"""

# D55: every submission is a Request (its client, explicit choices and the policy it started under) and its Session
# names it; policies are a person's or a Team's admission rules by repository and mapping preferences.
REQUESTS_POLICIES = """
CREATE TABLE IF NOT EXISTS requests(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), team_id TEXT REFERENCES teams(id), key TEXT NOT NULL,
 client TEXT NOT NULL, input TEXT NOT NULL, source TEXT NOT NULL, explicit TEXT NOT NULL, policy TEXT NOT NULL,
 created REAL NOT NULL, UNIQUE(owner_id,key));
CREATE INDEX IF NOT EXISTS requests_created ON requests(created);
CREATE TABLE IF NOT EXISTS policies(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('admission','mapping')), owner_id TEXT REFERENCES users(id),
 team_id TEXT REFERENCES teams(id), rule TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 CHECK((owner_id IS NULL) != (team_id IS NULL)));
CREATE INDEX IF NOT EXISTS policies_scope ON policies(kind,owner_id,team_id);
ALTER TABLE runs ADD COLUMN request_id TEXT REFERENCES requests(id);
CREATE INDEX IF NOT EXISTS runs_request ON runs(request_id);
"""

# D61: how many tasks a person or a Team runs at once — the default per person (subject ''), a person's, a Team's.
QUOTAS = """
CREATE TABLE IF NOT EXISTS quotas(
 scope TEXT NOT NULL CHECK(scope IN ('default','user','team')), subject TEXT NOT NULL DEFAULT '',
 running INTEGER NOT NULL CHECK(running BETWEEN 1 AND 50), updated_by TEXT NOT NULL REFERENCES users(id),
 updated REAL NOT NULL, PRIMARY KEY(scope,subject));
"""

# D64: a Session shared with a person (subject: user id), a Team (team id) or the whole company (subject '').
SHARES = """
CREATE TABLE IF NOT EXISTS shares(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), kind TEXT NOT NULL CHECK(kind IN ('user','team','everyone')),
 subject TEXT NOT NULL DEFAULT '', role TEXT NOT NULL CHECK(role IN ('viewer','collaborator')),
 created_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL, UNIQUE(run_id,kind,subject),
 CHECK(kind != 'everyone' OR role = 'viewer'));
CREATE INDEX IF NOT EXISTS shares_subject ON shares(kind,subject);
"""

# Owner 2026-10-22: a person's own pin and star on a Session they can read (「历史 Session，请增加收藏、置顶」).
MARKS = """
CREATE TABLE IF NOT EXISTS run_marks(
 user_id TEXT NOT NULL REFERENCES users(id), run_id TEXT NOT NULL REFERENCES runs(id),
 pinned INTEGER NOT NULL DEFAULT 0, starred INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL, PRIMARY KEY(user_id, run_id));
"""

# D74 (owner 2026-10-25): who a Session runs as — its creator until someone takes it over to their own boundary.
# D75: a Session's pages or running demo shared with anyone on the Internet — by an unguessable link, with a four-digit
# code or open; the code's failed tries are counted here so a restart does not reset them.
PUBLIC_SHARES = """
CREATE TABLE IF NOT EXISTS public_shares(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), token TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('pages','app')), mode TEXT NOT NULL CHECK(mode IN ('code','open')), code TEXT,
 entry TEXT, created_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL, expires REAL,
 views INTEGER NOT NULL DEFAULT 0, last_viewed REAL, failures INTEGER NOT NULL DEFAULT 0, failed_since REAL,
 locked_until REAL, total_failures INTEGER NOT NULL DEFAULT 0, code_retired REAL);
CREATE INDEX IF NOT EXISTS public_shares_run ON public_shares(run_id);
"""

# MVP 0.2.0 (S9): every new boundary's identity and OS user id from the control plane; numbers are never reused, a
# released boundary's name may be.
BOUNDARIES = """
CREATE TABLE IF NOT EXISTS boundaries(
 id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, uid INTEGER NOT NULL UNIQUE, name TEXT NOT NULL,
 user_id TEXT REFERENCES users(id), state TEXT NOT NULL DEFAULT 'allocated' CHECK(state IN ('allocated','ready','released')),
 created REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS boundaries_name ON boundaries(name) WHERE state != 'released';
"""

EXECUTOR = """
ALTER TABLE runs ADD COLUMN executor_id TEXT REFERENCES users(id);
"""

MIGRATIONS = [(1, "baseline", BASELINE), (2, "environments", ENVIRONMENTS), (3, "access", ACCESS),
              (4, "teams_workers", TEAMS_WORKERS), (5, "apps", APPS), (6, "credentials", CREDENTIALS),
              (7, "knowledge", KNOWLEDGE), (8, "knowledge_kept", KNOWLEDGE_KEPT),
              (9, "requests_policies", REQUESTS_POLICIES), (10, "quotas", QUOTAS), (12, "shares", SHARES), (13, "marks", MARKS),
              (14, "executor", EXECUTOR), (15, "public_shares", PUBLIC_SHARES),
              (16, "boundaries", BOUNDARIES)]   # 11: D57's (another branch)


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
        # D64: every number not applied yet, not only those above the highest — two branches each add one, and the one
        # that lands second is not skipped for good (their migrations are independent by rule).
        applied = {row[0] for row in db.execute("SELECT version FROM schema_version")}
        pending = sorted(m for m in migrations if m[0] not in applied)
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
