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
from . import recovery
from .security import Error


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
    def update(self, rid, status, error=None, **extra):
        with self.db.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            # A late result must not override a concurrently accepted stop.
            if run["desired"] != "active" and status not in {"stopping", "stopped", "reopening"}:
                # Preparation can finish after stop was requested. Retain the native
                # binding so the next cycle stops that parent, rather than orphaning it.
                binding = {k: v for k, v in extra.items() if k in {"native_parent", "workspace"}}
                if binding:
                    db.execute("UPDATE runs SET " + ",".join(k + "=?" for k in binding) + " WHERE id=?",
                               (*binding.values(), rid))
                return
            values = {"status": status, "error": error, "updated": time.time(), **extra}
            db.execute("UPDATE runs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                       (*values.values(), rid))
            if status != run["status"] or error != run["error"]:
                self.db.event(db, rid, "run.state", {"status": status, "error": error})

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
        unavailable = bool(self.retry_unavailable) and getattr(exc, "code", "") in {"worker_unavailable", "native_unavailable"}
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
            if getattr(exc, "code", "") in {"model_unavailable", "model_transport_unsupported", "invalid_request", "not_prepared"}:
                with self.db.transaction() as db:
                    db.execute("UPDATE attempts SET status='blocked',error=?,updated=? WHERE id=?",
                               (message, time.time(), current["id"]))
                    db.execute("UPDATE tasks SET status='blocked' WHERE current_attempt=?", (current["id"],))
                self.update(run["id"], "blocked", message)
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
                with self.db.transaction() as db:
                    db.execute("UPDATE runs SET status='stopped',lifecycle=?,desired='stopped',error=NULL,updated=? WHERE id=?",
                               (result["lifecycle"], time.time(), rid))
                    db.execute("UPDATE attempts SET status='cancelled',updated=? WHERE run_id=? AND status IN "
                               "('dispatching','running','dispatch_unknown','waiting_approval')", (time.time(), rid))
                    db.execute("UPDATE tasks SET status='stopped' WHERE run_id=? AND status IN "
                               "('dispatching','running','dispatch_unknown','waiting_approval')", (rid,))
                    db.execute("UPDATE recoveries SET status='cancelled',updated=? WHERE run_id=? AND status IN "
                               "('pending','dispatching','running','dispatch_unknown','waiting_approval')", (time.time(), rid))
                    self.db.event(db, rid, "session." + run["desired"], result)
            return
        if not run["worker_enabled"]:
            return self.update(rid, "diagnosing" if run["status"] == "diagnosing" else "blocked", "Worker 已停用")
        if run["status"] == "diagnosing":
            return recovery.Coordinator(self).advance(run)
        if not run["native_parent"]:
            result = self.request(config, "/prepare", {"run_id": rid, "source": json.loads(run["source"])}, timeout=240)
            self.update(rid, "ready", native_parent=result["native_parent"], workspace=result["workspace"])
            return
        with self.db.transaction() as db:
            fresh = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if fresh["desired"] != "active":
                return
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1", (rid,)).fetchone()
            if task is None:
                db.execute("UPDATE runs SET status='completed',updated=? WHERE id=?", (time.time(), rid))
                self.db.event(db, rid, "run.completed", {"quality": "原生执行与交接核对完成；不是自动安全认证"})
                return
            if task["status"] in {"failed", "blocked", "stopped"}:
                return
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
            if attempt is None:
                aid, now = uid("attempt"), time.time()
                number = db.execute("SELECT count(*) FROM attempts WHERE run_id=? AND task_id=?", (rid, task["id"])).fetchone()[0] + 1
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,created,updated) VALUES(?,?,?,?,?,'dispatching',?,?)",
                           (aid, rid, task["id"], number, task["spec"], now, now))
                db.execute("UPDATE tasks SET current_attempt=?,status='dispatching' WHERE run_id=? AND id=?", (aid, rid, task["id"]))
                self.db.event(db, rid, "attempt.created", {"id": aid, "task_id": task["id"], "number": number})
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
                db.execute("UPDATE attempts SET status=?,native_id=?,context_hash=?,error=?,updated=? WHERE id=?",
                           (state, result.get("native_id"), result.get("context_hash"), result.get("error"), time.time(), attempt["id"]))
                db.execute("UPDATE tasks SET status=? WHERE run_id=? AND id=? AND current_attempt=? "
                           "AND EXISTS (SELECT 1 FROM runs WHERE id=? AND desired='active')",
                           (state, rid, task["id"], attempt["id"], rid))
            self.update(rid, state, result.get("error"))
            return
        result = self.request(config, "/inspect", {"run_id": rid, "attempt_id": attempt["id"]})
        if result.get("status") == "dispatch_unknown":
            self.update(rid, "dispatch_unknown", result.get("error"))
            return
        native = result.get("native")
        if not native:
            with self.db.transaction() as db:
                db.execute("UPDATE attempts SET status='running',native_id=? WHERE id=?", (result["native_id"], attempt["id"]))
            self.update(rid, "running")
            return
        evidence = result.get("evidence", {})
        state, error = ("waiting_approval" if native.get("awaiting_approval") else "running"), None
        if native.get("done"):
            error = native.get("error") or ("原生任务已停止" if native.get("stopped") else None)
            failure_type = "native_stopped" if native.get("stopped") else "native_error"
            text = str(native.get("result", ""))
            if not error and (not text.strip() or text.strip() in {"_No response._", "_No result._"}
                              or re.search(r"(?im)^\s*(?:[-*]\s*)?BLOCKED\b", text)):
                error = "工作者未完成任务或报告 BLOCKED"
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
            if snapshot.get("execution_contract", 1) >= 2 and not native.get("error") and not native.get("stopped"):
                try:
                    report = recovery.outcome(result.get("outcome", ""))
                    evidence["outcome"] = report
                    if report["decision"] != "pass" and (not error or error == "工作者未完成任务或报告 BLOCKED"):
                        error, failure_type = report["summary"], report["failure_type"]
                except (Error, ValueError, TypeError) as exc:
                    if not error:
                        error, failure_type = str(exc), "contract"
            if error:
                evidence["failure_type"] = failure_type
            state = "blocked" if error else "accepted"
        with self.db.transaction() as db:
            current = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, task["id"])).fetchone()
            old = db.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
            db.execute("UPDATE attempts SET status=?,evidence=?,result=?,error=?,updated=? WHERE id=?",
                       (state, dumps(evidence), native.get("result"), error, time.time(), attempt["id"]))
            # Old observations cannot complete a newly retried task.
            active = db.execute("SELECT desired FROM runs WHERE id=?", (rid,)).fetchone()[0] == "active"
            if current["current_attempt"] == attempt["id"] and active:
                db.execute("UPDATE tasks SET status=? WHERE run_id=? AND id=?", (state, rid, task["id"]))
            if old["status"] != state or old["evidence"] != dumps(evidence):
                self.db.event(db, rid, "attempt.state", {"id": attempt["id"], "task_id": task["id"],
                                                       "status": state, "error": error, "evidence": evidence})
        self.update(rid, "ready" if state == "accepted" else state, error)
        if state == "blocked":
            recovery.automatic(self.db, rid, attempt["id"])
