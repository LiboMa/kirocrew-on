"""Durable stage progression. Native task execution stays with KiroCrew."""
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
import fcntl
import json
import logging
import re
import threading
import time

from .db import dumps, uid
from .transport import worker_request
from . import journal, planning, recovery
from .security import Error
from .domain import unserved


BLOCKED = "工作者未完成任务或报告 BLOCKED"
CANCELLED = re.compile(r"stop_reason=cancelled|turn cancelled by the runtime", re.I)
EXPIRED = re.compile(r"session (?:has )?expired|not logged in", re.I)


def transient(native, error, missing, blocked_line):
    """A failure the native runtime caused rather than the task (D34 §5.1): retried once without a recovery cycle."""
    if CANCELLED.search(native.get("error") or ""):
        return "runtime_cancelled"
    if error == BLOCKED and missing and not blocked_line:
        return "result_missing"
    return None


class Engine:
    def __init__(self, database, request=worker_request, workers=1, retry_unavailable=0.0, clock=time.monotonic):
        self.db, self.request = database, request
        self.stopping = threading.Event()
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="octopus-session")
        self.inflight, self.futures, self.guard = set(), set(), threading.Lock()
        self.retry_unavailable, self.clock, self.backoff = retry_unavailable, clock, {}
        self.turns, self.turn = {}, 0  # last submission order per Session, for round-robin fairness
    def start(self):
        self.lease = (self.db.root / "engine.lock").open("a")
        fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        thread = threading.Thread(target=self.loop, daemon=True, name="octopus-engine")
        thread.start()
        return thread

    def loop(self):
        while not self.stopping.is_set():
            try:
                self.tick(wait=False)
            except Exception as exc:
                # Error class only: exception messages may contain transport secrets.
                logging.error("Octopus observer cycle failed (%s)", type(exc).__name__)
            self.stopping.wait(1)

    def drain(self, timeout):
        """Stop admitting work, cancel anything not yet started, wait up to ``timeout`` for in-flight advances."""
        self.stopping.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        with self.guard:
            pending = {future for future in self.futures if not future.cancelled()}
        _, not_done = wait_futures(pending, timeout=timeout)
        return not not_done
    def update(self, rid, status, error=None, *, actor=journal.ENGINE, reason=None, **extra):
        with self.db.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            binding = {k: v for k, v in extra.items() if k in {"native_parent", "workspace"}}
            if binding:
                # The native parent and workspace the Worker prepared. Kept even when a stop raced the preparation,
                # so the next cycle stops that parent rather than orphaning it.
                journal.change(db, "runs", rid, journal.WORKER, "run.binding", **binding)
            # A late result must not override a concurrently accepted stop.
            if run["desired"] != "active" and status not in {"stopping", "stopped", "reopening"}:
                return
            rest = {k: v for k, v in extra.items() if k not in binding}
            journal.change(db, "runs", rid, actor, "run.state", reason, event={"status": status, "error": error},
                           status=status, error=error, updated=time.time(), **rest)

    def tick(self, wait=True):
        with self.guard:
            busy = frozenset(self.inflight)
        # Only this thread submits advances, and only after reconcile returns: in-flight Sessions are the
        # only ones an advance could be writing concurrently, so reconcile leaves them to their own advance.
        recovery.reconcile(self.db, skip=busy)
        with self.db.read() as db:
            ids = [r["id"] for r in db.execute(
                "SELECT r.id FROM runs r "
                "JOIN workers w ON w.id=r.worker_id WHERE r.lifecycle!='deleted' AND "
                "r.status IN ('preparing','ready','running','waiting_approval','dispatch_unknown','stopping','reopening','diagnosing') "
                "ORDER BY r.created")]
        now = self.clock()
        pending = [rid for rid in ids if self.backoff.get(rid, (0, 0.0))[1] <= now]
        # Least recently advanced first (stable by creation order): long-lived Sessions cannot starve newer ones.
        pending.sort(key=lambda rid: self.turns.get(rid, 0))
        submitted = []
        while pending and not self.stopping.is_set():
            batch = []
            with self.guard:
                # Submit no more than free workers: nothing queues behind a slow Session, and a
                # Session is read fresh when its advance actually starts.
                free = self.workers - len(self.inflight)
                while pending and free > 0:
                    rid = pending.pop(0)
                    if rid in self.inflight:
                        continue
                    self.inflight.add(rid)
                    self.turn += 1
                    self.turns[rid] = self.turn
                    free -= 1
                    future = self.pool.submit(self._advance_guarded, rid)
                    self.futures.add(future)
                    batch.append(future)
            for future in batch:
                future.add_done_callback(self._forget)
            submitted += batch
            if not wait or not batch:
                break
            for future in batch:
                future.result()
        return submitted
    def _forget(self, future):
        with self.guard:
            self.futures.discard(future)

    def _advance_guarded(self, rid):
        run = None
        try:
            run = self._active(rid)
            if run is None:
                return
            self.advance(run)
            self.backoff.pop(rid, None)
        except Exception as exc:
            if run is None:
                raise
            self._failed(run, exc)
        finally:
            with self.guard:
                self.inflight.discard(rid)

    def _active(self, rid):
        with self.db.read() as db:
            row = db.execute(
                "SELECT r.*,w.config worker_config,w.enabled worker_enabled FROM runs r "
                "JOIN workers w ON w.id=r.worker_id WHERE r.id=? AND r.lifecycle!='deleted' AND "
                "r.status IN ('preparing','ready','running','waiting_approval','dispatch_unknown','stopping','reopening','diagnosing')",
                (rid,)).fetchone()
        return dict(row) if row else None
    def _failed(self, run, exc):
        code = getattr(exc, "code", "")
        unavailable = bool(self.retry_unavailable) and code in {"worker_unavailable", "native_unavailable", "tool_auth_expired"}
        if unavailable:
            failures = self.backoff.get(run["id"], (0, 0.0))[0] + 1
            self.backoff[run["id"]] = (failures, self.clock() + min(60.0, self.retry_unavailable * 2 ** (failures - 1)))
        # Error codes are stable; do not publish transport credentials/tracebacks.
        message = str(exc) if hasattr(exc, "code") else "后台操作失败，请检查 Worker 与原生状态"
        with self.db.read() as db:
            current = db.execute(
                "SELECT a.* FROM attempts a JOIN tasks t ON t.current_attempt=a.id "
                "WHERE a.run_id=? AND a.status IN ('dispatching','running','dispatch_unknown','waiting_approval') "
                "ORDER BY t.ordinal LIMIT 1", (run["id"],)).fetchone()
        if current and current["status"] == "dispatching":
            if code == "tool_auth_expired":
                # Refused before anything was spawned: the same Attempt goes out again once the login is back (D34 §5.2).
                return self.update(run["id"], run["status"], message)
            if code in {"model_unavailable", "model_transport_unsupported", "invalid_request", "not_prepared"}:
                with self.db.transaction() as db:
                    journal.change(db, "attempts", current["id"], journal.WORKER, "attempt.state",
                                   event={"id": current["id"], "status": "blocked", "error": message},
                                   status="blocked", error=message, updated=time.time())
                    journal.change_rows(db, "tasks", "current_attempt=?", (current["id"],), journal.WORKER, "task.state",
                                        status="blocked")
                self.update(run["id"], "blocked", message, actor=journal.WORKER)
            else:
                # Worker HTTP acknowledgment may be lost after native spawn.
                # Reuse this Attempt on recovery; never permit a new retry yet.
                self.update(run["id"], "dispatch_unknown", message)
            return
        if unavailable:
            # Keep the current status: the Worker may come back; the next attempt reconciles first.
            return self.update(run["id"], run["status"], message)
        if run["status"] in {"running", "dispatch_unknown", "waiting_approval", "stopping", "reopening", "diagnosing"}:
            self.update(run["id"], run["status"], message)
        else:
            self.update(run["id"], "blocked", message)

    def advance(self, run):
        rid, config = run["id"], json.loads(run["worker_config"])
        if run["desired"] != "active":
            # A lost prepare response can leave a real parent before its binding
            # reaches SQL. Ask the Worker even when SQL has no native_parent yet.
            result = self.request(config, "/control", {"run_id": rid, "action": run["desired"]})
            if result["status"] == "stopped":
                now = time.time()
                with self.db.transaction() as db:
                    journal.change(db, "runs", rid, journal.WORKER, "session." + run["desired"], event=result,
                                   status="stopped", lifecycle=result["lifecycle"], desired="stopped", error=None, updated=now)
                    journal.change_rows(db, "attempts", "run_id=? AND status IN ('dispatching','running','dispatch_unknown','waiting_approval')", (rid,),
                                        journal.WORKER, "attempt.state", status="cancelled", updated=now)
                    journal.change_rows(db, "tasks", "run_id=? AND status IN ('dispatching','running','dispatch_unknown','waiting_approval')", (rid,),
                                        journal.WORKER, "task.state", status="stopped")
                    journal.change_rows(db, "recoveries", "run_id=? AND status IN "
                                        "('pending','dispatching','running','dispatch_unknown','waiting_approval')", (rid,),
                                        journal.WORKER, "recovery.state", status="cancelled", updated=now)
            return
        if run["status"] in {"preparing", "ready"}:
            # Before preparing or dispatching a new Attempt, the Worker must still serve the run's space (§6.2);
            # an Attempt already running is left to finish.
            with self.db.read() as db:
                reason = unserved(db, run)
            if reason:
                return self.update(rid, "waiting_worker", reason)
        if not run["worker_enabled"]:
            return self.update(rid, "diagnosing" if run["status"] == "diagnosing" else "blocked", "Worker 已停用")
        if run["status"] == "diagnosing":
            return recovery.Coordinator(self).advance(run)
        if not run["native_parent"]:
            result = self.request(config, "/prepare", {"run_id": rid, "source": json.loads(run["source"])}, timeout=240)
            self.update(rid, "ready", actor=journal.WORKER, native_parent=result["native_parent"], workspace=result["workspace"])
            return
        with self.db.transaction() as db:
            fresh = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if fresh["desired"] != "active":
                return
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1", (rid,)).fetchone()
            if task is None:
                journal.change(db, "runs", rid, journal.ENGINE, "run.completed",
                               event={"quality": "原生执行与交接核对完成；不是自动安全认证"}, status="completed", updated=time.time())
                return
            if task["status"] in {"failed", "blocked", "stopped"}:
                return
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
            if attempt is None:
                aid, now = uid("attempt"), time.time()
                number = db.execute("SELECT count(*) FROM attempts WHERE run_id=? AND task_id=?", (rid, task["id"])).fetchone()[0] + 1
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) VALUES(?,?,?,?,?,'dispatching',?,?)",
                           (aid, rid, task["id"], number, task["spec"], now, now))
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.created",
                               event={"id": aid, "task_id": task["id"], "number": number}, current_attempt=aid, status="dispatching")
                attempt = db.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
            task, attempt = dict(task), dict(attempt)
            upstream = [dict(t) for t in db.execute(
                "SELECT t.id,a.result,a.evidence FROM tasks t JOIN attempts a ON a.id=t.current_attempt "
                "WHERE t.run_id=? AND t.ordinal<? AND t.status='accepted' ORDER BY t.ordinal", (rid, task["ordinal"]))]
            repair = recovery.feedback(db, run, attempt)
        spec = json.loads(attempt["spec"])
        snapshot = json.loads(run["snapshot"])
        if attempt["status"] == "dispatching":
            result = self.request(config, "/dispatch", {
                "run_id": rid, "attempt_id": attempt["id"], "input": run["input"], "spec": spec, "upstream": upstream,
                **({"quality": True} if snapshot.get("execution_contract", 1) >= 2 else {}),
                **({"repair": repair} if repair else {}),
                **({"design": snapshot["design"]} if snapshot.get("purpose") == "workflow_design" else {})})
            state = result["status"]
            with self.db.transaction() as db:
                journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.dispatched",
                               event={"id": attempt["id"], "task_id": task["id"], "status": state},
                               status=state, native_id=result.get("native_id"), context_hash=result.get("context_hash"),
                               error=result.get("error"), updated=time.time())
                journal.change_rows(db, "tasks", "run_id=? AND id=? AND current_attempt=? "
                                    "AND EXISTS (SELECT 1 FROM runs WHERE id=? AND desired='active')",
                                    (rid, task["id"], attempt["id"], rid), journal.WORKER, "task.state", status=state)
            self.update(rid, state, result.get("error"), actor=journal.WORKER)
            return
        result = self.request(config, "/inspect", {"run_id": rid, "attempt_id": attempt["id"]})
        if result.get("status") == "dispatch_unknown":
            self.update(rid, "dispatch_unknown", result.get("error"))
            return
        native = result.get("native")
        if not native:
            with self.db.transaction() as db:
                journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.running",
                               status="running", native_id=result["native_id"])
            self.update(rid, "running", actor=journal.WORKER)
            return
        evidence = result.get("evidence", {})
        state, error = ("waiting_approval" if native.get("awaiting_approval") else "running"), None
        retry = None
        if native.get("done"):
            error = native.get("error") or ("原生任务已停止" if native.get("stopped") else None)
            failure_type = "native_stopped" if native.get("stopped") else "native_error"
            if EXPIRED.search(native.get("error") or ""):
                failure_type = "auth"   # needs an administrator's login; no automatic recovery (NEEDS_INPUT)
            text = str(native.get("result", "")).strip()
            missing = text in {"", "_No response._", "_No result._"}
            blocked_line = bool(re.search(r"(?im)^\s*(?:[-*]\s*)?BLOCKED\b", text))
            report, invalid = None, None
            if snapshot.get("execution_contract", 1) >= 2 and not native.get("error") and not native.get("stopped"):
                try:
                    report = recovery.outcome(result.get("outcome", ""))
                    evidence["outcome"] = report
                except (Error, ValueError, TypeError) as exc:
                    invalid = str(exc)
            # The final reply lost in transport while the Agent wrote a passing outcome and its handoff: the work is
            # done (D34 §5.1); judge it on the evidence like any other Attempt.
            lost = missing and not blocked_line and report is not None and report["decision"] == "pass" \
                and bool(result.get("handoff", "").strip())
            if not error and (missing or blocked_line) and not lost:
                error = BLOCKED
            if not error and not result.get("handoff", "").strip():
                error = "缺少本次交接文件"
                failure_type = "contract"
            if not error and evidence.get("actual_backend") != spec["tool"]:
                error = "实际 ACP 后端未核实或与配置不匹配"
                failure_type = "model"
            actual = re.sub(r"\[(low|medium|high|xhigh|max)\]$", "", evidence.get("actual_model") or "")
            if not error and spec["model"] not in {"auto", "default"} and actual != spec["model"]:
                error = "实际模型未核实或与本次配置不匹配"
                failure_type = "model"
            if not error and spec["effort"] and evidence.get("actual_effort") and evidence["actual_effort"] != spec["effort"]:
                error = "实际 Effort 与本次配置不匹配"
                failure_type = "model"
            if report is not None and report["decision"] != "pass" and (not error or error == BLOCKED):
                error, failure_type = report["summary"], report["failure_type"]
            if invalid and not error:
                error, failure_type = invalid, "contract"
            if lost and not error:
                evidence["result_missing"] = True
            if error:
                evidence["failure_type"] = failure_type
                retry = transient(native, error, missing, blocked_line)
            state = "blocked" if error else "accepted"
        with self.db.transaction() as db:
            current = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, task["id"])).fetchone()
            # Old observations cannot complete a newly retried task.
            active = db.execute("SELECT desired FROM runs WHERE id=?", (rid,)).fetchone()[0] == "active"
            if state == "accepted" and current["current_attempt"] == attempt["id"] and active and task["id"] == planning.PLAN_ID:
                fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
                if planning.pending(fresh):
                    try:   # the KiroCrew plan is applied (or refused) before the planning Attempt's outcome is written
                        planning.expand(db, rid, native.get("result") or "", config.get("capabilities", {}).get("models"))
                    except (Error, ValueError, TypeError) as exc:
                        state, error = "blocked", "KiroCrew 规划结果未通过校验：" + str(exc)
                        evidence["failure_type"] = "contract"
            if retry and current["current_attempt"] == attempt["id"] and active:
                previous = db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id=? AND number=?",
                                      (rid, task["id"], attempt["number"] - 1)).fetchone()
                if previous and json.loads(previous["evidence"]).get("transient"):
                    retry = None   # this Attempt already was the automatic retry: recovery decides from here
            else:
                retry = None
            if retry:
                evidence["transient"] = retry
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.state",
                           event={"id": attempt["id"], "task_id": task["id"], "status": state, "error": error,
                                  "evidence": evidence},
                           status=state, evidence=dumps(evidence), result=native.get("result"), error=error, updated=time.time())
            if retry:
                why = {"runtime_cancelled": "运行时取消了原生任务", "result_missing": "原生任务没有返回结果"}[retry]
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.retry", f"{why}，自动重试一次",
                               event={"attempt": attempt["id"], "transient": retry}, status="pending", current_attempt=None)
            elif current["current_attempt"] == attempt["id"] and active:
                journal.change(db, "tasks", (rid, task["id"]), journal.WORKER, "task.state", status=state)
        if retry:
            return self.update(rid, "ready", None, reason="瞬时失败，自动重试一次")
        self.update(rid, "ready" if state == "accepted" else state, error, actor=journal.WORKER)
        if state == "blocked":
            recovery.automatic(self.db, rid, attempt["id"])
