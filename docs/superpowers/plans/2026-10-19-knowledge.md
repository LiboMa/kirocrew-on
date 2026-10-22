# Knowledge（D46 P4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 专属执行环境中来自仓库、改了代码的会话完成时，平台追加 Knowledge 任务整理可复用的项目知识，核对后写成 KiroCrew lessons（按仓库标记限定范围），后续同仓库的任务派发时注入；共享环境不沉淀、不注入。

**Architecture:** Worker 增加仓库标记（准备时）、`include_lessons` 随派发请求、`/knowledge/write` 与 `/knowledge/forget`（调 KiroCrew `/api/lessons`）。平台新模块 `octopus/knowledge.py`（适用条件、仓库标识、追加任务、条目核对），引擎在准备、派发、完成前与 Knowledge 任务通过后接入；台账表 `knowledge`（迁移 7）；会话视图、控制台、MCP 显示。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-19-knowledge-design.md`

## Global Constraints

- 适用：`execution_contract >= 4`、`source.type == "git"`、Worker `scope != "shared"`、能力 `role_cards`。
- 标记：项目根 `.octopus-kb/<key>`（`key` = `host_owner_repo` 中非 `[A-Za-z0-9._-]` 替换为 `_`，≤120），写入 `.git/info/exclude` 一行 `/.octopus-kb/`。
- lessons 请求体：`{rule, category: "knowledge", applies: "always", repo_scope: ".octopus-kb/<key>", negative?}`，头 `X-Session-Key: dashboard:ui`。
- 条目：至多 5 条；`rule` ≤300、`avoid` ≤200；含 `sources.SUSPICIOUS` 命中者丢弃；同一 Worker 同一仓库台账中已有相同 `rule` 的跳过。
- 写入失败不改变任务与会话的状态。
- 角色 `knowledge`（kind design）加入 `octopus/roles`，卡片 ≤600。

## Review Focus

- 旧版 Worker（没有 `/knowledge/write`）：写入记为 skipped（原因：执行环境不支持），会话照常完成（Task 2 `test_an_older_worker_skips_the_write`）。
- 续作后再次完成：已有 Knowledge 任务在最后一次改动之后时不重复追加；在其之前时再追加一个（Task 2 `test_a_change_after_knowledge_gets_another`）。
- 共享环境上已存在的会话转到专属环境（改派）：之后的派发按新环境判断注入（Task 2 `test_injection_follows_the_current_environment`）。
- KiroCrew 拒绝（`ok: false` / `persistence_disabled`）：台账记 refused 及原因（Task 1 `test_refusals_are_reported`）。
- 删除不在台账中的条目或他人会话的条目：403 / 404（Task 2 `test_forgetting_is_checked`）。

---

### Task 1: Worker 侧（标记、注入、写入与删除）

**Files:**
- Modify: `octopus/gateway_client.py`（`request(..., headers=None)`）、`octopus/worker.py`（`native(..., headers=None)`、`prepare` 写标记、`dispatch` 的 `include_lessons`、`knowledge_write` / `knowledge_forget` 与路由）、`composition/templates.py`（协调者 `managedToolPolicy.exclude` 加 `learn_add`、`learn_remove`）
- Modify: `tests/kirocrew_contract.json`（`http` 加 `POST /api/lessons`、`DELETE /api/lessons`，模块 `dashboard/handlers/cron.py`）
- Create: `tests/test_knowledge_worker.py`

**Interfaces:**
- Produces: Worker `prepare` 接受 `knowledge_scope`（标记 key）；`dispatch` 接受 `lessons: bool`；`POST /knowledge/write {run_id, scope, items:[{rule, avoid?}]}` → `{results:[{rule, result: written|refused|superseded, reason?}]}`；`POST /knowledge/forget {run_id, scope, rule}` → `{ok}`；能力 `knowledge: True`。

- [ ] **Step 1: 写失败的测试** —— `tests/test_knowledge_worker.py`：
  - `test_a_prepared_repository_carries_its_mark_outside_git`：`prepare({"run_id", "source": {"type": "new"}, "knowledge_scope": "h_o_r"})` 后项目根存在 `.octopus-kb/h_o_r`，`.git/info/exclude` 含 `/.octopus-kb/`，`git status --porcelain` 为空。
  - `test_lessons_are_injected_only_when_asked`：用 `dispatch` 的既有测试夹具（`tests/test_worker_dispatch.py` 或 `tests/test_mvp.py` 中构造 dispatch 的写法，执行时以 grep 定位并记 Ruling），断言 `lessons: true` 时 spawn 体 `include_lessons` 为 True、缺省为 False，`include_memory` 恒为 False。
  - `test_knowledge_is_written_as_scoped_always_lessons`：stub `native` 记录调用；`knowledge_write({"run_id", "scope": "h_o_r", "items": [{"rule": "用 pnpm", "avoid": "不要用 npm"}]})` → 调用 `("/api/lessons", {"rule": "用 pnpm", "category": "knowledge", "applies": "always", "repo_scope": ".octopus-kb/h_o_r", "negative": "不要用 npm"})`，头含 `X-Session-Key: dashboard:ui`；返回 `results[0].result == "written"`。
  - `test_refusals_are_reported`：stub 返回 `{"ok": False, "reason": "volatile_session_fact"}` → `refused`；抛 `GatewayError(403, '{"code":"persistence_disabled"}')` → `refused`，reason 含 `persistence_disabled`；`{"ok": True, "superseded": [..]}` → `superseded`。
  - `test_forgetting_deletes_the_exact_scoped_lesson`：`knowledge_forget` → `("/api/lessons", {"rule": r, "exact": True, "repo_scope": ".octopus-kb/h_o_r"}, "DELETE")`。
  - `test_the_coordinator_cannot_write_lessons_itself`：种入后 `poc-coordinator` 的 `managedToolPolicy.exclude` 含 `learn_add`、`learn_remove`。
  - `test_the_contract_lists_the_lessons_endpoint`：`tests/kirocrew_contract.json` 的 `http` 含 `POST /api/lessons`（reads ⊇ rule、category、negative、repo_scope、applies）与 `DELETE /api/lessons`（reads ⊇ rule、exact、repo_scope），且 `tests.test_kirocrew_contract` 的扫描通过。
- [ ] **Step 2: 运行确认失败**（`.venv/bin/python -m unittest tests.test_knowledge_worker -v`）。
- [ ] **Step 3: 实现** —— 按 Interfaces；标记写在 `workspace.prepare_project` 之后、项目根下；exclude 追加且不重复；`knowledge_write` 逐条调用，捕获 `GatewayError` 记 refused（reason 取响应的 code 或前 200 字符）；`health()` 增 `"knowledge": True`；契约 JSON 加两项（reads 取 0.10.0 `LEARN_ADD_SCHEMA` 与删除处理函数：POST `rule, category, negative, repo_scope, applies, scope, workspace`；DELETE `rule, exact, repo_scope, scope, workspace`），本机先按 `/tmp/kc-research` 源码核对存在。
- [ ] **Step 4: 运行确认通过**（加 `tests.test_kirocrew_contract tests.test_kirocrew_p0 tests.test_composition`）。
- [ ] **Step 5: Commit** —— `feat(knowledge): Worker 侧——仓库标记、按需注入 lessons、写入与删除 KiroCrew lessons；协调者不能自行写 lessons；契约加 /api/lessons（D46 P4）`

### Task 2: 平台侧（适用、追加、写入与台账）

**Files:**
- Create: `octopus/knowledge.py`、`octopus/roles/knowledge/SKILL.md`、`tests/test_knowledge.py`
- Modify: `octopus/roles.py`（`KINDS` 加 `knowledge: ("design",)`、`LABELS` 加「Knowledge」）、`octopus/migrations.py`（迁移 7：表 `knowledge`）、`octopus/engine.py`（`_active` 选出 `w.scope worker_scope`；准备体加 `knowledge_scope`；派发体加 `lessons`；完成前在 `evaluation.append` 之后调 `knowledge.append`；Knowledge 任务通过后调 Worker 写入并记台账与事件）、`octopus/domain.py`（`mapping` 的来源、视图 `knowledge` 字段、`operate` 无关；新增 `Service.forget_knowledge(actor, rid, kid)` 与路由 `POST /v1/runs/{id}/knowledge/forget`）、`octopus/server.py`、`web/src/lib/labels.ts`（`run.knowledge`、`run.knowledge_forgotten`、角色标题）、`tests/test_roles.py`（角色集合加 knowledge，Ruling）
- Interfaces: `knowledge.applies(snapshot, source, scope, config) -> bool`、`knowledge.key(source) -> str | None`、`knowledge.needed(tasks) -> bool`、`knowledge.append(db, rid, snapshot, config) -> str | None`、`knowledge.items(report) -> (items, notes)`；台账列 `id, run_id, task_id, worker_id, repo, rule, avoid, result, reason, created, removed, removed_by`。

- [ ] **Step 1: 写失败的测试** —— `tests/test_knowledge.py`（夹具：`tests/test_evaluator.EvaluatorCase` 的写法，Worker 配置 `capabilities` 含 `role_cards`、`knowledge`、tools；workers 表 `scope` 设为 `dedicated`；源码 `{"type": "git", "url": "https://github.com/o/r.git"}`，准备由假 Worker 应答）：
  - 适用条件：dedicated + git + 契约 4 + role_cards → 准备体带 `knowledge_scope: "github.com_o_r"`、派发体 `lessons: True`；scope shared / 新建源码 / 契约 3 → 都不带。
  - 追加：改代码的会话完成前追加 `role: knowledge` 任务（工具 kiro，来源「平台追加（沉淀项目知识）」），排在 Evaluator 之后；只读设计任务不追加；`test_a_change_after_knowledge_gets_another`。
  - 写入：假 Worker `/knowledge/write` 记录请求并返回结果；Knowledge 任务结论 `knowledge: [{rule, avoid}]` → 写入请求体为核对后的条目；超过 5 条只取前 5、超长与含 `ghp_…` 的丢弃并记说明；台账行与 `run.knowledge` 事件；同一 Worker 同一仓库重复的跳过；`test_an_older_worker_skips_the_write`（404）会话仍完成。
  - 视图：`run["knowledge"]` 列出本会话的台账条目；`test_forgetting_is_checked`：创建者删除 → 假 Worker `/knowledge/forget` 收到 rule、台账 `removed`；他人会话 403；不存在的 id 404。
  - `test_injection_follows_the_current_environment`：会话的 worker_id 改到 scope shared 的 Worker 后，下一次派发 `lessons` 为 False。
- [ ] **Step 2: 运行确认失败。**
- [ ] **Step 3: 实现** —— 按 Interfaces 与 spec R1–R8；Knowledge 任务提示：「只读阅读本会话的需求、前序交接与改动，列出长期有效的项目知识（约定、决策、踩过的坑、可复用的做法），至多 5 条写入 outcome.json 的 knowledge：[{rule ≤300, avoid? ≤200}]；不写本次进度、个人信息、秘密或模型身份；没有就写空列表。不修改文件。」；验收项「列出可复用的项目知识（可为空）」；卡片 `octopus/roles/knowledge/SKILL.md` 同义 ≤600；`quality_instructions` 在 `role == knowledge` 时附 knowledge 字段格式。
- [ ] **Step 4: 运行确认通过**（加 `tests.test_roles tests.test_evaluator tests.test_migrations tests.test_event_labels tests.test_prompts_p1`）。
- [ ] **Step 5: Commit** —— `feat(knowledge): 平台侧——专属环境中来自仓库、改了代码的会话完成前追加 Knowledge 任务，核对条目后写成限定仓库的 KiroCrew lessons 并记台账；派发时按环境注入；共享环境不沉淀不注入（D46 P4）`

### Task 3: 控制台、MCP 与文档

- 会话页「本次沉淀的知识」：条目、结果（已写入 / 被拒 / 已替代 / 跳过及原因）、「删除」（`permissions.publish`）；共享环境的会话写「共享执行环境不沉淀、不注入知识」。
- MCP 精简状态 `knowledge`；OPERATIONS「Knowledge（D46 P4）」；Skill 说明。
- 测试：vitest（列表、删除调用 `POST /runs/<id>/knowledge/forget`、共享环境提示），Python（MCP 精简状态）。
- Commit —— `feat(console): 会话页「本次沉淀的知识」与删除、共享环境提示、MCP 与文档（D46 P4）`

### Task 4: 回归、终审与 EC2 验收

- `tools/regress.sh` → OK；opus 终审，Critical / Important 一次修复（RED→GREEN），Minor 记 BACKLOG。
- EC2：需要一个专属执行环境（向负责人确认：w1 改为某 Team 专属，或新增专属主机）；第一个同仓库会话沉淀并写入，第二个会话的原生上下文注入这些 lessons，删除后不再注入。
