# 访问管理 第 2 期「Team 与 Worker 管理」 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Team 可改名、归档 / 恢复、由管理员托管成员、成员自行退出、由管理员更换执行环境；Worker 可改名、退役、在控制台更新连接，服务的全部用户可刷新模型；Engine 每次派发前核对会话的 Worker 仍服务其空间，否则进入等待并可重新分配；Team 与执行环境的管理操作全部写入审计。

**Architecture:** 规则仍只落在 Octopus 的 API 与 Service 层。新增 `domain.unserved(db, run)` 作为「Worker 是否仍服务该会话」的唯一判断，Engine（派发前）、续作、诊断、分配 Worker 共用；Team 与 Worker 的管理操作是 `Service` 上的新方法，与第 1 期相同地在同一事务内调用 `audit()`。迁移 4 增加 `teams.archived`、`workers.retired`、`workers.last_team`。

**Tech Stack:** Python 3 标准库、SQLite、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-12-access-management-design.md`（§3.4、§5、§6、§10 第 2 期、§11）；参照 `docs/superpowers/specs/2026-10-11-team-execution-environments-design.md`（T4、T6）。

## Global Constraints

- 不新增依赖；面向用户的文字使用中文，风格与现有控制台一致。
- 错误语义沿用第 1 期：看不到的对象 404，看得到但不允许 403（说明缺什么），状态不允许 409（说明原因与下一步）。
- 越过他人资源的操作需要 `elevated(actor)`（管理员且 Token 含 `admin`）；只是管理员身份不够。
- Worker Token 只出现在请求体与 Worker 配置中，不写入审计、事件或日志。
- 迁移 4 走现有迁移框架（迁移前一致性备份）；所有按位置插入 `teams` 的语句改为显式列名（加列后按位置插入会失效）。
- 提交规则（负责人 CLAUDE.md）：在功能分支 `feat/m4-teams-workers` 本地提交；EC2 验收完成并经负责人确认后才推送。
- Python 测试：`.venv/bin/python -m unittest <模块>`；控制台：`cd web && npx vitest run`；完整回归：`PYTHON=$PWD/.venv/bin/python tools/regress.sh`。

## 设计裁定（与设计稿的差异，执行前请负责人确认）

| 编号 | 裁定 | 原因 | 若不对的代价 |
|---|---|---|---|
| P1 | `workers.retired` 等列放在**迁移 4**，不是设计稿写的迁移 3 | 迁移 3 已随第 1 期上线（EC2 已应用） | 无 |
| P2 | Team 的专属 Worker 主机在更换环境后改为空闲（§5），但**记住它服务过的 Team**（`last_team`）：只能再分配回这个 Team；要给其他 Team，须先重新部署主机（连接更新后执行边界变化即清除） | 主机上留有该 Team 的源码、工作区与 KiroCrew 记忆；T4「不改绑、不回收」的本意是不让下一个 Team 读到这些 | 管理员复用旧主机给别的 Team 前多一步重新部署 |
| P3 | 归档的 Team：会话可查看、停止、关闭、删除；续作、诊断、重新打开、分配 Worker 返回 409；已在执行的会话不被中断；顶栏空间切换仍列出（标「已归档」）以便查看旧会话，提交、共享、分配等选择器中隐藏 | §5「已有会话可读、可停止」「从选择器中隐藏」；成员只能通过顶栏进入 Team 空间查看旧会话 | 若负责人希望顶栏也隐藏，成员只能用链接或管理员视图查看旧会话 |
| P4 | 派发前核对（§6.2）发生在 `preparing` 与 `ready` 两个状态（准备工作区前、创建新 Attempt 前），以及续作（进入等待）与诊断（409）；Attempt 已在执行时不中断。Worker **已停用**且 Attempt 在执行中时维持现状（`blocked「Worker 已停用」`），诊断中维持现状 | 「已在执行的 Attempt 不中断，下一次派发时生效」；停用是管理员要求立即停止与该 Worker 通信 | 无 |
| P5 | 等待中的会话改派到**另一个** Worker 时清除原生父会话与工作区，在新环境重新准备（已完成的任务保留，原环境中的产出不迁移，提示中说明）；改派回原 Worker 时保留并直接继续 | 新 Worker 上没有原环境的原生父会话；T6「旧会话不迁移」 | 无 |
| P6 | 退役：仍是某 Team 的环境（专属或共享服务中有 Team）或有进行中的会话时拒绝；退役时移除它服务的个人空间、同时停用；退役后不出现在 `GET /v1/workers` 与 `GET /v1/admin/environments`，对它的管理操作返回 409 `retired` | §6.1；个人空间可再分配到其他共享环境 | 无 |
| P7 | 「创建平台账户」从「团队与成员」移到「用户管理」；Team 的「分配执行环境」改用新接口 `POST /v1/teams/<id>/environment` | 第 1 期 BACKLOG：用户管理整合到一处；同一接口覆盖分配与更换 | 无 |
| P8 | 旧版控制台 `/classic/` 的「新建 Team」不做 | `ui/index.html` 已不在仓库中，`/classic/` 只回落到新版控制台 | 无 |

## Review Focus

1. 更换环境的事务失败（新环境已停用、属于其他 Team、`last_team` 不符）时，旧环境的关系必须原样保留（Task 4 测试 `test_failed_change_keeps_the_old_environment`）。
2. 一个会话在 Attempt 执行中时 Team 被移出环境：本次 Attempt 继续被检查到完成，下一个任务才进入等待，不重复派发、不丢失结果（Task 6 测试 `test_running_attempt_finishes_then_the_next_task_waits`）。
3. 退役一个仍被历史会话引用的 Worker：会话详情、事件、修订仍可读，列表与选择器里不再出现，`space_workers` 不再返回它（Task 5 测试 `test_retired_worker_disappears_and_is_locked`）。
4. 成员退出后其仍在执行的 Team 会话：会话照常执行（Worker 服务的是 Team，不是个人），退出者不能再查看（Task 3 测试 `test_member_leaves_and_loses_access_to_team_sessions`）。
5. 管理员持不含 `admin` 的 Token 管理自己不在的 Team：仍是 404，不因账号是管理员而泄露 Team（Task 3 测试 `test_admin_without_admin_scope_is_not_a_host`）。

---

### Task 1: 迁移 4（Team 归档、Worker 退役与曾服务的 Team）

**Files:**
- Modify: `octopus/migrations.py`（新增 `TEAMS_WORKERS`，`MIGRATIONS` 追加 `(4, "teams_workers", TEAMS_WORKERS)`）、`octopus/domain.py:create_team`（`INSERT INTO teams(id,name,created)`）
- Modify（显式列名）：`tests/test_access.py`、`tests/test_mvp.py`、`tests/test_crew_entry.py`、`tests/test_observe.py`、`tests/test_migrations.py`、`tests/mvp_browser_fixture.py` 中所有 `INSERT INTO teams VALUES(`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `teams.archived INTEGER NOT NULL DEFAULT 0`、`workers.retired INTEGER NOT NULL DEFAULT 0`、`workers.last_team TEXT REFERENCES teams(id)`（空闲 Worker 主机曾服务的 Team，见 P2）。

- [ ] **Step 1: 写失败测试**（追加到 `MigrationTests`；把 `test_access_migration_adds_audit_and_activity_columns` 中的 `assertEqual(result["to"], 3)` 改为 `LATEST`）

```python
    def test_teams_workers_migration_adds_archive_and_retire_columns(self):
        self.legacy_database()
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result["to"], 4)
        with sqlite3.connect(self.path) as db:
            self.assertIn("archived", [c[1] for c in db.execute("PRAGMA table_info(teams)")])
            columns = [c[1] for c in db.execute("PRAGMA table_info(workers)")]
            self.assertIn("retired", columns)
            self.assertIn("last_team", columns)
            db.execute("INSERT INTO teams(id,name,created) VALUES('t9','新 Team',0)")
            self.assertEqual(db.execute("SELECT archived FROM teams WHERE id='t9'").fetchone()[0], 0)
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_migrations`，预期 `to` 为 3、列不存在。
- [ ] **Step 3: 实现**

```python
# Access management phase 2: an archived Team takes no new Sessions or members; a retired Worker leaves every listing;
# last_team is the Team a spare Worker host last served (its code and memories may remain there, P2).
TEAMS_WORKERS = """
ALTER TABLE teams ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workers ADD COLUMN retired INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workers ADD COLUMN last_team TEXT REFERENCES teams(id);
"""

MIGRATIONS = [(1, "baseline", BASELINE), (2, "environments", ENVIRONMENTS), (3, "access", ACCESS),
              (4, "teams_workers", TEAMS_WORKERS)]
```

并把 `domain.create_team` 与上列测试文件中的 `INSERT INTO teams VALUES(` 全部改为 `INSERT INTO teams(id,name,created) VALUES(`（`grep -rn "INSERT INTO teams VALUES" octopus tests` 结果为空）。

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_migrations tests.test_access tests.test_mvp tests.test_crew_entry tests.test_observe`，全部 OK。
- [ ] **Step 5: 提交** — `feat(migrations): 迁移 4——Team 归档、Worker 退役与空闲主机曾服务的 Team`

### Task 2: Team 改名与归档

**Files:**
- Modify: `octopus/domain.py`（`Service._team`、`Service.rename_team`、`Service.archive_team`、`active_team`；`start`、`operate`、`create_team` 加检查与审计）、`octopus/server.py`（`do_PATCH`；路由 `PATCH /v1/teams/<id>`、`POST /v1/teams/<id>/archive`；`required_scope`）
- Create: `tests/test_teams_workers.py`

**Interfaces:**
- Consumes: Task 1 的 `teams.archived`。
- Produces:
  - `active_team(db, team) -> sqlite3.Row`：Team 不存在 404；已归档 409 `team_archived`。
  - `Service._team(db, actor, tid, owner=False) -> dict`：管理员（elevated）只要求存在（404）；其他人按 `member(db, actor["id"], tid, owner)`。
  - `Service.rename_team(actor, tid, body) -> {"id", "name"}`、`Service.archive_team(actor, tid, body) -> {"id", "archived"}`。
  - 审计动作：`team.create`、`team.rename`（`{from, to}`）、`team.archive`、`team.restore`。
  - `required_scope`：`POST /v1/teams/<id>/archive`、`POST /v1/teams/<id>/environment` 需要 `admin`；`PATCH /v1/teams/<id>` 需要 `manage`。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_teams_workers.py`：

```python
"""Access management phase 2: Team and Worker management, the dispatch re-check, and their audit (spec §5, §6)."""
import json
import unittest
from unittest.mock import patch

from octopus.db import dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.test_access import PASSWORD, AccessCase


class TeamsWorkersCase(AccessCase):
    """carol owns「平台组」(team-p) with bob as a member, on the shared environment env-s; alice (admin) is not a member."""

    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            carol = create_user(db, "carol", PASSWORD)
            self.tokens["carol"] = issue_token(db, carol["id"])["token"]
            self.users["carol"] = self.c = authenticate(db, self.tokens["carol"])
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-p','平台组',0)")
            db.execute("INSERT INTO members VALUES('team-p',?,'owner')", (self.c["id"],))
            db.execute("INSERT INTO members VALUES('team-p',?,'member')", (self.b["id"],))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('env-s','共享',?,?,'shared')",
                       (self.a["id"], self.config("b-s")))
            db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES('env-s','team-p')")

    @staticmethod
    def config(boundary):
        return dumps({"url": "http://127.0.0.1:1", "token": "x" * 40,
                      "capabilities": {"isolation": "dedicated", "boundary": boundary, "host_principal": "hp-" + boundary}})

    def spare(self, wid="env-spare"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'spare')",
                       (wid, wid, self.a["id"], self.config("b-" + wid)))
        return wid

    def other_team(self, tid="team-r", name="研发组"):
        with self.db.transaction() as db:
            db.execute("INSERT INTO teams(id,name,created) VALUES(?,?,0)", (tid, name))
            db.execute("INSERT INTO members VALUES(?,?,'owner')", (tid, self.c["id"]))
        return tid

    def audits(self, action):
        with self.db.read() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit WHERE action=? ORDER BY id", (action,))]

    def code(self, call, *args, **kwargs):
        with self.assertRaises(Error) as caught:
            call(*args, **kwargs)
        return caught.exception.status, caught.exception.code


class TeamRenameArchiveTests(TeamsWorkersCase):
    def test_owner_renames_and_it_is_audited(self):
        self.assertEqual(self.service.rename_team(self.c, "team-p", {"name": "平台二组"})["name"], "平台二组")
        self.assertEqual({t["id"]: t["name"] for t in self.service.teams(self.c)}["team-p"], "平台二组")
        entry = self.audits("team.rename")[0]
        self.assertEqual((entry["actor_id"], json.loads(entry["detail"])), (self.c["id"], {"from": "平台组", "to": "平台二组"}))

    def test_admin_renames_a_team_they_are_not_in(self):
        self.service.rename_team(self.a, "team-p", {"name": "平台三组"})
        self.assertEqual(self.audits("team.rename")[0]["actor_id"], self.a["id"])

    def test_member_is_told_only_the_owner_renames_and_outsiders_learn_nothing(self):
        self.assertEqual(self.code(self.service.rename_team, self.b, "team-p", {"name": "x 组"}), (403, "forbidden"))
        self.assertEqual(self.code(self.service.rename_team, self.e, "team-p", {"name": "x 组"}), (404, "not_found"))
        self.assertEqual(self.code(self.service.rename_team, self.c, "team-p", {"name": ""}), (400, "invalid_request"))

    def test_duplicate_name_conflicts(self):
        self.other_team()
        self.assertEqual(self.code(self.service.rename_team, self.c, "team-p", {"name": "研发组"}), (409, "conflict"))

    def test_archive_requires_an_elevated_admin(self):
        self.assertEqual(self.code(self.service.archive_team, self.c, "team-p", {"archived": True}), (403, "forbidden"))
        self.assertEqual(self.code(self.service.archive_team, self.a, "team-p", {"archived": "yes"}), (400, "invalid_request"))

    def test_archived_team_refuses_new_sessions_and_members_but_old_sessions_stay_readable_and_stoppable(self):
        run = self.start(self.c, team_id="team-p")
        self.service.archive_team(self.a, "team-p", {"archived": True})
        self.assertEqual(self.code(self.start, self.c, "start-0000000002", team_id="team-p"), (409, "team_archived"))
        self.assertEqual(self.code(self.service.membership, self.c, "team-p", {"username": "eve"}), (409, "team_archived"))
        self.assertEqual(self.service.run(self.b, run["id"])["id"], run["id"])
        self.assertEqual(self.code(self.service.operate, self.c, run["id"], "continue",
                                   {"request_id": "continue-000001", "expected_version": 1, "task_id": "code"}),
                         (409, "team_archived"))
        self.service.operate(self.c, run["id"], "stop", {"request_id": "stop-00000001", "expected_version": 1})
        self.assertTrue({t["id"]: t for t in self.service.teams(self.c)}["team-p"]["archived"])
        self.service.archive_team(self.a, "team-p", {"archived": False})
        self.start(self.c, "start-0000000003", team_id="team-p")
        self.assertEqual([e["action"] for e in self.audits("team.archive") + self.audits("team.restore")],
                         ["team.archive", "team.restore"])

    def test_team_creation_is_audited(self):
        self.spare()
        created = self.service.create_team(self.a, {"name": "新组", "owner": "carol", "worker_id": "env-spare"})
        self.assertEqual(self.audits("team.create")[0]["target"], created["id"])
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_teams_workers`，预期 `AttributeError: 'Service' object has no attribute 'rename_team'` 等。
- [ ] **Step 3: 实现**

`octopus/domain.py`（模块级，`team_environment` 之后）：

```python
def active_team(db, team):
    """A Team that takes new Sessions and members: 409 once archived (spec §5)."""
    row = db.execute("SELECT * FROM teams WHERE id=?", (team,)).fetchone()
    require(row is not None, "Team 不存在或无权限", 404, "not_found")
    require(not row["archived"], f"Team「{row['name']}」已归档：会话可查看与停止，不能提交新会话或添加成员",
            409, "team_archived")
    return row
```

`Service` 中：

```python
    @staticmethod
    def _team(db, actor, tid, owner=False):
        """The Team an admin (any Team) or its members (owner=True: its owners) may manage; 404 / 403 as member()."""
        row = db.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone()
        if elevated(actor):
            require(row is not None, "Team 不存在", 404, "not_found")
        else:
            member(db, actor["id"], tid, owner=owner)
        return dict(row)

    def rename_team(self, actor, tid, body):
        with self.db.transaction() as db:
            team = self._team(db, actor, tid, owner=True)
            title = name(body.get("name"))
            require(not db.execute("SELECT 1 FROM teams WHERE name=? AND id!=?", (title, tid)).fetchone(),
                    "Team 名称已存在", 409, "conflict")
            db.execute("UPDATE teams SET name=? WHERE id=?", (title, tid))
            audit(db, actor, "team.rename", "team", tid, {"from": team["name"], "to": title})
        return {"id": tid, "name": title}

    def archive_team(self, actor, tid, body):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        require(isinstance(body.get("archived"), bool), "archived 必须为布尔值")
        with self.db.transaction() as db:
            self._team(db, actor, tid)
            db.execute("UPDATE teams SET archived=? WHERE id=?", (int(body["archived"]), tid))
            audit(db, actor, "team.archive" if body["archived"] else "team.restore", "team", tid)
        return {"id": tid, "archived": body["archived"]}
```

- `start`：两处 `member(db, actor["id"], team)` 之后各加 `active_team(db, team)`。
- `operate`：`run_access` 之后、`execute` 之前：

```python
            if run["team_id"] and action in {"continue", "recover", "reopen", "assign-worker"}:
                active_team(db, run["team_id"])   # an archived Team's Sessions stay readable and stoppable (P3)
```

- `membership`：`member(...)` 之后、非 `remove` 时 `active_team(db, tid)`（Task 3 会改写这一段，届时保留此检查）。
- `create_team`：插入后 `audit(db, actor, "team.create", "team", tid, {"owner": owner["id"], "worker_id": body["worker_id"]})`。

`octopus/server.py`：

```python
        def do_PATCH(self):
            self.dispatch("PATCH")
```

`required_scope` 的 admin 条件加 `(segments[:2] == ["v1", "teams"] and len(segments) == 4 and segments[3] in {"archive", "environment"})`；路由（放在 `/v1/teams/<id>/members` 之前）：

```python
                if len(segments) == 3 and segments[:2] == ["v1", "teams"]:
                    require(method == "PATCH", "Method not allowed", 405)
                    return self.respond(service.rename_team(actor, segments[2], body))
                if len(segments) == 4 and segments[:2] == ["v1", "teams"] and segments[3] == "archive":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.archive_team(actor, segments[2], body))
```

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_teams_workers tests.test_access tests.test_mvp`，全部 OK。
- [ ] **Step 5: 提交** — `feat(teams): Team 改名与归档（归档后拒绝新会话与新成员，旧会话可查看与停止）；Team 创建与管理写入审计`

### Task 3: 管理员托管成员与成员退出

**Files:**
- Modify: `octopus/domain.py:Service.membership`
- Test: `tests/test_teams_workers.py`

**Interfaces:**
- Consumes: Task 2 的 `Service._team`、`active_team`。
- Produces: `membership(actor, tid, body, remove=False)` 规则——管理员（elevated）或 owner 可增删与改角色；成员可以只删除自己（退出）；最后一位有效 owner 不能被移除或降级（含退出）。审计动作：`team.member.set`（`{user, role}`）、`team.member.remove`（`{user}`）、`team.member.leave`。

- [ ] **Step 1: 写失败测试**

```python
class MembershipTests(TeamsWorkersCase):
    def test_admin_hosts_members_without_being_one(self):
        self.service.membership(self.a, "team-p", {"username": "eve", "role": "owner"})
        roles = {m["username"]: m["role"] for m in {t["id"]: t for t in self.service.teams(self.a)}["team-p"]["members"]}
        self.assertEqual(roles["eve"], "owner")
        self.assertEqual(json.loads(self.audits("team.member.set")[0]["detail"]), {"user": self.e["id"], "role": "owner"})

    def test_admin_without_admin_scope_is_not_a_host(self):
        plain = {**self.a, "scopes": ["read", "execute", "manage"]}
        self.assertEqual(self.code(self.service.membership, plain, "team-p", {"username": "eve"}), (404, "not_found"))

    def test_member_leaves_and_loses_access_to_team_sessions(self):
        run = self.start(self.b, team_id="team-p")
        self.service.membership(self.b, "team-p", {"username": "bob"}, remove=True)
        self.assertEqual(self.audits("team.member.leave")[0]["actor_id"], self.b["id"])
        self.assertEqual(self.code(self.service.run, self.b, run["id"]), (404, "not_found"))
        self.assertEqual(self.service.run(self.c, run["id"])["status"], "preparing")   # the Team's Session runs on

    def test_member_cannot_remove_others_or_promote_themselves(self):
        self.assertEqual(self.code(self.service.membership, self.b, "team-p", {"username": "carol"}, remove=True),
                         (403, "forbidden"))
        self.assertEqual(self.code(self.service.membership, self.b, "team-p", {"username": "bob", "role": "owner"}),
                         (403, "forbidden"))

    def test_the_last_active_owner_cannot_leave(self):
        self.assertEqual(self.code(self.service.membership, self.c, "team-p", {"username": "carol"}, remove=True),
                         (409, "conflict"))

    def test_owner_removal_is_audited(self):
        self.service.membership(self.c, "team-p", {"username": "bob"}, remove=True)
        self.assertEqual(json.loads(self.audits("team.member.remove")[0]["detail"]), {"user": self.b["id"]})
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_teams_workers.MembershipTests`，预期管理员托管得 404、退出得 403、无审计。
- [ ] **Step 3: 实现** — 改写 `membership`：

```python
    def membership(self, actor, tid, body, remove=False):
        """Owners and admins (elevated, members or not) add, remove and re-role members; a member may remove only
        themselves (leave). The last active owner stays (B4)."""
        with self.db.transaction() as db:
            username = text(body.get("username"), "用户名", 64).lower()
            leaving = remove and username == actor["username"]
            self._team(db, actor, tid, owner=not leaving)
            if not remove:
                active_team(db, tid)
            target = db.execute("SELECT id FROM users WHERE username=? AND active=1", (username,)).fetchone()
            require(target is not None, "用户不存在", 404, "not_found")
            role = body.get("role", "member")
            require(role in {"owner", "member"}, "角色无效")
            current = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?",
                                 (tid, target["id"])).fetchone()
            if current and current["role"] == "owner" and (remove or role != "owner"):
                # Another *active* owner must remain: a deactivated owner cannot manage the Team (B4).
                require(db.execute("SELECT count(*) FROM members m JOIN users u ON u.id=m.user_id WHERE m.team_id=? "
                                   "AND m.role='owner' AND u.active=1 AND m.user_id!=?", (tid, target["id"])).fetchone()[0] > 0,
                        "不能移除最后一位有效的 Team Owner", 409, "conflict")
            if remove:
                require(current is not None, "该用户不是 Team 成员", 404, "not_found")
                db.execute("DELETE FROM members WHERE team_id=? AND user_id=?", (tid, target["id"]))
                audit(db, actor, "team.member.leave" if leaving else "team.member.remove", "team", tid,
                      None if leaving else {"user": target["id"]})
            else:
                db.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(team_id,user_id) DO UPDATE SET role=excluded.role",
                           (tid, target["id"], role))
                audit(db, actor, "team.member.set", "team", tid, {"user": target["id"], "role": role})
        return {"ok": True}
```

注意：`_team(owner=not leaving)` 对管理员只查存在；对成员退出只要求是成员；其余要求 owner。

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_teams_workers tests.test_access tests.test_mvp`；`PermissionMatrixTests` 中托管一格此时变为 `(400, 404, 400, 403, 404)`，按此更新该行并删除其上方「phase 2」注释。
- [ ] **Step 5: 提交** — `feat(teams): 管理员托管 Team 成员与角色、成员自行退出；成员变更写入审计`

### Task 4: 更换 Team 的执行环境

**Files:**
- Modify: `octopus/domain.py`（`give_environment`、`assign_spare`、`Service.change_environment`）、`octopus/server.py`（路由 `POST /v1/teams/<id>/environment`；`/v1/workers/<id>/connection` 在执行边界变化时清除 `last_team`）
- Test: `tests/test_teams_workers.py`

**Interfaces:**
- Consumes: Task 1 的 `workers.last_team`、`workers.retired`；Task 2 的 `Service._team`。
- Produces:
  - `Service.change_environment(actor, tid, body) -> {"id", "environment"}`：admin；`body.worker_id` 为启用、未退役的共享或空闲环境。原环境为共享时移除服务关系；原环境为该 Team 的专属 Worker 时改为空闲并记 `last_team=tid`。审计 `team.environment`（`{from, to}`）。Team 尚无环境时同样可用（即「分配执行环境」）。
  - `give_environment(db, worker, team)`：空闲环境的 `last_team` 不为空且不是该 Team 时 409 `environment_reuse`。
  - `assign_spare` 分配后 `last_team=NULL`。

- [ ] **Step 1: 写失败测试**

```python
class EnvironmentChangeTests(TeamsWorkersCase):
    def change(self, worker, actor=None):
        return self.service.change_environment(actor or self.a, "team-p", {"worker_id": worker})

    def test_moving_a_team_from_a_shared_environment_onto_a_spare_host(self):
        self.spare()
        self.assertEqual(self.change("env-spare")["environment"]["worker_id"], "env-spare")
        with self.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM worker_spaces WHERE team_id='team-p'").fetchone())
            row = db.execute("SELECT scope,team_id FROM workers WHERE id='env-spare'").fetchone()
        self.assertEqual(tuple(row), ("dedicated", "team-p"))
        self.assertEqual(json.loads(self.audits("team.environment")[0]["detail"]), {"from": "env-s", "to": "env-spare"})

    def test_the_teams_own_host_becomes_spare_and_serves_only_that_team_again(self):
        self.spare()
        self.change("env-spare")
        self.change("env-s")
        with self.db.read() as db:
            row = db.execute("SELECT scope,team_id,last_team FROM workers WHERE id='env-spare'").fetchone()
        self.assertEqual(tuple(row), ("spare", None, "team-p"))
        other = self.other_team()
        status, code = self.code(self.service.worker_space, self.a, "env-spare", {"team_id": other})
        self.assertEqual((status, code), (409, "environment_reuse"))
        self.change("env-spare")   # back to the Team whose code is on it

    def test_a_redeployed_host_may_serve_another_team(self):
        self.spare()
        self.change("env-spare")
        self.change("env-s")
        health = {"isolation": "dedicated", "boundary": "b-new", "host_principal": "hp-new"}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as call:
            status, _ = call("POST", "/v1/workers/env-spare/connection", self.tokens["alice"],
                             {"connection": {"url": "https://w.example", "token": "y" * 40}})
        self.assertEqual(status, 200)
        self.service.worker_space(self.a, "env-spare", {"team_id": self.other_team()})

    def test_failed_change_keeps_the_old_environment(self):
        self.spare()
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='env-spare'")
        self.assertEqual(self.code(self.change, "env-spare"), (409, "worker_unavailable"))
        self.assertEqual(self.service.teams(self.a)[0]["environment"]["worker_id"], "env-s")

    def test_same_environment_unknown_worker_and_non_admins(self):
        self.assertEqual(self.code(self.change, "env-s"), (409, "conflict"))
        self.assertEqual(self.code(self.change, "nope"), (404, "not_found"))
        self.assertEqual(self.code(self.change, "env-s", self.c), (403, "forbidden"))

    def test_a_team_without_an_environment_is_given_one(self):
        other = self.other_team()
        self.spare()
        self.assertEqual(self.service.change_environment(self.a, other, {"worker_id": "env-spare"})
                         ["environment"]["scope"], "dedicated")
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_teams_workers.EnvironmentChangeTests`，预期 `change_environment` 不存在。
- [ ] **Step 3: 实现**

```python
def assign_spare(db, worker, team):
    """A spare execution environment becomes the Team's dedicated one (D28, T4; P2 for a host that served a Team)."""
    changed = db.execute("UPDATE workers SET scope='dedicated', team_id=?, owner_id=NULL, last_team=NULL "
                         "WHERE id=? AND scope='spare' AND enabled=1 AND retired=0", (team, worker)).rowcount
    require(changed == 1, "该执行环境不可用或已分配", 409, "worker_unavailable")


def give_environment(db, worker, team):
    """Give a Team an execution environment: a shared Worker serves it, a spare one becomes its own."""
    row = db.execute("SELECT scope,last_team FROM workers WHERE id=? AND enabled=1 AND retired=0", (worker,)).fetchone()
    require(row is not None and row["scope"] in {"shared", "spare"}, "该执行环境不可用或已分配", 409, "worker_unavailable")
    require(team_environment(db, team) is None, "该 Team 已有执行环境（含已停用的）", 409, "team_has_environment")
    if row["scope"] == "shared":
        db.execute("INSERT INTO worker_spaces(worker_id,team_id) VALUES(?,?)", (worker, team))
        return
    if row["last_team"] not in (None, team):
        served = db.execute("SELECT name FROM teams WHERE id=?", (row["last_team"],)).fetchone()
        raise Error(409, "environment_reuse", f"该空闲环境曾服务 Team「{served['name'] if served else row['last_team']}」，"
                    "主机上留有其源码、工作区与记忆；请重新部署该主机（新的执行边界）后再分配给其他 Team")
    assign_spare(db, worker, team)
```

`Service`：

```python
    def change_environment(self, actor, tid, body):
        """Move a Team to another execution environment (spec §5). Old Sessions are not moved (T6): their next dispatch
        finds the Worker no longer serves the Team and waits to be reassigned (§6.2)."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        wid = body.get("worker_id")
        require(isinstance(wid, str) and wid, "需要 worker_id")
        with self.db.transaction() as db:
            self._team(db, actor, tid)
            require(db.execute("SELECT 1 FROM workers WHERE id=? AND retired=0", (wid,)).fetchone(),
                    "Worker 不存在", 404, "not_found")
            old = team_environment(db, tid)
            require(old is None or old["id"] != wid, "该 Team 已在使用这个执行环境", 409, "conflict")
            if old is not None and old["scope"] == "shared":
                db.execute("DELETE FROM worker_spaces WHERE worker_id=? AND team_id=?", (old["id"], tid))
            elif old is not None:
                # The Team's own host waits as a spare that remembers it: its code and memories are still there (P2).
                db.execute("UPDATE workers SET scope='spare', team_id=NULL, owner_id=?, last_team=? WHERE id=?",
                           (actor["id"], tid, old["id"]))
            give_environment(db, wid, tid)
            audit(db, actor, "team.environment", "team", tid, {"from": old["id"] if old else None, "to": wid})
            return {"id": tid, "environment": self._environment(db, tid)}
```

`server.py` 路由（`/v1/teams/<id>/archive` 旁）：

```python
                if len(segments) == 4 and segments[:2] == ["v1", "teams"] and segments[3] == "environment":
                    require(method == "POST", "Method not allowed", 405)
                    return self.respond(service.change_environment(actor, segments[2], body))
```

连接更新路由：在 `UPDATE workers SET config=?` 处改为

```python
                        moved = json.loads(row["config"]).get("capabilities", {}).get("boundary") != health.get("boundary")
                        # A new boundary is a redeployed host: what an earlier Team left there is gone (P2).
                        db.execute("UPDATE workers SET config=?, last_team=CASE WHEN ? THEN NULL ELSE last_team END "
                                   "WHERE id=?", (dumps({**config, "capabilities": health}), int(moved), row["id"]))
```

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_teams_workers tests.test_mvp tests.test_access tests.test_bootstrap`，全部 OK。
- [ ] **Step 5: 提交** — `feat(teams): 管理员更换 Team 执行环境——共享环境移除服务关系、专属主机改为空闲并只回到原 Team，重新部署后才可给其他 Team`

### Task 5: Worker 改名、退役、刷新模型的范围与执行环境操作审计

**Files:**
- Modify: `octopus/domain.py`（`Service._live_worker`、`rename_worker`、`retire_worker`；`workers`、`environments` 排除退役；`share_worker`、`dedicate_worker`、`worker_space` 加 `_live_worker` 与审计）、`octopus/server.py`（`PATCH /v1/workers/<id>`、`POST /v1/workers/<id>/retire`；`enabled`、`connection`、`POST /v1/workers` 加退役检查与审计；`capabilities` 按 `service.workers(actor)` 判断可见）
- Test: `tests/test_teams_workers.py`

**Interfaces:**
- Consumes: Task 1 的 `workers.retired`。
- Produces:
  - `Service._live_worker(db, wid) -> sqlite3.Row`：不存在 404；已退役 409 `retired`「执行环境已退役」。
  - `Service.rename_worker(actor, wid, body) -> {"id", "name"}`、`Service.retire_worker(actor, wid) -> {"id", "retired": True}`。
  - 审计动作：`worker.register`、`worker.rename`（`{from, to}`）、`worker.retire`、`worker.enabled`（`{enabled}`）、`worker.share`、`worker.dedicate`（`{team_id}`）、`worker.space.add` / `worker.space.remove`（`{team_id}` 或 `{user_id}`）、`worker.connection`（`{boundary_changed}`，不含 URL 与 Token）。

- [ ] **Step 1: 写失败测试**

```python
class WorkerManagementTests(TeamsWorkersCase):
    def test_rename_is_admin_only_and_audited(self):
        self.assertEqual(self.service.rename_worker(self.a, "env-s", {"name": "共享-1"})["name"], "共享-1")
        self.assertEqual(json.loads(self.audits("worker.rename")[0]["detail"]), {"from": "共享", "to": "共享-1"})
        self.assertEqual(self.code(self.service.rename_worker, self.c, "env-s", {"name": "x"}), (403, "forbidden"))

    def test_retire_refuses_a_team_environment(self):
        self.assertEqual(self.code(self.service.retire_worker, self.a, "env-s"), (409, "in_use"))

    def test_retire_refuses_sessions_in_progress(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('env-b','bob',?,?)", (self.b["id"], self.config("b-b")))
        self.start(self.b, worker_id="env-b")
        self.assertEqual(self.code(self.service.retire_worker, self.a, "env-b"), (409, "busy"))

    def test_retired_worker_disappears_and_is_locked(self):
        self.spare()
        with self.db.transaction() as db:
            db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('env-s',?)", (self.e["id"],))
        self.service.change_environment(self.a, "team-p", {"worker_id": "env-spare"})
        run = self.start(self.e)   # eve's personal space runs on env-s
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (run["id"],))
        self.service.retire_worker(self.a, "env-s")
        self.assertNotIn("env-s", [w["id"] for w in self.service.workers(self.a)])
        with patch("octopus.domain._probe", return_value={"reachable": False, "error": "x"}):
            self.assertNotIn("env-s", [w["id"] for w in self.service.environments(self.a)])
        with self.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM worker_spaces WHERE worker_id='env-s'").fetchone())
        self.assertEqual(self.service.run(self.e, run["id"])["worker_id"], "env-s")   # history keeps its reference
        self.assertEqual(self.code(self.service.rename_worker, self.a, "env-s", {"name": "x"}), (409, "retired"))
        with self.http() as call:
            self.assertEqual(call("POST", "/v1/workers/env-s/enabled", self.tokens["alice"], {"enabled": True})[0], 409)
        self.assertEqual(self.audits("worker.retire")[0]["target"], "env-s")

    def test_models_refresh_is_open_to_every_space_the_environment_serves(self):
        health = {"isolation": "dedicated", "boundary": "b-s", "host_principal": "hp-b-s", "models": {"claude": ["x"]}}
        with patch("octopus.server.worker_request", return_value=health), self.http() as call:
            self.assertEqual(call("GET", "/v1/workers/env-s/capabilities", self.tokens["bob"])[0], 200)
            self.assertEqual(call("GET", "/v1/workers/env-s/capabilities", self.tokens["eve"])[0], 404)

    def test_environment_operations_are_audited(self):
        self.service.worker_space(self.a, "env-s", {"user_id": self.e["id"]})
        self.service.worker_space(self.a, "env-s", {"user_id": self.e["id"]}, remove=True)
        health = {"isolation": "dedicated", "boundary": "b-r", "host_principal": "hp-r"}
        with patch("octopus.server.validate_worker", return_value=health), self.http() as call:
            self.assertEqual(call("POST", "/v1/workers/env-s/enabled", self.tokens["alice"], {"enabled": False})[0], 200)
            status, created = call("POST", "/v1/workers", self.tokens["alice"],
                                   {"name": "w9", "spare": True, "connection": {"url": "https://w9", "token": "z" * 40}})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(self.audits("worker.space.add")[0]["detail"]), {"user_id": self.e["id"]})
        self.assertEqual(len(self.audits("worker.space.remove")), 1)
        self.assertEqual(json.loads(self.audits("worker.enabled")[0]["detail"]), {"enabled": False})
        entry = self.audits("worker.register")[0]
        self.assertEqual(entry["target"], created["id"])
        self.assertNotIn("z" * 40, entry["detail"])
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_teams_workers.WorkerManagementTests`，预期方法不存在、bob 刷新得 404、无审计。
- [ ] **Step 3: 实现**

`Service`：

```python
    @staticmethod
    def _live_worker(db, wid):
        row = db.execute("SELECT * FROM workers WHERE id=?", (wid,)).fetchone()
        require(row is not None, "Worker 不存在", 404, "not_found")
        require(not row["retired"], "执行环境已退役", 409, "retired")
        return row

    def rename_worker(self, actor, wid, body):
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            title = name(body.get("name"))
            db.execute("UPDATE workers SET name=? WHERE id=?", (title, wid))
            audit(db, actor, "worker.rename", "worker", wid, {"from": row["name"], "to": title})
        return {"id": wid, "name": title}

    def retire_worker(self, actor, wid):
        """Retire an execution environment (spec §6.1, P6): no Team may still use it and nothing may be in progress on
        it; the personal spaces it served lose it; historical Sessions keep their reference."""
        require(elevated(actor), "需要平台管理员", 403, "forbidden")
        with self.db.transaction() as db:
            row = self._live_worker(db, wid)
            team = row["team_id"] or (db.execute("SELECT team_id FROM worker_spaces WHERE worker_id=? AND team_id IS NOT NULL",
                                                 (wid,)).fetchone() or [None])[0]
            if team:
                title = db.execute("SELECT name FROM teams WHERE id=?", (team,)).fetchone()
                raise Error(409, "in_use", f"仍是 Team「{title['name'] if title else team}」的执行环境：先为该 Team 更换执行环境")
            busy = db.execute("SELECT count(*) FROM runs WHERE worker_id=? AND lifecycle='open' AND status NOT IN "
                              f"({','.join('?' * len(SETTLED))})", (wid, *SETTLED)).fetchone()[0]
            require(busy == 0, f"还有 {busy} 个进行中的会话：先等待结束或停止", 409, "busy")
            db.execute("DELETE FROM worker_spaces WHERE worker_id=?", (wid,))
            db.execute("UPDATE workers SET retired=1, enabled=0 WHERE id=?", (wid,))
            audit(db, actor, "worker.retire", "worker", wid)
        return {"id": wid, "retired": True}
```

- `workers()`：`SELECT * FROM workers WHERE retired=0`；`environments()`：`SELECT * FROM workers WHERE retired=0 ORDER BY rowid`。
- `share_worker`、`dedicate_worker`、`worker_space`：把 `row = db.execute(...).fetchone(); require(row is not None, ...)` 换成 `row = self._live_worker(db, wid)`；成功后分别 `audit(db, actor, "worker.share", "worker", wid)`、`audit(db, actor, "worker.dedicate", "worker", wid, {"team_id": team})`、`audit(db, actor, "worker.space.remove" if remove else "worker.space.add", "worker", wid, {column: value})`。
- `server.py`：
  - `required_scope` 不变（`/v1/workers*` 非 GET 已是 admin）。
  - `/v1/workers/<id>` 路由：`PATCH` → `service.rename_worker`；`/v1/workers/<id>/retire` `POST` → `service.retire_worker`。
  - `enabled`：`Service._live_worker(db, segments[2])` 代替存在性检查，更新后 `audit(db, actor, "worker.enabled", "worker", segments[2], {"enabled": body["enabled"]})`。
  - `connection`：`row = Service._live_worker(db, segments[2])`；更新后 `audit(db, actor, "worker.connection", "worker", row["id"], {"boundary_changed": moved})`（`moved` 见 Task 4）。
  - `POST /v1/workers`：插入后 `audit(db, actor, "worker.register", "worker", wid, {"scope": "spare" if spare else "dedicated", "team_id": team, "owner_id": None if spare else user})`。
  - `capabilities`：

```python
                if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] == "capabilities":
                    require(method == "GET", "Method not allowed", 405)
                    # Everyone the environment serves may refresh its model catalogue (spec §6.1), shared ones included.
                    require(any(w["id"] == segments[2] for w in service.workers(actor)), "Worker 不存在或无权限", 404, "not_found")
                    with database.read() as db:
                        config = json.loads(db.execute("SELECT config FROM workers WHERE id=?", (segments[2],)).fetchone()["config"])
```

  其后不变（`worker_request(config, "/health")`、边界核对、写回）。从 `octopus.domain` 导入 `Service`（`_live_worker` 为 staticmethod）。

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_teams_workers tests.test_mvp tests.test_access tests.test_bootstrap`，全部 OK。
- [ ] **Step 5: 提交** — `feat(workers): Worker 改名与退役（退役后从列表与选择器消失、历史会话保留引用）；服务的全部用户可刷新模型；执行环境操作写入审计`

### Task 6: 派发前核对（§6.2）

**Files:**
- Modify: `octopus/domain.py`（`unserved`；`operate` 的 `continue`、`recover`、`assign-worker`；`_space`、`_attention`）、`octopus/engine.py:advance`
- Test: `tests/test_teams_workers.py`

**Interfaces:**
- Consumes: Task 4、Task 5（更换环境、退役产生的状态）。
- Produces:
  - `unserved(db, run) -> str | None`：`"执行环境已退役"`、`"执行环境已停用"`、`"执行环境已更换或已移出该空间"`，仍服务时 `None`。`run` 需含 `worker_id`、`owner_id`、`team_id`。
  - Engine：状态 `preparing` / `ready` 时 `unserved` 非空 → `update(rid, "waiting_worker", reason)`，不请求 Worker。
  - `continue`：Worker 不再服务时进入 `waiting_worker`，`error` 为原因。
  - `recover`：409 `environment_changed`「<原因>：请续作后重新分配 Worker」。
  - `assign-worker`：改派到另一个 Worker 时 `native_parent`、`workspace` 置空、状态 `preparing`；改派回原 Worker 时保留，状态为 `ready`（有原生父会话）或 `preparing`。
  - `_attention`：等待中且曾分配 Worker 时，标题为「<空间>的<原因>」，说明已完成任务保留、换环境时重新准备工作区。

- [ ] **Step 1: 写失败测试**

```python
class DispatchRecheckTests(TeamsWorkersCase):
    def setUp(self):
        super().setUp()
        self.calls, self.done = [], False

    def worker(self, config, path, body, **kwargs):
        self.calls.append(path)
        if path == "/prepare":
            return {"native_parent": "octopus-" + body["run_id"], "workspace": "/w/" + body["run_id"]}
        if path == "/dispatch":
            return {"status": "running", "native_id": "n-" + body["attempt_id"][-6:], "context_hash": "h"}
        if path == "/inspect":
            native = {"done": True, "result": "完成", "error": ""} if self.done else {"done": False}
            return {"status": "running", "native": native, "evidence": {"actual_backend": "claude"},
                    "handoff": "交接", "outcome": dumps({"decision": "pass", "summary": "通过"})}
        raise AssertionError(path)

    def tick(self, times=1):
        engine = Engine(self.db, request=self.worker)
        for _ in range(times):
            engine.tick()

    def two_steps(self, actor, request="start-0000000001"):
        return self.service.start(actor, {"request_id": request, "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": False, "max_cycles": 2},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"},
                                                    {"id": "check", "name": "核对", "tool": "claude", "prompt": "核对"}]})

    def assign(self, rid, worker):
        version = self.service.run(self.c, rid)["version"]
        self.service.operate(self.c, rid, "assign-worker",
                             {"request_id": "assign-" + worker, "expected_version": version, "worker_id": worker})
        return self.service.run(self.c, rid)

    def test_a_session_whose_team_moved_waits_instead_of_dispatching(self):
        run = self.start(self.c, team_id="team-p")
        self.service.change_environment(self.a, "team-p", {"worker_id": self.spare()})
        self.tick()
        self.assertEqual(self.calls, [])
        detail = self.service.run(self.c, run["id"])
        self.assertEqual((detail["status"], detail["error"]), ("waiting_worker", "执行环境已更换或已移出该空间"))
        self.assertIn("已更换", detail["attention"]["title"])
        self.assertEqual(self.assign(run["id"], "env-spare")["status"], "preparing")

    def test_running_attempt_finishes_then_the_next_task_waits(self):
        run = self.two_steps(self.c)
        self.tick(3)                                    # prepare, dispatch, inspect (still running)
        with self.db.transaction() as db:
            db.execute("DELETE FROM worker_spaces WHERE team_id='team-p'")
        self.done = True
        self.tick(3)
        detail = self.service.run(self.c, run["id"])
        self.assertEqual([t["status"] for t in detail["tasks"]], ["accepted", "pending"])
        self.assertEqual(detail["status"], "waiting_worker")
        self.assertEqual(self.calls.count("/dispatch"), 1)

    def test_reassigning_the_same_environment_resumes_without_preparing_again(self):
        run = self.two_steps(self.c)
        self.tick()                                     # prepared: ready with a native parent
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='env-s'")
        self.tick()
        self.assertEqual(self.service.run(self.c, run["id"])["error"], "执行环境已停用")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=1 WHERE id='env-s'")
        detail = self.assign(run["id"], "env-s")
        self.assertEqual((detail["status"], detail["native_parent"]), ("ready", "octopus-" + run["id"]))

    def test_moving_to_another_environment_prepares_afresh(self):
        run = self.two_steps(self.c)
        self.tick()
        self.service.change_environment(self.a, "team-p", {"worker_id": self.spare()})
        self.tick()
        detail = self.assign(run["id"], "env-spare")
        self.assertEqual((detail["status"], detail["native_parent"], detail["workspace"]), ("preparing", None, None))

    def test_continue_checks_the_environment(self):
        run = self.start(self.c, team_id="team-p")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='stopped' WHERE id=?", (run["id"],))
            db.execute("DELETE FROM worker_spaces WHERE team_id='team-p'")
        self.service.operate(self.c, run["id"], "continue",
                             {"request_id": "continue-000001", "expected_version": 1, "task_id": "code"})
        detail = self.service.run(self.c, run["id"])
        self.assertEqual((detail["status"], detail["error"]), ("waiting_worker", "执行环境已更换或已移出该空间"))

    def test_recover_refuses_an_environment_that_no_longer_serves(self):
        run = self.start(self.c, team_id="team-p")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked', native_parent='octopus-x' WHERE id=?", (run["id"],))
            db.execute("UPDATE workers SET enabled=0 WHERE id='env-s'")
        self.assertEqual(self.code(self.service.operate, self.c, run["id"], "recover",
                                   {"request_id": "recover-000001", "expected_version": 1}), (409, "environment_changed"))
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_teams_workers.DispatchRecheckTests`，预期旧环境仍被请求 `/prepare`、状态不是 `waiting_worker`。
- [ ] **Step 3: 实现**

`octopus/domain.py`（模块级，`space_workers` 之后）：

```python
def unserved(db, run):
    """Why the run's Worker no longer runs it (spec §6.2): None while it is enabled and still serves the run's space."""
    row = db.execute("SELECT enabled,retired FROM workers WHERE id=?", (run["worker_id"],)).fetchone()
    if row is None or row["retired"]:
        return "执行环境已退役"
    if not row["enabled"]:
        return "执行环境已停用"
    if all(w["id"] != run["worker_id"] for w in space_workers(db, run["owner_id"], run["team_id"])):
        return "执行环境已更换或已移出该空间"
    return None
```

`octopus/engine.py:advance`，在 `if run["desired"] != "active": ... return` 之后、`if not run["worker_enabled"]` 之前：

```python
        if run["status"] in {"preparing", "ready"}:
            # Before preparing or dispatching a new Attempt, the Worker must still serve the run's space (§6.2);
            # an Attempt already running is left to finish.
            with self.db.read() as db:
                reason = unserved(db, run)
            if reason:
                return self.update(rid, "waiting_worker", reason)
```

（`from .domain import unserved`。）

`operate`：
- `continue`：`UPDATE runs SET input=?,desired='active',status=?,error=? WHERE id=?`，其中 `reason = unserved(db, run) if run["worker_id"] else None`，`status = "ready" if run["worker_id"] and not reason else "waiting_worker"`，`error = reason`。
- `recover`：在状态 `require` 之后加

```python
                    reason = unserved(db, run)
                    require(not reason, f"{reason}：请续作后重新分配 Worker", 409, "environment_changed")
```

- `assign-worker`：

```python
                elif action == "assign-worker":
                    require(run["status"] == "waiting_worker" and run["lifecycle"] == "open",
                            "运行已分配 Worker 或已关闭", 409, "invalid_state")
                    # The Session's own space, not the operator's (an admin may assign for someone else).
                    self._worker(db, run["owner_id"], body.get("worker_id"), run["team_id"])
                    if run["worker_id"] not in (None, body["worker_id"]):
                        # Another environment has no native parent of this Session: prepare there afresh (P5).
                        db.execute("UPDATE runs SET worker_id=?,native_parent=NULL,workspace=NULL,status='preparing',"
                                   "error=NULL WHERE id=?", (body["worker_id"], rid))
                    else:
                        db.execute("UPDATE runs SET worker_id=?,status=?,error=NULL WHERE id=?",
                                   (body["worker_id"], "ready" if run["native_parent"] else "preparing", rid))
```

`_attention` 的 `waiting_worker` 分支开头（`where` 之后）：

```python
            if run.get("worker_id"):
                # It ran on an environment that no longer runs it (§6.2).
                title = f"{where}的{run.get('error') or '执行环境不可用'}"
                detail = "已完成的任务保留；换到另一个执行环境时，工作区在新环境中重新准备，原环境中的产出不会迁移。"
                action = ("选择 Worker 后继续执行。" if space["workers"]
                          else "请平台管理员为该空间分配执行环境后重新分配 Worker。")
                return {"title": title, "detail": detail, "action": action, "workers": space["workers"], "task_id": None}
```

- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_teams_workers tests.test_mvp tests.test_recovery tests.test_engine_concurrency tests.test_access`，全部 OK（`test_disabled_worker_keeps_diagnosis_resumable_without_new_cycle` 维持通过：诊断中不受影响）。
- [ ] **Step 5: 提交** — `feat(engine): 派发前核对 Worker 仍服务会话所在空间（已更换 / 已移出 / 已停用 / 已退役时进入等待）；续作同样核对；改派到新环境时重新准备`

### Task 7: 权限矩阵与 OpenAPI

**Files:**
- Modify: `tests/test_access.py:PermissionMatrixTests`、`octopus/schema.py`
- Test: `tests/test_access.py`、`tests/test_teams_workers.py`

**Interfaces:**
- Consumes: Task 2–5 的路由。
- Produces: OpenAPI 中 `PATCH /v1/teams/{id}`（`Rename`）、`POST /v1/teams/{id}/archive`（`TeamArchive`）、`POST /v1/teams/{id}/environment`（`TeamEnvironment`）、`PATCH /v1/workers/{id}`（`Rename`）、`POST /v1/workers/{id}/retire`（`Empty`）；后四个加「执行环境（管理员）」标签；`Token.scopes` 枚举含 `admin`（第 1 期遗漏）。

- [ ] **Step 1: 写失败测试** — 矩阵追加（角色顺序 alice、alice_plain、carol、bob、eve）：

```python
            ("PATCH", "/v1/teams/team-m", {"name": ""}, (400, 404, 400, 403, 404)),
            ("POST", "/v1/teams/team-m/archive", {"archived": "x"}, (400, 403, 403, 403, 403)),
            ("POST", "/v1/teams/team-m/environment", {"worker_id": "env-m"}, (409, 403, 403, 403, 403)),
            ("PATCH", "/v1/workers/env-m", {"name": ""}, (400, 403, 403, 403, 403)),
            ("POST", "/v1/workers/env-m/retire", {}, (409, 403, 403, 403, 403)),
```

`tests/test_teams_workers.py`：

```python
class SchemaTests(unittest.TestCase):
    def test_phase_two_routes_are_documented(self):
        from octopus.schema import openapi
        paths = openapi()["paths"]
        self.assertIn("patch", paths["/v1/teams/{id}"])
        self.assertIn("patch", paths["/v1/workers/{id}"])
        for path in ("/v1/teams/{id}/archive", "/v1/teams/{id}/environment", "/v1/workers/{id}/retire"):
            self.assertEqual(paths[path]["post"].get("tags"), ["执行环境（管理员）"], path)
        self.assertNotIn("tags", paths["/v1/teams/{id}"]["patch"])
        self.assertIn("admin", openapi()["components"]["schemas"]["Token"]["properties"]["scopes"]["items"]["enum"])
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_access.PermissionMatrixTests tests.test_teams_workers.SchemaTests`；矩阵此前已通过 Task 2–5 实现，若矩阵已全部通过则只有 `SchemaTests` 失败（记录在台账中）。
- [ ] **Step 3: 实现** — `schema.py`：`"Rename": obj({"name": text}, ["name"])`、`"TeamArchive": obj({"archived": {"type": "boolean"}}, ["archived"])`、`"TeamEnvironment": obj({"worker_id": text}, ["worker_id"])`；路径 `"/v1/teams/{id}": {"patch": "Rename"}`、`"/v1/teams/{id}/archive": {"post": "TeamArchive"}`、`"/v1/teams/{id}/environment": {"post": "TeamEnvironment"}`、`"/v1/workers/{id}": {"patch": "Rename"}`、`"/v1/workers/{id}/retire": {"post": "Empty"}`；`admin` 集合追加后四者（`PATCH /v1/teams/{id}` 不加，owner 也可调用）；`Token.scopes.items.enum` 加 `"admin"`。若 `openapi()` 的路径生成不认识 `patch`，按现有 `get` / `post` / `delete` 的方式补上。
- [ ] **Step 4: 运行通过** — `.venv/bin/python -m unittest tests.test_access tests.test_teams_workers`，全部 OK。
- [ ] **Step 5: 提交** — `test(access): 权限矩阵覆盖 Team 与 Worker 管理；OpenAPI 记录第 2 期接口`

### Task 8: 控制台「团队与成员」——每个 Team 一页

**Files:**
- Modify: `web/src/features/settings/TeamDialog.tsx`（重写）、`web/src/api/types.ts`（`Team.archived?: boolean | number`）、`web/src/api/client.ts`（`Method` 加 `"PATCH"`）、`web/src/features/console/TopBar.tsx`（归档 Team 标「已归档」）、`web/src/features/launch/steps.tsx` 与 `LaunchWizard.tsx`（不提供归档 Team；目标为归档 Team 时说明原因）、`web/src/features/workflows/ShareDialog.tsx`、`web/src/features/settings/WorkersDialog.tsx`（候选 Team 排除归档）
- Test: `web/src/features/settings/settings.test.tsx`

**Interfaces:**
- Consumes: Task 2–4 的接口：`PATCH /teams/<id> {name}`、`POST /teams/<id>/archive {archived}`、`POST /teams/<id>/environment {worker_id}`、`POST|DELETE /teams/<id>/members`。
- Produces: 对话框结构——管理员「创建 Team」（不变）；「Team」选择器（管理员：全部 Team；其他人：所在 Team；默认当前空间）；所选 Team 一页：名称与改名（owner / 管理员）、执行环境与更换（管理员，二次确认，说明「旧会话不迁移，下次派发时进入等待，由成员或管理员重新分配」）、成员（owner / 管理员：设为 owner / 改为 member、移除；本人：退出 Team，二次确认）、添加成员（owner / 管理员，归档时隐藏）、归档 / 恢复（管理员，二次确认）。「创建平台账户」移到 Task 9 的「用户管理」。

- [ ] **Step 1: 写失败测试**（`settings.test.tsx`；沿用 `start()`、`openSetting()`；把原测试「a Team without an execution environment can be given the shared one / a spare Worker host」的期望改为 `POST /v1/teams/team-1/environment {worker_id}`，删除依赖「创建平台账户」在该对话框中的测试，Task 9 重写）
  - 管理员（不是成员）选择 Team「平台组」，改名为「平台二组」→ `PATCH /v1/teams/team-1 {name:"平台二组"}`。
  - 管理员把成员 bob「设为 owner」→ `POST /v1/teams/team-1/members {username:"bob", role:"owner"}`；「移除」→ `DELETE … {username:"bob"}`。
  - 管理员「更换执行环境」先显示确认文字（含「旧会话不迁移」），确认后 `POST /v1/teams/team-1/environment {worker_id:"w2"}`。
  - 管理员「归档」确认后 `POST /v1/teams/team-1/archive {archived:true}`；归档的 Team 显示「已归档」、没有「添加 / 更新」、按钮变为「恢复」。
  - 普通成员（`role: "member"`）看到自己行的「退出 Team」，确认后 `DELETE /v1/teams/team-1/members {username:"fixture-owner"}`；看不到改名、移除他人、归档、更换执行环境。
  - 顶栏空间切换中归档 Team 显示「· 已归档」；新建会话向导的空间选项中没有归档 Team。
- [ ] **Step 2: 运行确认失败** — `cd web && npx vitest run src/features/settings`。
- [ ] **Step 3: 实现** — 沿用 `FormDialog`、`NativeSelect`、`Button`、`Input`、`act()` 模式（失败显示服务端消息）；危险操作（更换环境、归档、移除、退出）使用与 `UsersDialog` 相同的行内确认条；`canManage = elevated(me) || team.role === "owner"`、`canHost = elevated(me)`（`@/lib/access`）；更换环境的候选为启用的共享环境与空闲环境、排除当前环境；归档判断 `!!team.archived`。
- [ ] **Step 4: 运行通过** — `cd web && npx vitest run && npm run build`。
- [ ] **Step 5: 提交** — `feat(console): 「团队与成员」每个 Team 一页——改名、归档 / 恢复、成员与角色、退出 Team、管理员更换执行环境；归档 Team 不出现在提交与共享选择器中`

### Task 9: 控制台「执行环境」与「用户管理」

**Files:**
- Modify: `web/src/features/settings/WorkersDialog.tsx`（管理员：改名、更新连接、退役）、`web/src/features/settings/UsersDialog.tsx`（创建平台账户及其个人空间执行环境；每个用户显示个人空间执行环境，未分配时「分配共享环境」）
- Test: `web/src/features/settings/settings.test.tsx`

**Interfaces:**
- Consumes: Task 4–5 的接口：`PATCH /workers/<id> {name}`、`POST /workers/<id>/connection {connection:{url, token}}`、`POST /workers/<id>/retire`；现有 `POST /users`、`POST /workers/<id>/spaces {user_id}`。
- Produces: Worker 行的「改名」（行内输入）、「更新连接」（URL + Token 表单，说明「用于主机替换或 Token 轮换；执行边界变化后该环境可分配给其他 Team」）、「退役」（确认条说明「退役后不再出现在列表与选择器中；仍是 Team 的环境或有进行中的会话时会被拒绝」）；「用户管理」顶部「创建平台账户」。

- [ ] **Step 1: 写失败测试**
  - 管理员在 Worker 行改名 → `PATCH /v1/workers/w1 {name:"共享-1"}`。
  - 「更新连接」填 URL 与 Token → `POST /v1/workers/w1/connection {connection:{url, token}}`；Token 输入为 `type=password`。
  - 「退役」确认后 → `POST /v1/workers/w1/retire`；服务端 409 时 toast 显示服务端消息。
  - 非管理员看不到改名、更新连接、退役，仍能「刷新模型」。
  - 「用户管理」中创建用户 → `POST /v1/users {username, password, worker_id}`（选择共享环境时）；「团队与成员」中不再有「创建用户」。
- [ ] **Step 2: 运行确认失败** — `cd web && npx vitest run src/features/settings`。
- [ ] **Step 3: 实现** — 沿用对话框现有模式；`UsersDialog` 从 `useConsole()` 取 `workers` 与 `spaceWorkers`，创建后失效 `["admin","users"]` 与 `keys.users`、`keys.workers`。
- [ ] **Step 4: 运行通过** — `cd web && npx vitest run && npm run build`。
- [ ] **Step 5: 提交** — `feat(console): 执行环境改名、更新连接与退役；创建平台账户并入「用户管理」`

### Task 10: 文档、回归与 EC2 验收

**Files:**
- Modify: `docs/OPERATIONS.md`（Team 管理、更换执行环境与旧会话的等待、Worker 退役与空闲主机复用规则 P2）、`docs/MVP-RELEASE.md`（D33：访问管理第 2 期；§14.7 验收记录）、`docs/BACKLOG.md`（移除第 1 期遗留的托管与审计条目；记录延后项）、`skills/kirocrew-on/SKILL.md`（`waiting_worker` 的原因：执行环境已更换 / 已移出 / 已停用 / 已退役，以及 `team_archived`）、`docs/ARCHITECTURE.md`（§6 派发前核对一行）

- [ ] **Step 1:** `PYTHON=$PWD/.venv/bin/python tools/regress.sh` 全部通过。
- [ ] **Step 2:** 经 SSM 只读查询确认 EC2 没有进行中的会话后，`deploy.sh push`（迁移 4，备份文件名记入验收记录）；`deploy.sh worker-host push w1`。
- [ ] **Step 3:** 验收（管理员 Token 只从本机 0600 文件读取；测试用户与临时 Token 用后停用 / 吊销），结果记入 `docs/MVP-RELEASE.md` §14.7：
  1. 管理员在共享环境上创建测试 Team「e2e-组」，owner 为 `e2e-member`（恢复第 1 期的测试用户，验收后停用）；改名为「e2e-二组」，审计中有 `team.create`、`team.rename`。
  2. 管理员（不是成员）为该 Team 添加自己为 member、再移除；`e2e-member` 不能移除自己（唯一 owner，409）；管理员把自己设为 owner 后 `e2e-member` 可以退出。
  3. 归档后提交得 409 `team_archived`、添加成员得 409；恢复后可提交。
  4. 派发前核对（**会执行任务，提交前先请负责人确认**）：在该 Team 提交一个单任务会话，进入执行后立即停止；把该 Team 移出共享环境；续作 → `waiting_worker`，控制台显示「执行环境已更换或已移出该空间」；`POST /v1/teams/<id>/environment` 重新给共享环境，分配原 Worker → 状态 `ready` 且保留原生父会话；随后关闭。
  5. 「执行环境」对话框中 `personal` 与 `w1` 可改名再改回；对 `w1`（Web 应用软件研发组的环境）退役得 409 `in_use`；普通用户可以刷新模型。
  6. 归档测试 Team、停用 `e2e-member`，记录到验收记录。
- [ ] **Step 4:** 向负责人报告；确认后推送 `feat/m4-teams-workers`（快进开发分支）。
