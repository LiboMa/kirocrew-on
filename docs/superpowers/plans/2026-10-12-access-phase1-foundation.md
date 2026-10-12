# 访问管理 第 1 期「权限底座」 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `admin` scope 并统一 403 / 404 语义；管理员可查看并操作全部会话（原生入口除外）且留下审计；用户可改密码，管理员可重置、停用 / 恢复、升降管理员与吊销他人 Token；Token 记录最近使用并清理过期；预览链接 10 分钟并按用户复核权限。

**Architecture:** 规则只落在 Octopus 的 API 与 Service 层（`docs/ARCHITECTURE.md` §6）：`server.required_scope()` 决定路由需要的 scope，`security.elevated()` 判断「管理员且 Token 含 admin」，`domain.run_access()` 统一会话可见性（看不到 404、看得到不能做 403）并在管理员越权时放行，`domain.audit()` 记录管理与越权操作。迁移 3 增加审计表与最近登录 / 使用时间。

**Tech Stack:** Python 3 标准库、SQLite、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-12-access-management-design.md`（§3、§4、§9 B1–B6、§10 第 1 期）

## Global Constraints

- 不新增依赖；面向用户的文字使用中文，风格与现有控制台一致。
- Token、密码只出现在请求体与哈希中，不写入日志、审计或事件。
- 迁移 3 走现有迁移框架（迁移前一致性备份）；`tokens` 的 `INSERT` 改为显式列名（加列后按位置插入会失效）。
- 原生 KiroCrew 入口不因管理员身份放开（A2）：`crew_entry` 不使用管理员越权。
- 提交规则（负责人 CLAUDE.md）：每个任务在功能分支 `feat/m4-access` 本地提交；EC2 验收完成并经负责人确认后才推送。
- Python 测试：`.venv/bin/python -m unittest <模块>`；完整回归：`PYTHON=…/.venv/bin/python tools/regress.sh`。

## Review Focus

1. 管理员已签发、不含 `admin` 的长期 Token（如 `claude MCP`：read + execute）：管理接口返回 403，但提交、查看、观察本人会话不受影响；`GET /v1/workers` 只返回其所在空间的 Worker（Task 2 测试 `test_admin_token_without_admin_scope_is_an_ordinary_user`）。
2. `bootstrap-worker` 与 `deploy.sh worker-host register` 经登录取得 scope：登录必须给管理员 `admin`，否则登记 Worker 失败（Task 2 测试 `test_admin_login_includes_admin_scope`；`tests/test_bootstrap.py` 全部通过）。
3. 把「看得到但不能做」从 404 改为 403：控制台与 MCP 显示服务端给出的原因；现有期望 404 的测试逐条确认后改为 403（Task 3 测试 `test_member_cannot_delete_someone_elses_team_session_and_is_told_why`）。
4. `last_used` 不能让每个读请求都写库：同一 Token 60 秒内只更新一次，写入用独立的短事务（Task 6 测试 `test_last_used_is_written_at_most_once_a_minute`）。
5. 停用一个有进行中会话的用户：其 Token 立即失效、不能登录，会话继续执行，管理员可以停止（Task 5 测试 `test_deactivation_revokes_tokens_but_leaves_sessions_running`）。

---

### Task 1: 迁移 3（审计表、最近登录与使用时间）

**Files:**
- Modify: `octopus/migrations.py`（`MIGRATIONS`）、`octopus/security.py:issue_token`（显式列名）
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `users.last_login REAL`、`tokens.last_used REAL`；表 `audit(id, at, actor_id, action, kind, target, detail)`，索引 `audit_at`。

- [ ] **Step 1: 写失败测试**

```python
    def test_access_migration_adds_audit_and_activity_columns(self):
        self.legacy_database()          # existing helper: a schema-1 database with data
        result = migrations.apply(self.path, backup_dir=self.backups)
        self.assertEqual(result["to"], 3)
        with sqlite3.connect(self.path) as db:
            self.assertIn("last_login", [c[1] for c in db.execute("PRAGMA table_info(users)")])
            self.assertIn("last_used", [c[1] for c in db.execute("PRAGMA table_info(tokens)")])
            db.execute("INSERT INTO audit(at,actor_id,action,kind,target) VALUES(1,'u1','user.deactivate','user','u2')")
            self.assertEqual(db.execute("SELECT detail FROM audit").fetchone()[0], "{}")
```

- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m unittest tests.test_migrations`，预期 `to` 为 2。
- [ ] **Step 3: 实现**

```python
ACCESS = """
ALTER TABLE users ADD COLUMN last_login REAL;
ALTER TABLE tokens ADD COLUMN last_used REAL;
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
 action TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
"""
MIGRATIONS = [(1, "baseline", BASELINE), (2, "environments", ENVIRONMENTS), (3, "access", ACCESS)]
```

`issue_token` 的插入改为 `INSERT INTO tokens(hash,id,user_id,scopes,expires,label) VALUES(?,?,?,?,?,?)`。
- [ ] **Step 4: 运行通过** — `tests.test_migrations tests.test_backup tests.test_mvp` 全部 OK。
- [ ] **Step 5: 提交** — `git commit -m "feat(access): 迁移 3——审计表、最近登录与 Token 最近使用时间"`

### Task 2: `admin` scope 与路由门禁

**Files:**
- Modify: `octopus/security.py`（`SCOPES`、`issue_token`、`elevated`）、`octopus/server.py`（登录、`required_scope`、替换 `actor["admin"]`）、`octopus/domain.py`（`teams`、`workers`、`create_team`、`share_worker`、`worker_space`、`dedicate_worker`、`environments` 的管理员判断）
- Test: `tests/test_access.py`（新）、`tests/test_bootstrap.py`

**Interfaces:**
- Produces: `security.SCOPES = ("read", "execute", "manage", "admin")`；`security.elevated(actor) -> bool`（`actor["admin"]` 且 `"admin" in actor["scopes"]`）；`server.required_scope(method: str, path: str) -> str`。
- 登录：管理员 `["read","execute","manage","admin"]`，其他用户 `["read","execute","manage"]`。签发 Token 时请求的 scope ⊆ 当前 Token 的 scope（现有规则）。

- [ ] **Step 1: 写失败测试**

```python
class ScopeTests(unittest.TestCase):
    def test_required_scope_by_route(self):
        cases = {("GET", "/v1/runs"): "read", ("POST", "/v1/runs"): "execute", ("POST", "/v1/runs/r/stop"): "execute",
                 ("POST", "/v1/workflows"): "manage", ("POST", "/v1/me/password"): "manage",
                 ("POST", "/v1/users"): "admin", ("POST", "/v1/users/u/active"): "admin", ("POST", "/v1/teams"): "admin",
                 ("POST", "/v1/workers"): "admin", ("POST", "/v1/workers/w/share"): "admin",
                 ("GET", "/v1/admin/environments"): "admin", ("GET", "/v1/admin/users"): "admin",
                 ("GET", "/v1/workers"): "read", ("POST", "/v1/workflows/resolve"): "read",
                 ("POST", "/v1/runs/r/preview"): "read", ("DELETE", "/v1/tokens/t"): "manage"}
        for (method, path), expected in cases.items():
            self.assertEqual(server.required_scope(method, path), expected, (method, path))

    def test_admin_login_includes_admin_scope(self): ...        # POST /v1/login as admin → scopes include admin; as bob → not
    def test_admin_token_without_admin_scope_is_an_ordinary_user(self): ...
        # issue_token(alice, ["read","execute"]): GET /v1/admin/environments → 403 "Token 缺少 admin scope";
        # POST /v1/teams → 403; GET /v1/workers → only Workers of alice's spaces; GET /v1/teams → only her Teams;
        # POST /v1/runs in her personal space → 201
    def test_non_admin_cannot_mint_admin_scope(self): ...        # bob POST /v1/tokens {"scopes":["admin"]} → 403
```

- [ ] **Step 2: 运行确认失败** — `tests.test_access`：`required_scope` 不存在。
- [ ] **Step 3: 实现**

```python
# security.py
SCOPES = ("read", "execute", "manage", "admin")

def elevated(actor):
    """Platform administration needs both: an admin account and a token that carries the admin scope (A3)."""
    return bool(actor.get("admin")) and "admin" in actor.get("scopes", ())

# server.py
READ_POSTS = {"/v1/workflows/resolve", "/v1/workflows/parse"}

def required_scope(method, path):
    segments = path.strip("/").split("/")
    if path.startswith("/v1/admin/") or (segments[:2] == ["v1", "workers"] and method != "GET") or \
            (segments[:2] == ["v1", "users"] and (method != "GET" or len(segments) > 2)) or \
            (path == "/v1/teams" and method == "POST"):
        return "admin"
    if method == "GET" or path in READ_POSTS or path.endswith("/preview"):
        return "read"
    return "execute" if path.startswith(("/v1/runs", "/v1/designs")) else "manage"
```

`issue_token` 校验改为 `set(scopes) <= set(SCOPES)`；登录 `issue_token(db, user["id"], ["read","execute","manage"] + (["admin"] if user["admin"] else []), label="Web login")`；
`scope(actor, required_scope(method, path))` 取代 `server.py:225-227`，缺 `admin` 时消息为「Token 缺少 admin scope（需要管理员以含 admin 的 Token 调用）」。
平台管理处的 `actor["admin"]` 全部改为 `elevated(actor)`（`server.py` 的 Worker 登记 / 连接 / 启停、`/v1/users` POST；`domain.py` 的 `teams`、`workers`、`create_team`、`share_worker`、`worker_space`、`dedicate_worker`、`environments`）。
- [ ] **Step 4: 运行通过** — `tests.test_access tests.test_mvp tests.test_bootstrap tests.test_crew_entry tests.test_observe` 全部 OK（测试夹具中的管理员 Token 需含 `admin`：`issue_token(db, user["id"])` 对管理员默认加 `admin`，即 `scopes=None` 时按用户身份决定）。
- [ ] **Step 5: 提交** — `feat(access): admin scope——管理接口需要管理员且 Token 含 admin；登录为管理员签发 admin`

### Task 3: 403 / 404 语义与会话的 `permissions`

**Files:**
- Modify: `octopus/domain.py`（`run_access`、`workflow_access`、`member`、`start` 的检查顺序、`_view`）、`octopus/crew_entry.py`、`octopus/designs.py`
- Test: `tests/test_access.py`、受影响的 `tests/test_mvp.py` 用例

**Interfaces:**
- Produces: `run_access(db, actor: dict, rid, owner=False, admin=True) -> dict`（`actor` 取代 `user_id`）；会话视图增加 `permissions: {"operate": bool, "delete": bool}`。
- Consumes: Task 2 的 `elevated`。

- [ ] **Step 1: 写失败测试**

```python
    def test_member_cannot_delete_someone_elses_team_session_and_is_told_why(self):
        # alice (owner) and bob (member) in a Team; alice's Team run; bob POST /runs/<id>/delete → 403 with
        # "只有 Team owner 可以删除他人创建的会话"; eve (not a member) → 404; bob GET run → permissions.delete False
    def test_viewer_cannot_edit_a_workflow_and_is_told_why(self):   # viewer grant → save version 403; no grant → 404
    def test_non_member_does_not_learn_a_team_name(self):           # eve POST /v1/runs team_id=<Team without env> → 404
```

- [ ] **Step 2: 运行确认失败**（当前分别为 404、404、409 `team_without_worker` 且消息含 Team 名）。
- [ ] **Step 3: 实现**

```python
def run_access(db, actor, rid, owner=False, admin=True):
    """The run if the actor may see it (404 otherwise, never revealing it exists). owner=True also requires the
    right to delete it (403 with the reason). An elevated admin sees and operates every run unless admin=False."""
    row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    require(row is not None, "Session 不存在或无权限", 404, "not_found")
    if admin and elevated(actor):
        return dict(row)
    if row["team_id"]:
        role = db.execute("SELECT role FROM members WHERE team_id=? AND user_id=?", (row["team_id"], actor["id"])).fetchone()
        require(role is not None, "Session 不存在或无权限", 404, "not_found")
        require(not owner or role["role"] == "owner" or row["owner_id"] == actor["id"],
                "只有 Team owner 可以删除他人创建的会话", 403, "forbidden")
    else:
        require(row["owner_id"] == actor["id"], "Session 不存在或无权限", 404, "not_found")
    return dict(row)
```

`workflow_access`：完全无权 → 404；有权但低于要求 → 403「需要 {role} 权限」。`member`：非成员 → 404「Team 不存在或无权限」；成员缺 owner → 403。
`start` 在环境检查前先调 `member(db, actor["id"], team)`。`_view(db, run, actor, detail)` 计算 `permissions`（`delete` 用 `run_access(..., owner=True)` 是否抛 403）。
全部调用点由 `actor["id"]` 改为 `actor`；`crew_entry.create` 与其复核处用 `admin=False`。
- [ ] **Step 4: 运行通过** — `tests.test_access tests.test_mvp tests.test_crew_entry tests.test_observe tests.test_designs`；对因语义改变而失败的旧用例，逐条确认属于「看得到不能做」后把期望改为 403，并在提交说明中列出。
- [ ] **Step 5: 提交** — `feat(access): 看不到返回 404、看得到不能做返回 403 并说明原因；会话视图给出 permissions`

### Task 4: 管理员查看并操作全部会话，并留下审计

**Files:**
- Modify: `octopus/domain.py`（`audit()`、`runs(actor, all=False)`、`operate` 的 assign-worker 空间与审计）、`octopus/server.py`（`GET /v1/runs?all=1`、`GET /v1/admin/audit`）
- Test: `tests/test_access.py`

**Interfaces:**
- Produces: `domain.audit(db, actor, action, kind, target, detail=None)`；`Service.runs(actor, all=False)`；`Service.audit_log(actor, before=None, limit=50)`。

- [ ] **Step 1: 写失败测试**

```python
    def test_admin_reads_and_operates_every_session_but_not_its_native_entry(self):
        # bob's personal run: alice (elevated) GET /runs/<id> 200, events 200, observe passes access, stop → accepted;
        # crew-entry → 404; audit has {"action": "run.stop", "kind": "run", "target": rid, actor alice};
        # the run's command event payload names the actor
    def test_admin_lists_all_sessions_only_on_request(self):        # GET /v1/runs → own; ?all=1 → bob's too; bob ?all=1 → own only
    def test_admin_assigns_a_worker_from_the_sessions_own_space(self):  # bob's waiting run + bob's shared env → alice assign 200
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**

```python
def audit(db, actor, action, kind, target, detail=None):
    db.execute("INSERT INTO audit(at,actor_id,action,kind,target,detail) VALUES(?,?,?,?,?,?)",
               (time.time(), actor["id"], action, kind, target, dumps(detail or {})))
```

`operate`：越权（`elevated(actor)` 且非本人 / 非成员）时 `audit(db, actor, "run." + action, "run", rid)`；assign-worker 用 `self._worker(db, run["owner_id"], worker, run["team_id"])`（按会话所属空间）。
`runs(actor, all=False)`：`all and elevated(actor)` 时返回全部，否则保持本人与所在 Team。`GET /v1/admin/audit?before=&limit=`（admin）。
- [ ] **Step 4: 运行通过**。
- [ ] **Step 5: 提交** — `feat(access): 管理员可查看并操作全部会话（原生入口除外），越权操作写入审计`

### Task 5: 用户生命周期

**Files:**
- Modify: `octopus/domain.py`（`Service.change_password`、`reset_password`、`set_active`、`set_admin`、`admin_users`、`user_tokens`、`revoke_user_token`；`revoke_tokens()`）、`octopus/server.py`（路由，登录写 `last_login`）、`octopus/schema.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Produces：`POST /v1/me/password {current, password}`；`POST /v1/users/<id>/password {password}`、`/active {active}`、`/admin {admin}`；`GET /v1/admin/users`；`GET /v1/admin/users/<id>/tokens`、`DELETE /v1/admin/users/<id>/tokens/<tid>`。

- [ ] **Step 1: 写失败测试**

```python
    def test_changing_my_password_revokes_my_other_tokens(self): ...      # wrong current → 403; new works; other token 401; this one 200
    def test_admin_resets_a_password_and_every_token_of_that_user_stops(self): ...
    def test_deactivation_revokes_tokens_but_leaves_sessions_running(self): ...
        # bob deactivated: his token 401, login 401, his running run keeps status, alice can stop it; reactivate → login ok
    def test_last_active_owner_and_last_admin_are_protected(self): ...    # deactivate sole owner → 409 lists the Team; demote last admin → 409
    def test_admin_user_list_and_token_revocation(self): ...              # fields; revoking bob's token → it 401s; audited
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 要点：
  - `revoke_tokens(db, user, keep=None)`：`DELETE FROM tokens WHERE user_id=? AND id IS NOT ?`。
  - `set_active(False)`：拒绝时机——该用户是某 Team 唯一的 `active` owner（`409 last_owner`，消息列出 Team 名）；成功后 `revoke_tokens`，`audit("user.deactivate")`。
  - `set_admin(False)`：停用或降级后若有效管理员数为 0 → `409 last_admin`。
  - `admin_users`：`id, username, admin, active, created, last_login, teams:[{id,name,role}], tokens: <未过期数>`。
  - 登录成功：`UPDATE users SET last_login=?`；删除全部已过期 Token（`DELETE FROM tokens WHERE expires<?`）。
  - 不提供删除用户（B2）；MCP 不涉及（B7，第 3 期）。
- [ ] **Step 4: 运行通过**。
- [ ] **Step 5: 提交** — `feat(access): 用户生命周期——改密码、重置、停用与恢复、升降管理员、管理员用户与 Token 管理`

### Task 6: Token 卫生

**Files:**
- Modify: `octopus/security.py:authenticate`（返回 `last_used`）、`octopus/server.py`（`identity` 后写 `last_used`；Token 列表；DELETE 允许无请求体）
- Test: `tests/test_access.py`

- [ ] **Step 1: 写失败测试**

```python
    def test_last_used_is_written_at_most_once_a_minute(self): ...   # two calls within 60 s → one write; clock +61 → second write
    def test_token_list_hides_expired_and_shows_last_used(self): ...
    def test_delete_needs_no_body(self): ...                          # DELETE /v1/tokens/<id> without Content-Type/body → 200
```

- [ ] **Step 2: 运行确认失败**（第三项为 400「需要 application/json」）。
- [ ] **Step 3: 实现** — `identity()`：认证后若 `last_used is None or now - last_used >= 60`，另开 `database.transaction()` 执行 `UPDATE tokens SET last_used=? WHERE id=?`；
  请求体解析：`DELETE` 且 `Content-Length` 为 0 时 `body = {}`、不要求 `Content-Type`（跨站 DELETE 本身需要预检，`Origin` 校验保留）；
  `GET /v1/tokens` 加 `AND expires>?` 并返回 `last_used`。
- [ ] **Step 4: 运行通过**。
- [ ] **Step 5: 提交** — `feat(access): Token 最近使用时间（每分钟最多写一次）、列表隐藏过期、删除不需要请求体`

### Task 7: 预览链接 10 分钟并按用户复核

**Files:**
- Modify: `octopus/server.py`（`PREVIEW_TTL`、`preview_ticket`、`preview_run`、预览路由）、`octopus/domain.py:preview_file`、`skills/kirocrew-on/SKILL.md`、`octopus/mcp.py`（`octopus_preview` 描述）
- Test: `tests/test_preview.py`（现有预览测试所在模块；若在 `tests/test_mvp.py` 则加在原处）

**Interfaces:**
- Produces: 票据 `"{rid}.{user_id}.{expires}.{sig}"`；`preview_run(ticket, key) -> (rid, user_id)`；`Service.preview_file(rid, relative, user_id)`。

- [ ] **Step 1: 写失败测试** — 票据 10 分钟过期；用户被移出 Team 后同一票据取文件 → 403 `preview_forbidden`；旧格式（3 段）票据 → 403 `preview_invalid`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `PREVIEW_TTL = 600`；`preview_file` 读取该用户（`active=1`），以 `{"id", "admin", "scopes": ["read"] + (["admin"] if admin else [])}` 调 `run_access`，失败统一 403 `preview_forbidden`「预览权限已撤销，请在控制台重新打开」。Skill 与 MCP 描述中的「1 小时」改为「10 分钟」。
- [ ] **Step 4: 运行通过**。
- [ ] **Step 5: 提交** — `feat(access): 预览链接 10 分钟有效，每次取文件按用户复核会话权限`

### Task 8: 控制台

**Files:**
- Create: `web/src/features/settings/UsersDialog.tsx`、`web/src/features/settings/PasswordDialog.tsx`
- Modify: `web/src/api/types.ts`（`Run.permissions`、`AdminUser`、`ApiToken.last_used`、`Me.scopes` 含 `admin`）、`web/src/features/console/*`（设置菜单「用户管理」、账户菜单「修改密码」、侧栏管理员「全部会话」切换）、`web/src/lib/session.ts`（`canDelete` 等改用 `permissions`）、`web/src/features/settings/TokensDialog.tsx`
- Test: `web/src/features/settings/settings.test.tsx`、`web/src/features/sessions/SessionView.test.tsx`、侧栏测试

- [ ] **Step 1: 写失败测试**
  - 管理员打开「用户管理」：列表显示用户名、管理员、有效、最近登录、所在 Team；「停用」后发出 `POST /users/<id>/active {active:false}`；「重置密码」要求两次输入一致并 ≥ 12 字符后发出请求；「Token」展开后可吊销（`DELETE /admin/users/<id>/tokens/<tid>`）。
  - 非管理员看不到「用户管理」。
  - 「修改密码」：原密码 + 新密码两次一致后发出 `POST /me/password`；服务端 403 时显示「原密码不正确」。
  - 会话 `permissions.delete=false` 时「删除会话」停用并说明「只有 Team owner 可以删除他人创建的会话」。
  - 管理员侧栏「全部会话」开启后请求 `GET /runs?all=1`；非管理员没有该开关。
  - Token 列表显示「最近使用」。
- [ ] **Step 2: 运行确认失败** — `npx vitest run src/features/settings src/features/sessions src/features/console`。
- [ ] **Step 3: 实现** — 沿用 `FormDialog`、`NativeSelect`、`Button`；用户列表用 `useQuery(["admin","users"], () => api<AdminUser[]>("/admin/users"), { enabled: admin })`；危险操作（停用、降级、吊销）二次确认；所有失败用 toast 显示服务端消息。现有「团队与成员」中的创建用户保留，第 2 期再整合。
- [ ] **Step 4: 运行通过** — `npx vitest run` 全部、`npm run build`。
- [ ] **Step 5: 提交** — `feat(console): 用户管理、修改密码、管理员全部会话、按权限停用操作并说明原因`

### Task 9: 文档、回归与 EC2 验收

**Files:**
- Modify: `docs/OPERATIONS.md`（用户管理、admin scope 迁移说明）、`docs/MVP-RELEASE.md`（D31：访问管理第 1 期；§14.4）、`docs/ARCHITECTURE.md` §6（scope 一行）、`skills/kirocrew-on/SKILL.md`（403 的含义）

- [ ] **Step 1:** `tools/regress.sh` 全部通过。
- [ ] **Step 2:** 确认 EC2 无进行中的会话后 `deploy.sh push`（迁移 3，备份文件名记录到验收记录）。
- [ ] **Step 3:** 验收（结果记入 `docs/MVP-RELEASE.md` §14）：
  1. 管理员登录 Token 含 `admin`；现有 `claude MCP` Token 仍能查询与观察本人会话，调管理接口得 403。
  2. 创建专用测试用户 `e2e-member`（密码只写入本机 0600 临时文件），不分配执行环境；以其身份提交一个会话（停在 `waiting_worker`，不执行），调 `GET /v1/admin/users` 得 403。
  3. 管理员 `GET /v1/runs?all=1` 能看到该会话；关闭它成功，审计中有 `run.close`；其原生入口对管理员返回 404。
  4. 管理员重置 `e2e-member` 密码 → 其旧 Token 401；停用 → 登录 401；`GET /v1/admin/users` 显示停用。
  5. 管理员本人修改密码后其他登录 Token 失效（验收结束前把密码改回原值，原密码文件 `/etc/kirocrew-on/admin-password` 不变）。
- [ ] **Step 4:** 向负责人报告；确认后推送 `feat/m4-access`（快进开发分支），并按确认进入第 2 期。
