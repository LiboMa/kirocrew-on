# Team 执行环境 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建 Team 时必须分配执行环境（共享 / 专属）；先上线 1 台独立 Worker 主机作为空闲专属环境，经私有 CA 的 TLS 由平台统一调度，验收后扩到 3 台。

**Architecture:** 平台层在 `workers` 上加 `scope` 与 `worker_spaces`，一个函数 `space_workers()` 统一回答「某空间能用哪些 Worker」，提交、校验、提示、控制台都用它。主机层新增 Worker 主机 CloudFormation 栈与 `al2023.sh` 的 Worker 主机模式；nginx 终结 TLS，平台用 `worker_ca_file` 只信任自己的 CA。

**Tech Stack:** Python 3 标准库（+ PyYAML）、SQLite、React + TypeScript + Vitest、Bash、CloudFormation、nginx、openssl CLI。

**Spec:** `docs/superpowers/specs/2026-10-11-team-execution-environments-design.md`

## Global Constraints

- 不新增 Python 依赖；证书操作使用主机上的 `openssl` 命令。
- 面向用户的文字使用中文，风格与现有控制台一致。
- 不修改平台主机安全组 `HostSecurityGroup` 的任何属性（修改会替换主机）；平台栈只增加 Outputs。
- 非本机 Worker 必须 HTTPS（`octopus/transport.py:33-34`）保持不变。
- 共享环境说明必须包含：「环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围」。
- 提交规则（负责人 CLAUDE.md）：每个阶段的 E2E 完成并经负责人确认后才提交；任务之间不单独提交。
- 在独立 worktree（分支 `feat/team-environments`，基于 `feat/m1-runtime-foundation`）开发，避免与并行会话改同一工作区；合并前与其协调 `octopus/server.py`、`octopus/mcp.py`。
- Python 测试：`.venv/bin/python -m unittest <模块>`；完整回归：`tools/regress.sh`。

## Review Focus

1. EC2 上已有 4 个没有环境的 Team，以及在 `waiting_worker` 的 run-b74e9f0d：迁移后不能报错；这些 Team 分配共享环境后，等待中的会话可用 `assign-worker` 指定共享 Worker 继续（A2 测试 `test_legacy_waiting_run_can_use_shared_worker`）。
2. 旧版 MCP 客户端仍按 `owner_id` 自动选择并传入共享 Worker 的 ID：服务端必须按 `worker_spaces` 接受（A2 测试 `test_shared_worker_id_from_old_client_is_accepted`）。
3. 把 personal Worker 改为共享时，正在运行的会话不受影响（Worker ID 不变、`runs.worker_id` 不改）（A4 测试 `test_share_requires_confirmation_and_keeps_existing_runs`）。
4. 共享 Worker 被停用：它服务的所有空间都视为没有可用 Worker，提示与拒绝一致（A2 测试 `test_disabled_shared_worker_counts_as_none`）。
5. Worker 主机替换后私网 IP 变化，证书 SAN 不匹配：平台访问失败时给出可读错误而不是挂起（B1 测试 `test_untrusted_or_mismatched_certificate_is_refused`）。

---

## 阶段 A：平台执行环境

### Task 1 (A1): 迁移 2（`scope` 与 `worker_spaces`）

**Files:**
- Modify: `octopus/migrations.py`（`MIGRATIONS` 末尾）
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `workers.scope TEXT NOT NULL DEFAULT 'dedicated'`（`dedicated|shared|spare`）；表 `worker_spaces(worker_id, team_id, user_id)`；索引 `worker_spaces_team`（`team_id` 部分唯一）。

- [ ] **Step 1: 写失败测试**

```python
    def test_environments_migration_keeps_workers_dedicated(self):
        self.legacy_database()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w1','personal','u1','{}')")
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result["to"], 2)
        self.assertIsNotNone(result["backup"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT scope FROM workers WHERE id='w1'").fetchone()[0], "dedicated")
            db.execute("INSERT INTO teams VALUES('t1','A',0)")
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('w1','t1')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO worker_spaces(worker_id,team_id,user_id) VALUES('w1','t1','u1')")
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('w2','other','u1','{}')")
            with self.assertRaises(sqlite3.IntegrityError):  # a Team has one environment
                db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('w2','t1')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE workers SET scope='bogus' WHERE id='w1'")
```

同时把 `test_fresh_database_gets_baseline_without_backup` 的期望改为 `{"from": 0, "to": 2, "backup": None}`，`self.version()` 期望 2。

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_migrations`，预期 `to` 为 1、`scope` 列不存在。

- [ ] **Step 3: 实现**

```python
ENVIRONMENTS = """
ALTER TABLE workers ADD COLUMN scope TEXT NOT NULL DEFAULT 'dedicated'
 CHECK(scope IN ('dedicated','shared','spare'));
CREATE TABLE IF NOT EXISTS worker_spaces(
 worker_id TEXT NOT NULL REFERENCES workers(id), team_id TEXT REFERENCES teams(id), user_id TEXT REFERENCES users(id),
 CHECK((team_id IS NULL) != (user_id IS NULL)), UNIQUE(worker_id, team_id), UNIQUE(worker_id, user_id));
CREATE UNIQUE INDEX IF NOT EXISTS worker_spaces_team ON worker_spaces(team_id) WHERE team_id IS NOT NULL;
"""

MIGRATIONS = [(1, "baseline", BASELINE), (2, "environments", ENVIRONMENTS)]
```

- [ ] **Step 4: 运行通过** — 同上命令全部 OK；再跑 `tests.test_mvp` 确认未受影响。

### Task 2 (A2): 归属规则、服务端自动选择、未分配 Team 拒绝

**Files:**
- Modify: `octopus/domain.py`（新函数 `space_workers`；`Service.start` 开头；`_worker` `:337-341`；`_space` `:406-414`）
- Test: `tests/test_mvp.py`（新类 `EnvironmentTests(MVPTests)`，复用其 `setUp` 的用户 alice（管理员）/ bob / eve）

**Interfaces:**
- Produces: `space_workers(db, user_id: str, team_id: str | None) -> list[sqlite3.Row]` — 已启用、对该空间可用的 Worker（`dedicated` 按 `owner_id`/`team_id`；`shared` 按 `worker_spaces`；`spare` 永不可用），按 `name` 排序。
- Produces: `Error(409, "team_without_worker", "Team「<名>」尚未分配执行环境，请联系平台管理员分配")`。
- Produces（测试夹具，A3/A4 复用）：`EnvironmentTests.shared(wid="shared") -> str`、`EnvironmentTests.shared_team(wid, name="共享 Team") -> str`。

- [ ] **Step 1: 写失败测试**

```python
class EnvironmentTests(MVPTests):
    def shared(self, wid="shared"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?, 'shared')",
                       (wid, wid, self.a["id"], dumps({"capabilities": {}})))
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, self.a["id"]))
        return wid

    def shared_team(self, wid, name="共享 Team"):
        tid = "team-" + name.encode().hex()[:12]
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES(?,?,0)", (tid, name))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.a["id"]))
            db.execute("INSERT INTO members VALUES(?,?,'member')", (tid, self.b["id"]))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (wid, tid))
        return tid

    def test_space_workers_cover_dedicated_shared_and_spare(self):
        from octopus.domain import space_workers
        wid = self.shared()
        tid = self.shared_team(wid)
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('sp','spare',?,'{}','spare')", (self.a["id"],))
        with self.db.read() as db:
            self.assertEqual([r["id"] for r in space_workers(db, self.a["id"], tid)], [wid])
            self.assertEqual([r["id"] for r in space_workers(db, self.a["id"], None)], [wid])
            self.assertEqual(space_workers(db, self.e["id"], None), [])

    def test_server_picks_the_only_usable_worker(self):
        wid = self.shared()
        with self.db.transaction() as db:
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES(?,?)", (wid, self.b["id"]))
        run = self.service.run(self.b, self.start(self.b, workflow_id=None, template_id="proof")["id"])
        self.assertEqual((run["worker_id"], run["status"]), (wid, "preparing"))

    def test_team_without_environment_is_refused(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams VALUES('t-legacy','旧 Team',0)")
            db.execute("INSERT INTO members VALUES('t-legacy',?,'owner')", (self.a["id"],))
        with self.assertRaises(Error) as caught:
            self.start(team_id="t-legacy")
        self.assertEqual(caught.exception.code, "team_without_worker")

    def test_shared_worker_id_from_old_client_is_accepted(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        self.assertEqual(self.service.run(self.a, self.start(team_id=tid, worker_id=wid)["id"])["worker_id"], wid)

    def test_disabled_shared_worker_counts_as_none(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id=?", (wid,))
        with self.assertRaises(Error) as caught:
            self.start(team_id=tid)
        self.assertEqual(caught.exception.code, "team_without_worker")

    def test_legacy_waiting_run_can_use_shared_worker(self):
        wid = self.shared()
        tid = self.shared_team(wid)
        started = self.start(team_id=tid)
        with self.db.transaction() as db:   # as left by the platform before environments existed
            db.execute("UPDATE runs SET worker_id=NULL, status='waiting_worker' WHERE id=?", (started["id"],))
        self.service.operate(self.a, started["id"], "assign-worker",
                             {"request_id": "assign-00001", "expected_version": 1, "worker_id": wid})
        self.assertEqual(self.service.run(self.a, started["id"])["status"], "preparing")
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_mvp -k Environment`，预期 `space_workers` 不存在、Team 提交未被拒绝。

- [ ] **Step 3: 实现**

```python
def space_workers(db, user, team):
    """Enabled Workers usable in a space: dedicated by owner/team, shared through worker_spaces; spare never."""
    if team:
        query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.team_id=?) OR "
                 "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)))")
    else:
        query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.owner_id=?) OR "
                 "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.user_id=?)))")
    return db.execute(query + " ORDER BY w.name", (team or user, team or user)).fetchall()
```

- `_worker(db, user, worker, team)`：`row = next((r for r in space_workers(db, user, team) if r["id"] == worker), None)`；`require(row is not None, "Worker 不属于本次执行空间", 403, "forbidden")`；返回 `dict(row)`。
- `Service.start` 开头（`planning` 判断之前）：`body.get("worker_id")` 为空时，在 `self.db.read()` 中取 `space_workers(db, actor["id"], body.get("team_id") or None)`；恰好一个则 `body = {**body, "worker_id": rows[0]["id"]}`；`team_id` 存在且结果为空时，读取 Team 名称并 `raise Error(409, "team_without_worker", f"Team「{name}」尚未分配执行环境，请联系平台管理员分配")`。
- `_space`：`workers` 改为 `len(space_workers(db, run["owner_id"], run["team_id"]))`。

- [ ] **Step 4: 运行通过** — `-k Environment` OK；`tests.test_mvp tests.test_recovery` 中依赖旧版 `create_team` 的 Team 测试在 A3 更新夹具前可能失败，记下失败列表，A3 处理。

### Task 3 (A3): Team 与用户（管理员创建、必须带环境）

**Files:**
- Modify: `octopus/domain.py`（新函数 `assign_spare`；`create_team` `:99-107`；`teams` `:86-97`）、`octopus/server.py`（`/v1/users` POST `:218-224`）
- Modify: `tests/test_mvp.py`（`MVPTests.team()` 夹具）、`tests/test_crew_entry.py`、`tests/mvp_browser_fixture.py`（凡调用 `create_team` 处）
- Test: `tests/test_mvp.py`（`EnvironmentTests`）

**Interfaces:**
- Consumes: `space_workers`、`EnvironmentTests.shared`（A2）。
- Produces: `assign_spare(db, worker_id: str, team_id: str) -> None` — `spare` → `dedicated` 并写入 `team_id`、清空 `owner_id`；不满足时 `Error(409, "worker_unavailable")`。
- Produces: `create_team(actor, {"name", "owner", "worker_id"})` → `{"id","name","role","environment"}`；`teams(actor)` 每项加 `environment: {"worker_id","name","scope"} | None`，管理员得到全部 Team（非成员时 `role` 为 `None`）；`POST /v1/users` 接受可选 `worker_id`（须为 `shared`）。

- [ ] **Step 1: 写失败测试**

```python
    def test_only_admins_create_teams_and_an_environment_is_required(self):
        wid = self.shared()
        with self.assertRaises(Error):
            self.service.create_team(self.b, {"name": "B", "owner": "bob", "worker_id": wid})
        with self.assertRaises(Error):
            self.service.create_team(self.a, {"name": "B", "owner": "bob"})
        t = self.service.create_team(self.a, {"name": "B", "owner": "bob", "worker_id": wid})
        self.assertEqual(t["environment"]["scope"], "shared")
        self.assertEqual({x["id"]: x["role"] for x in self.service.teams(self.b)}[t["id"]], "owner")
        self.assertIn(t["id"], {x["id"] for x in self.service.teams(self.a)})   # the admin sees every Team

    def test_spare_is_assigned_once(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('sp','team-host-1',?,?,'spare')",
                       (self.a["id"], dumps({"capabilities": {"isolation": "dedicated"}})))
        t1 = self.service.create_team(self.a, {"name": "T1", "owner": "alice", "worker_id": "sp"})
        with self.db.read() as db:
            row = db.execute("SELECT scope,team_id,owner_id FROM workers WHERE id='sp'").fetchone()
        self.assertEqual(tuple(row), ("dedicated", t1["id"], None))
        with self.assertRaises(Error) as caught:
            self.service.create_team(self.a, {"name": "T2", "owner": "alice", "worker_id": "sp"})
        self.assertEqual(caught.exception.code, "worker_unavailable")

    def test_admin_creates_a_user_on_the_shared_environment(self):
        from octopus.domain import space_workers
        wid = self.shared()
        with self.http() as (_, call):
            status, user = call("POST", "/v1/users", "alice",
                                {"username": "carol", "password": "Test-password-123!", "worker_id": wid})
        self.assertEqual(status, 201)
        with self.db.read() as db:
            self.assertEqual([r["id"] for r in space_workers(db, user["id"], None)], [wid])
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**

```python
def assign_spare(db, worker, team):
    changed = db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL "
                         "WHERE id=? AND scope='spare' AND enabled=1", (team, worker)).rowcount
    require(changed == 1, "该执行环境不可用或已分配", 409, "worker_unavailable")
```

- `create_team`：`require(actor["admin"], "只有平台管理员可以创建 Team", 403, "forbidden")`；`owner` 为有效的活动用户名（否则 404）；`worker_id` 必填（否则 400 `worker_required`）；一个事务内：建 Team → `members` 写入 owner → Worker 为 `shared` 时插入 `worker_spaces(team)`，为 `spare` 时 `assign_spare`，其他情况 409 `worker_unavailable`。返回值的 `role` 为操作者在该 Team 的角色（不是成员时为 `None`）。
- `teams(actor)`：管理员 `SELECT t.*, m.role FROM teams t LEFT JOIN members m ON m.team_id=t.id AND m.user_id=?`；非管理员保持现有 JOIN；每项 `environment` 取 `space_workers(db, actor["id"], t["id"])` 的第一项（`{"worker_id","name","scope"}`），没有为 `None`。
- `/v1/users` POST：`create_user` 之后，`worker_id` 存在时校验其 `scope='shared'` 并插入 `worker_spaces(user)`，同一事务。
- 夹具：`MVPTests.team()` 改为先插入一个共享 Worker（同 `EnvironmentTests.shared` 的 SQL），再 `create_team(self.a, {"name": "研发 Team", "owner": "alice", "worker_id": ...})` 并 `membership` 加入 bob；`tests/test_crew_entry.py`、`tests/mvp_browser_fixture.py` 中的 `create_team` 调用同样补上 `owner` 与共享 Worker。

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` 全部 OK。

### Task 4 (A4): Worker 管理接口

**Files:**
- Modify: `octopus/server.py`（`/v1/workers` GET 与 POST `:296-345`；在现有 `enabled`/`capabilities` 子路由旁新增 `share`、`spaces`）
- Modify: `octopus/bootstrap.py`（`register_boundary_worker(..., spare=False)`）、`octopus/__main__.py`（`bootstrap-worker --spare`）
- Test: `tests/test_mvp.py`（`EnvironmentTests`，HTTP 层用 `self.http()`）

**Interfaces:**
- Consumes: `space_workers`（A2）、`create_team` 新签名（A3）、`MVPTests.worker_run()`（现有：插入 alice 的 `dedicated` Worker `worker` 并启动一个会话）。
- Produces: `POST /v1/workers/<id>/share {"confirm": true}`；`POST|DELETE /v1/workers/<id>/spaces {"team_id"}|{"user_id"}`；`POST /v1/workers {"name","spare":true,"connection"}`；`GET /v1/workers` 每项增加 `scope` 与 `spaces`（`[{"team_id": ...}|{"user_id": ...}]`；非管理员只包含自己及自己所在 Team 的空间）。

- [ ] **Step 1: 写失败测试**

```python
    def test_share_requires_confirmation_and_keeps_existing_runs(self):
        run = self.worker_run()
        with self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers/worker/share", "alice", {})[0], 400)
            self.assertEqual(call("POST", "/v1/workers/worker/share", "bob", {"confirm": True})[0], 403)
            self.assertEqual(call("POST", "/v1/workers/worker/share", "alice", {"confirm": True})[0], 200)
            listed = {w["id"]: w for w in call("GET", "/v1/workers", "alice")[1]}
        self.assertEqual(listed["worker"]["scope"], "shared")
        self.assertIn({"user_id": self.a["id"]}, listed["worker"]["spaces"])
        self.assertEqual(self.service.run(self.a, run["id"])["worker_id"], "worker")

    def test_spaces_add_and_remove_with_one_environment_per_team(self):
        wid = self.shared()
        t = self.service.create_team(self.a, {"name": "T1", "owner": "bob", "worker_id": wid})
        with self.http() as (_, call):
            self.assertEqual(call("POST", f"/v1/workers/{wid}/spaces", "alice", {"user_id": self.e["id"]})[0], 200)
            self.assertEqual(call("POST", f"/v1/workers/{wid}/spaces", "alice", {"team_id": t["id"]})[0], 409)
            self.assertEqual(call("DELETE", f"/v1/workers/{wid}/spaces", "alice", {"team_id": t["id"]})[0], 200)
            self.assertEqual(call("GET", "/v1/workers", "bob")[1], [])
            self.assertEqual([w["id"] for w in call("GET", "/v1/workers", "eve")[1]], [wid])

    def test_spare_registration_requires_a_dedicated_unseen_boundary(self):
        health = {"isolation": "dedicated", "boundary": "b-1", "host_principal": "hp-1"}
        with mock.patch("octopus.server.validate_worker", return_value=health), self.http() as (_, call):
            body = {"name": "team-host-1", "spare": True, "connection": {"url": "https://10.0.0.9:8943", "token": "t" * 40}}
            self.assertEqual(call("POST", "/v1/workers", "alice", body)[0], 201)
            self.assertEqual(call("POST", "/v1/workers", "alice", body)[0], 409)          # same boundary again
        with mock.patch("octopus.server.validate_worker", return_value={**health, "boundary": "b-2", "isolation": "trusted-local"}), \
                self.http() as (_, call):
            self.assertEqual(call("POST", "/v1/workers", "alice", {**body, "name": "x"})[0], 409)
```

（`mock` 即 `from unittest import mock`；`validate_worker` 若以 `from .transport import validate_worker` 导入到 `server`，按该名称打补丁。）

- [ ] **Step 2: 运行确认失败**（子路由 404、字段缺失）。
- [ ] **Step 3: 实现**
- `share`：管理员；`body.get("confirm") is True` 否则 400 `confirmation_required`；仅 `scope='dedicated'`（否则 409）；一个事务：把原 `team_id`（若有）或 `owner_id` 写入 `worker_spaces`；`UPDATE workers SET scope='shared', team_id=NULL, owner_id=?`（操作的管理员）。
- `spaces`：管理员；Worker 必须 `shared`；`team_id`：Team 存在且 `space_workers(db, actor["id"], team)` 为空，否则 409 `team_has_environment`；`user_id`：用户存在且 `active=1`；`DELETE` 删除对应行，不存在时 404。
- `POST /v1/workers`：`spare` 为真时不得同时给 `owner_id`/`team_id`；要求 `health["isolation"] == "dedicated"`（否则 409 `isolation_required`）；边界唯一性检查遍历**全部** Worker（含停用与空闲）；插入 `scope='spare'`、`owner_id=actor["id"]`。非 `spare` 的现有分支保持不变。
- `GET /v1/workers`：可见性 = 管理员全部；否则 `dedicated` 按现有规则，`shared` 当 `worker_spaces` 中有该用户或其所在 Team；每项输出加 `scope` 与 `spaces`。
- `bootstrap-worker --spare`：`register_boundary_worker(..., spare=True)` 发送 `{"name", "spare": True, "connection"}`；已登记判断改为同名且 `scope` 为 `spare` 或 `dedicated`。

- [ ] **Step 4: 运行通过** — `tests.test_mvp` 全部 OK。

### Task 5 (A5): 控制台

**Files:**
- Modify: `web/src/api/types.ts`（`Worker.scope`、`Worker.spaces`、`Team.environment`）
- Modify: `web/src/features/console/context.tsx:52-54`（`spaceWorkers`）
- Modify: `web/src/features/settings/WorkersDialog.tsx`、`web/src/features/settings/TeamDialog.tsx`
- Modify: `web/src/features/launch/steps.tsx`（空间选择处的未分配提示与禁止提交）
- Test: `web/src/features/settings/*.test.tsx`（新建 `TeamDialog.test.tsx`、`WorkersDialog.test.tsx`）、`web/src/features/launch/LaunchWizard.test.tsx`、`web/src/features/console/context.test.ts`（若无则新建）

**Interfaces:**
- Consumes: A3（Team、用户）与 A4（Worker）的接口与字段。
- Produces: `spaceWorkers(workers, space, me)` 对 `scope === "shared"` 按 `spaces` 判断；`scope === "spare"` 永不返回。

- [ ] **Step 1: 写失败测试**（Vitest，沿用 `@/test/api` 的 `mockApi`）
  - `spaceWorkers`：共享 Worker 的 `spaces` 含 `{team_id: "t1"}` 时在 `t1` 可用、在个人空间不可用；含 `{user_id: me.id}` 时个人空间可用；`spare` 永不可用。
  - `TeamDialog`：非管理员看不到「新建 Team」；管理员表单的「执行环境」为必选，下拉包含共享与空闲环境；没有可选环境时提交按钮禁用并显示「先在主机上开通执行环境，或把一个 Worker 设为共享环境」；提交发送 `{name, owner, worker_id}`；未分配环境的 Team 显示「分配执行环境」并发送 `POST /workers/<id>/spaces {team_id}`。
  - `WorkersDialog`：管理员对 `dedicated` Worker 有「设为共享环境」，确认框文字包含 Global Constraints 中的共享说明，确认后发送 `{confirm: true}`；`shared` 显示「共享环境」标记及服务的空间数；`spare` 显示「空闲」。
  - 发起向导：所选 Team `environment === null` 时显示「该 Team 尚未分配执行环境，请联系平台管理员」，提交按钮禁用。
- [ ] **Step 2: 运行确认失败** — `npm --prefix web test`。
- [ ] **Step 3: 实现**（按上述行为；表单控件沿用对话框内现有的 `Input`、`NativeSelect`、`Button`、`Alert`）。
- [ ] **Step 4: 运行通过** — `npm --prefix web test` 与 `npx --prefix web tsc -b` OK。

### Task 6 (A6): 文档、回归与本机 E2E

**Files:**
- Modify: `docs/WORKERS.md`（§3 归属规则表加入共享 / 空闲；§7 现状）、`docs/OPERATIONS.md`（「执行环境：设为共享、创建 Team、分配」）
- [ ] **Step 1:** 更新文档。
- [ ] **Step 2:** `tools/regress.sh` 全部通过。
- [ ] **Step 3: 本机 E2E**：临时平台（`init` + `serve`，同 `81394f9` 的验证方式）加一个本机 Worker（`tools/local_stack.sh` 的 Worker 或 dry 的 `/health` 替身仅用于登记）：管理员把 Worker 设为共享 → 新建 Team 必选环境 → 未分配 Team 被拒 → 成员在 Team 提交会话自动选用共享 Worker；浏览器核对控制台各入口。
- [ ] **Step 4:** 向负责人报告 E2E 结果，确认后提交阶段 A。

---

## 阶段 B：独立 Worker 主机

### Task 7 (B1): 平台信任私有 CA

**Files:**
- Modify: `octopus/config.py`（`PATHS` 增加 `worker_ca_file`）、`octopus/transport.py`（`worker_request`、`validate_worker`）、`octopus/__main__.py`（`serve` 时把设置传入）
- Test: `tests/test_transport_tls.py`（新建）

**Interfaces:**
- Produces: `transport.configure(ca_file: Path | None)`；`worker_request` 对 `https` 使用 `ssl.create_default_context(cafile=ca_file)`（未配置时为系统默认）。

- [ ] **Step 1: 写失败测试** — 测试内用 `openssl` 生成 CA 与 SAN 为 `127.0.0.1` 的服务端证书，`http.server` + `ssl` 起本机 HTTPS `/health`：
  - 配置该 CA 时 `worker_request(..., "/health")` 返回 JSON；
  - 未配置或配置另一个 CA 时抛 `Error(503, "worker_unavailable")`，消息包含「证书」；
  - 证书 SAN 为 `10.0.0.9` 而访问 `127.0.0.1` 时同样拒绝（`test_untrusted_or_mismatched_certificate_is_refused`）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `except ssl.SSLError` 先于 `OSError` 捕获，消息「Worker TLS 证书校验失败（未受信任的 CA 或地址不匹配）」。
- [ ] **Step 4: 运行通过**。

### Task 8 (B2): Worker 主机安装模式

**Files:**
- Modify: `deploy/install/al2023.sh`（`WORKER_HOST=1` 时跳过平台 API、管理员、平台配置、原生入口与 `bootstrap-worker`；边界 `worker_isolation = "dedicated"`；启用 `octopus-gateway@<b>`、`octopus-worker@<b>` 与 nginx）；平台模式下若无 `/etc/kirocrew-on/worker-ca.key` 则生成私有 CA（root 0600 / `worker-ca.pem` 0644）并在 `platform.toml` 写入 `worker_ca_file`
- Create: `deploy/nginx/kirocrew-worker.conf`（`listen 8943 ssl` → `proxy_pass http://127.0.0.1:__WORKER_PORT__`，证书 `/etc/kirocrew-on/worker-tls.{crt,key}`）
- Test: `tests/test_deploy_assets.py`

- [ ] **Step 1: 写失败测试** — 静态检查：`WORKER_HOST` 分支不出现 `octopus-api`、`bootstrap-worker`、`admin-password`；写入 `dedicated`；nginx 模板含 `ssl_certificate`、`127.0.0.1`；平台模式生成 CA 的 `openssl req -x509` 与 `chmod 0600`；`bash -n` 通过。
- [ ] **Step 2–4:** 实现并通过。

### Task 9 (B3): Worker 主机栈与 `deploy.sh worker-host`

**Files:**
- Create: `deploy/aws/kirocrew-on-worker.yaml`（参数：`VpcId`、`SubnetId`、`PlatformSecurityGroupId`、`ImageId`、`InstanceType`、`VolumeSizeGiB`、`ArtifactBucket`、`WorkerPort=8943`；资源：安全组（入站仅 `SourceSecurityGroupId: PlatformSecurityGroupId` 的 8943）、角色（SSM、Bedrock、读 `releases/*`）、实例（IMDSv2、加密 gp3、`DeleteOnTermination: false`））
- Modify: `deploy/aws/kirocrew-on-ec2.yaml`（只加 Outputs：`VpcId`、`SubnetId`、`HostSecurityGroupId`、`ImageId`）
- Modify: `deploy/aws/deploy.sh`（`worker-host up|push|tls|register|status <名称>`；`kiro-login --host <名称>`）
- Test: `tests/test_deploy_assets.py`

**Interfaces:**
- `worker-host up <名称>`：栈 `<平台栈>-worker-<名称>`，参数取平台栈 Outputs。
- `worker-host push <名称>`：与 `push` 相同的发布包，在 Worker 主机以 `WORKER_HOST=1` 运行 `al2023.sh <名称>`。
- `worker-host tls <名称>`：Worker 主机生成私钥与 CSR（SAN = 私网 IP）→ 平台主机用私有 CA 签发 → 写回 Worker 主机并 `reload nginx`。
- `worker-host register <名称>`：经 SSM 读取 Worker Token，在平台主机执行 `bootstrap-worker --spare --worker-url https://<私网 IP>:8943`。

- [ ] **Step 1: 写失败测试** — dry-run：`worker-host up w1` 打印 `cloudformation deploy` 与 `kirocrew-on-worker.yaml`、`PlatformSecurityGroupId`；`worker-host register w1` 打印 `bootstrap-worker --spare` 与 `https://`；名称不合法时退出码 2；平台模板 Outputs 含四个新键且 `HostSecurityGroup` 段与 `HEAD` 相同。
- [ ] **Step 2–4:** 实现并通过；`cfn-lint` 不可用时以 `aws cloudformation validate-template` 校验两个模板。

### Task 10 (B4): EC2 验收

- [ ] **Step 1:** 确认 EC2 无运行中的会话，并与并行会话确认 `PREVIEW_KEY` 修复的状态（发布会重启 API）。
- [ ] **Step 2:** `deploy.sh up`（只新增 Outputs，变更集无替换）→ `deploy.sh push`（阶段 A + B1/B2 的平台部分，生成 CA）。
- [ ] **Step 3:** `worker-host up w1` → `worker-host push w1` → `worker-host tls w1` → `worker-host register w1` → `kiro-login --host w1`（负责人完成设备码授权）。
- [ ] **Step 4:** 控制台：personal 设为共享；把 `w1` 分配给「Web应用软件研发组」；该组成员经 Skill 提交会话并完成；为一个普通用户分配共享环境并完成一个个人会话；run-b74e9f0d 分配后继续。
- [ ] **Step 5:** 隔离检查：Worker 主机上读不到平台主机文件；平台以外的地址访问 `w1:8943` 不通；不带 Token 调用 Worker 返回 401。
- [ ] **Step 6:** 向负责人报告；确认后提交阶段 B，并按确认再开 `w2`、`w3`（重复 Step 3）。
