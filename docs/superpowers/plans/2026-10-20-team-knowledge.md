# 团队层知识（D52）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话发布到仓库时，把它的 Knowledge 条目提到仓库 `.octopus/knowledge.md` 的 Draft PR（固定分支 `octopus/knowledge`，人审核合并）；之后的任务派发时指向这个经评审的文件。

**Architecture:** 新模块 `octopus/knowledge_repo.py`：渲染与去重（纯函数）、托管方适配（GitHub contents API / GitLab commits API，经 `publish._api`）、`propose(...)`；`Service.publish` 在代码 PR 之后调用，失败只记录。`knowledge.gathers` 让共享环境也追加 Knowledge 任务（只用于仓库层），engine 对共享环境不写 lessons、记为 `kept`。Worker 派发时检出目录有该文件即在任务说明加一节。

**Tech Stack:** Python 3.12 标准库、unittest；React + TypeScript + vitest。

**Spec:** `docs/superpowers/specs/2026-10-20-team-knowledge-design.md`（负责人以重新启动 /loop 认可推荐的 R3、R4）

## Global Constraints

- 文件 `.octopus/knowledge.md`；分支 `octopus/knowledge`；PR 标题「项目知识更新（KiroCrew ON）」。
- 只写这一个文件；从不写默认分支、不强推；提议失败不影响代码 PR。
- 条目沿用 P4 的校验（`knowledge.items`：至多 5 条、rule ≤300、avoid ≤200、秘密扫描）。
- 共享环境：追加 Knowledge 任务，条目记 `kept`（「仅提到仓库」），不写 lessons、不注入 lessons。
- 审计 `knowledge.proposed`；时间线 `run.knowledge_proposed`；snapshot `knowledge_proposal`。

## Review Focus

- 仓库已有 `octopus/knowledge` 分支与打开的 PR：在其上追加，条目与分支上的文件去重——Task 2 `test_an_open_proposal_gets_the_new_items_only`。
- 分支存在而 PR 已合并或关闭：重新从默认分支开分支？——以 GitHub「分支已存在」时更新该分支、再开新 PR 处理——Task 2 `test_a_branch_without_an_open_request_gets_a_new_one`。
- 会话没有条目、或条目都已在仓库文件中：不写仓库——Task 2 `test_nothing_new_writes_nothing`。
- 托管方拒绝（403 无 contents 权限）：代码 PR 照常，记原因——Task 2 `test_a_refusal_never_fails_the_publish`。
- 共享环境的会话：任务追加但不调用 /knowledge/write——Task 1 `test_a_shared_environment_keeps_items_for_the_repository_only`。

---

### Task 1: 共享环境也提取（仅仓库层）
- 文件：`octopus/knowledge.py`（`gathers`、`KEPT`、`view` 的说明）、`octopus/engine.py`（`append` 的条件、`_knowledge` 对共享环境记 `kept`）。
- 测试：`tests/test_team_knowledge.py`：`test_a_shared_environment_gathers_for_the_repository`、`test_a_shared_environment_keeps_items_for_the_repository_only`、`test_dedicated_environments_still_write_lessons`。
- Commit：`feat(knowledge): 共享执行环境也追加 Knowledge 任务，只用于仓库层（不写、不注入 lessons）（D52）`

### Task 2: 发布时提到仓库的知识 PR
- 文件：新建 `octopus/knowledge_repo.py`（`render(text, items, rid, day)`、`propose(kind, host, path, items, rid, secret, api=...)`）；`octopus/domain.py` `Service.publish` 在代码 PR 后调用、记录、审计。
- 测试（fake `_api`）：`test_render_adds_new_items_only`、`test_a_missing_branch_is_made_from_the_default_one`、`test_an_open_proposal_gets_the_new_items_only`、`test_a_branch_without_an_open_request_gets_a_new_one`、`test_nothing_new_writes_nothing`、`test_a_refusal_never_fails_the_publish`、`test_gitlab_uses_one_commit`、`test_publish_records_and_audits_the_proposal`。
- Commit：`feat(knowledge): 会话发布时把 Knowledge 条目提到仓库 .octopus/knowledge.md 的 Draft PR（D52）`

### Task 3: 派发时指向仓库的知识文件
- 文件：`octopus/worker.py` dispatch（`project_dir/.octopus/knowledge.md` 存在时 extra_prompt 加「## 项目知识（经评审）」）。
- 测试：`test_a_task_is_pointed_at_the_reviewed_knowledge`、`test_without_the_file_nothing_is_added`。
- Commit：`feat(knowledge): 任务说明指向仓库中经评审的 .octopus/knowledge.md（D52）`

### Task 4: 可见、文档、回归与终审
- `knowledge.view` 带 `proposal`；控制台 KnowledgePanel「已提交到仓库知识 PR #n」；报告「## 发布」；MCP 精简状态 `session.knowledge.proposal`；OPERATIONS「团队层知识（D52）」；`tools/regress.sh`；opus 终审一次修复；EC2 验收由负责人确认仓库与凭证后进行。
