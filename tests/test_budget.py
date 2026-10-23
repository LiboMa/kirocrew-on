"""D42: a Session's automatic recovery budget — the Coordinator's rounds, automatic retries and time — counted from the
Session's start or the user's latest continue / requested diagnosis; what ran out is said."""
import json
import re
import time
import unittest

from octopus import recovery
from octopus.security import Error
from tests.test_replan import ReplanCase

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["可运行"]},
         {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查", "kind": "review", "acceptance": ["无阻断问题"]}]
CANCELLED = {"done": True, "result": "", "error": "cancelled (stop_reason=cancelled): turn cancelled by the runtime"}
RETRY = {"action": "retry", "target_task_id": "review", "reason": "审查未通过", "instructions": "修复后复审"}
SANDBOX = {"error": "kiro_crew.acp.client.AcpSandboxInitFailed: sandbox failed to initialize"}
TORN_DOWN = {"stopped": True, "error": "ACP process exited (code=-9, SIGKILL, killed by Crew (forced stop))"}
WAIT = {"action": "wait_for_user", "missing": "input", "reason": "等待", "instructions": "检查"}   # D71: what only the user has


class BudgetCase(ReplanCase):
    def setUp(self):
        super().setUp()
        self.cancelled, self.failing = set(), set()
        self.decisions = [RETRY] * 2 + [WAIT] * 4

    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        record = self.bodies.get(body.get("attempt_id"), {})
        if path == "/inspect" and not record.get("coordination"):
            spec = record["spec"]
            number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == spec["id"])
            if (spec["id"], number) in self.cancelled:
                result["native"] = dict(CANCELLED)
        return result

    def begin(self, **policy):
        return self.start(STEPS, recovery={"enabled": True, "max_cycles": 2, **policy})

    def run_row(self, rid):
        with self.db.read() as db:
            return db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()

    def budget(self, rid):
        with self.db.read() as db:
            return recovery.budget(db, db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())

    def attempts(self, run, task):
        return sorted({t["id"]: t for t in run["tasks"]}[task]["attempts"], key=lambda a: a["number"])


class BudgetTests(BudgetCase):
    def test_the_budget_settings_are_checked_and_defaults_not_stored(self):
        self.assertEqual(recovery.policy({"max_retries": 2})["max_retries"], 2)
        self.assertEqual(recovery.policy({"budget_minutes": 60})["budget_minutes"], 60)
        self.assertEqual(recovery.policy({"max_retries": 4, "budget_minutes": 720}), {"enabled": True, "max_cycles": 2})
        self.assertEqual(recovery.policy({"max_retries": 0})["max_retries"], 0)
        for bad in ({"max_retries": -1}, {"max_retries": 21}, {"max_retries": "2"}, {"max_retries": True},
                    {"budget_minutes": 10}, {"budget_minutes": 5000}):
            with self.subTest(bad), self.assertRaises(Error):
                recovery.policy(bad)

    def test_an_existing_session_counts_from_its_creation(self):
        rid = self.begin()
        budget = self.budget(rid)
        self.assertEqual(budget["since"], self.run_row(rid)["created"])
        self.assertEqual((budget["cycles"], budget["retries"], budget["minutes"]),
                         ({"used": 0, "limit": 2}, {"used": 0, "limit": 4}, {"used": 0, "limit": 720}))
        self.assertIsNone(budget["exhausted"])

    def test_a_continue_gives_the_coordinator_new_rounds(self):
        self.failing = {("review", 1), ("review", 2), ("review", 3)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(run["recovery"]["history"]), 1)
        self.assertEqual(self.budget(rid)["exhausted"], "cycles")
        self.service.operate(self.actor, rid, "continue", {"request_id": "budget-continue-1", "expected_version": run["version"],
                                                           "task_id": "review"})
        run = self.tick(rid, 40)
        self.assertEqual(len(run["recovery"]["history"]), 2)   # diagnosed again after the user's continue
        self.assertEqual(self.budget(rid)["cycles"], {"used": 1, "limit": 1})

    def test_a_requested_diagnosis_is_not_counted_and_resets_the_window(self):
        self.failing = {("review", 1), ("review", 2), ("review", 3)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(self.budget(rid)["exhausted"], "cycles")
        self.service.operate(self.actor, rid, "recover", {"request_id": "budget-recover-1", "expected_version": run["version"]})
        run = self.tick(rid, 40)
        history = run["recovery"]["history"]
        self.assertEqual([bool(r["context"].get("manual")) for r in history], [False, True, False])
        self.assertEqual(self.budget(rid)["cycles"], {"used": 1, "limit": 1})

    def test_the_retry_budget_spans_the_session(self):
        self.cancelled = {("code", 1), ("review", 1)}
        self.decisions = [RETRY, WAIT]
        rid = self.begin(max_retries=1)
        run = self.tick(rid, 40)
        code, review = self.attempts(run, "code"), self.attempts(run, "review")
        self.assertEqual(code[1]["evidence"].get("transient_retry_of"), code[0]["id"])   # the first one is retried
        self.assertEqual(len(run["recovery"]["history"]), 1)                              # the second goes to diagnosis
        self.assertFalse(review[1]["evidence"].get("transient_retry_of"))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.budget(rid)["retries"], {"used": 1, "limit": 1})

    def faulty_diagnoses(self, faults):
        """The next diagnoses end with ``faults`` in turn (D57 §6.3: the platform's, not the Coordinator's)."""
        original = self.request
        def request(config, path, body, **kwargs):
            result = original(config, path, body, **kwargs)
            if path == "/inspect" and self.bodies[body["attempt_id"]].get("coordination") and faults:
                self.decisions.insert(0, json.loads(result.pop("decision")))   # its decision was never read
                result["native"] = {**result["native"], **faults.pop(0)}
            return result
        self.engine = __import__("octopus.engine", fromlist=["Engine"]).Engine(self.db, request)

    def diagnoses(self):
        return [b for b in self.bodies.values() if b.get("coordination")]

    def test_a_diagnosis_retried_after_a_platform_fault_is_a_retry_not_a_cycle(self):
        self.failing = {("review", 1)}
        self.faulty_diagnoses([dict(SANDBOX)])
        rid = self.begin()
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.diagnoses()), 2)
        budget = self.budget(rid)
        self.assertEqual((budget["cycles"]["used"], budget["retries"]["used"]), (1, 1))

    def test_the_retry_budget_spans_tasks_and_diagnoses(self):
        self.cancelled, self.failing = {("code", 1)}, {("review", 1)}
        self.faulty_diagnoses([dict(SANDBOX)])
        rid = self.begin(max_retries=1)
        run = self.tick(rid, 40)
        self.assertEqual(self.attempts(run, "code")[1]["evidence"].get("transient_retry_of"), self.attempts(run, "code")[0]["id"])
        self.assertEqual(len(self.diagnoses()), 1)   # the one retry was the task's: the diagnosis is not retried
        self.assertEqual(self.budget(rid)["retries"], {"used": 1, "limit": 1})
        # D71: the diagnosis decided nothing, so the failed task is retried as it failed (a cycle, not a retry)
        self.assertEqual(run["recovery"]["history"][0]["decision"]["fallback_from"], "none")
        self.assertEqual(run["recovery"]["history"][0]["decision"]["reason"],   # Task 15: which limit refused the retry
                         "诊断员没能启动（平台临时故障，自动重试已达到 1 次上限，没有再试）：原生运行时的沙箱未能初始化")

    def test_a_diagnosis_not_retried_because_retries_are_off_says_so(self):   # Task 15
        self.failing = {("review", 1)}
        self.faulty_diagnoses([dict(SANDBOX)])
        run = self.tick(self.begin(max_retries=0), 40)
        self.assertEqual(run["recovery"]["history"][0]["decision"]["reason"],
                         "诊断员没能启动（平台临时故障，自动重试已关闭，没有再试）：原生运行时的沙箱未能初始化")

    def test_a_diagnosis_the_platform_failed_twice_says_so_and_the_task_is_retried(self):   # Task 15; D71: no stop
        self.failing = {("review", 1)}
        self.faulty_diagnoses([dict(SANDBOX), dict(SANDBOX)])
        rid = self.begin()
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "completed")
        decision = run["recovery"]["history"][0]["decision"]
        self.assertEqual(decision["reason"], "诊断员没能启动（平台临时故障，已自动重试 1 次）：原生运行时的沙箱未能初始化")
        self.assertIn("按失败原因直接修复后重新执行本任务", decision["instructions"])

    def test_a_diagnosis_the_platform_ended_twice_says_whose_problem_and_what_to_do(self):   # Task 15
        self.failing = {("review", 1)}
        self.faulty_diagnoses([dict(TORN_DOWN), dict(TORN_DOWN)])   # KiroCrew's teardown: a stop nobody made
        rid = self.begin()
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        attention = run["attention"]
        self.assertEqual(attention["title"], "诊断员没能完成")
        self.assertEqual(attention["detail"], "平台临时故障：原生任务被 KiroCrew 回收（期间没有人打开原生页面）。已自动重试 1 次，仍未成功。"
                                              "这是平台的问题，不是你的任务；你的任务结果都在。")
        self.assertEqual(attention["action"], "点「请求协调者诊断」重新诊断；或展开该任务的「执行证据与历史 Attempt」查看结果。")

    def test_past_the_time_budget_nothing_automatic_starts(self):
        self.cancelled = {("code", 1)}
        rid = self.begin(budget_minutes=30)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(self.attempts(run, "code")), 1)
        self.assertEqual(run["recovery"]["history"], [])
        self.assertEqual(self.budget(rid)["exhausted"], "minutes")

    def test_a_continue_restarts_the_clock(self):
        rid = self.begin(budget_minutes=30)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-?, snapshot=json_set(snapshot,'$.budget_waited',60) WHERE id=?",
                       (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "completed")
        before = time.time()
        self.service.operate(self.actor, rid, "continue", {"request_id": "budget-continue-2", "expected_version": run["version"],
                                                           "task_id": "review"})
        self.assertGreaterEqual(json.loads(self.run_row(rid)["snapshot"])["budget_since"], before)
        self.assertNotIn("budget_waited", json.loads(self.run_row(rid)["snapshot"]))   # a new window has waited for nothing
        self.assertEqual(self.budget(rid)["minutes"]["used"], 0)


class BudgetVisibleTests(BudgetCase):
    def test_the_summary_and_the_attention_say_the_rounds_ran_out(self):
        self.failing = {("review", 1), ("review", 2)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(run["recovery"]["budget"]["exhausted"], "cycles")
        self.assertEqual(run["recovery"]["budget"]["cycles"], {"used": 1, "limit": 1})
        self.assertTrue(run["attention"]["action"].endswith(
            "自动纠错已达到 1 轮上限（自会话开始）；续作会重新计算预算。"), run["attention"]["action"])

    def test_the_attention_says_the_retries_ran_out(self):
        self.cancelled = {("code", 1), ("review", 1)}
        rid = self.start(STEPS, recovery={"enabled": False, "max_cycles": 2, "max_retries": 1})
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(self.attempts(run, "review")), 1)
        self.assertIn("自动重试已达到 1 次上限（自会话开始）", run["attention"]["action"])

    def test_the_note_names_what_refused_not_what_ran_out(self):
        # the Coordinator waited for the user; hours later neither time nor retries are why the Session stopped
        self.failing, self.decisions = {("review", 1)}, [WAIT]
        rid = self.begin(max_retries=0)
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "blocked")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (13 * 3600, rid))
        action = self.service.run(self.actor, rid)["attention"]["action"]
        self.assertNotIn("自动纠错时长", action)
        self.assertNotIn("自动重试", action)

    def test_a_disabled_session_past_the_time_says_why_it_was_not_retried(self):
        self.cancelled = {("code", 1)}
        rid = self.start(STEPS, recovery={"enabled": False, "max_cycles": 2, "budget_minutes": 30})
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertEqual(len(self.attempts(run, "code")), 1)
        self.assertIn("自动纠错时长已超过 30 分钟", run["attention"]["action"])

    def test_a_settled_session_stops_counting(self):
        rid = self.begin()
        run = self.tick(rid, 40)
        self.assertEqual(run["status"], "completed")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-?, updated=created-? WHERE id=?", (13 * 3600, 13 * 3600 - 120, rid))
        budget = self.budget(rid)
        self.assertEqual(budget["minutes"]["used"], 2)    # the time it ran, not the time since
        self.assertIsNone(budget["exhausted"])
        from octopus import report
        self.assertNotIn("预算（", report.markdown(self.service.run(self.actor, rid)))   # nothing recovered, nothing to show

    def test_a_zero_limit_is_not_shown_as_used_up(self):
        self.assertIsNone(self.budget(self.begin(max_retries=0))["exhausted"])

    def test_assigning_a_worker_restarts_the_budget(self):
        with self.db.transaction() as db:   # no environment to choose from: the Session waits for one
            db.execute("UPDATE workers SET enabled=0")
        rid = self.service.start(self.actor, {"request_id": "budget-unassigned-1", "input": "实现并验证", "steps": STEPS,
                                              "source": {"type": "new"}, "recovery": {"enabled": True, "max_cycles": 2}})["id"]
        run = self.service.run(self.actor, rid)
        self.assertEqual(run["status"], "waiting_worker")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (13 * 3600, rid))
            db.execute("UPDATE workers SET enabled=1")
        self.service.operate(self.actor, rid, "assign-worker", {"request_id": "budget-assign-1", "expected_version": run["version"],
                                                                "worker_id": "worker"})
        self.assertEqual(self.budget(rid)["minutes"]["used"], 0)

    def test_restarting_the_budget_is_not_logged_as_a_settings_change(self):
        self.failing = {("review", 1), ("review", 2), ("review", 3)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        with self.db.read() as db:
            before = db.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND kind='run.settings'", (rid,)).fetchone()[0]
        self.service.operate(self.actor, rid, "recover", {"request_id": "budget-recover-2", "expected_version": run["version"]})
        with self.db.read() as db:
            after = db.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND kind='run.settings'", (rid,)).fetchone()[0]
            command = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='command.recover'", (rid,)).fetchone()
        self.assertEqual(after, before)   # execution_contract was already 2: nothing about the settings changed
        self.assertNotIn("snapshot", json.loads(command["payload"]).get("changes", {}))

    def test_the_attention_says_the_time_ran_out(self):
        self.cancelled = {("code", 1)}
        rid = self.begin(budget_minutes=30)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET created=created-? WHERE id=?", (31 * 60, rid))
        run = self.tick(rid, 40)
        self.assertIn("自动纠错时长已超过 30 分钟（自会话开始）", run["attention"]["action"])

    def test_the_line_says_where_the_budget_counts_from(self):
        value = {"reset": False, "cycles": {"used": 1, "limit": 2}, "retries": {"used": 0, "limit": 4},
                 "minutes": {"used": 35, "limit": 720}, "exhausted": None}
        self.assertEqual(recovery.budget_line(value), "预算（自会话开始）：纠错 1/2 轮 · 自动重试 0/4 次 · 35/720 分钟")
        value.update(reset=True, exhausted="cycles", cycles={"used": 2, "limit": 2})
        self.assertEqual(recovery.budget_line(value),
                         "预算（自上次续作 / 请求诊断起）：纠错 2/2 轮（已用完） · 自动重试 0/4 次 · 35/720 分钟")

    def test_mcp_and_the_report_show_the_budget(self):
        from octopus import mcp, report
        self.failing = {("review", 1), ("review", 2)}
        rid = self.start(STEPS, recovery={"enabled": True, "max_cycles": 1})
        run = self.tick(rid, 40)
        self.assertEqual(mcp.compact_status(run, [])["session"]["recovery"]["budget"], run["recovery"]["budget"])
        line = recovery.budget_line(run["recovery"]["budget"])
        self.assertIn(line, report.markdown(run))
        self.assertIn(line, report.html(run))


# The platform's own names that must not reach a person (Task 15, D57 §8): snake_case or dotted identifiers (field, gate and
# setting names) and the decision and status words; the MCP tools a Coding Agent is pointed to are the exception.
INTERNAL = re.compile(r"(?<![A-Za-z0-9_])(?!octopus_)[a-z]+(?:[_.][a-z]+)+(?![A-Za-z0-9_])"
                      r"|(?<![A-Za-z])(?:gates?|failure|verify|retry|rework|replan|missing|credentials?|repository|"
                      r"rediagnose|blocked|diagnosing)(?![A-Za-z])")
BUDGET = {"reset": False, "cycles": {"used": 2, "limit": 2}, "retries": {"used": 0, "limit": 0},
          "minutes": {"used": 3, "limit": 720}, "exhausted": "cycles"}


def view(status="blocked", *, error="检查命令失败：npm test（退出码 1）", failure="tests", evidence=None, latest=None,
         task="blocked", tool="claude", **extra):
    """A Session's detail view as ``Service._attention`` reads it, its first task in ``task`` with one Attempt."""
    attempt = {"id": "att-1", "error": error, "spec": {"tool": tool},
               "evidence": {**({"failure_type": failure} if failure else {}), **(evidence or {})}}
    return {"status": status, "error": error, "source": {"type": "git"},
            "native_parent": None if task == "pending" else "octopus-run",
            "tasks": [{"id": "code", "status": task, "spec": {"name": "实现", "tool": tool}, "attempts": [attempt]}],
            "recovery": {"latest": latest, "budget": BUDGET}, **extra}


def diagnosis(status="blocked", **fields):
    return {"number": 1, "status": status, "failed_attempt_id": "att-1", "decision": None, "evidence": {}, "context": {},
            **fields}


class AttentionCopyTests(unittest.TestCase):
    """Task 15 (D57 §8): every stop or hint says what happened, whose problem it is and what to do — in words people read."""

    def attention(self, run, space=None):
        from octopus.domain import Service
        return Service._attention(run, space)

    def cases(self):
        """name: (view, space, the title of the branch it reaches)."""
        coordinator = "目标 API 需要测试账号；请配置测试账号后继续"
        retrying = lambda status, fault: diagnosis(status, context={"retry_of": "d1",
                                                                     "retry": {"transient": fault, "at": 1.0}})
        teardown = {"platform_fault": "runtime_teardown"}
        return {
            "环境已更换": (view("waiting_worker", worker_id="w1", error="执行环境已更换"), {"team": None, "workers": 1},
                      "个人空间的执行环境已更换"),
            "环境已退役（没有可选的）": (view("waiting_worker", worker_id="w1", error="执行环境已退役"),
                              {"team": "前端", "workers": 0}, "Team「前端」的执行环境已退役"),
            "待选 Worker": (view("waiting_worker"), {"team": None, "workers": 2}, "需求已保存，等待选择 Worker"),
            "Team 环境停用": (view("waiting_worker"), {"team": "前端", "workers": 0, "disabled": True}, "Team「前端」的执行环境已停用"),
            "Team 没有 Worker": (view("waiting_worker"), {"team": "前端", "workers": 0}, "Team「前端」没有可用 Worker"),
            "个人空间没有 Worker": (view("waiting_worker"), {"team": None, "workers": 0}, "个人空间没有可用 Worker"),
            "等待批准": (view("waiting_approval", error=None), None, "等待批准"),
            "自动发布未完成": (view("completed", publish_auto={"ok": False, "error": "凭证没有写权限"}), None, "自动发布未完成"),
            "自动发布未完成（新仓库）": (view("completed", source={"type": "new"}, publish_auto={"ok": False, "error": "仓库已存在"}),
                              None, "自动发布未完成"),
            "新仓库待确认": (view("completed", publish_suggestion={"repository": {"owner": "acme", "name": "app"}}), None,
                       "发布到新仓库待确认"),
            "即将继续": (view("awaiting_acceptance"), None, "即将继续"),
            "即将开始": (view("awaiting_confirmation"), None, "即将开始"),
            "诊断中": (view("diagnosing", latest=diagnosis("running")), None, "协调者正在诊断与安排修复"),
            "诊断等待批准": (view("diagnosing", latest=diagnosis("waiting_approval")), None, "诊断在等待批准一次工具调用"),
            "重试的诊断等待批准": (view("diagnosing", latest=retrying("waiting_approval", "runtime_sandbox")), None,
                          "诊断在等待批准一次工具调用"),
            "诊断重试中": (view("diagnosing", latest=retrying("pending", "runtime_sandbox")), None, "诊断员没能启动，正在自动重试"),
            "回收后诊断重试中": (view("diagnosing", latest=retrying("running", "runtime_teardown")), None,
                         "诊断员没能完成，正在自动重试"),
            "资源排队": (view("running", task="running", evidence={"queued": {"since": 1.0, "reason": "low_memory"}}), None,
                     "排队：等待执行环境资源（执行环境内存不足）"),
            "并发排队": (view("ready", task="pending", quota_wait={"scope": "user", "used": 2, "limit": 2}), None,
                     "排队：已达到并发上限（你同时运行的任务已达 2 个，上限 2）"),
            "不能推送": (view(error="任务要求推送到远端仓库", failure="auth"), None, "Agent 不能推送或创建 PR"),
            "不能推送（无仓库）": (view(error="任务要求推送到远端仓库", failure="auth", source={"type": "none"}), None,
                           "Agent 不能推送或创建 PR"),
            "不能推送（新项目）": (view(error="任务要求推送到远端仓库", failure="auth", source={"type": "new"}), None,
                           "Agent 不能推送或创建 PR"),
            "环境停用": (view(error="Worker 已停用"), None, "执行环境已停用"),
            "原生停止": (view(error="原生任务已停止", failure="native_stopped"), None, "实现：已停止"),
            "凭证不可用": (view(task="pending", credentials={"source": {"unavailable": True}}, error="仓库凭证已停用"), None,
                      "仓库凭证不可用"),
            "准备未完成": (view(task="pending", error="克隆仓库失败"), None, "项目准备未完成"),
            "看门狗": (view(error="原生任务 31 分钟没有进展", failure="native_error",
                         evidence={"watchdog": {"reason": "原生任务 31 分钟没有进展，看门狗已停止"}}), None, "原生任务卡住，看门狗已停止"),
            "指定工具不可用": (view(error="执行环境没有 Codex", failure="tool", evidence={"requested": {"tool": "codex"}}), None,
                        "需求指定的工具或模型不可用"),
            "换工具": (view(error="实际模型与本次配置不匹配", failure="tool"), None, "需要换用工具或模型"),
            "缺信息": (view(error="缺少需求中的接口地址", failure="input"), None, "等待补充项目或任务信息"),
            "Kiro 登录正常": (view(error="session expired", failure="auth", tool="kiro",
                                 evidence={"waiting": {"for": "kiro_login", "ok_seen_at": 2.0}}), None, "Kiro 登录正常，需要处理"),
            "等 Kiro 登录": (view(error="session expired", failure="auth", tool="kiro"), None, "等待 Kiro 登录恢复"),
            "认证或模型": (view(error="模型未开通", failure="model"), None, "等待处理认证、权限或模型配置"),
            "协调者等凭证": (view(error=coordinator, latest=diagnosis(decision={
                "action": "wait_for_user", "missing": "credentials", "reason": "目标 API 需要测试账号",
                "instructions": "请配置测试账号后继续"})), None, "需要你提供凭证或授权"),
            "协调者等需求": (view(error="缺少验收标准；请补充", latest=diagnosis(decision={
                "action": "wait_for_user", "missing": "input", "reason": "缺少验收标准", "instructions": "请补充"})), None,
                       "需要你补充需求信息"),
            "协调者的旧等待": (view(error="请确认接口；按说明处理", latest=diagnosis(decision={   # a round from before D71
                "action": "wait_for_user", "reason": "请确认接口", "instructions": "按说明处理"})), None, "协调者需要你处理具体阻塞"),
            "诊断员被回收两次": (view(error="诊断员没能完成", latest=diagnosis(evidence=teardown, context={"retry_of": "d1"})), None,
                         "诊断员没能完成"),
            "诊断员被回收（重试已关闭）": (view(error="诊断员没能完成", latest=diagnosis(
                evidence={**teardown, "budget_refused": "retries"})), None, "诊断员没能完成"),
            "诊断员被回收（时长用完）": (view(error="诊断员没能完成", latest=diagnosis(
                evidence={**teardown, "budget_refused": "minutes"})), None, "诊断员没能完成"),
            "诊断员没能启动": (view(error="协调者未能启动：原生环境拒绝", latest=diagnosis(error="协调者未能启动：原生环境拒绝")), None,
                        "诊断员没能启动"),
            "诊断被停止": (view(error="自动诊断未形成可执行决定：协调者执行失败或已停止", latest=diagnosis(
                error="自动诊断未形成可执行决定：协调者执行失败或已停止")), None, "诊断已停止，没有形成决定"),
            "原生出错": (view(error="原生进程异常退出", failure="native_error"), None, "原生任务出错，没有完成"),
            "执行环境问题": (view(error="缺少编译器", failure="environment"), None, "执行环境有问题，任务没有完成"),
            "未通过检查": (view(evidence={"budget_refused": "retries"}), None, "任务未通过交接或质量检查"),
        }

    def test_each_case_reaches_its_branch_and_names_no_internal_word(self):
        for name, (run, space, title) in self.cases().items():
            with self.subTest(name):
                attention = self.attention(run, space)
                self.assertIsNotNone(attention)
                self.assertEqual(attention["title"], title)
                for key in ("title", "detail", "action"):
                    self.assertIsNone(INTERNAL.search(attention.get(key) or ""), (key, attention.get(key)))
                self.assertTrue(attention["action"], attention)

    def test_the_known_branches_say_whose_problem_and_what_to_do(self):
        cases = self.cases()
        ours = "这是平台的问题，不是你的任务；你的任务结果都在。"
        approve = ("在会话页「待批准」中查看工具与命令后批准或拒绝；Coding Agent 可用 octopus_approvals 查看、"
                   "经用户同意后用 octopus_approve 答复。")
        expected = {
            "诊断中": (None, "无需操作：诊断完成后自动按结果修复（重试、返工或换工具）；缺少凭证或需求信息时会告诉你缺什么。也可以停止本会话。"),
            "诊断等待批准": ("协调者的诊断请求执行一次需要批准的工具调用，答复之前诊断不会继续。", approve),
            "重试的诊断等待批准": ("协调者的诊断请求执行一次需要批准的工具调用，答复之前诊断不会继续。", approve),
            "诊断重试中": ("平台临时故障：原生运行时的沙箱未能初始化。平台已自动重试 1 次，正在等结果。" + ours,
                       "无需操作：诊断完成后自动按结果修复；缺少凭证或需求信息时会告诉你缺什么。也可以停止本会话。"),
            "回收后诊断重试中": ("平台临时故障：原生任务被 KiroCrew 回收（期间没有人打开原生页面）。平台已自动重试 1 次，正在等结果。" + ours,
                         "无需操作：诊断完成后自动按结果修复；缺少凭证或需求信息时会告诉你缺什么。也可以停止本会话。"),
            "诊断员被回收（重试已关闭）": ("平台临时故障：原生任务被 KiroCrew 回收（期间没有人打开原生页面）。自动重试已关闭，没有再试。" + ours,
                              "点「请求协调者诊断」重新诊断；或展开该任务的「执行证据与历史 Attempt」查看结果。"),
            "诊断员被回收（时长用完）": ("平台临时故障：原生任务被 KiroCrew 回收（期间没有人打开原生页面）。自动纠错时长已超过 720 分钟，没有再试。"
                             + ours, "点「请求协调者诊断」重新诊断；或展开该任务的「执行证据与历史 Attempt」查看结果。"),
            "诊断员没能启动": ("协调者未能启动：原生环境拒绝。这是执行环境的问题，不是你的任务；你的任务结果都在。",
                        "稍后点「请求协调者诊断」重新诊断，反复出现请联系平台管理员；或展开该任务的「执行证据与历史 Attempt」查看结果。"),
            "诊断被停止": (None, "停止的诊断不会自动再派。需要时点「请求协调者诊断」重新诊断；或在该任务上「配置并续作」。"),
            "协调者的旧等待": ("请确认接口；按说明处理", "按详情中协调者的说明处理后，点「请求协调者诊断」，或在该任务上「配置并续作」。"),
        }
        for name, (detail, action) in expected.items():
            with self.subTest(name):
                attention = self.attention(*cases[name][:2])
                if detail:
                    self.assertEqual(attention["detail"], detail)
                self.assertEqual(attention["action"], action)

    def test_a_wait_for_what_only_the_user_has_names_it_and_who_supplies_it(self):   # D71 WAIT_FOR
        cases = self.cases()
        credentials = self.attention(*cases["协调者等凭证"][:2])
        self.assertEqual(credentials["detail"], "目标 API 需要测试账号；请配置测试账号后继续")
        self.assertTrue(credentials["action"].startswith("缺少：仓库凭证、授权或认证。由你或凭证管理者提供"), credentials["action"])
        needs = self.attention(*cases["协调者等需求"][:2])
        self.assertTrue(needs["action"].startswith("缺少：只有你能提供的需求信息。由你补充"), needs["action"])
        task = self.attention(*cases["缺信息"][:2])
        self.assertTrue(task["action"].startswith("缺少：只有你能提供的源码来源或需求信息（详情中是任务的说明）。由你补充"), task["action"])

    def test_a_diagnosis_of_an_earlier_attempt_does_not_explain_this_stop(self):
        run = view(latest=diagnosis(failed_attempt_id="att-0", decision={
            "action": "wait_for_user", "missing": "input", "reason": "缺少验收标准", "instructions": "请补充"}))
        self.assertEqual(self.attention(run)["title"], "任务未通过交接或质量检查")

    def test_the_retry_budget_switched_off_is_said_without_its_setting_name(self):
        self.assertEqual(recovery.budget_note(BUDGET, "retries"), "自动重试已关闭（自会话开始）；续作会重新计算预算。")


if __name__ == "__main__":
    unittest.main()
