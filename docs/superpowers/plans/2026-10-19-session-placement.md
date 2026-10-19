# 会话的执行位置（D49 第 2 期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Team 会话在提交者自己的边界执行（没有时回退到 Team 的环境并提示），默认位置由服务端统一回答，客户端不再各自选择。

**Architecture:** `domain.space_workers` 仍是「某空间对某人可用哪些环境」的唯一实现，Team 分支加上成员本人的专属边界；新的 `domain.placement(db, user, team)`
给出默认选择、原因与提示，`start` 与 `GET /v1/placement` 共用；会话 snapshot 记 `placement`。控制台向导与分配对话框、MCP `octopus_environments`
改读 `/v1/placement`。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-19-session-placement-design.md`

## Global Constraints

- `placement` 取值：`own`（本人的边界）、`team`（Team 的环境）、`only`（空间唯一的环境）、`requested`（客户端指定）。
- 回退提示（R5，原文）：「你还没有自己的执行边界：本次在 Team 的环境执行，与其他成员共用并发与 kiro 额度；可请平台管理员为你建立（deploy.sh boundary add <名称> --owner <用户名>）」。
- 「服务」按会话创建者判断（R3）；原生入口按操作者判断（R7，现有规则不改）。
- 客户端仍可传 `worker_id`（R9）；已有会话不迁移。
- `GET /v1/placement?team_id=<id>` 或 `?run_id=<id>`，scope `read`；返回 `{space, default: {id, name, scope, placement} | null, hint, options: [{id, name, scope, isolation, models, own}]}`。

## Review Focus

- Team 换了执行环境（`teams/<id>/environment`）后，在创建者边界上的 Team 会话照常继续（不进入等待）——Task 1 `test_a_team_environment_change_leaves_member_boundaries_alone`。
- 创建者的边界停用后会话等待，分配到 Team 的环境后继续——Task 1 `test_a_disabled_boundary_waits_and_can_move_to_the_team`。
- 管理员（不是 Team 成员）分配时，可选项仍按创建者——Task 2 `test_the_options_for_a_session_follow_its_creator`。
- 一个人有两个专属边界时 Team 会话也等待选择——Task 1 `test_two_own_boundaries_wait_in_a_team_too`。
- 旧客户端为 Team 会话指定了 Team 的环境（本人有边界）：照用并记 `requested`——Task 1 `test_a_named_environment_is_kept_and_recorded`。

---

### Task 1: 位置规则（domain）

**Files:**
- Modify: `octopus/domain.py`（`space_workers`、新增 `placement`、`unserved`、`Service.start` 的选择与 snapshot、`_view` 的键）
- Test: `tests/test_session_placement.py`（新建）

**Interfaces:**
- Produces: `placement(db, user, team) -> {"worker": row | None, "placement": str | None, "hint": str | None, "options": [row]}`；
  `HINT`（回退提示原文）；会话视图的 `placement: {"kind", "hint"?}`；`run.created` 事件的 `placement`。

- [ ] **Step 1: 写失败的测试**（夹具同 `tests/test_personal_boundaries.py::ChoiceTests`：alice 有边界 `alice-b`、bob 没有、两人都是 `team-a` 成员，
  `teamenv` 为 `team-a` 的专属环境，`shared` 服务 alice 与 bob 的个人空间）：
  `test_a_team_session_runs_on_the_submitters_own_boundary`（`worker_id == "alice-b"`、`placement.kind == "own"`）、
  `test_without_one_it_falls_back_to_the_teams_environment_with_a_hint`（bob → `teamenv`、`kind == "team"`、hint 含 `boundary add`）、
  `test_a_team_without_an_environment_takes_a_member_with_a_boundary`（删去 `teamenv`：alice → `alice-b`；bob → 409 `team_without_worker`）、
  `test_another_members_boundary_cannot_be_named`（bob 指定 `alice-b` → 403）、
  `test_two_own_boundaries_wait_in_a_team_too`、`test_a_named_environment_is_kept_and_recorded`（alice 指定 `teamenv` → `requested`）、
  `test_personal_sessions_are_unchanged`（alice 个人 → `own`；bob 个人 → `shared`、`only`）、
  `test_the_creator_leaving_the_team_makes_it_wait`（删除 alice 的成员关系 → `unserved` 写明「会话创建者已不在该 Team」）、
  `test_a_disabled_boundary_waits_and_can_move_to_the_team`（停用 `alice-b` → `unserved` 为「执行环境已停用」；Team owner `assign_worker(teamenv)` 成功）、
  `test_a_team_environment_change_leaves_member_boundaries_alone`（`teamenv` 移出 Team 后 alice 的会话 `unserved is None`）、
  `test_the_timeline_records_the_placement`（`run.created` 的 payload 与 `start` 的返回值含 `placement`）。
- [ ] **Step 2: 运行，确认失败**：`.venv/bin/python -m unittest tests.test_session_placement` → Team 会话落在 `teamenv`、`placement` KeyError。
- [ ] **Step 3: 实现**

```python
HINT = ("你还没有自己的执行边界：本次在 Team 的环境执行，与其他成员共用并发与 kiro 额度；"
        "可请平台管理员为你建立（deploy.sh boundary add <名称> --owner <用户名>）")

def space_workers(db, user, team):
    """Enabled Workers usable in a space: dedicated by owner/team, shared through worker_spaces; spare never (D28). In a
    Team's space also the member's own boundary (D49 phase 2: Team Sessions run on the submitter's boundary)."""
    if team:
        query = ("SELECT w.* FROM workers w WHERE w.enabled=1 AND ((w.scope='dedicated' AND w.team_id=?) OR "
                 "(w.scope='shared' AND EXISTS(SELECT 1 FROM worker_spaces s WHERE s.worker_id=w.id AND s.team_id=?)) OR "
                 "(w.scope='dedicated' AND w.team_id IS NULL AND w.owner_id=? AND "
                 "EXISTS(SELECT 1 FROM members m WHERE m.team_id=? AND m.user_id=?)))")
        return db.execute(query + " ORDER BY w.name", (team, team, user, team, user)).fetchall()
    ...  # personal: unchanged

def own(row, user):
    return row["scope"] == "dedicated" and not row["team_id"] and row["owner_id"] == user

def placement(db, user, team):
    """Where a new Session of this person in this space runs when no Worker is named (D49 R2): their one own boundary;
    else the space's only environment; else nothing (a choice is waited for)."""
    options = space_workers(db, user, team)
    mine = [w for w in options if own(w, user)]
    if len(mine) == 1:
        return {"worker": mine[0], "placement": "own", "hint": None, "options": options}
    if len(options) == 1:
        return {"worker": options[0], "placement": "team" if team else "only", "hint": HINT if team else None, "options": options}
    return {"worker": None, "placement": None, "hint": None, "options": options}
```

  `Service.start`：不带 `worker_id` 时用 `placement(db, actor["id"], team)`（替换 `own` / `len(usable) == 1` 的那段）；Team 且 `options` 为空时保留
  `environment_disabled` / `team_without_worker`，后者文案加「你也还没有自己的执行边界」；带 `worker_id` 时 `placement = "requested"`。
  snapshot `placement = {"kind": …, **({"hint": …} if hint else {})}`（无选择时不写）；`journal.record(..., "run.created", ..., placement=kind)`；
  `_view` 的键加 `"placement"`；`start` 的返回值带 `placement`（R5）。`unserved`：`team_id` 且 Worker 是创建者本人的边界、而创建者已不是成员时返回
  「会话创建者已不在该 Team：请 Team owner 或管理员把会话分配到 Team 的环境」（停用、退役仍先判断）。
- [ ] **Step 4: 运行，确认通过**：同上命令 → OK；再跑 `tests.test_personal_boundaries tests.test_teams_workers tests.test_access tests.test_waiting tests.test_mvp tests.test_confirmation`。
- [ ] **Step 5: Commit**：`feat(placement): Team 会话在提交者自己的边界执行，没有时回退到 Team 的环境并提示；会话记执行位置（D49 第 2 期）`

### Task 2: 接口、MCP 与 Skill

**Files:**
- Modify: `octopus/domain.py`（`Service.placement`）、`octopus/server.py`（`GET /v1/placement`）、`octopus/schema.py`（路径）、`octopus/mcp.py`
  （`octopus_environments` 改读 `/v1/placement` 并去掉 `usable`；`compact_status` 带 `placement`）、`skills/kirocrew-on/SKILL.md`
- Test: `tests/test_session_placement.py`

**Interfaces:**
- Consumes: Task 1 的 `placement`、`space_workers`、`own`。
- Produces: `Service.placement(actor, team=None, rid=None) -> {"space", "default", "hint", "options"}`；`GET /v1/placement`。

- [ ] **Step 1: 写失败的测试**：`test_the_placement_endpoint_answers_the_default`（alice / team-a → `alice-b` own；bob → `teamenv` + hint；
  非成员 403/404）、`test_the_options_for_a_session_follow_its_creator`（管理员与 Team owner 以 `run_id` 查询：选项为 `teamenv`、`alice-b`，`default`
  为当前 Worker）、`test_only_the_boundary_owner_opens_the_native_entry`（bob 对 alice 的 Team 会话 `crew_entry.create` → 403，不发网络请求）、
  `test_other_members_see_the_session_and_its_report`（bob `service.run` 与 `report.markdown`）、`test_the_mcp_lists_environments_with_the_default`
  （桩客户端：调用 `GET /v1/placement?team_id=team-a`，输出 `environments[].default`）、`test_the_compact_status_carries_the_placement`、
  `test_the_route_and_schema`（`required_scope("GET", "/v1/placement") == "read"`、OpenAPI 有 `/v1/placement`）。
- [ ] **Step 2: 运行，确认失败**。
- [ ] **Step 3: 实现**

```python
def placement(self, actor, team=None, rid=None):
    """Where a new Session would run in a space, or the environments a Session may move to (D49 R4, R6)."""
    with self.db.read() as db:
        if rid:
            run = run_access(db, actor, rid)
            user, team = run["owner_id"], run["team_id"]
            options = space_workers(db, user, team)
            chosen = next((w for w in options if w["id"] == run["worker_id"]), None)
            snapshot = json.loads(run["snapshot"]).get("placement") or {}
            answer = {"worker": chosen, "placement": snapshot.get("kind"), "hint": snapshot.get("hint"), "options": options}
        else:
            if team:
                member(db, actor["id"], team)
            user, answer = actor["id"], placement(db, actor["id"], team)
    row = lambda w: {"id": w["id"], "name": w["name"], "scope": w["scope"], "own": own(w, user),
                     "isolation": json.loads(w["config"]).get("capabilities", {}).get("isolation"),
                     "models": json.loads(w["config"]).get("capabilities", {}).get("models", {})}
    chosen = answer["worker"]
    return {"space": team or "personal", "hint": answer["hint"], "options": [row(w) for w in answer["options"]],
            "default": {**{k: row(chosen)[k] for k in ("id", "name", "scope")}, "placement": answer["placement"]} if chosen else None}
```

  server：`if path == "/v1/placement" and method == "GET": return self.respond(service.placement(actor, (query.get("team_id") or [None])[0], (query.get("run_id") or [None])[0]))`；
  schema 加 `"/v1/placement": {"get": None}`。MCP：`octopus_environments` 返回 `{"space", "hint", "environments": [{…, "default": id == default.id}]}`；
  `compact_status` 的 session 带 `placement`。Skill：`worker_id` 通常省略——平台先用你自己的执行边界，没有时用 Team 的环境；返回的 `placement.hint`
  原样告诉用户。
- [ ] **Step 4: 运行，确认通过**（加 `tests.test_mcp_status tests.test_mcp_roles tests.test_connect tests.test_crew_entry`）。
- [ ] **Step 5: Commit**：`feat(placement): GET /v1/placement——默认位置与可选环境；MCP octopus_environments 标出默认；Skill 说明（D49 第 2 期）`

### Task 3: 控制台

**Files:**
- Modify: `web/src/api/types.ts`（`Placement`、`Run.placement`）、`web/src/api/hooks.ts`（`usePlacement(team, runId?)`）、
  `web/src/features/launch/LaunchWizard.tsx`、`web/src/features/launch/steps.tsx`、`web/src/features/sessions/dialogs.tsx`、
  `web/src/features/sessions/SessionView.tsx`、`web/src/test/api.ts`（`GET /placement` 默认路由）
- Test: `web/src/features/launch/LaunchWizard.test.tsx`、`web/src/features/sessions/SessionView.test.tsx`

**Interfaces:**
- Consumes: Task 2 的 `GET /v1/placement`；Task 1 的 `run.placement`。

- [ ] **Step 1: 写失败的测试**：向导「执行 Worker」首项为「自动：将在 owner 执行」、不预选、提交的请求不带 `worker_id`、显示回退提示；
  KiroCrew 建议默认用 `default.id`；会话页显示 `placement.hint`；分配对话框的选项来自 `/placement?run_id=`。
- [ ] **Step 2: 运行，确认失败**：`cd web && npx vitest run src/features/launch src/features/sessions/SessionView.test.tsx`。
- [ ] **Step 3: 实现**：`initialDraft` 的 `worker` 恒为 `""`；首项文案 `default ? \`自动：将在 ${default.name} 执行\` : "稍后分配 Worker"`；
  模型取 `draft.worker || default.id`；`recommendWorker` 初值 `default.id`；提示 `<p id="placement-hint">`；会话页在执行环境旁 `<p id="placement-hint">`；
  分配对话框 `choices = placement.options`。
- [ ] **Step 4: 运行，确认通过**；`npx tsc --noEmit -p .`。
- [ ] **Step 5: Commit**：`feat(console): 发起向导默认交给平台选择执行位置并显示；会话页的位置提示；分配对话框按创建者列出可选环境（D49 第 2 期）`

### Task 4: 文档、回归、终审与 EC2

- [ ] OPERATIONS「每人一个边界」补第 2 期规则；MVP-RELEASE §14.37 更新；`tools/regress.sh`；opus 终审一次修复；BACKLOG 记延后项。
- [ ] EC2（spec §4）：admin 向一个 Team 提交 → owner 边界；无边界的成员 → Team 环境 + 提示；另一成员看报告、原生入口被拒；向导显示「自动：将在 owner 执行」。
