# 候选提交与命名门禁 第 1 期（D37） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个改代码任务结束时在 `octopus/<run>` 上留下候选提交，每个 Attempt 由命名门禁判定并逐条记录；平台亲自运行任务的 `verify` 命令；审查任务绑定它审查的候选；发布只推送已验证的候选。

**Architecture:**
- **门禁判定**：`octopus/gates.py` 的纯函数。
  - Engine 先用它替换 `advance` 中内联的五项检查（行为不变，只是有了名字并记录）。
  - 再接上三类 Worker 调用：
    - `POST /candidate`：做候选、核对审查；
    - `POST /verify/start`：启动检查命令；
    - `POST /verify/status`：查询检查命令。
- **检查命令进行中**：Attempt 仍是 `running`，证据带 `checks`。Engine 下一轮推进时查询它，不再 `/inspect`。
- **版本**：新门禁只对 `execution_contract ≥ 3` 的会话生效。
- **旧 Worker**：回答 404 的旧 Worker 被视为不支持，相应门禁记为 `skip`。

**Tech Stack:** Python 3 标准库 + 主机 git、React + TypeScript + Vitest。

**Spec:** `docs/superpowers/specs/2026-10-14-candidates-gates-report-design.md`（§3–§10）

## Global Constraints

- **门禁顺序**：`native_completed` → `identity_match` → `handoff_present` → `outcome_valid` → `outcome_pass` → `diff_present` → `no_secrets` → `checks_pass`；审查任务为 `review_bound`。
- **门禁记录**：第一个失败的门禁决定结果；判定过的门禁都写入 Attempt 证据 `gates: [{name, status: pass|fail|skip, detail, at}]`。
- **失败类型**：门禁失败的 Attempt 为 `blocked`，`failure_type: "gate"`，证据 `gate: <名称>`；`gate` 不属于 `NEEDS_INPUT`，照常进入自动纠错。
- **代码任务**：`kind` ∈ {`implement`, `test`, `deliver`}，或没有 `kind`。`review`、`design` 不产生候选。
  - `diff_present` 只要求 `implement`、`deliver`、`test`。
  - 没有 `kind` 的旧式任务：记录候选，`diff_present` 记为 `skip`。
- **候选提交**：作者 `KiroCrew ON`，信息 `KiroCrew ON：<任务名>（<run>/<task> 第 <n> 次）`，不运行钩子、不签名。
  - 排除 `.env` 与依赖、缓存目录（`sources.LEFT_OUT`）。
  - 检出秘密时分支不动。
- **检查命令**：`bash -lc <命令>`，在项目目录中运行，标准输入为空，`PATH` 与远程预览相同。
  - 超时：每条 600 秒，合计 1200 秒；超时结束整个进程组。
  - 证据：每条记录 `{command, exit, seconds, tail}`，`tail` 为输出末尾 4096 字节，经 `sources.scrub` 与 `SUSPICIOUS` 隐去。
- **版本**：新会话的 `execution_contract` 为 3；续作与「诊断」只把它提升到至少 2，不降低。
- **不变的部分**：前五个门禁的判定与现行完全一致（现有测试必须原样通过）；`execution_contract < 3` 的会话与旧 Worker 行为不变。
- **提交与部署**：功能分支本地提交；EC2 验收并经负责人确认后推送；部署前确认没有进行中的会话。

## Review Focus

1. **挂住的检查命令**：命令挂住或派生后台子进程（如 `sleep 999 &`）时，超时结束整个进程组，不留下进程；Attempt 以「超时」失败并写明哪条命令。（`test_a_hanging_command_and_its_children_are_ended`）
2. **停止与续作**：检查命令运行中停止或续作会话时，任务被结束；迟到的结果不会改写新的 Attempt。（`test_stopping_during_checks_ends_them_and_ignores_late_results`）
3. **Agent 改写历史**：两个候选之间 Agent 执行了 `git reset`，下一个候选的 `parent` 不是其祖先时，记录 `rewritten: true` 并继续，不报错。（`test_a_rewritten_history_is_marked_not_fatal`）
4. **审查修改代码**：审查任务改了源码时 `review_bound` 失败；只改了排除的文件（`.env`）时仍通过。（`test_a_review_that_changes_code_is_not_bound`）
5. **旧会话、旧 Worker**：`execution_contract` 为 2 的会话只记录前五个门禁，行为不变；Worker 对 `/candidate` 回答 404 时，新门禁记为 `skip` 并照常验收。（`test_old_sessions_and_old_workers_keep_todays_behaviour`）

---

### Task 1: 为现有检查命名并记录（`octopus/gates.py`）

**Files:**
- Create: `octopus/gates.py`
- Modify: `octopus/engine.py`（`advance` 的验收链，约 313–356 行）
- Test: `tests/test_gates.py`

**Interfaces:**
- Produces:
  - `gates.record(name, status, detail=None) -> dict`：返回 `{"name", "status", "detail", "at"}`。
  - `gates.basic(native, result, spec, evidence, report, invalid, lost, missing, blocked_line) -> (error, failure_type, list)`：
    - 按 Global Constraints 的顺序判定前五个门禁；
    - `error` 与 `failure_type` 与现行内联代码逐字相同。

- [ ] **Step 1: 写失败测试**（`tests/test_gates.py`）

```python
"""D37 §4: the first five gates are today's checks, named and recorded; their verdicts do not change."""
import unittest

from octopus import gates
from octopus.engine import BLOCKED

SPEC = {"tool": "claude", "model": "auto", "effort": ""}
DONE = {"done": True, "result": "完成", "error": ""}


def judge(native=DONE, handoff="交接", evidence=None, report=None, invalid=None, lost=False, missing=False, blocked=False):
    return gates.basic(native, {"handoff": handoff}, SPEC, {"actual_backend": "claude", **(evidence or {})}, report,
                       invalid, lost, missing, blocked)


class BasicGateTests(unittest.TestCase):
    def names(self, records):
        return [(r["name"], r["status"]) for r in records]

    def test_all_pass(self):
        error, failure, records = judge(report={"decision": "pass", "summary": "ok", "failure_type": "none"})
        self.assertEqual((error, failure), (None, None))
        self.assertEqual(self.names(records), [("native_completed", "pass"), ("identity_match", "pass"),
                                               ("handoff_present", "pass"), ("outcome_valid", "pass"), ("outcome_pass", "pass")])

    def test_each_failure_keeps_todays_message_and_type(self):
        cases = [
            (dict(native={**DONE, "error": "boom"}), ("boom", "native_error", "native_completed")),
            (dict(native={**DONE, "stopped": True}), ("原生任务已停止", "native_stopped", "native_completed")),
            (dict(missing=True), (BLOCKED, "native_error", "native_completed")),
            (dict(handoff=""), ("缺少本次交接文件", "contract", "handoff_present")),
            (dict(evidence={"actual_backend": "codex"}), ("实际 ACP 后端未核实或与配置不匹配", "model", "identity_match")),
            (dict(invalid="outcome.json 无效"), ("outcome.json 无效", "contract", "outcome_valid")),
            (dict(report={"decision": "changes_requested", "summary": "缺测试", "failure_type": "tests"}),
             ("缺测试", "tests", "outcome_pass")),
        ]
        for kwargs, (message, failure, gate) in cases:
            with self.subTest(gate=gate, message=message):
                error, kind, records = judge(**kwargs)
                self.assertEqual((error, kind), (message, failure))
                self.assertEqual(records[-1]["name"], gate)
                self.assertEqual(records[-1]["status"], "fail")

    def test_a_blocked_reply_with_a_failing_outcome_reports_the_outcome(self):
        report = {"decision": "blocked", "summary": "缺少数据库地址", "failure_type": "input"}
        error, failure, records = judge(missing=True, report=report)
        self.assertEqual((error, failure), ("缺少数据库地址", "input"))
        self.assertEqual((records[-1]["name"], records[-1]["status"]), ("native_completed", "fail"))

    def test_an_expired_login_is_an_auth_failure(self):
        error, failure, _ = judge(native={**DONE, "error": "Session has expired, please log in"})
        self.assertEqual(failure, "auth")

    def test_no_outcome_before_contract_2_skips_the_outcome_gates(self):
        error, failure, records = judge(report=None)
        self.assertIsNone(error)
        self.assertEqual(self.names(records)[-2:], [("outcome_valid", "skip"), ("outcome_pass", "skip")])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `$PY -m unittest tests.test_gates -v`
Expected: FAIL：`No module named 'octopus.gates'` 或 `module 'octopus.gates' has no attribute 'basic'`。

- [ ] **Step 3: 实现** `octopus/gates.py`：把 `engine.py` 中从 `error = native.get("error") or ...` 到 `if invalid and not error:` 的判定原样移入 `basic`，并在每一步追加记录。

```python
"""Named gates (D37 §4): each Attempt's acceptance as a list of recorded verdicts. The first five are the checks the
Engine always made; their messages and failure types are unchanged."""
import re
import time

EXPIRED = re.compile(r"session (?:has )?expired|not logged in", re.I)
BLOCKED = "工作者未完成任务或报告 BLOCKED"
EFFORT = re.compile(r"\[(low|medium|high|xhigh|max)\]$")


def record(name, status, detail=None):
    return {"name": name, "status": status, **({"detail": detail} if detail else {}), "at": time.time()}


def basic(native, result, spec, evidence, report, invalid, lost, missing, blocked_line):
    records = []
    error = native.get("error") or ("原生任务已停止" if native.get("stopped") else None)
    failure = "native_stopped" if native.get("stopped") else "native_error"
    if EXPIRED.search(native.get("error") or ""):
        failure = "auth"
    if not error and (missing or blocked_line) and not lost:
        error = BLOCKED
        if report is not None and report["decision"] != "pass":   # as today: the outcome explains a BLOCKED reply
            error, failure = report["summary"], report["failure_type"]
    records.append(record("native_completed", "fail" if error else "pass", error))
    if error:
        return error, failure, records
    if not result.get("handoff", "").strip():
        records.append(record("identity_match", "skip"))   # checked in today's order: handoff first
        records.append(record("handoff_present", "fail", "缺少本次交接文件"))
        return "缺少本次交接文件", "contract", records
    actual = EFFORT.sub("", evidence.get("actual_model") or "")
    for wrong, message in ((evidence.get("actual_backend") != spec["tool"], "实际 ACP 后端未核实或与配置不匹配"),
                           (spec["model"] not in {"auto", "default"} and actual != spec["model"], "实际模型未核实或与本次配置不匹配"),
                           (bool(spec["effort"] and evidence.get("actual_effort") and evidence["actual_effort"] != spec["effort"]),
                            "实际 Effort 与本次配置不匹配")):
        if wrong:
            records += [record("handoff_present", "pass"), record("identity_match", "fail", message)]
            return message, "model", records
    records += [record("identity_match", "pass"), record("handoff_present", "pass")]
    if report is None and invalid is None:
        records += [record("outcome_valid", "skip"), record("outcome_pass", "skip")]
        return None, None, records
    if invalid:
        records.append(record("outcome_valid", "fail", invalid))
        return invalid, "contract", records
    records.append(record("outcome_valid", "pass"))
    if report["decision"] != "pass":
        records.append(record("outcome_pass", "fail", report["summary"]))
        return report["summary"], report["failure_type"], records
    records.append(record("outcome_pass", "pass"))
    return None, None, records
```

  与现行代码对照：BLOCKED 的回答若带有不通过的 `outcome.json`，错误信息与类型取自 outcome（现行 `(not error or error == BLOCKED)` 一行）。其余顺序一致：先原生错误，再交接文件，再身份。实现后先跑现有的 `tests.test_recovery`、`tests.test_runtime`、`tests.test_mvp`、`tests.test_contract`；任何差异以现行行为为准。

- [ ] **Step 4: Engine 调用** `gates.basic`：
  - 替换内联判定；`evidence["gates"] = records`。
  - 保留 `transient(...)`、`lost`、`result_missing` 的现行处理。
- [ ] **Step 5: 运行**：`$PY -m unittest tests.test_gates tests.test_recovery tests.test_runtime tests.test_mvp tests.test_contract tests.test_journal`。
  - Expected: 全部 PASS（现有测试原样通过）。
- [ ] **Step 6: 提交**：`feat(gates): 为现有的五项验收检查命名并逐条记录（行为不变）`

### Task 2: Worker 候选提交（`POST /candidate`）

**Files:**
- Modify: `octopus/sources.py`（新增 `task_candidate`、`clean_against`）、`octopus/worker.py`（`candidate(body)` 与路由）
- Test: `tests/test_candidates.py`

**Interfaces:**
- Consumes: `sources.publish_candidate`、`sources.unpublishable`、`sources.advance`、`sources.left_out`（D35 第 2 期）。
- Produces: Worker 的 `POST /candidate`。
  - 请求 `{run_id, attempt_id, mode: "commit"|"check", message?, parent?, expected?}`。
  - `commit` 模式回答 `{sha, parent, changed, files, insertions, deletions, rewritten, secrets: []}`。
    - `parent` 缺省为克隆基线（`project.json` 的 `commit`）。
    - `secrets` 非空时分支不动，`sha` 为未采用的候选。
  - `check` 模式回答 `{head, clean, changed_files: []}`：HEAD 与 `expected` 比较，并检查是否有未提交的改动（排除 `left_out` 的路径）。

- [ ] **Step 1: 写失败测试**（`tests/test_candidates.py`，沿用 `tests.test_archive.make_worker`）：
  - `test_a_change_becomes_a_candidate_on_the_branch`：写 `src/a.js` → `commit` → `changed` 为真，`files == 1`，分支 HEAD 等于 `sha`，作者 `KiroCrew ON`，信息含任务名。
  - `test_no_change_is_the_parent_itself`：无改动 → `sha == parent`，`changed` 为假。
  - `test_agent_commits_are_kept`：Agent 先提交一次，再留一处未提交改动 → 候选的父链包含 Agent 的提交。
  - `test_a_secret_leaves_the_branch_where_it_was`：`token = "ghp_…"` → `secrets == ["src/x.js"]`，HEAD 不变。
  - `test_a_rewritten_history_is_marked_not_fatal`（Review Focus 3）：上一个候选之后 `git reset --hard <base>` 再改文件 → `rewritten` 为真，`parent` 为传入值，候选仍生成。
  - `test_check_reports_head_and_pending_changes`：`check` 模式下 HEAD 等于 `expected`；修改 `src/a.js` → `clean` 为假且列出文件；只修改 `.env` → `clean` 为真。
  - `test_an_unknown_route_answers_404`：旧行为——`/candidate` 以外未知路径仍为 404（Engine 依赖它识别旧 Worker）。
- [ ] **Step 2: 运行**：`$PY -m unittest tests.test_candidates -v`。Expected: FAIL（404 或缺少函数）。
- [ ] **Step 3: 实现**：
  - `sources.task_candidate(project, message, branch, scratch, parent)`：
    1. `head = rev-parse HEAD`；
    2. `candidate = publish_candidate(...)`；
    3. `rewritten = parent and merge-base --is-ancestor parent candidate` 不成立；
    4. `secrets = unpublishable(project, parent if not rewritten else <基线>, candidate)`；
    5. 无秘密时 `advance(project, branch, candidate, head)`；
    6. 用 `git diff --numstat parent candidate` 统计 files / insertions / deletions（二进制文件计为 1 个文件、0 行）。
  - `sources.clean_against(project, expected)`：返回 `rev-parse HEAD == expected`，以及 `git status --porcelain=v1 -z` 中不被 `left_out` 排除的路径。
  - `Worker.candidate(body)`：挂到 `serve` 的 `methods` 映射中。`ValueError` 转为 `Error(409, "candidate_failed", scrub(...))`。
- [ ] **Step 4: 运行通过**：`$PY -m unittest tests.test_candidates tests.test_publish tests.test_sources`。
- [ ] **Step 5: 提交**：`feat(worker): 任务候选提交——临时索引提交、检查新增内容、通过才移动会话分支；审查核对 HEAD 与未提交改动`

### Task 3: Worker 运行检查命令（`POST /verify/start`、`/verify/status`）

**Files:**
- Create: `octopus/checks.py`
- Modify: `octopus/worker.py`（`Worker.__init__` 建 `self.checks = Checks(path_prepend=<与 Apps 相同>)`；`_control` 的 stop / close / delete 调 `self.checks.cancel(run_id)`；两个路由）
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces:
  - `Checks(path_prepend, per_command=600, total=1200)`；
  - `start(run_id, attempt_id, project, commands) -> {"job": id}`：同一 Attempt 重复调用时返回同一个任务；
  - `status(job) -> {"state": "running"|"done", "results": [...], "passed": bool|None, "failed": index|None}`；
  - `cancel(run_id)`：结束该会话所有进行中的检查。
  - Worker 路由：`POST /verify/start {run_id, attempt_id, commands}`、`POST /verify/status {job}`。

- [ ] **Step 1: 写失败测试**（`tests/test_checks.py`）：
  - `test_commands_run_in_order_and_stop_at_the_first_failure`：`["true", "echo out; exit 3", "touch never"]` → `results` 两条，退出码为 0、3，`passed` 为假，`failed == 1`，`never` 不存在。
  - `test_output_tail_is_kept_and_scrubbed`：输出 10 KB 再输出 `ghp_` 形式的令牌 → `tail` 长度 ≤ 4096，不含该令牌，含隐去标记 `sources.HIDDEN`。
  - `test_a_hanging_command_and_its_children_are_ended`（Review Focus 1）：`per_command=1`，命令 `sleep 30 & sleep 30` → 2 秒内 `done`，结果写明超时，`exit` 为 `None`。用 `ps` 检查进程组已不存在。
  - `test_the_total_limit_ends_the_rest`：`total=1`，`["sleep 2", "true"]` → 第一条超时，第二条不运行。
  - `test_cancel_ends_a_running_job`：`start` 后 `cancel(run_id)` → `state: done`，`passed` 为假，结果标「已停止」。
  - `test_the_same_attempt_gets_the_same_job`：重复 `start` 返回同一个 `job`。
- [ ] **Step 2: 运行**：`$PY -m unittest tests.test_checks -v`。Expected: FAIL（`No module named 'octopus.checks'`）。
- [ ] **Step 3: 实现** `octopus/checks.py`：
  - 每个任务一个后台线程，顺序运行命令。
  - 每条命令：`subprocess.Popen(["bash", "-lc", cmd], cwd=project, stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, start_new_session=True, env={**os.environ, "PATH": prepend + os.pathsep + PATH})`。
  - 输出边读边保留末尾 4096 字节。
  - 超时用 `os.killpg(pgid, SIGTERM)`，5 秒后 `SIGKILL`，再 `wait`（同 `apps._kill`）。
  - 任务记录在内存字典中（Worker 重启后不存在：`status` 回答 404 `check_unknown`，Engine 视为需重新运行）。
- [ ] **Step 4: 运行通过**：`$PY -m unittest tests.test_checks tests.test_apps`。
- [ ] **Step 5: 提交**：`feat(worker): 运行任务的检查命令——顺序执行、每条与合计超时并结束进程组、保留隐去凭证的输出末尾、停止会话时结束`

### Task 4: Engine 接上候选、检查命令与审查绑定

**Files:**
- Modify:
  - `octopus/engine.py`：`advance`——前五个门禁通过之后的新流程，以及检查进行中的查询；
  - `octopus/domain.py`：
    - 新会话 `execution_contract=3`（约 762 行）；
    - 续作 / 诊断改为 `max(old, 2)`（约 1374、1410 行）；
  - `octopus/recovery.py`：
    - `quality_instructions` 对审查任务要求 `reviewed_commit`；
    - `schedule` 的上下文带 `gates`；
  - `octopus/worker.py`：审查任务派发时把 `candidate` 写入 `context.json` 与 Prompt。
- Test: `tests/test_engine_gates.py`（沿用 `tests.test_contract` 的 `request=worker` 假 Worker 方式）

**Interfaces:**
- Consumes：Task 1 的 `gates.basic`、`gates.record`；Task 2 的 `/candidate`；Task 3 的 `/verify/start`、`/verify/status`。
- Produces：
  - 证据 `candidate`、`checks: {job, started}`（进行中）、`check_results`、`gates`；
  - `failure_type: "gate"` 与 `gate`；
  - 派发体中的 `candidate: {sha, parent}`（审查任务）。

- [ ] **Step 1: 写失败测试**（`tests/test_engine_gates.py`）。假 Worker 记录调用，并按测试设定回答 `/candidate`、`/verify/*`：
  - `test_a_code_task_gets_a_candidate_and_its_checks_run`：`execution_contract` 3、`kind: implement`、`verify: ["npm test"]`。
    - 调用顺序为 `/inspect` → `/candidate` → `/verify/start` → `/verify/status`（running）→ `/verify/status`（done，passed）；
    - 之后任务才为 `accepted`；
    - `gates` 末尾为 `diff_present`、`no_secrets`、`checks_pass`，均 pass。
  - `test_failing_checks_send_the_task_back_through_recovery`：`/verify/status` 回答 `passed: false` →
    - Attempt `blocked`，`failure_type: gate`，`gate: checks_pass`；
    - 恢复记录的上下文含失败命令与 `tail`。
  - `test_an_empty_diff_fails_an_implement_task`：`changed: false` → `diff_present` fail；`kind` 缺省时为 skip。
  - `test_a_secret_fails_no_secrets`：`secrets: ["src/x.js"]` → `no_secrets` fail，详情只列文件。
  - `test_a_review_is_bound_to_the_latest_candidate`：
    - 实现任务候选 `c1` → 审查任务派发体含 `candidate.sha == "c1"`；
    - 审查 outcome `reviewed_commit: "c1"`，`check` 回答 `clean` → `review_bound` pass；
    - `reviewed_commit: "c0"` → fail。
  - `test_a_review_that_changes_code_is_not_bound`（Review Focus 4）：`check` 回答 `clean: false, changed_files: ["src/a.js"]` → fail，详情为「审查不应修改代码」。
  - `test_stopping_during_checks_ends_them_and_ignores_late_results`（Review Focus 2）：检查进行中 `operate(stop)` →
    - 下一轮 `/control` 之后不再查询 `/verify/status`；
    - 续作后新 Attempt 的证据不含旧任务的结果。
  - `test_old_sessions_and_old_workers_keep_todays_behaviour`（Review Focus 5）：
    - `execution_contract` 2 → 不调用 `/candidate`，`gates` 只有前五个；
    - `execution_contract` 3 而 `/candidate` 抛出 `Error(404, "not_found")` → 新门禁均为 `skip`（原因「Worker 版本不支持」），任务 `accepted`。
  - `test_continue_does_not_lower_the_contract`：3 的会话续作后仍为 3。
- [ ] **Step 2: 运行**：`$PY -m unittest tests.test_engine_gates -v`。Expected: FAIL。
- [ ] **Step 3: 实现**（`engine.advance`，在 `state = "blocked" if error else "accepted"` 之前）：
  - **何时生效**：`execution_contract ≥ 3`、无错误，且任务为代码任务或审查任务。
  - **代码任务**：
    1. 调用 `/candidate`（`mode: commit`，`parent` 为上一个已验收代码任务证据中的 `candidate.sha`；没有时省略）。
    2. 判定 `diff_present`、`no_secrets`。
    3. 有 `verify` 时调用 `/verify/start`，写入 `evidence["checks"]`，Attempt 保持 `running` 并返回（本轮不验收）。
  - **检查进行中**：在 `/inspect` 之前，若当前 Attempt 的证据有 `checks` 且无结果，改为调用 `/verify/status`。
    - `running` 时返回；
    - `done` 时写 `check_results` 与 `checks_pass`，再按门禁结果 `accepted` 或 `blocked` 并 `recovery.automatic`；
    - `check_unknown`（Worker 重启）时重新 `/verify/start` 一次，再丢失则 `checks_pass` 失败，详情「检查命令中断」。
  - **审查任务**：
    1. 派发时附 `candidate`（最近一个已验收代码任务的候选）。
    2. 完成时调用 `/candidate`（`mode: check`，`expected` 为该 sha），并核对 `report["reviewed_commit"]`，判定 `review_bound`。
  - **门禁失败**：`error` 为门禁详情的一句话，`failure_type = "gate"`，`evidence["gate"] = 名称`。
  - **旧 Worker**：`/candidate` 或 `/verify/start` 抛出 404 时，相应门禁记为 `skip`。
  - **停止后的旧任务**：`desired != active` 时不再查询检查任务，`/control` 已让 Worker 结束它；续作新建的 Attempt 没有 `checks`。
- [ ] **Step 4: 运行通过**：`$PY -m unittest tests.test_engine_gates tests.test_gates tests.test_recovery tests.test_runtime tests.test_mvp tests.test_contract`。
- [ ] **Step 5: 提交**：`feat(engine): 代码任务的候选与检查命令、审查绑定候选——门禁失败进入自动纠错；新会话执行契约 3，旧会话与旧 Worker 行为不变`

### Task 5: 只发布已验证的候选

**Files:**
- Modify:
  - `octopus/domain.py`：`_verified(db, rid)`、`_view` 的 `verified`、`publish` 传 `commit`；
  - `octopus/worker.py`：`publish_plan` 接受 `commit`，核对 HEAD 等于它且干净，否则 409 `publish_unverified`；
  - `octopus/sources.py`：复用 `clean_against`。
- Test: `tests/test_publish.py`（`PublishTests` 新增）

**Interfaces:**
- Produces:
  - `Service._verified(db, rid) -> sha | None`：
    - 最后一个 `review_bound` 与 `outcome_pass` 均为 pass 的审查所绑定的候选；
    - 没有审查任务时，为最后一个门禁全部通过的代码任务候选；
    - 都没有时返回 None（旧会话）。
  - 会话视图 `verified: {sha, task_id} | null`。

- [ ] **Step 1: 写失败测试**：
  - `test_only_the_verified_candidate_is_published`：会话证据中已验证候选为 `c1`（工作区 HEAD 也为 `c1`）→ 发布推送 `c1`，Worker 收到的请求含 `commit: c1`，Worker 没有再做自动提交。
  - `test_changes_after_the_last_review_are_refused`：HEAD 为 `c1`，但 `src/a.js` 有未提交改动 → 409 `publish_unverified`，提示含「续作」。
  - `test_sessions_without_candidates_publish_as_before`：沿用现有测试，不传 `commit`。
- [ ] **Step 2: 运行**：`$PY -m unittest tests.test_publish -v`。Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**：
  - `publish_plan`：给了 `commit` 时跳过 `publish_candidate` 与自动提交。
    1. `clean_against(project, commit)` 不通过 → 409 `publish_unverified`。
    2. 仍检查 `unpublishable(base, commit)`。
    3. `head = commit`。
  - `Service.publish`：`commit = self._verified(db, rid)`；不为 None 时放入 Worker 请求。
- [ ] **Step 4: 运行通过**：`$PY -m unittest tests.test_publish tests.test_candidates`。
- [ ] **Step 5: 提交**：`feat(credentials): 发布只推送已验证的候选，之后有改动时 409 publish_unverified`

### Task 6: 控制台、MCP 与文档

**Files:**
- Modify:
  - `web/src/features/sessions/TaskList.tsx`：每个 Attempt 一行门禁（✓ ✗ –，`title` 为详情）、候选短 sha 与 `+增 −删`；
  - `web/src/features/sessions/SessionView.tsx`：「已验证候选」；
  - `web/src/features/sessions/PublishControl.tsx`：「将推送 <短 sha>」；
  - `web/src/api/types.ts`：`Gate`、`Candidate`、`Run.verified`；
  - `octopus/mcp.py`：`compact_status` 的每个任务带 `gates` 摘要（失败的门禁名与详情首行）与 `candidate`，会话带 `verified`；
  - `skills/kirocrew-on/SKILL.md`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`。
- Test: `web/src/features/sessions/SessionView.test.tsx`、`tests/test_mcp_roles.py`（或 `tests/test_mcp.py` 中 `compact_status` 的测试）

- [ ] **Step 1: 写失败测试**：
  - 控制台：Attempt 有 `gates` 时显示 `checks_pass ✗` 与详情；有 `candidate` 时显示 `abc1234 +12 −3`；会话 `verified` 时显示「已验证候选 abc1234」；发布按钮旁显示「将推送 abc1234」。
  - MCP：`compact_status` 的任务含 `gates: [{"name": "checks_pass", "status": "fail", "detail": "npm test 退出码 1"}]`，会话含 `verified`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（`npm --prefix web test`、`$PY -m unittest tests.test_mcp_roles`）。
- [ ] **Step 5: 提交**：`feat(console): 任务的门禁与候选、会话的已验证候选；MCP 状态带门禁摘要；文档`

### Task 7: 回归与 EC2 验收

- [ ] **Step 1:** `PYTHON=$PY tools/regress.sh` 全部通过。
- [ ] **Step 2:** 部署前确认没有进行中的会话；部署平台与 `w1`。
- [ ] **Step 3: 验收**（`LiboMa/bedrock-claude-call`，负责人同意的测试仓库）：
  1. 一个实现加审查的会话：实现任务的 `verify` 先引用一个尚不存在的检查（如 `python -m pytest tests/test_publish_check.py`），检查失败触发自动返工，返工后通过；
  2. 审查绑定候选；
  3. 会话页显示门禁与候选；
  4. 发布推送已验证候选，PR 中有各任务的提交；
  5. 发布前在工作区留一处改动 → 409 `publish_unverified`。
- [ ] **Step 4:** 向负责人报告；确认后推送。
