# M4-1 会话共享给个人 / Team / 全公司 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话可共享给某人 / 某 Team / 全公司（viewer 或 collaborator）；读写入口按角色放行，取消立即生效。

**Architecture:** 迁移 12 的 `shares` 表与 `octopus/shares.py`；`domain.run_access` 增加 `read`（读入口接受任何共享）与 `shares`（False 时排除一切
共享）两个参数，**默认按写**：只有 collaborator 共享能过；每个读入口显式 `read=True`。collaborator 等同普通 Team 成员，`may_publish` 与
`directs`（D49 R10）不变。迁移器改为执行缺失的编号。

**Tech Stack:** Python 3.12 标准库、SQLite；React + TanStack Query。

**Spec:** `docs/superpowers/specs/2026-10-22-m4-session-sharing-design.md`（D64）

## Global Constraints

- 角色：`viewer` / `collaborator`；`everyone` 只能 `viewer`（400）。
- 删除（`owner=True`）与原生入口（`crew_entry.create`）对任何共享都拒绝：403「共享的会话不能删除」/「共享的会话不能打开原生 KiroCrew 入口」。
- viewer 做写操作：403「你以查看身份访问此会话，不能操作」。看不到：404 照旧。
- 共享 / 取消：创建者、Team owner（Team 会话）、平台管理员；审计 `share.create` / `share.delete`；已归档 Team 的会话不能新增共享（409 `team_archived`）。
- Team 共享只能选共享人所在、未归档的 Team（管理员任意未归档）；个人共享按 `username`，不存在或停用 404。
- D55 Request 不因共享可见（`_readable` 用 `shares=False`）；默认会话列表不含共享来的，`shared=1` 时只含共享来的。

## Review Focus

- **漏标的读入口**：viewer 被拒（不是放出写权限）——Task 2 的矩阵测试逐个入口覆盖。
- **同时是 Team 成员又被共享为 viewer**：仍按成员（完整）权限，共享不降权（Task 1 测试）。
- **取消共享时打开着的事件流**：一秒内 `access_revoked`（Task 2 测试）。
- **共享给 Team 后新加入的成员**：立即可见；被移出的立即不可见（Task 1 测试）。
- **迁移器**：数据库已到 12 而缺 11 时，11 仍被执行；数据库版本高于程序时照旧拒绝启动（Task 1 测试）。

---

### Task 1: 共享的存储、访问判定与接口

**Files:** Modify `octopus/migrations.py`（`apply` 执行缺失编号；迁移 12 `shares`）；Create `octopus/shares.py`；Modify `octopus/domain.py`
（`run_access`、`access_of`、`Service.shares/share/unshare`）、`octopus/server.py`（`/v1/runs/<id>/shares[/<sid>]`）、`octopus/schema.py`；
Test `tests/test_sharing.py`

**Interfaces — Produces:**
- `run_access(db, actor, rid, owner=False, admin=True, read=False, shares=True) -> dict`（行）
- `domain.access_of(db, actor, run) -> {"via": "owner"|"team"|"share"|"admin", "role"?: str, "by"?: str}`
- `shares.best(db, actor, run) -> sqlite3.Row | None`（个人 > Team > 全公司中角色最高的一条）
- `Service.shares(actor, rid) -> list`、`Service.share(actor, rid, body) -> dict`、`Service.unshare(actor, rid, sid) -> dict`

- [ ] 测试：迁移器补跑缺失的 11；迁移 12 建表；三种对象 × 两种角色的读 / 写 / 删除 / 原生判定；everyone 只能 viewer；成员不因 viewer 共享降权；Team 新成员可见、移出即不可见；
  共享与取消的权限、审计、已归档、未知用户 / 非本人 Team；同一对象再次共享即改角色；HTTP。
- [ ] 失败；实现；通过（含 `tests.test_migrations tests.test_access`）；提交。

### Task 2: 每个入口按读写放行；列表与视图

**Files:** Modify `octopus/domain.py`（逐个调用点；`operate` 各动作；`run_permissions`；`runs(shared=...)`；`_view` 的 `access`；`_readable`）、
`octopus/crew_entry.py`、`octopus/designs.py`、`octopus/listing.py`、`octopus/server.py`（`shared` 查询参数）；Test `tests/test_sharing.py`

**Interfaces — Consumes:** Task 1 的 `run_access(read=, shares=)`、`access_of`。**Produces:** `Service.runs(..., shared=False)`；会话视图 `access`；`permissions.share`。

- [ ] 测试：viewer 能读的入口（run、events、revisions、revision、revision_diff、changes、attempt_diff、approvals 列表、archive / 导出、app 状态与预览文件、
  bundle_entry、designs.status、crew_entry.observe、placement）全部 200；写入口（operate 的 continue / recover / stop / close / reopen / confirm、sync、app_start / app_stop）
  viewer 403、collaborator 与成员相同；删除、原生入口、publish、steer、answer_approval、accept、assign-worker、rebind、forget_knowledge、set_publish_policy 对共享一律拒绝；
  取消后 404、事件流关闭；`shared=1` 视图；默认列表不含共享来的；`permissions` 与 `access`。
- [ ] 失败；实现；通过（含 `tests.test_access tests.test_team_sessions tests.test_requests` 等访问相关模块）；提交。

### Task 3: MCP 与控制台

**Files:** Modify `octopus/mcp.py`（`octopus_runs` 的 `shared`、`octopus_status` 的 `access`）、`skills/kirocrew-on/SKILL.md`；控制台：会话页「共享」对话框、
「共享给我」视图、访问身份提示、按 `permissions` 隐藏按钮；Test MCP 用例、`web/src/features/sessions/ShareDialog.test.tsx` 等

- [ ] 测试：MCP 参数与字段；对话框列出 / 添加（个人按用户名、本人所在 Team、全公司仅查看）/ 取消；viewer 看不到操作按钮；「共享给我」视图。
- [ ] 失败；实现；通过（`npx vitest run`、`npx tsc --noEmit -p .`）；提交。

### Task 4: 文档、回归、终审、部署

- [ ] OPERATIONS（共享一节：角色、谁能、取消、收不回的）、MVP-RELEASE、progress（m4-org）；全量回归；终审；部署（先通知 D57 会话）；EC2：把一个会话共享给全公司与某个 Team，
  核对共享列表、视图、审计，取消后核对；他人账号的核对留给负责人。
