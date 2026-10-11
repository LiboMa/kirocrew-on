"""Durable Coordinator decisions and bounded repair, using the existing Worker."""
import json
import time

from .db import dumps, uid
from .security import Error, require

LIVE = {"pending", "dispatching", "dispatch_unknown", "running", "waiting_approval"}
FAILURES = {"implementation", "tests", "environment", "input", "auth", "model",
            "native_stopped", "native_error", "contract", "none"}
NEEDS_INPUT = {"input", "auth", "model", "native_stopped"}


def policy(value=None, *, legacy=False):
    if value is None:
        return {"enabled": not legacy, "max_cycles": 2}
    require(isinstance(value, dict) and set(value) <= {"enabled", "max_cycles"}, "自动纠错配置无效")
    enabled, limit = value.get("enabled", True), value.get("max_cycles", 2)
    require(isinstance(enabled, bool) and type(limit) is int and 1 <= limit <= 5,
            "自动纠错次数须为 1–5，enabled 须为布尔值")
    return {"enabled": enabled, "max_cycles": limit}


def outcome(text):
    try:
        require(isinstance(text, str) and len(text) <= 64000, "任务结果文件缺失或过大")
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise Error(409, "outcome_invalid", "任务缺少有效的结构化 outcome.json，无法判定审查是否通过") from exc
    require(isinstance(value, dict) and value.get("decision") in {"pass", "changes_requested", "blocked"},
            "outcome.json 的 decision 必须为 pass、changes_requested 或 blocked", 409, "outcome_invalid")
    require(isinstance(value.get("summary"), str) and 0 < len(value["summary"]) <= 8000,
            "outcome.json 缺少具体结论", 409, "outcome_invalid")
    failure = value.get("failure_type", "none" if value["decision"] == "pass" else "implementation")
    require(failure in FAILURES, "outcome.json failure_type 无效", 409, "outcome_invalid")
    def finding(item):
        return isinstance(item, str) or (isinstance(item, dict) and
               any(isinstance(item.get(k), str) and item[k].strip()
                   for k in ("issue", "message", "description", "title")))
    require(isinstance(value.get("findings", []), list) and
            all(finding(item) for item in value.get("findings", [])),
            "outcome.json findings 应为问题列表", 409, "outcome_invalid")
    return {**value, "failure_type": failure}


def quality_instructions(folder):
    return f"""

## 必须交付可判定的任务结论
除 handoff.md 外，将实际结论写到 {folder / 'outcome.json'}，严格 JSON：
{{"decision":"pass","summary":"具体完成内容与实际验证",
  "failure_type":"none","findings":[]}}
decision=pass 仅在本任务及验收实际完成时使用。
发现必须修复的源码、测试或安全问题，使用 changes_requested，并逐项填写 findings；
findings 使用字符串列表，或带 issue/message/description/title 之一的对象列表（可附 file、line、severity）。
不要仅写“审查完成”就使用 pass。需要输入、认证或权限时使用 blocked。
failure_type 可选 implementation/tests/environment/input/auth/model/native_error/contract/none。
缺少源码、仓库来源或需求信息归为 input；权限、认证、额度归为 auth。
本文件是本次 Attempt 的真实结论，不能复制旧结论或编造命令输出。
"""


def coordinator_instructions(context, folder):
    return f"""
## 你是此 Run 的诊断与纠错 Coordinator
完整读取 context.json、失败任务的真实返回、交接路径及当前源码，判断可执行恢复办法。
只诊断和提出修复指令；不要自己实现业务修复或派生任务。Engine 会按决定派发所选 Coding Agent。
保留工作流和工具配置，不迁移源码、不换模型、不删除历史。不要把停止或权限拒绝当作可绕过的失败。
判断是否已有足够源码、依赖与输入；缺少仓库、需要用户授权或认证时 wait_for_user，不猜测项目地址。
若是当前任务可自行修复的问题用 retry；若审查问题需要上游实现者修复用 rework。
目标只能是失败步骤或其上游步骤。把失败反馈、所需改动与重新验证条件写具体。
结果契约：decision 为 pass/changes_requested/blocked；summary 为具体结论；findings 为字符串列表，
或具有 issue/message/description/title 之一的对象列表。pass 可以记录不阻断本步骤验收的观察项，
不能从 findings 非空推断 JSON 格式错误。区分已核实原因和推测，不发明引擎的额外规则。
这是诊断任务；失败业务任务的输入在 coordination 中。下一次重试一定携带新的 repair 字段，
其中包含本决定和失败证据，不能因为本诊断文件没有 repair 就称下一次业务执行为首次执行。
当前原始失败及工作流：{dumps(context)}

把唯一有效的处置写到 {folder / 'decision.json'}，严格 JSON：
{{"action":"retry","target_task_id":"当前或上游 Task ID",
  "reason":"真实根因与判断依据","instructions":"具体修复动作、输入与复验要求"}}
action 只能为 retry、rework、wait_for_user；wait_for_user 不需 target_task_id，
instructions 要说明缺少什么、用户下一步如何补充。
同时写 handoff.md，最后汇总判断。提出 wait_for_user 是诊断完成，不是诊断 Agent 自身执行失败。
"""


def summary(db, run):
    snapshot = json.loads(run["snapshot"])
    rows = [dict(row) for row in db.execute("SELECT * FROM recoveries WHERE run_id=? ORDER BY number", (run["id"],))]
    for row in rows:
        for key in ("context", "evidence", "decision"):
            row[key] = json.loads(row[key]) if row[key] else None
    return {"policy": policy(snapshot.get("recovery"), legacy=True), "used": len(rows),
            "latest": rows[-1] if rows else None, "history": rows}


def schedule(db, database, run, task, attempt, *, manual=False):
    settings = policy(json.loads(run["snapshot"]).get("recovery"), legacy=True)
    rows = list(db.execute("SELECT * FROM recoveries WHERE run_id=? ORDER BY number", (run["id"],)))
    if any(r["status"] in LIVE for r in rows):
        require(not manual, "协调者正在处理此 Session", 409, "busy")
        return None
    failure = json.loads(attempt["evidence"]).get("failure_type", "native_error")
    if not manual:
        if (not settings["enabled"] or len(rows) >= settings["max_cycles"] or failure in NEEDS_INPUT or
                any(r["failed_attempt_id"] == attempt["id"] for r in rows)):
            return None
    ordered = [dict(t) for t in db.execute("SELECT id,ordinal,spec FROM tasks WHERE run_id=? ORDER BY ordinal",
                                         (run["id"],))]
    context = {"run_id": run["id"], "input": run["input"], "workspace": run["workspace"],
               "source": json.loads(run["source"]),
               "failed_task_id": task["id"], "failed_attempt_id": attempt["id"],
               "error": attempt["error"], "failure_type": failure,
               "failed_result": attempt["result"], "evidence": json.loads(attempt["evidence"]),
               "tasks": [{**t, "spec": json.loads(t["spec"])} for t in ordered],
               "previous_decisions": [json.loads(r["decision"]) for r in rows if r["decision"]],
               "manual": manual}
    rid, aid, now = uid("recovery"), uid("attempt"), time.time()
    db.execute("INSERT INTO recoveries(id,run_id,number,failed_task_id,failed_attempt_id,attempt_id,status,context,created,updated) "
               "VALUES(?,?,?,?,?,?,'pending',?,?,?)",
               (rid, run["id"], len(rows)+1, task["id"], attempt["id"], aid, dumps(context), now, now))
    db.execute("UPDATE runs SET status='diagnosing',desired='active',updated=? WHERE id=?", (now, run["id"]))
    database.event(db, run["id"], "recovery.started", {"id": rid, "number": len(rows)+1, "task_id": task["id"],
                                                    "failed_attempt_id": attempt["id"], "manual": manual})
    return rid


def automatic(database, rid, aid):
    with database.transaction() as db:
        run = dict(db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())
        if run["desired"] != "active" or run["lifecycle"] != "open":
            return
        task = db.execute("SELECT * FROM tasks WHERE run_id=? AND current_attempt=?", (rid, aid)).fetchone()
        if not task or task["status"] != "blocked":
            return
        attempt = db.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
        schedule(db, database, run, task, attempt)


def reconcile(database, skip=frozenset()):
    """Recover a crash between persisting task failure and scheduling diagnosis.

    ``skip``: Sessions currently being advanced; their own advance schedules diagnosis.
    """
    with database.transaction() as db:
        rows = list(db.execute(
            "SELECT DISTINCT r.id FROM runs r JOIN tasks t ON t.run_id=r.id "
            "WHERE r.desired='active' AND r.lifecycle='open' AND r.status IN ('running','ready','blocked') "
            "AND t.status='blocked' AND t.current_attempt IS NOT NULL"))
        for row in rows:
            if row["id"] in skip:
                continue
            run = dict(db.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1",
                              (run["id"],)).fetchone()
            if not task or task["status"] != "blocked":
                continue
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
            if run["status"] != "blocked":
                db.execute("UPDATE runs SET status='blocked',error=?,updated=? WHERE id=?",
                           (attempt["error"], time.time(), run["id"]))
            schedule(db, database, run, task, attempt)


def feedback(db, run, attempt):
    row = db.execute("SELECT * FROM recoveries WHERE run_id=? AND status='applied' AND updated<=? "
                     "ORDER BY number DESC LIMIT 1", (run["id"], attempt["created"])).fetchone()
    if not row:
        return None
    return {"recovery_id": row["id"], "cycle": row["number"],
            "decision": json.loads(row["decision"]), "failure": json.loads(row["context"]),
            "instruction": "本轮为纠错后的重新执行；实施修复并重新验证，后续审查必须针对本轮源码。"}


class Coordinator:
    def __init__(self, engine):
        self.engine, self.db = engine, engine.db

    def advance(self, run):
        with self.db.read() as db:
            row = db.execute("SELECT * FROM recoveries WHERE run_id=? ORDER BY number DESC LIMIT 1",
                             (run["id"],)).fetchone()
        require(row is not None, "缺少协调者记录", 409, "recovery_missing")
        item = dict(row)
        if item["status"] not in LIVE:
            return
        context = json.loads(item["context"])
        config = json.loads(run["worker_config"])
        body = {"run_id": run["id"], "attempt_id": item["attempt_id"], "input": context["input"],
                "spec": {"id": "diagnose", "name": "协调者诊断与纠错", "tool": "kiro", "model": "auto", "effort": "",
                         "prompt": "分析失败证据，提出可执行的重试、上游返工或等待输入决定。"},
                "upstream": [], "coordination": context}
        if item["status"] in {"pending", "dispatching", "dispatch_unknown"}:
            try:
                result = self.engine.request(config, "/dispatch", body)
            except Error as exc:
                if exc.status in {400, 401, 403, 404, 409, 413, 422}:
                    self.finish(run, item, error="协调者启动请求被拒绝：" + str(exc))
                    return
                raise
            state = result["status"]
            with self.db.transaction() as db:
                db.execute("UPDATE recoveries SET status=?,native_id=?,context_hash=?,error=?,updated=? WHERE id=?",
                           (state, result.get("native_id"), result.get("context_hash"), result.get("error"),
                            time.time(), item["id"]))
            if state == "blocked":
                self.finish(run, item, error="协调者未能启动：" + (result.get("error") or "请检查原生环境"))
            return
        result = self.engine.request(config, "/inspect", {"run_id": run["id"], "attempt_id": item["attempt_id"]})
        native, evidence = result.get("native") or {}, result.get("evidence") or {}
        if not native.get("done"):
            state = "waiting_approval" if native.get("awaiting_approval") else "running"
            with self.db.transaction() as db:
                db.execute("UPDATE recoveries SET status=?,evidence=?,updated=? WHERE id=?",
                           (state, dumps(evidence), time.time(), item["id"]))
            return
        with self.db.transaction() as db:
            db.execute("UPDATE recoveries SET result=?,evidence=?,updated=? WHERE id=?",
                       (native.get("result"), dumps(evidence), time.time(), item["id"]))
        try:
            require(not native.get("error") and not native.get("stopped"), "协调者执行失败或已停止")
            require(result.get("handoff") and evidence.get("actual_backend") == "kiro", "协调者交接或原生后端未核实")
            raw = result.get("decision", "")
            require(isinstance(raw, str) and len(raw) <= 64000, "协调者决定缺失或过大")
            decision = json.loads(raw)
            require(isinstance(decision, dict) and set(decision) <= {"action", "target_task_id", "reason", "instructions"},
                    "协调者决定字段无效")
            require(decision.get("action") in {"retry", "rework", "wait_for_user"}, "协调者动作无效")
            require(all(isinstance(decision.get(k), str) and 0 < len(decision[k]) <= 8000
                        for k in ("reason", "instructions")), "协调者必须给出原因与具体操作")
            self.finish(run, item, decision=decision)
        except (Error, ValueError, TypeError) as exc:
            self.finish(run, item, error="自动诊断未形成可执行决定：" + str(exc))

    def finish(self, run, item, *, decision=None, error=None):
        now = time.time()
        with self.db.transaction() as db:
            fresh = db.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone()
            failed = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?",
                                (run["id"], item["failed_task_id"])).fetchone()
            if (fresh["desired"] != "active" or fresh["lifecycle"] != "open" or fresh["status"] != "diagnosing" or
                    not failed or failed["current_attempt"] != item["failed_attempt_id"]):
                db.execute("UPDATE recoveries SET status='cancelled',updated=? WHERE id=?", (now, item["id"]))
                return
            target = None
            if decision and decision["action"] != "wait_for_user":
                target = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?",
                                    (run["id"], decision.get("target_task_id"))).fetchone()
                if (not target or target["ordinal"] > failed["ordinal"] or
                        decision["action"] == "retry" and target["id"] != failed["id"]):
                    error = "协调者返工目标必须是失败 Task 或其上游；retry 必须重试当前 Task"
            if error or decision["action"] == "wait_for_user":
                message = error or (decision["reason"] + "；" + decision["instructions"])
                db.execute("UPDATE recoveries SET status='blocked',decision=?,error=?,updated=? WHERE id=?",
                           (dumps(decision) if decision else None, message, now, item["id"]))
                db.execute("UPDATE runs SET status='blocked',error=?,updated=? WHERE id=?", (message, now, run["id"]))
                self.db.event(db, run["id"], "recovery.blocked", {"id": item["id"], "error": message})
                return
            invalidated = [row["id"] for row in db.execute(
                "SELECT id FROM tasks WHERE run_id=? AND ordinal>=? ORDER BY ordinal", (run["id"], target["ordinal"]))]
            db.execute("UPDATE tasks SET status='pending',current_attempt=NULL WHERE run_id=? AND ordinal>=?",
                       (run["id"], target["ordinal"]))
            version = fresh["version"] + 1
            db.execute("UPDATE runs SET status='ready',error=NULL,version=?,updated=? WHERE id=?",
                       (version, now, run["id"]))
            db.execute("UPDATE recoveries SET status='applied',decision=?,error=NULL,updated=? WHERE id=?",
                       (dumps(decision), now, item["id"]))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                       (run["id"], version, dumps({"recovery_id": item["id"], "decision": decision,
                                                  "invalidated": invalidated}), fresh["owner_id"], now))
            self.db.event(db, run["id"], "recovery.applied", {"id": item["id"], "number": item["number"],
                                                           "target_task_id": target["id"], "invalidated": invalidated})
