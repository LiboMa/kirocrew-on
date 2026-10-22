# 会话变更记录与运行优化（D34） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话的每次写入都记录操作者、客户端、字段前后值与原因并可查询；续作只重跑所选与必须重跑的任务；KiroCrew 规划产出结构化任务，需求与验收标准传给每个任务；瞬时失败自动重试一次、kiro-cli 登录过期在派发前等待、部署时 Gateway 未变不重启。

**Architecture:** 新模块 `octopus/journal.py` 是 `runs / tasks / attempts / recoveries` 唯一的 `UPDATE` 入口，写入时比较前后值并在同一事务内写一条带 `actor / changes / reason` 的事件（沿用 `events` 表，无迁移）；守护测试禁止绕过。任务结构字段由 `domain.steps()` 校验、随任务 `spec` 保存；Engine 派发时把它们作为独立的 `contract` 发送（`spec` 仍是旧 6 字段，未升级的 Worker 照常工作），并附上规划补全的 `requirement`。续作的重跑范围在 `operate("continue")` 内按规则补全。

**Tech Stack:** Python 3 标准库、SQLite（`json_extract`）、Bash（安装脚本）、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-12-session-changes-and-runtime-design.md`（§4–§7、§9）

## Global Constraints

- 不新增依赖；面向用户的文字使用中文，风格与现有控制台一致。
- 变更记录中不出现 Worker Token、原生登录链接、原生输出全文；长于 500 字符的文本只记 `{"chars", "sha256"}`。
- `events` 表结构不变；现有事件种类与 payload 字段保留（控制台时间线读取 `payload.by`、`status`、`error` 等），只增加 `actor`、`changes`、`reason`。
- 派发请求的 `spec` 仍只含 `id, name, tool, model, effort, prompt`；新增内容放在 `contract`、`requirement`（未升级的 Worker 忽略）。
- 提交规则（负责人 CLAUDE.md）：功能分支 `feat/m5-session-runtime` 本地提交；EC2 验收完成并经负责人确认后才推送。需要执行任务的验收先征得负责人同意；部署前确认目标主机没有进行中的会话。
- Python：`/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-on/.venv/bin/python -m unittest <模块>`（下文记作 `$PY`）；控制台：`npm --prefix web test`；完整回归：`PYTHON=$PY tools/regress.sh`。

## Review Focus

1. 同一事务内多次写同一行（如 `operate` 先改状态、再改版本）：每次写入各记一条变更，前值取本事务内已写入的值，不重复、不遗漏（Task 2 测试 `test_one_command_records_every_field_it_changes`）。
2. 旧事件的 `actor` 是用户 id 字符串：变更查询把它们归为「人」并补用户名，不报错（Task 4 测试 `test_legacy_events_are_listed_with_their_user`）。
3. 运行时取消发生在用户已请求停止之后：不能自动重试（会话期望状态已不是 active）（Task 6 测试 `test_a_cancellation_after_stop_is_not_retried`）。
4. 规划给出 `depends_on` 指向不存在或后续的任务、或 `rerun` 列出所选任务之前的任务：明确拒绝并说明原因（Task 9 测试 `test_depends_on_must_point_to_earlier_tasks`，Task 11 测试 `test_rerun_lists_only_later_tasks`）。
5. 未升级的 Worker（`w1` 部署前）收到新派发：`spec` 不含新字段，派发成功（Task 10 测试 `test_dispatch_spec_keeps_the_legacy_fields`）。

---

### Task 1: 变更记录模块与客户端识别

**Files:**
- Create: `octopus/journal.py`、`tests/test_journal.py`
- Modify: `octopus/security.py:authenticate`（查询 `t.label`，返回 `client`）

**Interfaces:**
- Produces:
  - `journal.ENGINE / WORKER / KIROCREW / RECOVERY`：`{"kind": "engine" | "worker" | "kirocrew" | "recovery"}`。
  - `journal.person(actor) -> {"kind": "user", "id", "username", "client"}`。
  - `journal.client(label) -> "console" | "mcp" | "api"`。
  - `journal.record(db, rid, kind, actor, changes=None, reason=None, **extra)`：写一条事件（payload = extra + actor + changes + reason）。
  - `journal.change(db, table, key, actor, kind, reason=None, event=None, **fields) -> dict`：单行写入；`key` 为 id，`tasks` 为 `(run_id, task_id)`；总是执行 UPDATE（保持 `updated` 等时间戳行为），只有非 `updated` 字段变化时记事件，返回 `{字段: [前, 后]}`。
  - `journal.change_rows(db, table, where, params, actor, kind, reason=None, event=None, **fields) -> list`：多行写入，事件 `changes` 为 `[{"id", "changes"}]`；无行匹配或无变化时不记事件。
  - `authenticate()` 的返回值增加 `client`。

- [ ] **Step 1: 写失败测试** — `tests/test_journal.py`：

```python
"""D34 §4: the change journal records who changed which fields of a Session, from what, to what, and why."""
import json
import tempfile
import unittest

from octopus import journal
from octopus.db import Database, dumps
from octopus.security import authenticate, create_user, issue_token


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        with self.db.transaction() as db:
            self.user = create_user(db, "alice", "Test-password-123!", admin=True)
            db.execute("INSERT INTO runs(id,owner_id,snapshot,input,source,status,created,updated) "
                       "VALUES('run-1',?,'{}','需求','{}','ready',0,0)", (self.user["id"],))
            db.execute("INSERT INTO tasks(run_id,id,ordinal,spec,status) VALUES('run-1','code',0,'{}','pending')")

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        with self.db.read() as db:
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM events ORDER BY id")]

    def test_change_records_only_what_changed(self):
        with self.db.transaction() as db:
            diff = journal.change(db, "runs", "run-1", journal.ENGINE, "run.state", reason="派发前核对",
                                  event={"status": "waiting_worker"}, status="waiting_worker", error=None, updated=5)
        self.assertEqual(diff, {"status": ["ready", "waiting_worker"]})
        (event,) = self.events()
        self.assertEqual((event["run_id"], event["kind"]), ("run-1", "run.state"))
        self.assertEqual(event["payload"], {"status": "waiting_worker", "actor": {"kind": "engine"},
                                            "changes": {"status": ["ready", "waiting_worker"]}, "reason": "派发前核对"})
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT updated FROM runs").fetchone()[0], 5)

    def test_unchanged_write_records_nothing(self):
        with self.db.transaction() as db:
            self.assertEqual(journal.change(db, "runs", "run-1", journal.ENGINE, "run.state", status="ready", updated=9), {})
        self.assertEqual(self.events(), [])

    def test_long_text_is_recorded_by_length_and_hash(self):
        with self.db.transaction() as db:
            journal.change(db, "runs", "run-1", journal.person({"id": self.user["id"], "username": "alice", "client": "mcp"}),
                           "command.continue", input="长" * 600)
        change = self.events()[0]["payload"]["changes"]["input"]
        self.assertEqual(change[0], "需求")
        self.assertEqual(change[1]["chars"], 600)
        self.assertEqual(len(change[1]["sha256"]), 64)

    def test_task_rows_and_many_rows(self):
        with self.db.transaction() as db:
            journal.change(db, "tasks", ("run-1", "code"), journal.WORKER, "attempt.state", status="running")
            journal.change_rows(db, "tasks", "run_id=? AND status=?", ("run-1", "running"), journal.ENGINE, "task.reset",
                                status="pending")
        first, second = self.events()
        self.assertEqual(first["payload"]["changes"], {"status": ["pending", "running"]})
        self.assertEqual(second["payload"]["changes"], [{"id": "code", "changes": {"status": ["running", "pending"]}}])

    def test_clients_from_token_labels(self):
        self.assertEqual([journal.client(x) for x in ("Web login", "claude MCP", "kiro MCP", "API", None)],
                         ["console", "mcp", "mcp", "api", "api"])
        with self.db.transaction() as db:
            token = issue_token(db, self.user["id"], label="claude MCP")["token"]
            self.assertEqual(authenticate(db, token)["client"], "mcp")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_journal`，预期 `ImportError: cannot import name 'journal'`。
- [ ] **Step 3: 实现** — `octopus/journal.py`：

```python
"""Session change journal (D34 §4): the only way to update a Session's rows. Each write compares old and new values and
records, in the same transaction, who changed which fields from what to what and why — as an event on the Session."""
import hashlib
import time

from .db import dumps

ENGINE, WORKER, KIROCREW, RECOVERY = ({"kind": kind} for kind in ("engine", "worker", "kirocrew", "recovery"))
KEYS = {"runs": ("id",), "tasks": ("run_id", "id"), "attempts": ("id",), "recoveries": ("id",)}
QUIET = {"updated"}   # timestamps change on every write and are not a change worth recording
LONG = 500


def client(label):
    return "console" if label == "Web login" else "mcp" if "MCP" in (label or "") else "api"


def person(actor):
    return {"kind": "user", "id": actor["id"], "username": actor.get("username"), "client": actor.get("client", "api")}


def shown(value):
    """A value as the journal keeps it: long text by length and hash, never in full."""
    if isinstance(value, str) and len(value) > LONG:
        return {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


def record(db, rid, kind, actor, changes=None, reason=None, **extra):
    payload = {**extra, "actor": actor, **({"changes": changes} if changes else {}), **({"reason": reason} if reason else {})}
    db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)", (rid, kind, dumps(payload), time.time()))


def _diff(row, fields):
    return {k: [shown(row[k]), shown(v)] for k, v in fields.items() if k not in QUIET and row[k] != v}


def change(db, table, key, actor, kind, reason=None, event=None, **fields):
    columns = KEYS[table]
    where = " AND ".join(f"{c}=?" for c in columns)
    params = tuple(key) if isinstance(key, tuple) else (key,)
    row = db.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchone()
    if row is None:
        return {}
    db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) + f" WHERE {where}", (*fields.values(), *params))
    diff = _diff(row, fields)
    if diff:
        record(db, row["id"] if table == "runs" else row["run_id"], kind, actor, diff, reason, **(event or {}))
    return diff


def change_rows(db, table, where, params, actor, kind, reason=None, event=None, **fields):
    rows = db.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
    if not rows:
        return []
    db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) + f" WHERE {where}", (*fields.values(), *params))
    by_run = {}
    for row in rows:
        diff = _diff(row, fields)
        if diff:
            by_run.setdefault(row["id"] if table == "runs" else row["run_id"], []).append({"id": row["id"], "changes": diff})
    for rid, items in by_run.items():
        record(db, rid, kind, actor, items, reason, **(event or {}))
    return [item for items in by_run.values() for item in items]
```

`security.authenticate`：SELECT 增加 `t.label`；`value["client"] = client(value.pop("label"))`（`from .journal import client` 放在函数内，避免循环导入）。`server.py` 的 `/v1/me` 继续排除 `token_id`、`last_used`。

- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_journal tests.test_access`，全部 OK。
- [ ] **Step 5: 提交** — `feat(journal): 会话变更记录模块——比较前后值、记录操作者与客户端；Token 识别控制台 / Coding Agent / API`

### Task 2: 用户操作经变更记录写入（domain）

**Files:**
- Modify: `octopus/domain.py`（`start` 的 `run.created` 事件、`operate` 全部分支）
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: Task 1 的 `journal.change / change_rows / record / person`。
- Produces: `operate` 每个分支的写入经 `journal`，`actor = person(actor)`；命令事件 `command.<action>` 保持原 payload（`version`、`oversight`、`by`）并增加 `actor`（对象）与本命令的参数（`task_id`、`worker_id`、`rerun` 等）；`run.created` 的 `actor` 改为 `person(actor)`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_journal.py`，新建 `CommandJournalTests(AccessCase)`，从 `tests.test_access` 导入 `AccessCase`）

```python
class CommandJournalTests(AccessCase):
    def changes(self, rid):
        with self.db.read() as db:
            return [(r["kind"], json.loads(r["payload"])) for r in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (rid,))]

    def test_one_command_records_every_field_it_changes(self):
        env = self.shared_env()
        run = self.start(self.a, worker_id=env)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='stopped' WHERE id=?", (run["id"],))   # fixture only
        actor = {**self.a, "client": "console"}
        self.service.operate(actor, run["id"], "continue", {"request_id": "continue-000001", "expected_version": 1,
                                                            "task_id": "code", "input": "研发需求与真实验证（修订）"})
        kinds = [k for k, _ in self.changes(run["id"])]
        self.assertEqual(kinds[0], "run.created")
        command = next(p for k, p in self.changes(run["id"]) if k == "command.continue")
        self.assertEqual(command["actor"], {"kind": "user", "id": self.a["id"], "username": "alice", "client": "console"})
        self.assertEqual(command["task_id"], "code")
        self.assertEqual(command["changes"]["status"], ["stopped", "ready"])
        self.assertEqual(command["changes"]["input"], ["研发需求与真实验证", "研发需求与真实验证（修订）"])
        self.assertEqual(command["changes"]["version"], [1, 2])
        reset = next(p for k, p in self.changes(run["id"]) if k == "task.reset")
        self.assertEqual(reset["changes"][0]["id"], "code")

    def test_assign_worker_records_the_worker(self):
        run = self.start(self.a)   # no environment: waiting_worker
        env = self.shared_env()
        self.service.operate({**self.a, "client": "mcp"}, run["id"], "assign-worker",
                             {"request_id": "assign-0000001", "expected_version": 1, "worker_id": env})
        command = next(p for k, p in self.changes(run["id"]) if k == "command.assign-worker")
        self.assertEqual(command["worker_id"], env)
        self.assertEqual(command["changes"]["worker_id"], [None, env])
        self.assertEqual(command["actor"]["client"], "mcp")
```

- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_journal.CommandJournalTests`，预期 `command.continue` 的 payload 没有 `changes`、`task.reset` 不存在。
- [ ] **Step 3: 实现** — `operate` 的 `execute()` 内：
  - 先算出命令参数 `params = {k: body[k] for k in ("task_id", "worker_id", "rerun", "confirmation") if k in body}`；把原来的 `db.execute("UPDATE runs ...")` / `db.execute("UPDATE tasks ...")` 依次改为 `journal.change(db, "runs", rid, who, "command." + action, event=..., **fields)` 与 `journal.change_rows(db, "tasks", ..., who, "task.reset", ...)`，`who = person(actor)`。
  - 为了「一个命令一条命令事件」：命令内对 `runs` 的多次写入合并为一次 `journal.change(...)` 调用（先在局部 `fields` 字典累积 `input / desired / status / error / worker_id / native_parent / workspace / lifecycle / version / updated`，最后一次写入）；`snapshot` 的变化（纠错策略、执行契约）单独记 `run.settings`，`event={"recovery": [旧策略, 新策略]}` 或 `{"execution_contract": [旧, 新]}`。
  - 原来的 `self.db.event(db, rid, "command." + action, {...})` 删除：命令事件由合并后的 `journal.change` 写出，`event = {"version": version, **params, **(oversight 字段)}`；没有字段变化的命令（理论上不存在：至少 `version` 变化）也会记录。
  - `start` 中 `self.db.event(db, rid, "run.created", {"actor": actor["id"], ...})` 改为 `journal.record(db, rid, "run.created", person(actor), worker_assigned=bool(worker))`。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_journal tests.test_access tests.test_mvp tests.test_teams_workers tests.test_revisions`，全部 OK（时间线依赖的 `payload.by` 保持不变）。
- [ ] **Step 5: 提交** — `feat(journal): 会话命令经变更记录写入——操作者、客户端、命令参数与字段前后值`

### Task 3: 系统写入经变更记录（engine、planning、recovery）与守护测试

**Files:**
- Modify: `octopus/engine.py`（`update`、`advance`、`_failed`）、`octopus/planning.py:expand`、`octopus/recovery.py`（`schedule`、`automatic`、`reconcile`、`Coordinator`）
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: Task 1 的 `journal`。
- Produces:
  - `Engine.update(rid, status, error=None, *, actor=journal.ENGINE, reason=None, **extra)`：经 `journal.change(..., "run.state", event={"status", "error"})`；`native_parent / workspace` 的写入记 `run.binding`。
  - 派发回执 `attempt.dispatched`（`actor=WORKER`）、进入执行 `attempt.running`（`WORKER`）、执行结果 `attempt.state`（`WORKER`）、创建 `attempt.created`（`ENGINE`）、完成 `run.completed`（`ENGINE`）、停止核对 `session.<desired>`（`WORKER`）、规划 `plan.applied`（`KIROCREW`）、纠错 `recovery.*` 与 `recovery.state`（`RECOVERY`；手动诊断的 `recovery.started` 为发起的用户）。

- [ ] **Step 1: 写失败测试**

```python
import re
from pathlib import Path


class GuardTests(unittest.TestCase):
    def test_session_rows_are_updated_only_through_the_journal(self):
        pattern = re.compile(r"(UPDATE|DELETE FROM)\s+(runs|tasks|attempts|recoveries)\b")
        offenders = [f"{path.name}:{number}" for path in sorted(Path("octopus").glob("*.py")) if path.name != "journal.py"
                     for number, line in enumerate(path.read_text().splitlines(), 1) if pattern.search(line)]
        self.assertEqual(offenders, [])


class SystemJournalTests(AccessCase):
    def test_engine_and_worker_changes_name_their_actor(self):
        env = self.shared_env()
        run = self.start(self.a, worker_id=env)
        calls = []
        def worker(config, path, body, **kwargs):
            calls.append(path)
            if path == "/prepare":
                return {"native_parent": "octopus-x", "workspace": "/w/x"}
            if path == "/dispatch":
                return {"status": "running", "native_id": "abc12345", "context_hash": "h"}
            return {"status": "running", "native": {"done": False}}
        from octopus.engine import Engine
        engine = Engine(self.db, request=worker)
        for _ in range(3):
            engine.tick()
        with self.db.read() as db:
            rows = [(r["kind"], json.loads(r["payload"])) for r in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run["id"],))]
        actors = {kind: payload["actor"]["kind"] for kind, payload in rows if isinstance(payload.get("actor"), dict)}
        self.assertEqual(actors["run.binding"], "worker")
        self.assertEqual(actors["attempt.created"], "engine")
        self.assertEqual(actors["attempt.dispatched"], "worker")
        binding = next(p for k, p in rows if k == "run.binding")
        self.assertEqual(binding["changes"]["native_parent"], [None, "octopus-x"])
```

- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_journal.GuardTests tests.test_journal.SystemJournalTests`，守护测试列出 `engine.py`、`planning.py`、`recovery.py` 的写入行；`run.binding` 不存在。
- [ ] **Step 3: 实现** — 逐处替换（行号以 `ec02d73` 后的当前文件为准，替换后守护测试为空）：
  - `engine.update`：`binding` 分支与 `values` 分支改为 `journal.change(db, "runs", rid, actor, "run.binding" if 只有绑定字段 else "run.state", reason, event={"status": status, "error": error}, **values)`；准备完成时 `self.update(rid, "ready", actor=journal.WORKER, native_parent=..., workspace=...)` 记一条 `run.state`（含 `native_parent / workspace` 的变化）并另记 `run.binding`——实现为：绑定字段与状态分开两次 `change`，先绑定（`run.binding`，WORKER）后状态。
  - 停止核对（`advance` 中 `desired != active` 分支）：`runs`、`attempts`、`tasks`、`recoveries` 四处写入改为 `journal.change` / `change_rows`（`WORKER`，`session.<desired>`），原 `self.db.event(... "session." + desired, result)` 删除，`result` 并入第一条的 `event`。
  - 完成：`journal.change(db, "runs", rid, ENGINE, "run.completed", event={"quality": ...}, status="completed", updated=...)`。
  - 新 Attempt：`attempts` 的 INSERT 保留；`tasks` 的更新改 `journal.change(..., ENGINE, "attempt.created", event={"id", "task_id", "number"})`，原事件删除。
  - 派发回执：`attempts` 更新 `journal.change(..., WORKER, "attempt.dispatched")`，`tasks` 条件更新 `journal.change_rows(..., WORKER, "attempt.dispatched", where="run_id=? AND id=? AND current_attempt=? AND EXISTS (SELECT 1 FROM runs WHERE id=? AND desired='active')")`。
  - `/inspect` 未完成：`journal.change(..., WORKER, "attempt.running", status="running", native_id=...)`。
  - 执行结果：`attempts`、规划失败改写、`tasks` 三处改 `journal.change`（`WORKER`，`attempt.state`，`event` 为原 payload），原 `self.db.event(... "attempt.state" ...)` 删除（只在有变化时记录，与原条件等价）。
  - `_failed` 的阻断分支：`attempts` / `tasks` 改 `journal.change` / `change_rows`（`WORKER`，`attempt.state`）。
  - `planning.expand`：`runs` 更新改 `journal.change(..., KIROCREW, "plan.applied", event={"tasks", "intent", "notes"})`；`engine.py` 中原 `self.db.event(db, rid, "plan.applied", ...)` 删除。
  - `recovery.py`：`schedule` 的 `runs` 更新（`recovery.started`，`event` 为原 payload，actor 为 `RECOVERY`，手动时为 `person(actor)`——`schedule` 增加参数 `actor=None`，`operate` 的 recover 分支传入）、`reconcile` 的 `runs` 更新、`Coordinator` 内全部 `recoveries` / `runs` / `tasks` 更新（`recovery.state` / `recovery.blocked` / `recovery.applied`，`RECOVERY`），原 `self.db.event` / `database.event` 调用并入。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_journal tests.test_mvp tests.test_recovery tests.test_engine_concurrency tests.test_planning tests.test_teams_workers tests.test_access tests.test_native_unavailable`，全部 OK。
- [ ] **Step 5: 提交** — `feat(journal): Engine、Worker 回执、KiroCrew 规划与纠错的写入经变更记录；守护测试禁止绕过`

### Task 4: 变更记录接口与导出

**Files:**
- Modify: `octopus/domain.py`（`Service.changes`）、`octopus/server.py`（`GET /v1/runs/<id>/changes`、导出加 `changes`）、`octopus/schema.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Produces: `Service.changes(actor, rid, who=None, kind=None, before=None, limit=50) -> list[{"id", "at", "kind", "actor", "changes", "reason", "detail"}]`；`who` 为 `user` / `system`；`kind` 为前缀；旧事件 `actor` 为字符串时转为 `{"kind": "user", "id", "username"}`，没有 `actor` 时为 `{"kind": "legacy"}`（归为系统）。`detail` 为 payload 去掉 `actor / changes / reason` 后的其余字段。

- [ ] **Step 1: 写失败测试**

```python
class ChangesApiTests(AccessCase):
    def test_changes_are_filtered_by_actor_and_kind_and_follow_session_visibility(self):
        run = self.start(self.a)
        with self.db.transaction() as db:
            db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                       (run["id"], "run.state", dumps({"status": "ready", "actor": {"kind": "engine"}}), 1))
        people = self.service.changes(self.a, run["id"], who="user")
        self.assertEqual([c["kind"] for c in people], ["run.created"])
        self.assertEqual([c["kind"] for c in self.service.changes(self.a, run["id"], who="system")], ["run.state"])
        self.assertEqual([c["kind"] for c in self.service.changes(self.a, run["id"], kind="run.")], ["run.state", "run.created"])
        with self.http() as call:
            self.assertEqual(call("GET", f"/v1/runs/{run['id']}/changes", self.tokens["eve"])[0], 404)
            status, body = call("GET", f"/v1/runs/{run['id']}/changes?who=user&limit=1", self.tokens["alice"])
            self.assertEqual((status, len(body)), (200, 1))
            self.assertIn("changes", call("GET", f"/v1/runs/{run['id']}/export", self.tokens["alice"])[1])

    def test_legacy_events_are_listed_with_their_user(self):
        run = self.start(self.a)
        with self.db.transaction() as db:
            db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                       (run["id"], "command.stop", dumps({"actor": self.a["id"], "version": 2}), 2))
            db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                       (run["id"], "attempt.state", dumps({"status": "accepted"}), 3))
        listed = {c["kind"]: c for c in self.service.changes(self.a, run["id"])}
        self.assertEqual(listed["command.stop"]["actor"], {"kind": "user", "id": self.a["id"], "username": "alice"})
        self.assertEqual(listed["attempt.state"]["actor"], {"kind": "legacy"})
        self.assertEqual([c["kind"] for c in self.service.changes(self.a, run["id"], who="user")], ["command.stop", "run.created"])
```

- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_journal.ChangesApiTests`，预期 `changes` 方法不存在。
- [ ] **Step 3: 实现**

```python
    def changes(self, actor, rid, who=None, kind=None, before=None, limit=50):
        """The Session's change journal, newest first (D34 §4.3): who changed what, from what, to what and why."""
        require(who in (None, "user", "system"), "who 只能是 user 或 system")
        require(type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200 的整数")
        person = ("(json_type(payload,'$.actor')='text' OR json_extract(payload,'$.actor.kind')='user')")
        sql = "SELECT * FROM events WHERE run_id=? AND id<?"
        params = [rid, before or 2 ** 62]
        if who:
            sql += f" AND {'' if who == 'user' else 'NOT '}{person}"
        if kind:
            sql += " AND kind LIKE ? ESCAPE '\\'"
            params.append(kind.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        with self.db.read() as db:
            run_access(db, actor, rid)
            users = dict(db.execute("SELECT id,username FROM users").fetchall())
            rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload"])
            who_ = payload.pop("actor", None)
            if isinstance(who_, str):
                who_ = {"kind": "user", "id": who_, "username": users.get(who_)}
            result.append({"id": row["id"], "at": row["created"], "kind": row["kind"], "actor": who_ or {"kind": "legacy"},
                           "changes": payload.pop("changes", None), "reason": payload.pop("reason", None), "detail": payload})
        return result
```

  注意：`json_type(payload,'$.actor')` 在 `actor` 缺失时为 NULL，`NOT (NULL OR ...)` 为 NULL → 被 `WHERE` 排除；系统筛选写成 `AND NOT coalesce(<person>, 0)`。`server.py` 在 `revisions` 路由旁增加 `changes`：校验 `who`、`kind`（≤ 40 字符）、`before`、`limit` 为整数后调用；导出增加 `"changes": service.changes(actor, rid, limit=200)`。`schema.py`：`"/v1/runs/{id}/changes": {"get": None}`，描述写明参数。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_journal tests.test_access tests.test_mvp`，全部 OK。
- [ ] **Step 5: 提交** — `feat(journal): 会话变更查询接口（按人 / 系统与动作筛选）与导出`

### Task 5: 控制台「变更记录」面板

**Files:**
- Create: `web/src/features/sessions/ChangesPanel.tsx`
- Modify: `web/src/api/types.ts`（`ChangeEntry`）、`web/src/api/hooks.ts`（`useChanges`）、`web/src/features/sessions/SessionView.tsx`（在「修订历史」之后加入面板）
- Test: `web/src/features/sessions/SessionView.test.tsx`

**Interfaces:**
- Consumes: Task 4 的 `GET /runs/<id>/changes?who=&limit=200`。
- Produces: `ChangesPanel({ run })`：折叠面板「变更记录」，展开后加载；筛选 `全部 / 人 / 系统`（`NativeSelect id="changes-who"`）；每行 `data-change={id}`：时间（`formatTime`）、操作者（用户：`用户名 · 控制台 | Coding Agent | API`；系统：`Engine | Worker | KiroCrew | 协调者 | 系统（旧记录）`）、动作（`labels.eventLabel` 已有的名称，未知种类显示原文）、字段变化（`字段：前 → 后`；`{chars}` 显示为「N 字符」；多行写入显示任务 / 执行 ID）、原因。

- [ ] **Step 1: 写失败测试** — 会话页展开「变更记录」后请求 `/v1/runs/run-a/changes?limit=200`，显示「alice · Coding Agent」「续作」「状态：stopped → ready」「需求：25 字符 → 31 字符」与 Engine 行；选择「人」后请求 `?who=user&limit=200`。
- [ ] **Step 2: 运行确认失败** — `npm --prefix web test -- src/features/sessions`。
- [ ] **Step 3: 实现** — 沿用 `RevisionsPanel` 的折叠与加载样式；`useChanges(id, who, enabled)` 用 `useQuery(["changes", id, who], …, { enabled, placeholderData: (p) => p })`；字段名中文映射：`status 状态`、`input 需求`、`desired 期望`、`lifecycle 生命周期`、`worker_id Worker`、`native_parent 原生会话`、`workspace 工作区`、`version 版本`、`error 错误`、`spec 任务配置`、`current_attempt 当前执行`、`native_id 原生任务`、`result 结果`、`evidence 证据`、`snapshot 配置`，其余原文。
- [ ] **Step 4: 运行通过** — `npm --prefix web test`、`npm --prefix web run build`。
- [ ] **Step 5: 提交** — `feat(console): 会话页「变更记录」——按人 / 系统筛选，显示操作者、客户端、字段前后值与原因`

### Task 6: 瞬时失败自动重试与认证失败归类

**Files:**
- Modify: `octopus/engine.py`（执行结果判定）
- Test: `tests/test_runtime.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `journal` 写入方式。
- Produces:
  - `engine.transient(native, text) -> "runtime_cancelled" | "result_missing" | None`。
  - 执行结束且会话期望状态为 active：`runtime_cancelled`，或 `result_missing` 且没有有效的 pass outcome → 若该任务上一次 Attempt 不是瞬时失败：本 Attempt 记 `status='blocked'`、`evidence.transient=<类别>`，任务回到 `pending`、`current_attempt=NULL`，记 `attempt.retry`（ENGINE，`reason`），会话 `ready`，不调用 `recovery.automatic`。
  - `result_missing` 且执行契约 ≥ 2、交接存在、outcome 有效且为 pass：照常验收，`evidence.result_missing=True`。
  - 原生错误含 `session has expired` / `Session expired` / `not logged in`（不区分大小写）：`failure_type="auth"`。

- [ ] **Step 1: 写失败测试** — `tests/test_runtime.py`，沿用 `tests/test_teams_workers.py` 中 `DispatchRecheckTests` 的假 Worker 写法（`/prepare`、`/dispatch`、`/inspect` 可配置返回）：

```python
class TransientTests(TeamsWorkersCase):
    # self.native 控制 /inspect 返回的 native；self.outcome、self.handoff 控制结论与交接
    def test_runtime_cancellation_is_retried_once_without_a_recovery_cycle(self): ...
        # 第一次 native={"done": True, "error": "cancelled (stop_reason=cancelled): turn cancelled by the runtime"}
        # → 任务回到 pending、出现第 2 次 Attempt 并验收；recovery.used == 0；事件含 attempt.retry
    def test_a_second_transient_failure_goes_to_recovery(self): ...
        # 连续两次运行时取消 → 第 2 次后会话 blocked，自动纠错按策略启动（recovery.used == 1）
    def test_a_cancellation_after_stop_is_not_retried(self): ...
        # 取消返回前 desired 已为 stop → 不新建 Attempt
    def test_missing_result_with_a_passing_outcome_is_accepted(self): ...
        # native result "_No response._"，handoff 与 pass outcome 存在 → accepted，evidence.result_missing
    def test_missing_result_without_outcome_is_retried(self): ...
    def test_expired_login_is_an_auth_failure_without_automatic_recovery(self): ...
        # error "kiro_crew.acp.client.AcpError: Your session has expired" → failure_type auth，recovery.used == 0
```

  （每个测试写出完整断言：`[a["status"] for a in task["attempts"]]`、`detail["recovery"]["used"]`、`evidence["transient"]`。）
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_runtime`。
- [ ] **Step 3: 实现** — 在 `advance` 的 `if native.get("done"):` 分支：计算 `transient` 与 `previous`（同任务 `number-1` 的 Attempt 的 `evidence.transient`）；认证提示匹配时 `failure_type = "auth"`；`result_missing` 且 outcome 判定为 pass（复用现有 `recovery.outcome` 解析）时不设错误；需要重试时在写结果的事务内：`journal.change(attempts …, status="blocked", evidence=…)`、`journal.change(tasks …, status="pending", current_attempt=None)`、`journal.record(db, rid, "attempt.retry", journal.ENGINE, reason=…, attempt=attempt["id"], transient=transient)`，然后 `self.update(rid, "ready", error=None, reason=…)` 并 `return`（不进入 `recovery.automatic`）。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_runtime tests.test_mvp tests.test_recovery tests.test_teams_workers tests.test_journal`。
- [ ] **Step 5: 提交** — `feat(engine): 运行时取消与结果丢失自动重试一次、不占纠错轮次；登录过期归为认证失败`

### Task 7: kiro-cli 登录预检

**Files:**
- Modify: `octopus/worker.py`（`tool_auth`、`health`、`dispatch`）、`octopus/engine.py:_failed`、`web/src/features/settings/WorkersDialog.tsx`（健康信息显示）、`web/src/api/types.ts`（`Environment.health.tool_auth`）
- Test: `tests/test_runtime.py`、`web/src/features/settings/settings.test.tsx`

**Interfaces:**
- Produces:
  - `Worker.tool_auth(tool="kiro") -> "ok" | "expired" | "unknown"`：以配置的 `tools.kiro_cli`（缺省在 `PATH` 中查找 `kiro-cli`）执行 `whoami`，`HOME` 为 Worker 的 HOME，超时 5 s；退出码 0 为 `ok`，非 0 为 `expired`，找不到程序或超时为 `unknown`；缓存 60 s。
  - `health()` 增加 `"tool_auth": {"kiro": …}`；`HEALTH_FIELDS` 增加 `tool_auth`。
  - `dispatch()`：派发记录不存在、且后端为 Kiro（`spec.tool == "kiro"` 或带 `coordination` / `design`）时，`tool_auth() == "expired"` → `Error(503, "tool_auth_expired", "Worker 上 kiro-cli 登录已过期：请管理员执行 deploy.sh kiro-login")`，不写派发记录。
  - `Engine._failed`：`tool_auth_expired` 与 `worker_unavailable` 同样退避；Attempt 为 `dispatching` 时保持会话状态与 Attempt 不变（不进入 `dispatch_unknown`）。

- [ ] **Step 1: 写失败测试** — Worker 侧（参照 `tests/test_worker_approval.py` 构造 Worker，`patch("octopus.worker.subprocess.run")` 返回退出码 1）：Kiro 任务派发得 503 `tool_auth_expired` 且 `dispatch.json` 不存在；claude 任务照常；`health()["tool_auth"]["kiro"] == "expired"`；60 s 内只调用一次 `whoami`。Engine 侧：假 Worker 对 `/dispatch` 抛 `Error(503, "tool_auth_expired", …)` → 会话仍为 `ready`、`error` 为该消息、Attempt 仍为 `dispatching`；之后返回成功 → 正常执行。控制台：环境健康 `tool_auth.kiro = "expired"` 时显示「kiro-cli 登录已过期」。
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_runtime`；`npm --prefix web test -- src/features/settings`。
- [ ] **Step 3: 实现** — 按 Interfaces；`subprocess.run([binary, "whoami"], capture_output=True, timeout=5, env={**os.environ, "HOME": str(self.home)})`；`WorkersDialog` 的 `health()` 文案在可达时追加「 · kiro-cli 登录已过期」（`expired`）。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_runtime tests.test_worker_approval tests.test_mvp tests.test_native_unavailable`；`npm --prefix web test`。
- [ ] **Step 5: 提交** — `feat(worker): 派发前检查 kiro-cli 登录，过期时会话等待而不失败；执行环境显示登录状态`

### Task 8: 部署时 Gateway 未变不重启

**Files:**
- Modify: `deploy/install/al2023.sh`（systemd 一节）
- Test: `tests/test_deploy_assets.py`

**Interfaces:**
- Produces: `$BASE/gateway.fingerprint`；指纹为下列内容的 SHA-256：`readlink -f /opt/kirocrew/current`、`/opt/kirocrew-on/current/composition/*.py`、`octopus/{config,prompts,__init__}.py`、`$BOUNDARY_TOML`、`/etc/kirocrew-on/boundary-$BOUNDARY.env`（不存在时为空）、`/etc/systemd/system/octopus-gateway@.service`。

- [ ] **Step 1: 写失败测试** — 在 `tests/test_deploy_assets.py` 中提取安装脚本的 Gateway 片段（两个注释标记 `# gateway:begin` / `# gateway:end` 之间），在临时目录以假 `systemctl`、假 `readlink` 的 PATH 运行两次：第一次调用 `systemctl restart octopus-gateway@b1`；第二次（指纹未变、`is-active` 成功）不调用；修改边界配置后第三次调用；`is-active` 失败时调用。
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_deploy_assets`，预期找不到标记。
- [ ] **Step 3: 实现**

```bash
# gateway:begin
# The Gateway runs every native task: restart it only when what it loads changed (D34 §5.3), so a deploy of the
# platform alone does not interrupt work in flight. The Worker and API restart as before.
GATEWAY_STAMP="$BASE/gateway.fingerprint"
GATEWAY_PRINT="$( { readlink -f /opt/kirocrew/current
  cat /opt/kirocrew-on/current/composition/*.py /opt/kirocrew-on/current/octopus/config.py \
      /opt/kirocrew-on/current/octopus/prompts.py /opt/kirocrew-on/current/octopus/__init__.py \
      "$BOUNDARY_TOML" /etc/systemd/system/octopus-gateway@.service
  cat "/etc/kirocrew-on/boundary-$BOUNDARY.env" 2>/dev/null || true; } | sha256sum | cut -d' ' -f1)"
if [ "$(cat "$GATEWAY_STAMP" 2>/dev/null || true)" = "$GATEWAY_PRINT" ] && systemctl is-active --quiet "octopus-gateway@$BOUNDARY"; then
  log "Gateway 未变化，保持运行（进行中的原生任务不中断）"
else
  systemctl restart "octopus-gateway@$BOUNDARY"
  printf '%s\n' "$GATEWAY_PRINT" > "$GATEWAY_STAMP"
fi
# gateway:end
```

  替换原 `systemctl restart "octopus-gateway@$BOUNDARY"` 一行；`BOUNDARY_TOML` 在该位置已定义（同文件第 267 行使用）。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_deploy_assets tests.test_deploy_safety`。
- [ ] **Step 5: 提交** — `feat(deploy): Gateway 依赖未变时部署不重启，进行中的原生任务不中断`

### Task 9: 任务结构字段与规划校验

**Files:**
- Modify: `octopus/domain.py:steps`、`octopus/workflow_import.py:normalize`（放行结构字段）、`octopus/planning.py`（`EXAMPLE`、`plan_step` 提示、`expand` 校验）、`octopus/revisions.py`（快照与对比包含结构字段）、`octopus/schema.py`（`Step` 增加字段）
- Test: `tests/test_contract.py`（新建）、`tests/test_planning.py`、`tests/test_revisions.py`

**Interfaces:**
- Produces:
  - `domain.CONTRACT = ("kind", "deliverables", "acceptance", "verify", "depends_on", "paths")`、`domain.KINDS = ("implement", "review", "test", "deliver", "design")`。
  - `steps()`：接受上述可选字段，只在提供时出现在结果中；列表为字符串、去空白、去重、数量与长度按规格；`depends_on` 只能指向本列表中更早的任务。
  - `planning.expand`：每个任务须有 `kind` 与 ≥ 1 条 `acceptance`；`implement` / `test` 须有 ≥ 1 条 `verify`；否则 `Error(409, "plan_invalid", "任务「<名称>」缺少 …")`。
  - `revisions._task` 包含结构字段；`revisions.FIELDS` 之外新增 `CONTRACT_LABELS`，对比的 `fields` 包含结构字段的变化。

- [ ] **Step 1: 写失败测试** — `tests/test_contract.py`：

```python
import unittest
from octopus.domain import steps
from octopus.security import Error


class ContractTests(unittest.TestCase):
    base = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}

    def test_structured_fields_are_kept_only_when_given(self):
        self.assertNotIn("acceptance", steps([self.base])[0])
        step = steps([{**self.base, "kind": "implement", "acceptance": [" 登录成功 ", "登录成功"], "verify": ["npm test"],
                       "deliverables": ["src/login.ts"], "paths": ["src/**"]}])[0]
        self.assertEqual((step["acceptance"], step["verify"], step["kind"]), (["登录成功"], ["npm test"], "implement"))

    def test_depends_on_must_point_to_earlier_tasks(self):
        review = {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查"}
        steps([self.base, {**review, "depends_on": ["code"]}])
        for bad in (["review"], ["nope"]):
            with self.subTest(bad=bad), self.assertRaises(Error):
                steps([self.base, {**review, "depends_on": bad}])

    def test_bad_kinds_and_oversized_lists_are_refused(self):
        for extra in ({"kind": "deploy"}, {"acceptance": ["x"] * 21}, {"verify": "npm test"}, {"acceptance": ["x" * 501]}):
            with self.subTest(extra=extra), self.assertRaises(Error):
                steps([{**self.base, **extra}])
```

  `tests/test_planning.py` 增加：规划 JSON 中某任务缺 `acceptance` → `expand` 抛 `plan_invalid` 且消息含任务名；`implement` 缺 `verify` → 同上；完整结构 → 追加的任务 `spec` 含这些字段。`tests/test_revisions.py` 增加：续作修改 `acceptance` 后，对比的 `fields` 含 `acceptance` 前后值。
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_contract tests.test_planning tests.test_revisions`。
- [ ] **Step 3: 实现** — `steps()` 允许字段集合加入 `CONTRACT`；新增

```python
def strings(value, field, items, chars):
    require(isinstance(value, list) and len(value) <= items and all(isinstance(x, str) for x in value),
            f"{field} 须为不超过 {items} 项的字符串列表")
    result = list(dict.fromkeys(x.strip() for x in value if x.strip()))
    require(all(len(x) <= chars for x in result), f"{field} 每项不超过 {chars} 个字符")
    return result
```

  `kind` 校验属于 `KINDS`；`depends_on` 每项属于 `seen`（本步骤之前已校验的 ID）。`workflow_import.normalize` 结果中保留源对象的 `CONTRACT` 字段（交给 `steps()` 校验）。`planning.EXAMPLE["steps"][0]` 加入 `"kind": "implement", "deliverables": [...], "acceptance": ["可检查的验收项"], "verify": ["实际验证命令"], "depends_on": [], "paths": ["本任务可改动的路径"]`；`plan_step` 的「任务拆分」一条改为要求每个任务给出 kind、交付物、可检查的验收项、验证命令（implement / test）、依赖与可改动路径，审查 / 测试任务的验收项须来自补全需求中的验收标准。`expand` 在 `import_workflow` 之后逐个检查。`revisions._task` 增加 `**{k: spec[k] for k in CONTRACT if k in spec}`，`diff()` 的字段比较包含 `CONTRACT`。`schema.py` 的 `Step` 增加对应属性。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_contract tests.test_planning tests.test_revisions tests.test_workflow_import tests.test_mvp`。
- [ ] **Step 5: 提交** — `feat(planning): 任务结构字段（类别、交付物、验收项、验证命令、依赖、可改动路径）与 KiroCrew 规划校验`

### Task 10: 需求与任务契约传给每个任务；逐项验收结论

**Files:**
- Modify: `octopus/engine.py`（派发请求）、`octopus/worker.py:dispatch`（指令与上下文）、`octopus/recovery.py`（`quality_instructions`、`outcome`）、`web/src/features/sessions/TaskList.tsx`、`web/src/api/types.ts`
- Test: `tests/test_contract.py`、`web/src/features/sessions/SessionView.test.tsx`

**Interfaces:**
- Consumes: Task 9 的结构字段。
- Produces:
  - 派发请求：`spec` 只含 6 个旧字段；`contract`（任务的结构字段，有则发送）；`requirement = {"intent", "summary"}`（会话已应用 KiroCrew 规划时）；有 `depends_on` 时 `upstream` 只含所依赖任务。
  - Worker 指令：`## 需求与验收标准（KiroCrew 规划补全）`（`intent`、`summary`）与 `## 本任务契约`（类别、交付物、验收项、验证命令、可改动路径）；`context.json` 含 `requirement`、`contract`。
  - `quality_instructions(folder, contract=None)`：有验收项时要求 `outcome.json` 的 `acceptance` 逐项给出 `pass / fail / n/a` 与证据；验收标准之外、非必须修复的发现写入 `suggestions`。
  - `outcome()`：可选 `acceptance`（每项 `item` 字符串、`result ∈ {pass, fail, n/a}`、可选 `evidence` 字符串）与 `suggestions`（≤ 50 个字符串）；格式错误时 `outcome_invalid`。
  - 控制台执行证据显示「验收」逐项（✓ / ✗ / —）与「建议」。

- [ ] **Step 1: 写失败测试**

```python
class DispatchContractTests(TeamsWorkersCase):   # from tests.test_teams_workers
    def test_dispatch_spec_keeps_the_legacy_fields(self): ...
        # 带 acceptance / depends_on 的两个任务的会话：第 2 个任务的 /dispatch 请求 spec 的键集合为 6 个旧字段，
        # contract 含 acceptance，upstream 只含 depends_on 列出的任务；会话 snapshot 有 planning.summary 时 requirement 等于它
    def test_worker_instructions_include_requirement_and_contract(self): ...
        # 以 Worker.dispatch（假原生 spawn）验证 instructions.md 含两节标题与验收项文字，context.json 含 contract


class OutcomeTests(unittest.TestCase):
    def test_acceptance_results_and_suggestions_are_validated(self):
        from octopus.recovery import outcome
        good = outcome('{"decision":"pass","summary":"完成","acceptance":[{"item":"登录成功","result":"pass","evidence":"npm test 0"}],'
                       '"suggestions":["补 CHANGELOG"]}')
        self.assertEqual(good["suggestions"], ["补 CHANGELOG"])
        for bad in ('{"decision":"pass","summary":"x","acceptance":[{"item":"a","result":"ok"}]}',
                    '{"decision":"pass","summary":"x","suggestions":"补"}'):
            with self.subTest(bad=bad), self.assertRaises(Error):
                outcome(bad)
```

  控制台：执行证据中 `outcome.acceptance` 显示「验收 · 登录成功 ✓」与「建议 · 补 CHANGELOG」。
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_contract`；`npm --prefix web test -- src/features/sessions`。
- [ ] **Step 3: 实现** — Engine：`LEGACY = ("id", "name", "tool", "model", "effort", "prompt")`，`body["spec"] = {k: spec[k] for k in LEGACY if k in spec}`，`contract = {k: spec[k] for k in CONTRACT if k in spec}`；`snapshot["planning"]` 已应用时加 `requirement`；`depends_on` 过滤 `upstream`。Worker：在 `instructions` 的「## 任务」之前插入两节（只在提供时）；`quality_instructions(folder, body.get("contract"))`。`outcome()` 增加校验。`TaskList.tsx` 在 `交接结论` 之后渲染 `acceptance` 与 `suggestions`。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_contract tests.test_mvp tests.test_recovery tests.test_worker_approval tests.test_planning`；`npm --prefix web test`。
- [ ] **Step 5: 提交** — `feat(engine): 规划补全的需求与任务契约传给每个任务，结论逐项对照验收项并单列建议`

### Task 11: 续作只重跑受影响的任务（服务端）

**Files:**
- Modify: `octopus/domain.py`（`rerun_plan`、`operate("continue")`、`_view` 的 `kept`）、`octopus/schema.py`（`Continue.rerun`）、`octopus/mcp.py`（`octopus_continue` 增加 `rerun`）、`skills/kirocrew-on/SKILL.md`
- Test: `tests/test_contract.py`

**Interfaces:**
- Consumes: Task 9 的 `depends_on`、`kind`。
- Produces:
  - `domain.rerun_plan(ordered, index, rerun=None) -> (rerunning: list[str], kept: list[str], reasons: dict[str, str])`：`ordered` 为按序的任务行（含 `spec`、`status`）；`rerun=None` 时下游全部重跑；否则所选任务、`rerun` 所列任务、`depends_on` 含重跑任务者、`kind ∈ {review, test}` 者、未验收者重跑；`reasons` 说明被补全的原因（`depends` / `review` / `pending`）。
  - `continue`：`rerun` 须为字符串列表且只含所选任务之后的任务（否则 400「rerun 只能列出所选任务之后的任务」）；只重置 `rerunning` 中的任务；修订内容增加 `rerun`、`kept`；摘要在有沿用时为「续作「x」：重跑 a、b，沿用 c」。
  - `_view`（详情）增加 `kept: {"version", "tasks": [...]}`（最近一次 `continued` 修订的 `kept`，仅列出仍为已验收的任务），无则省略。

- [ ] **Step 1: 写失败测试**

```python
class RerunTests(TeamsWorkersCase):
    flow = [{"id": "design", "name": "设计", "tool": "kiro", "prompt": "设计", "kind": "design", "acceptance": ["x"]},
            {"id": "backend", "name": "后端", "tool": "claude", "prompt": "后端", "kind": "implement",
             "acceptance": ["x"], "verify": ["go test"], "depends_on": ["design"]},
            {"id": "frontend", "name": "前端", "tool": "claude", "prompt": "前端", "kind": "implement",
             "acceptance": ["x"], "verify": ["npm test"], "depends_on": ["design"]},
            {"id": "check", "name": "核对", "tool": "codex", "prompt": "核对", "kind": "test",
             "acceptance": ["x"], "verify": ["npm test"], "depends_on": ["backend", "frontend"]}]

    def settled(self):   # a completed run of the four tasks
        run = self.service.start(self.c, {"request_id": "start-0000000001", "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": False, "max_cycles": 2}, "steps": self.flow})
        with self.db.transaction() as db:   # fixture: every task accepted, run completed
            db.execute("UPDATE tasks SET status='accepted' WHERE run_id=?", (run["id"],))
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (run["id"],))
        return run["id"]

    def test_only_the_chosen_and_required_tasks_rerun(self):
        rid = self.settled()
        self.service.operate(self.c, rid, "continue", {"request_id": "continue-000001", "expected_version": 1,
                                                        "task_id": "frontend", "rerun": []})
        detail = self.service.run(self.c, rid)
        self.assertEqual({t["id"]: t["status"] for t in detail["tasks"]},
                         {"design": "accepted", "backend": "accepted", "frontend": "pending", "check": "pending"})
        self.assertEqual(detail["kept"], {"version": 2, "tasks": []})   # check is a test task after a rerun task
        revision = self.service.revisions(self.c, rid)[-1]
        self.assertEqual((revision["content"]["rerun"], revision["content"]["kept"]), (["frontend", "check"], []))

    def test_dependents_rerun_and_others_are_kept(self):
        rid = self.settled()
        self.service.operate(self.c, rid, "continue", {"request_id": "continue-000002", "expected_version": 1,
                                                        "task_id": "design", "rerun": ["frontend"]})
        detail = self.service.run(self.c, rid)
        # backend depends on design → reruns although not chosen
        self.assertEqual([t["status"] for t in detail["tasks"]], ["pending", "pending", "pending", "pending"])

    def test_omitted_rerun_keeps_todays_behaviour(self): ...   # 不传 rerun：所选任务及其后全部 pending，kept 省略

    def test_rerun_lists_only_later_tasks(self):
        rid = self.settled()
        with self.assertRaises(Error) as caught:
            self.service.operate(self.c, rid, "continue", {"request_id": "continue-000003", "expected_version": 1,
                                                            "task_id": "frontend", "rerun": ["design"]})
        self.assertEqual(caught.exception.status, 400)
```

  另加：没有结构字段的旧任务，`rerun=["c"]` 时只重跑所选与 `c`（旧任务没有 `kind`，不补全）；修订摘要含「沿用」。
- [ ] **Step 2: 运行确认失败** — `$PY -m unittest tests.test_contract.RerunTests`。
- [ ] **Step 3: 实现** — 按 Interfaces；`operate` 中原 `for row in ordered[index:]` 循环改为只遍历 `rerunning`；`invalidated` 改为 `rerunning`；命令事件 `event` 带 `rerun`、`kept`。`schema.py` 的 `Continue` 增加 `"rerun": {"type": "array", "items": text}`；`mcp.py` 的 `octopus_continue` 输入增加 `rerun` 并在描述中说明「只列出受影响的后续任务；依赖它们的任务与审查 / 测试任务会自动一并重跑」；`SKILL.md` 续作一节加入同样说明并要求在回复中告诉用户沿用了哪些任务。
- [ ] **Step 4: 运行通过** — `$PY -m unittest tests.test_contract tests.test_revisions tests.test_mvp tests.test_mcp_status tests.test_access tests.test_teams_workers`。
- [ ] **Step 5: 提交** — `feat(sessions): 续作只重跑所选与必须重跑的任务（依赖与审查 / 测试规则补全），其余沿用上一版结果`

### Task 12: 控制台续作选择与「沿用」标注

**Files:**
- Modify: `web/src/features/sessions/dialogs.tsx:ContinueDialog`、`web/src/lib/session.ts`（`rerunPlan`）、`web/src/features/sessions/TaskList.tsx`（「沿用 v<N> 结果」）、`web/src/features/sessions/RevisionsPanel.tsx`（结构字段名称）、`web/src/api/types.ts`（`Run.kept`、`TaskSpec` 结构字段）
- Test: `web/src/features/sessions/SessionView.test.tsx`、`web/src/lib/session.test.ts`（如无则新建）

**Interfaces:**
- Consumes: Task 11 的 `rerun` 与 `kept`。
- Produces: `rerunPlan(run, taskId, chosen: Set<string>) -> { rerun: Set<string>; forced: Map<string, string> }`，规则与服务端相同，`forced` 的说明为「依赖重跑的任务」「审查 / 测试在改动后必须复验」「尚未验收」；对话框「同时重跑的后续任务」列出下游任务的勾选框（`data-rerun={id}`，默认全选），`forced` 中的任务勾选且停用并显示说明；提交时发送 `rerun: [...]`（所勾选者）。

- [ ] **Step 1: 写失败测试** — `rerunPlan` 的单元测试（依赖、审查、未验收三条规则）；对话框：取消勾选「后端」后提交，请求体 `rerun` 为 `["check"]` 之类；「核对」（test）勾选且停用，显示「审查 / 测试在改动后必须复验」；`run.kept` 含 `backend` 时任务列表显示「沿用 v3 结果」。
- [ ] **Step 2: 运行确认失败** — `npm --prefix web test -- src/features/sessions src/lib`。
- [ ] **Step 3: 实现** — 按 Interfaces；`RevisionsPanel` 的 `FIELD` 增加 `kind 类别`、`deliverables 交付物`、`acceptance 验收项`、`verify 验证命令`、`depends_on 依赖`、`paths 可改动路径`，列表值以「；」连接。
- [ ] **Step 4: 运行通过** — `npm --prefix web test`、`npm --prefix web run build`。
- [ ] **Step 5: 提交** — `feat(console): 续作对话框选择要重跑的后续任务（规则补全不可取消），任务列表标注沿用的结果`

### Task 13: 文档、回归与 EC2 验收

**Files:**
- Modify: `docs/MVP-RELEASE.md`（D34；§14.8 验收记录）、`docs/OPERATIONS.md`（变更记录查询、kiro-cli 登录状态、部署不重启 Gateway）、`docs/ARCHITECTURE.md`（§3 变更记录一行）、`docs/BACKLOG.md`（D34 后续项）、`README.md`（能力清单一行）

- [ ] **Step 1:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 2:** 只读确认平台与 `w1` 没有进行中的会话后：`deploy.sh push`、`deploy.sh worker-host push w1`（Worker 端有改动：登录预检、指令）；记录 Gateway 是否重启（预期：KiroCrew 与 Gateway 依赖若未变则不重启——本轮改了 `prompts.py` 时会重启一次，记录原因）。
- [ ] **Step 3:** 验收（结果记入 §14.8）：
  1. `GET /v1/runs/<现有会话>/changes` 返回带操作者的新记录与旧记录；`who=user` / `system` 筛选正确；导出含 `changes`。
  2. 两台主机 `health.tool_auth.kiro` 为 `ok`，「执行环境」对话框显示正常。
  3. 再次 `deploy.sh push`（无代码变化）：日志显示「Gateway 未变化，保持运行」，Gateway 进程 PID 不变。
  4. **需要执行任务（先征得负责人同意）**：以测试会话（两个结构化任务：实现 + 测试）验证规划后的派发请求含 `requirement` / `contract`、结论含逐项验收；续作时 `rerun` 只选实现任务 → 测试任务被补全重跑、修订显示重跑与沿用；变更记录显示每次写入的操作者与前后值。
- [ ] **Step 4:** 向负责人报告；确认后推送。
