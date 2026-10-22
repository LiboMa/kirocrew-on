# 门禁减负与安全底线（D57）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 除安全底线、结果不可信、用户明确要求的验收外，门禁一律降为「提示」不拦；安全底线扩大到所有交到人手里的内容，命中即停、只能由人判断，误报逐处记忆。

**Architecture:** 门禁记录新增 `warn` 状态（不使 Attempt blocked）；检查命令分「必过 / 建议」并全部执行；新增 `octopus/guard.py`（安全阻塞状态机）与 `octopus/allowances.py`（误报记忆）；扫描与打码集中在 `octopus/sources.py` 的规则表；纠错增加防空转与平台故障自动重试。

**Tech Stack:** Python 3.12 标准库 + SQLite（unittest）；React + TypeScript + vitest（web/）；git CLI。

**Spec:** `docs/superpowers/specs/2026-10-21-gate-relief-design.md`（D57，含 §13 实现决定）

## Global Constraints

- 不做任何模式或开关（spec §2.4）；所有会话同一套规则。
- 安全底线不降级：规划者、协调者、Agent、API / MCP Token 都不能绕过；安全阻塞期间续作、恢复、确认验收、发布一律拒绝，只允许「判断」与停止 / 关闭（spec §3.3）。
- 判断只接受控制台登录（actor `token_label == "Web login"`）的会话创建者、Team owner 或管理员（spec §13.5）。
- 任何地方不出现密钥明文：平台保存的副本在写入时打码；打码形式「首 4 位…尾 2 位」（spec §3.1、§3.3）。
- 误报记忆只对（空间，仓库，位置，内容 SHA-256）完全相同的一处生效；不存明文；只能人加；可撤销；增删写审计（spec §3.4）。
- `warn` 不使任务 blocked、不触发纠错；报告与会话页如实区分 ✓ ⚠ ✗（spec §4、§9）。
- 提示文案写三件事：发生了什么 / 谁的问题 / 一个首选操作；不出现 `failure_type`、门禁英文名等内部词，英文名只放详情（spec §8）。
- 测试：`.venv/bin/python -m unittest <module>`（在工作树根，`.venv` 已链接到主检出）；全量 `tools/regress.sh`；前端 `npm --prefix web test`、`npm --prefix web run build`。
- 提交信息中文，带 `（D57）`，结尾 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`；只在本地分支 `feat/gate-relief` 提交，不推送 GitHub。

## Review Focus

- 疑似密钥恰好跨过检查输出截断边界（`TAIL=4096`）：必须先识别、打码，再截断，截断后的尾部不能残留半个密钥 → 加在 Task 6 的测试。
- 误报复扫时原生结果已过期（KiroCrew 结果只保留 24 小时）：重判必须只用平台保存的 Attempt 结果，不依赖 Gateway → 加在 Task 10。
- 必过检查与建议检查的命令文本完全相同，或被 `verify_fixes` 调整了顺序：必过身份按命令原文判定，与位置无关 → 加在 Task 5。
- 会话已完成并已发布，之后的下载才扫到密钥：会话改为安全阻塞，再次发布被拒 → 加在 Task 11。
- 用 MCP Token、或非会话所有者尝试「判断」或添加误报：一律 403，且不写任何记忆 → 加在 Task 8 与 Task 9。

---

### Task 0: 准备与基线

**Files:** 无代码改动。

- [ ] **Step 1:** 确认工作树 `.worktrees/gate-downgrade`（分支 `feat/gate-relief`）的 `.venv`、`web/node_modules` 链接可用：`.venv/bin/python -c "import octopus"`、`npm --prefix web test -- --run --reporter=dot` 能启动。
- [ ] **Step 2:** 跑一次全量基线并记下已有失败：`PY=.venv/bin/python tools/regress.sh 2>&1 | tail -30`。把失败用例名写进 `/tmp/d57-baseline-failures.txt`，后续任务只对新增失败负责。

### Task 1: 门禁状态 `warn`

**Files:**
- Modify: `octopus/gates.py`（`record`）、`octopus/engine.py`（`advance` 判定处 ~1061-1088、`_settle` ~816）、`octopus/mcp.py`（`latest()` gates 汇总 ~416-421、session view）、`octopus/report.py:11`、`web/src/api/types.ts:148-152`、`web/src/features/sessions/TaskList.tsx:106-114`、`web/src/features/sessions/SessionView.tsx`
- Test: `tests/test_gates.py`、`tests/test_engine_gates.py`、`tests/test_mcp_status.py`、`tests/test_report.py`、`web/src/features/sessions/SessionView.test.tsx`

**Interfaces:**
- Produces: `gates.STATUSES = ("pass", "warn", "fail", "skip")`；`gates.record(name, status, detail=None)`（status 不在 STATUSES 时 `ValueError`）；`gates.warnings(records) -> list[dict]`（status 为 warn 的记录）；Attempt `evidence["warnings"]`（任务 accepted 时写入，等于 `gates.warnings(evidence["gates"])`）；MCP `latest.gates.warned: [{name, detail}]`；MCP session `warnings: int`（各任务最新 Attempt 的 warn 总数）。

- [ ] **Step 1: 写失败测试**
  - `test_gates.GateRecordTests.test_warn_is_a_recorded_status`：`record("x", "warn", "d")["status"] == "warn"`；`record("x", "maybe")` 抛 `ValueError`。
  - `test_engine_gates.EngineGateTests.test_a_warn_gate_does_not_block`：用 `patch.object(Engine, "_more_gates")` 让它追加 `gates.record("diff_present", "warn", "本任务没有改动入库文件")` 并返回 `(None, None, False)`；断言 Attempt 为 `accepted`、`evidence["warnings"]` 长度 1、Run 未进入 `diagnosing`。
  - `test_mcp_status.test_warned_gates_and_the_session_count_are_listed`：`latest["gates"]["warned"] == [{"name": "diff_present", "detail": …}]`，`session["warnings"] == 1`。
  - `test_report`：带 warn 的 Attempt 报告行含 `⚠`。
  - `SessionView.test.tsx`：门禁列表对 warn 显示 `⚠`；已完成且有 1 项 warn 的会话标题旁显示「完成 · 1 项提示」。
- [ ] **Step 2:** 运行上述测试，确认因功能缺失而失败。
- [ ] **Step 3:** 实现：`record` 校验；engine 在置 accepted 前写 `evidence["warnings"]`；`domain._verified` 已按「没有 fail」判定，保持不变并加注释；`report.MARK["warn"] = "⚠"`；web 类型加 `"warn"`，TaskList 映射 `warn: "⚠"`、样式 `text-amber-600`；SessionView 在客户端按任务最新 Attempt 的 `evidence.gates` 统计 warn 数并显示。
- [ ] **Step 4:** 跑本任务全部测试与 `tests.test_engine_gates`、`tests.test_mcp_status`、`tests.test_report`、`npm --prefix web test`，全部通过。
- [ ] **Step 5:** 提交 `feat(gates): 门禁新增「提示」状态 warn，不使任务 blocked、不触发纠错（D57）`。

### Task 2: 五个门禁降为提示

**Files:**
- Modify: `octopus/engine.py`（diff_present 651、artifacts_locked 658-664、tests_fail_on_baseline 759-763）、`octopus/gates.py`（handoff_present 34-37、outcome_valid 53-55）、`octopus/engine.py:45-61`（`handoff()` 作为缺交接时的生成来源）
- Test: `tests/test_engine_gates.py`、`tests/test_gates.py`、`tests/test_coordinator_targets.py`（D54 用例）

**Interfaces:**
- Consumes: Task 1 的 `warn`。
- Produces: 缺交接时 `evidence["handoff_generated"] = True`，`result["handoff"]` 为平台生成文本（首行「（平台生成：执行者没有写交接文件）」）。

- [ ] **Step 1: 改写 / 新增测试**
  - `test_an_empty_diff_fails_implement_but_not_test_or_untyped_tasks` 改为 `test_an_empty_diff_is_a_warning_for_every_kind`：implement / deliver / test 都 accepted，implement 的 diff_present 为 warn，detail == `"本任务没有改动入库文件。如果它本来就只产出补丁或报告，可以忽略。"`。
  - 643 行「无法生成候选」仍为 fail：`test_a_worker_refusal_is_a_gate_failure_not_a_hang` 保持不变。
  - `test_a_later_task_may_not_change_an_accepted_tasks_files_unless_its_paths_say_so` 改为 `test_changing_an_accepted_tasks_files_is_a_warning_naming_them`：accepted，artifacts_locked 为 warn，记录带 `files`。D54 的 3 个 `locked_session` 用例改为断言：gate 为 warn、不调度诊断；`paths_add` 校验函数本身的单元测试保留。
  - `test_a_test_tasks_tests_must_fail_on_the_baseline` 改为 `test_tests_that_pass_on_the_baseline_are_a_warning`：accepted，detail == `"测试在改动前也能通过，可能没测到改动"`。
  - `test_gates.test_each_failure_keeps_todays_message_and_type` 拆出：缺交接 → handoff_present warn 且后续门禁继续判定；outcome 不合法 → outcome_valid warn，结果有单独一行 BLOCKED 时仍按 BLOCKED 失败，否则按通过。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现上述五处：只改判定与文案，记录照常追加；缺交接时用 `engine.handoff()` 的回退文本生成交接并置 `handoff_generated`。
- [ ] **Step 4:** 跑 `tests.test_engine_gates tests.test_gates tests.test_coordinator_targets tests.test_baseline`，全部通过。
- [ ] **Step 5:** 提交 `feat(gates): diff_present、artifacts_locked、tests_fail_on_baseline、handoff_present、outcome_valid 降为提示（D57）`。

### Task 3: 审查改动由平台撤回

**Files:**
- Modify: `octopus/worker.py:614-642`（`Worker.candidate` 新增 mode `rewind`）、`octopus/sources.py`（复用 `restore`；新增 `rewind(project, expected) -> dict`）、`octopus/engine.py:608-631`（review 分支）
- Test: `tests/test_candidates.py`、`tests/test_engine_gates.py`

**Interfaces:**
- Produces: Worker `/candidate {mode: "rewind", expected}` → `{head, clean, changed_files: [], reverted: [str], rewound: int}`；`expected` 不是当前 HEAD 的祖先时 409 `conflict`。
- `sources.rewind(project, expected)`：HEAD 不等于 expected 时 `git reset --hard expected`（未跟踪文件保留）；再 `restore` 剩余的已跟踪改动；返回撤回的文件名与回退的提交数。

- [ ] **Step 1: 写失败测试**
  - `test_candidates.test_rewind_resets_a_reviewers_commits_and_edits`：在候选 C1 上，审查者提交 1 次并改 1 个已跟踪文件；rewind 后 HEAD == C1、工作区干净、`reverted` 含两处文件、`rewound == 1`；未跟踪文件仍在。
  - `test_candidates.test_rewind_refuses_a_head_that_does_not_descend_from_the_candidate`：409。
  - `test_engine_gates.test_a_review_that_changes_code_is_not_bound` 改为 `test_a_review_that_changes_code_is_reverted_and_still_counts`：review_bound 为 warn，detail 列出撤回的文件，Attempt accepted，`evidence["reviewed"] == C1`。
  - `test_a_review_is_bound_to_the_latest_candidate` 的 C0 情形改为 warn（「审查报告的版本与候选不符」），结论照常采用；rewind 返回 409 时 review_bound 为 fail。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现 Worker mode 与 engine 分支（先 `check`，有改动或 HEAD 移动则 `rewind`）。
- [ ] **Step 4:** 跑 `tests.test_candidates tests.test_engine_gates`，通过。
- [ ] **Step 5:** 提交 `feat(review): 审查改动的文件由平台撤回、会话分支回到候选，结论照常采用（D57）`。

### Task 4: 检查运行器：全部执行、git 身份、环境失败识别

**Files:**
- Modify: `octopus/checks.py`（`_run` 环境 83-84、执行循环、`status()`）
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `checks.UNRUNNABLE`（[(regex, 文案模板)]）；`checks.unrunnable(result: dict) -> str | None`，返回人话原因，如「命令不存在：pytest」「缺少 Python 模块：yaml」「检查环境没有 git 提交者身份」；不是环境原因返回 None。
- `Checks.status(job)` 的 `results[]` 包含每条命令（不再在第一条失败处停下）；`passed` = 全部退出 0；`failed` 保留为第一条失败的序号（兼容）。超过总时限后未运行的命令记 `{"skipped": "total_timeout"}`。

- [ ] **Step 1: 写失败测试**
  - `test_every_command_runs_even_after_a_failure`：`["false", "echo ok > ran.txt"]` → 两条都有结果，`ran.txt` 存在，`passed` 为 False。
  - `test_git_commits_work_without_a_configured_identity`：临时 HOME 无 gitconfig，项目内 `git init`，检查 `git commit --allow-empty -m x` 退出 0。
  - `test_unrunnable_recognises_environment_failures`：表驱动——退出码 127；tail 含 `bash: pytest: command not found`；`ModuleNotFoundError: No module named 'yaml'`；`Committer identity unknown` → 非 None；`AssertionError: 1 != 2`（退出 1）→ None。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现：环境用 `env.setdefault` 注入 `GIT_AUTHOR_NAME/EMAIL`、`GIT_COMMITTER_NAME/EMAIL`，取值 `sources.COMMITTER`（「KiroCrew ON」 / 「kirocrew-on@localhost」）；循环不再 break；新增 `unrunnable`。
- [ ] **Step 4:** 跑 `tests.test_checks tests.test_baseline`，通过（`test_output_tail_is_kept_and_scrubbed` 不变）。
- [ ] **Step 5:** 提交 `feat(checks): 检查命令全部执行、注入默认 git 身份、识别环境原因的失败（D57）`。

### Task 5: 必过检查与建议检查

**Files:**
- Modify: `octopus/domain.py`（`CONTRACT`/`LIMITS` 54-56、`steps()` 77-139、续作 override ~2189）、`octopus/planning.py`（REQUESTED 文案 15-17、`plan_invalid` 161-165、`expand` ~119、`_fix_kept_checks` 231-260、提示 297-298）、`octopus/recovery.py`（`_replan`）、`octopus/engine.py`（`_poll_checks` 769-814）、`octopus/schema.py:18-22`
- Test: `tests/test_engine_gates.py`、`tests/test_planning.py`、`tests/test_continue.py`、`tests/test_replan.py`

**Interfaces:**
- Consumes: Task 4 `checks.unrunnable`；Task 1 `warn`。
- Produces:
  - 合同字段 `verify_required: list[str]`（每条须在 `verify` 中）；规划者另可给 `verify_basis: {命令: 需求原文片段}`。
  - `domain.required_checks(run, spec) -> list[str]`：spec 有 `verify_required` 用它；否则 `run["match_mode"] == "agent"` 的会话为 `[]`，其余为 `spec["verify"]`。
  - 规划者任务的 spec 写 `origin: "planner"`。

- [ ] **Step 1: 写失败测试**
  - `test_engine_gates.test_failing_checks_send_the_task_back_through_recovery` 改为 `test_a_failing_required_check_still_goes_to_recovery`（用户 steps 写的检查）。
  - `test_engine_gates.test_a_failing_advisory_check_is_a_warning`：agent 会话的规划者检查失败 → accepted，checks_pass 为 warn，detail 含「建议检查未通过」与命令前 80 字符、退出码。
  - `test_engine_gates.test_an_unrunnable_required_check_is_a_warning`：必过检查退出 127 → warn，detail 以「无法执行（环境）：」开头，含「请确认环境，或把这条检查改写成可执行的形式」。
  - `test_engine_gates.test_a_required_check_is_known_by_its_text_not_its_position`：同一命令文本出现在必过与建议两处、顺序被调换 → 按命令原文判定为必过。
  - `test_planning.test_the_planner_may_mark_a_check_required_only_with_a_quote_from_the_requirement`：basis 是需求原文子串（空白规范化后）→ 必过；不是 → 降为建议，计划 notes 写明原因。
  - `test_planning.test_implement_tasks_no_longer_need_verify`：implement 任务不写 verify 时计划合法。
  - `test_continue.test_checks_given_in_a_continue_are_required`：`execution.verify` 无 `verify_required` 时全部必过；带 `verify_required` 时须为子集，否则 400。
  - `test_replan.test_a_replan_must_keep_the_required_checks_of_the_tasks_it_replaces`：新任务未承接被替换任务的必过检查 → 计划不合法（拒绝原因写明缺哪条）。
  - `test_planning.test_verify_fixes_cannot_change_a_required_check`：针对必过命令的 fix 被拒，并写进 notes。
  - `test_engine_gates.test_checks_of_agent_sessions_planned_before_d57_are_advisory`：无 `verify_required` 的 agent 会话任务按建议检查。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现：`steps()` 校验 `verify_required ⊆ verify`；用户来源（非 planned）缺省时 `verify_required = verify`；规划路径按 basis 子串校验；`_poll_checks` 逐条判定（环境 → warn；必过失败 → fail，沿用现有消息与 `failure_type="gate"`；建议失败 → warn）；`schema.py` 与 OpenAPI 加字段。
- [ ] **Step 4:** 跑上述模块与 `tests.test_workflow_import tests.test_requested`，通过。
- [ ] **Step 5:** 提交 `feat(checks): 检查分必过与建议，只有必过检查失败才拦；规划者引原文才能标必过（D57）`。

### Task 6: 密钥识别与打码原语

**Files:**
- Modify: `octopus/sources.py:23-31`（规则表、`SUSPICIOUS` 由规则表合成）
- Create: `tests/test_secret_rules.py`

**Interfaces:**
- Produces:
  - `sources.SECRET_RULES: list[tuple[str, re.Pattern[bytes]]]`，kind 取值：`github`、`gitlab`、`aws_access_key`（`AKIA|ASIA`）、`aws_secret_key`（紧跟 `aws_secret_access_key` 的 40 位）、`anthropic`（`sk-ant-`）、`openai`（`sk-` / `sk-proj-`，排在 anthropic 之后）、`slack`（`xox[abprs]-`）、`octopus_token`（`oct_[A-Za-z0-9_-]{43}`）、`private_key`。
  - `sources.find_secrets(data: bytes, extra: Sequence[bytes] = ()) -> list[dict]`：每项 `{kind, start, end, line, masked, hash}`；`masked` = 前 4 字符 + `…` + 后 2 字符；`hash` = 匹配内容的 SHA-256 十六进制；`extra` 是需要按原文识别的值（Worker Token），kind 为 `worker_token`。
  - `sources.mask_text(text: str, extra=()) -> tuple[str, list[dict]]`：每处替换为 `masked`。

- [ ] **Step 1: 写失败测试**（真样本由测试运行时拼接生成，不在源码里写成完整字面量）
  - `test_each_kind_is_found_with_its_kind`、`test_lookalikes_are_not_secrets`（`oct_fixture`、`sk-test`、`ghp_test_secret_value`、无前缀 base64、单独的 `xoxb`、`AKIA` 加小写）、`test_masked_keeps_four_and_two`、`test_hash_is_stable`、`test_an_extra_literal_is_found_as_worker_token`、`test_mask_happens_before_truncation`（密钥跨 4096 字节边界：先 `mask_text` 再截断，尾部不残留原文）。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现规则表与两个函数；`checks.py:150-151` 改用 `mask_text`（先打码后截断），并把命中写进该条结果的 `findings`。
- [ ] **Step 4:** 跑 `tests.test_secret_rules tests.test_checks tests.test_publish tests.test_candidates tests.test_knowledge`，通过。
- [ ] **Step 5:** 提交 `feat(security): 密钥识别规则扩充（AWS 临时凭证与 Secret Key、Anthropic、OpenAI、Slack、平台 Token、Worker Token）与统一打码（D57）`。

### Task 7: 写入时打码与文本命中

**Files:**
- Modify: `octopus/engine.py`（保存 result / evidence / handoff / outcome 处 ~1045-1070、1115-1119）、`octopus/recovery.py`（保存 result / evidence / decision ~555-556、622-625）、`octopus/journal.py`（事件写入）、`octopus/mcp.py`（observe 返回）、`octopus/worker.py`（`Worker.observe` 453、parent note 1366-1375）
- Test: `tests/test_engine_gates.py`、`tests/test_journal.py`、`tests/test_observe.py`、`tests/test_recovery.py`

**Interfaces:**
- Consumes: Task 6 `mask_text`。
- Produces: 文本命中交给 Task 8 的 `guard.block(..., stage="text", findings=[{..., "location": "<类别>:<task_id>"}])`，类别取 `result`、`handoff`、`outcome`、`checks`、`error`。

- [ ] **Step 1: 写失败测试**
  - `test_a_secret_in_the_result_is_stored_masked_and_blocks`：数据库里 attempts.result、evidence、journal 都没有原文；Run 为安全阻塞，finding 的 location == `"result:task-1"`。
  - `test_a_secret_in_a_check_tail_blocks_the_session`：location == `"checks:task-1"`。
  - `test_journal.test_events_never_hold_a_secret`。
  - `test_observe.test_mcp_observe_masks_secrets`（只打码，不阻塞——原生实时对话是已知限制）。
  - `test_recovery.test_a_diagnosis_result_is_stored_masked`。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 在各写入点调用 `mask_text`，`extra` 传入该会话 Worker 的 Token（平台侧取调用 Worker 用的那枚，Worker 侧取自身配置的那枚）；engine 汇总命中（扣除 Task 9 的误报记忆）后调用 `guard.block`。Task 8 落地前，guard 的调用由 Task 8 的测试一并验证。
- [ ] **Step 4:** 跑上述模块，通过。
- [ ] **Step 5:** 提交 `feat(security): 平台保存的结果、交接、检查输出、事件与诊断记录在写入时打码，命中即安全阻塞（D57）`。

### Task 8: 安全阻塞状态机与「判断」

**Files:**
- Create: `octopus/guard.py`、`tests/test_security_block.py`
- Modify: `octopus/engine.py`（no_secrets 655-656 改为 `guard.block`；`transient()` 遇 security 返回 None）、`octopus/recovery.py`（`schedule` 对 security 自动与手动都不调度）、`octopus/domain.py`（`operate` 2146 起的拒绝；`_attention` 新分支；新增 `Service.security_judge`）、`octopus/server.py`（`POST /v1/runs/<id>/security`）、`octopus/schema.py`、`web/src/features/sessions/SessionView.tsx`（安全阻塞的判断表单）

**Interfaces:**
- Produces:
  - `guard.block(db, run, *, task_id, attempt_id, stage, findings, who) -> None`：Run → blocked；`snapshot["security"] = {stage, task_id, attempt_id, findings, at}`；Attempt → blocked，`failure_type="security"`，`evidence["gate"]="no_secrets"`；事件 `security.blocked`。stage 取 `candidate`、`text`、`preview`、`download`、`completion`、`publish`、`remote_preview`。
  - `guard.active(run) -> dict | None`。
  - `Service.security_judge(actor, rid, body) -> dict`，body `{verdict: "false_positive" | "real", reason: str}`；actor `token_label != "Web login"` 或不是创建者 / Team owner / 管理员 → 403 `forbidden`；没有安全阻塞 → 409。
  - `operate` 在 `guard.active` 时对 continue、recover、confirm、accept、publish、reopen、assign-worker 返回 409 `security_blocked`，message「会话因疑似密钥暂停，需要负责人先判断真假。」

- [ ] **Step 1: 写失败测试**
  - `test_a_secret_in_a_candidate_blocks_without_diagnosis_or_retry`（`test_engine_gates.test_a_secret_fails_no_secrets` 改为引用本用例的断言）。
  - `test_continue_recover_accept_and_publish_are_refused_while_blocked`（控制台、API、MCP Token 三种来源都 409）。
  - `test_only_a_console_owner_can_judge`：MCP Token → 403；非所有者 → 403；所有者控制台登录 → 200。
  - `test_attention_names_file_line_and_kind_masked`：title「发现疑似密钥，会话已暂停」；detail 含文件、行号、种类、`masked`，不含原文；action 说明「这是安全问题，需要你判断：真密钥要删除并轮换；误报请标记并写一句理由」。
  - `test_manual_recover_never_diagnoses_a_security_block`。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现 guard、拒绝、attention、判断接口；判断后的复扫与续行由 Task 10 接上（本任务里判断只更新状态并返回 `{verdict}`）。
- [ ] **Step 4:** 跑 `tests.test_security_block tests.test_engine_gates tests.test_recovery tests.test_mcp_status`，通过。
- [ ] **Step 5:** 提交 `feat(security): 安全阻塞——不诊断、不重试，续作恢复验收发布一律拒绝，只有控制台登录的负责人能判断（D57）`。

### Task 9: 误报记忆

**Files:**
- Create: `octopus/allowances.py`、`tests/test_allowances.py`、`web/src/features/settings/SecretAllowancesDialog.tsx`（+ `.test.tsx`）
- Modify: `octopus/migrations.py:173-176`（追加下一号迁移）、`octopus/domain.py`（`security_judge` 判误报时写记忆；列表与撤销）、`octopus/server.py`（`GET /v1/secret-allowances`、`DELETE /v1/secret-allowances/<id>`）、`octopus/mcp.py`（只读列表工具 `octopus_secret_allowances`）、`web/src/api/hooks.ts`、`web/src/api/types.ts`、`web/src/features/console/context.tsx`、`DialogHost.tsx`、`TopBar.tsx`（设置菜单「安全误报记忆」）

**Interfaces:**
- 表 `secret_allowances(id TEXT PRIMARY KEY, space TEXT NOT NULL, repo TEXT NOT NULL, location TEXT NOT NULL, hash TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL, created_by TEXT NOT NULL, created REAL NOT NULL, revoked_by TEXT, revoked REAL)`，索引 `(space, repo, location, hash)`。`space` 为 `user:<id>` 或 `team:<id>`；`repo` 为规范化的来源地址，新建项目为 `run:<id>`。
- `allowances.scope(run) -> tuple[str, str]`；`allowances.allowed(db, run, location, hash) -> bool`；`allowances.add(db, actor, run, finding, reason) -> str`；`allowances.revoke(db, actor, aid) -> None`；`allowances.listing(db, actor) -> list[dict]`（无 hash 原文以外的敏感内容）。审计动作 `secret.allow`、`secret.revoke`。
- 文件命中的 location 为相对路径；文本命中的 location 为 `<类别>:<task_id>`。

- [ ] **Step 1: 写失败测试**
  - `test_the_same_place_and_content_is_skipped`；`test_another_file_still_blocks`；`test_changed_content_still_blocks`；`test_another_space_on_the_same_repo_still_blocks`；`test_revoking_makes_it_block_again`；`test_add_and_revoke_are_audited`；`test_a_token_cannot_add_or_revoke`（MCP Token → 403，表中无记录）。
  - `SecretAllowancesDialog.test.tsx`：列出记忆（种类、位置、理由、谁、何时），可撤销。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现迁移、模块、接口、控制台；所有扫描点（Task 7、8、11）在调用 `guard.block` 前用 `allowed` 过滤。
- [ ] **Step 4:** 跑 `tests.test_allowances tests.test_migrations tests.test_security_block`、`npm --prefix web test`，通过。
- [ ] **Step 5:** 提交 `feat(security): 误报记忆——按空间与仓库逐处记哈希，只能人加、可撤销、写审计（D57）`。

### Task 10: 判断之后：复扫重判与清理任务

**Files:**
- Modify: `octopus/engine.py`（新增 `Engine.rejudge`）、`octopus/worker.py`（`/candidate mode=commit` 接受 `allow`）、`octopus/sources.py`（`task_candidate` / `unpublishable` 接受 allow 列表）、`octopus/guard.py`（`CLEANUP_PROMPT`、`cleanup(...)`）、`octopus/domain.py`（`security_judge` 调用两条路径）
- Test: `tests/test_security_block.py`

**Interfaces:**
- Consumes: Task 8 `guard`、Task 9 `allowances`。
- Produces:
  - `Engine.rejudge(run_id: str, attempt_id: str) -> None`：只用平台保存的 `attempts.result` 与 `evidence` 重跑 `gates.basic` 与 `_more_gates`，不调用 Worker `/inspect`，不重新派发；`/candidate` 带 `allow: [{path, hash}]`。
  - `guard.CLEANUP_PROMPT`：固定文案，要求删除所列位置（文件、行号、种类）的疑似密钥，改写本会话分支的历史（不改基线），不改其他文件，并在交接中说明。
  - 判为真密钥：同一任务新建 Attempt（同工具、同模型），spec 的 prompt 替换为 `CLEANUP_PROMPT` + 位置清单，`evidence["security_cleanup"] = {"of": <原 attempt>}`；之后按正常门禁判定。

- [ ] **Step 1: 写失败测试**
  - `test_a_false_positive_resumes_the_same_attempt_from_the_remaining_gates`：判误报后没有新 dispatch，checks 照常运行，Attempt accepted，会话继续后续任务。
  - `test_rejudge_uses_the_stored_result_when_the_native_result_is_gone`：FakeWorker 的 inspect 抛 404，重判照样完成。
  - `test_a_rescan_that_still_hits_stops_again`。
  - `test_a_real_key_dispatches_a_cleanup_attempt_and_then_continues`：新 Attempt 的 prompt 以 `CLEANUP_PROMPT` 开头，候选被改写后通过 no_secrets，会话继续。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现；清掉 `snapshot["security"]` 时移入 `snapshot["security_history"]`。
- [ ] **Step 4:** 跑 `tests.test_security_block tests.test_engine_gates tests.test_candidates`，通过。
- [ ] **Step 5:** 提交 `feat(security): 判误报后同一 Attempt 从剩余门禁重判；判真密钥派固定清理任务改写会话分支（D57）`。

### Task 11: 交到人手里的内容都要扫；「不提交」的交付物

**Files:**
- Modify: `octopus/domain.py`（`preview_file` 1760-1777、`archive_entry` 1100、`app` 1617、会话完成处）、`octopus/worker.py`（`archive_plan` 511-564、`app` 764、新增 `/scan`）、`octopus/apps.py`（`Apps.start` 75、`tail` 34）、`octopus/publish.py:60-89`（`receive` 复扫）、`octopus/sources.py`（`task_candidate` 排除 `untracked`）、`octopus/domain.py:54-56`（合同字段 `untracked`）、`octopus/planning.py`（提示：补丁、预览页等写进 `untracked`）
- Test: `tests/test_preview.py`、`tests/test_archive.py`、`tests/test_apps.py`、`tests/test_publish.py`、`tests/test_candidates.py`、`tests/test_security_block.py`

**Interfaces:**
- Consumes: Task 6、8、9。
- Produces:
  - 合同 `untracked: list[str]`（LIMITS (20, 500)）；`task_candidate` 以 pathspec 排除这些路径。
  - Worker `POST /scan {run_id}` → `{findings: [...]}`：扫描相对会话基线新增或改动的已跟踪内容、未跟踪且未被忽略的文件、合同 `untracked` 路径。
  - 命中时：预览拒绝（403 `preview_blocked`）、下载拒绝（409 `archive_secret_found`）、远端预览不启动、发布拒绝；全部调用 `guard.block`（stage 依次 `preview`、`download`、`remote_preview`、`publish`；会话完成前的扫描为 `completion`）。完成后才扫到时关联最后一个产生候选的任务。

- [ ] **Step 1: 写失败测试**
  - `test_preview.test_a_preview_file_with_a_secret_is_refused_and_blocks`。
  - `test_archive.test_a_download_with_a_secret_in_new_git_objects_is_refused`（基线里原有的示例 Key 不算）。
  - `test_apps.test_an_app_with_a_secret_does_not_start`；`test_apps.test_the_app_log_tail_is_masked`。
  - `test_publish.test_the_platform_rescans_a_received_bundle`。
  - `test_candidates.test_untracked_deliverables_stay_out_of_the_candidate`。
  - `test_security_block.test_completion_scan_blocks_a_finished_session`；`test_security_block.test_a_published_session_found_later_cannot_publish_again`。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现各扫描点；平台侧对经它中转的内容（预览文件、发布 bundle）自己扫，Worker 侧对不经平台的内容（下载打包、远端预览、完成前扫描）扫并返回命中。
- [ ] **Step 4:** 跑上述模块与 `tests.test_remote_preview`，通过。
- [ ] **Step 5:** 提交 `feat(security): 预览、下载、远端预览、发布与完成前都扫描；「不提交」的交付物不进候选（D57）`。

### Task 12: 有提示就不自动发布

**Files:**
- Modify: `octopus/domain.py`（`auto_publish_pending`，D47）
- Test: `tests/test_auto_publish.py`

- [ ] **Step 1:** 写失败测试 `test_a_session_with_warnings_is_not_auto_published`（会话页 publish 状态写「有提示，等负责人确认后再发布」）与 `test_without_warnings_it_still_is`。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 自动发布前检查各任务最新 accepted Attempt 的 `evidence.warnings`；手动发布不受影响。
- [ ] **Step 4:** 跑 `tests.test_auto_publish tests.test_publish`，通过。
- [ ] **Step 5:** 提交 `feat(publish): 会话有提示时不自动发布，等负责人确认（D57）`。

### Task 13: 纠错：防空转、平台故障自动重试、replan 豁免

**Files:**
- Modify: `octopus/recovery.py`（`schedule` 381 起、`Coordinator.advance` 548-574、`_replan`）、`octopus/engine.py`（`transient` 29-42、消息 1121-1122）、`octopus/domain.py`（recover 2254-2272 透传 `rediagnose`；`_attention` 2142 前新分支）、`octopus/mcp.py`（`octopus_recover` 接受 `rediagnose`）、`octopus/server.py`、`octopus/schema.py`
- Test: `tests/test_recovery.py`、`tests/test_runtime.py`、`tests/test_replan.py`、`tests/test_budget.py`

**Interfaces:**
- Produces:
  - `recovery.PLATFORM_FAULT`（正则：`AcpSandboxInitFailed|sandbox failed to initialize`、`waiting for memory|never started`，外加 engine 现有的 CANCELLED / EXITED / CLEANUP）。
  - `recovery.waits(rows, task_id, gate) -> int`：该任务、该失败门禁（无门禁时按 failure_type）在最近一次续作或通过之后，连续 `wait_for_user` 的次数。
  - 手动恢复 body `{rediagnose: bool}`；guard 命中且未带 rediagnose → 409 `already_waiting`，message 为上一次 wait_for_user 的 reason 与 instructions。
  - 诊断平台故障重试：同一 recoveries 行换新 `attempt_id`、status 回到 pending、`context["retry_of"]`；事件 `recovery.retry`；计入自动重试预算，不计纠错轮数；每行最多重试 1 次。
  - `transient()` 新返回值 `runtime_sandbox`、`runtime_memory`。
  - replan 豁免：失败 Attempt 的证据显示 checks_pass「无法执行（环境）」或 `failure_type == "contract"` 时，replan 不占「每会话一次」额度（由平台按证据判定，不由协调者声明），豁免最多 2 次。

- [ ] **Step 1: 写失败测试**
  - `test_two_waits_for_the_same_gate_stop_further_diagnosis`；`test_rediagnose_overrides_the_guard`；`test_a_continue_resets_the_guard`；`test_a_diagnosis_that_dies_in_the_sandbox_is_retried_once`（第二次仍失败 → 安全收尾，attention 文案见 Task 15）；`test_a_sandbox_init_failure_of_a_task_is_retried_once`、`test_a_memory_admission_end_is_retried_once`（`test_runtime`）；`test_an_environment_replan_does_not_use_the_quota`（`test_replan`）；`test_budget` 中重试计入 retries 而非 cycles。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 按 Interfaces 实现；防空转的计数复用 `schedule` 已读取的 rows。
- [ ] **Step 4:** 跑 `tests.test_recovery tests.test_runtime tests.test_replan tests.test_budget tests.test_watchdog tests.test_switch_tool tests.test_coordinator_targets`，通过。
- [ ] **Step 5:** 提交 `feat(recovery): 同一根因两次等用户后不再派诊断（可「仍要重新诊断」）；沙箱与内存类平台故障自动重试；环境与合同问题的 replan 不占额度（D57）`。

### Task 14: 等待主机内存要看得见

**Files:**
- Modify: `octopus/worker.py`（`dispatch` 1183、`inspect` 1305-1327、健康信息）、`octopus/engine.py`（未完成分支 1018-1031）、`octopus/recovery.py:548-552`、`octopus/domain.py`（`_attention` running / diagnosing 分支）、`web/src/features/settings/WorkersDialog.tsx`
- Test: `tests/test_waiting.py`、`tests/test_native_unavailable.py`、`tests/test_teams_workers.py`

**Interfaces:**
- Produces: dispatch 回执 `status == "queued"` 或 `reason == "low_memory"` 时 `dispatch.json["queued"] = {reason, detail, since}`；`inspect` 在任务尚未启动（无 `state.json`）且有 `queued` 时返回 `{status: "running", queued}`，不再抛 `native_unavailable`；`evidence["host_wait"]`；Worker 健康信息含 `memory_available_mb`（读 `/proc/meminfo` 的 MemAvailable，读不到为 None）。

- [ ] **Step 1: 写失败测试**
  - `test_waiting.test_a_queued_spawn_shows_waiting_for_host_memory`：attention title「等待主机内存」，detail「执行环境可用内存不足，任务在排队（已等 N 分钟）。这是平台的问题，不是你的任务。」，action「无需操作，内存释放后会自动开始；长时间不动请联系平台管理员。」
  - `test_native_unavailable.test_inspect_of_a_queued_task_is_not_unverifiable`。
  - Worker 健康包含 `memory_available_mb`；`WorkersDialog` 显示「可用内存 X.X GB」。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 跑上述模块与 `npm --prefix web test`，通过。
- [ ] **Step 5:** 提交 `feat(worker): 内存不足排队时显示「等待主机内存」，执行环境页显示主机可用内存（D57）`。

### Task 15: 提示文案

**Files:**
- Modify: `octopus/domain.py`（`_attention` 2033-2152）、`octopus/recovery.py:574`（诊断失败的消息）、`web/src/lib/labels.ts`（`GATE_LABELS`、`EVENT_LABELS`）、`web/src/features/sessions/TaskList.tsx`
- Test: `web/src/features/sessions/SessionView.test.tsx`、`web/src/lib/labels.test.ts`、`tests/test_event_labels.py`、`tests/test_budget.py`（文案用例）

**Interfaces:**
- Produces: `GATE_LABELS`：native_completed「原生执行」、identity_match「实际工具与模型」、handoff_present「交接文件」、outcome_valid「结果文件」、outcome_pass「任务结论」、diff_present「入库改动」、no_secrets「密钥检查」、artifacts_locked「已验收的文件」、checks_pass「检查命令」、tests_fail_on_baseline「测试是否针对改动」、review_bound「审查对象」、read_only「知识沉淀只读」、evaluated「Evaluator」；英文名只在 `title` 提示里。新事件 `security.blocked`、`security.judged`、`secret.allow`、`secret.revoke`、`recovery.retry` 都有中文标签。

- [ ] **Step 1: 写失败测试**：诊断员平台故障 → title「诊断员没能启动」，detail「平台临时故障，已自动重试 1 次。你的任务结果都在。」，action 首选「重新诊断」，次选「查看结果」；diff_present 提示显示为「⚠ 入库改动 · 本任务没有改动入库文件。如果它本来就只产出补丁或报告，可以忽略。」；任何 attention 不含 `failure_type`、`gate` 等英文内部词（对已知分支逐一断言）。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现文案与标签。
- [ ] **Step 4:** 跑 `npm --prefix web test`、`tests.test_event_labels tests.test_budget`，通过。
- [ ] **Step 5:** 提交 `feat(console): 提示写清发生了什么、谁的问题、建议怎么做；门禁显示中文名（D57）`。

### Task 16: /tmp 不再是 Agent 的下载仓库

**Files:**
- Modify: `deploy/install/boundary-lib.sh` 或 `deploy/install/al2023.sh`（为 `octopus-gateway@` 与 Worker 单元设置 `Environment=TMPDIR=/var/lib/octopus/boundaries/<名称>/tmp`，目录 0700 属 crew-<名称>）；新增 `deploy/install/octopus-tmp.conf`（systemd-tmpfiles：该目录下超过 3 天未改动的条目清理）
- Test: `tests/test_deploy_assets.py`

- [ ] **Step 1:** 写失败测试：单元模板含 `TMPDIR=`；tmpfiles 配置存在且只作用于边界的 tmp 目录。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 跑 `tests.test_deploy_assets tests.test_deploy_safety`，通过。
- [ ] **Step 5:** 提交 `fix(deploy): 执行边界的临时目录改到磁盘并定期清理，不再占用内存盘 /tmp（D57）`。

### Task 17: 文档

**Files:** `docs/OPERATIONS.md`（新增「门禁与安全底线（D57）」一节，含原生实时对话无法打码的已知限制）、`skills/` 下 kirocrew-on Skill 模板（状态里的 ⚠、安全阻塞只能在控制台判断、`rediagnose`）、`docs/BACKLOG.md`（关闭 2026-10-21 的三条）、`docs/MVP-RELEASE.md` 进度、`octopus/schema.py` OpenAPI 新路由。

- [ ] **Step 1:** 更新上述文档；`tests.test_skills`、`tests.test_contract` 如引用 Skill 文本须通过。
- [ ] **Step 2:** 提交 `docs: D57 门禁减负与安全底线——OPERATIONS、Skill、BACKLOG、进度`。

### Task 18: 全量回归与本地端到端

- [ ] **Step 1:** `PY=.venv/bin/python tools/regress.sh`：除 Task 0 记下的已有失败外全部通过。
- [ ] **Step 2:** `npm --prefix web run build`；`npm --prefix web run test:browser -- tests/react-console.browser.js` 通过。
- [ ] **Step 3:** 本地起平台（`octopus init` + `octopus serve`，临时数据目录），用浏览器验证：⚠ 显示与「完成 · N 项提示」；安全阻塞的判断表单只在控制台出现；「安全误报记忆」对话框可列出与撤销；MCP Token 判断被拒。记录截图到 `/tmp/d57-e2e/`。

### Task 19: 部署 EC2 并按 spec §10 验收

- [ ] **Step 1:** 通知 `kirocrew-on-ce` 部署时间；`git rebase` 到当时的 `feat/m11-gates` 最新提交并重跑 Task 18 Step 1；确认主机没有进行中的 SSM 与活跃会话。
- [ ] **Step 2:** 从干净的分离工作树执行 `deploy/aws/deploy.sh push`；日志确认「完成：KiroCrew ON <sha>」与各边界 Gateway 状态。
- [ ] **Step 3:** 在 EC2 上逐条跑 spec §10 的 10 个场景（用新建项目会话与测试仓库构造现场；密钥样本由测试脚本运行时生成，绝不使用真实凭证），记录每条的会话 ID 与结果。
- [ ] **Step 4:** 向负责人报告结果；合入 `feat/m11-gates` 等负责人看过之后再做。
