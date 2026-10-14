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
from . import gates, journal, planning, recovery
from .security import Error
from .domain import CONTRACT, unserved
from .gates import BLOCKED

LEGACY = ("id", "name", "tool", "model", "effort", "prompt")


CANCELLED = re.compile(r"stop_reason=cancelled|turn cancelled by the runtime", re.I)


def transient(native, error, missing, blocked_line):
    """A failure the native runtime caused rather than the task (D34 §5.1): retried once without a recovery cycle. A stop
    someone made in the native KiroCrew page is theirs to resume, never retried."""
    if native.get("stopped"):
        return None
    if CANCELLED.search(native.get("error") or ""):
        return "runtime_cancelled"
    if error == BLOCKED and missing and not blocked_line:
        return "result_missing"
    return None


class Engine:
    def __init__(self, database, request=worker_request, workers=1, retry_unavailable=0.0, clock=time.monotonic,
                 credentials_key_file=None, credentials_private_hosts=()):
        self.db, self.request = database, request
        from .credentials import CredentialStore
        self.credentials = CredentialStore(database, credentials_key_file, credentials_private_hosts)   # opened only for a clone (D35 §5.1)
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
            if code == "conflict":
                # The Worker holds a different request under this Attempt id (e.g. sent before an upgrade): reconcile
                # what it recorded instead of sending again on every cycle.
                with self.db.transaction() as db:
                    journal.change(db, "attempts", current["id"], journal.WORKER, "attempt.state", message,
                                   event={"id": current["id"], "status": "dispatch_unknown"},
                                   status="dispatch_unknown", updated=time.time())
                return self.update(run["id"], "dispatch_unknown", message, actor=journal.WORKER)
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

    @staticmethod
    def _last_candidate(db, rid, ordinal):
        """The candidate of the last accepted code task before ``ordinal`` (D37 §3): the next candidate's parent, and
        what a review is bound to."""
        for row in db.execute("SELECT a.evidence FROM tasks t JOIN attempts a ON a.id=t.current_attempt WHERE t.run_id=? "
                              "AND t.ordinal<? AND t.status='accepted' ORDER BY t.ordinal DESC", (rid, ordinal)):
            candidate = json.loads(row["evidence"] or "{}").get("candidate")
            if candidate and candidate.get("sha"):
                return candidate
        return None

    def _more_gates(self, run, config, task, attempt, spec, evidence, report, latest):
        """diff_present, no_secrets and the start of checks_pass for a code task; review_bound for a review (D37 §4).
        (error, failure_type, checking): ``checking`` when the verify commands were started."""
        records, kind = evidence["gates"], spec.get("kind")

        def fail(name, detail):
            records.append(gates.record(name, "fail", detail))
            evidence["gate"] = name
            return detail, "gate", False
        if kind == "design":
            return None, None, False
        if kind == "review":
            if latest is None:
                records.append(gates.record("review_bound", "skip", "此前没有候选"))
                return None, None, False
            try:
                check = self.request(config, "/candidate", {"run_id": run["id"], "attempt_id": attempt["id"], "mode": "check",
                                                            "expected": latest["sha"]})
            except Error as exc:
                if exc.status != 404:
                    raise
                records.append(gates.record("review_bound", "skip", gates.OLD_WORKER))
                return None, None, False
            sha, reviewed = latest["sha"], str((report or {}).get("reviewed_commit") or "")
            if check["changed_files"]:
                return fail("review_bound", "审查不应修改代码，修改应交给实现任务：" + "、".join(check["changed_files"][:10]))
            if check["head"] != sha:
                return fail("review_bound", f"审查期间会话分支被改动（HEAD 不是候选 {sha[:7]}）")
            if not (len(reviewed) >= 7 and sha.startswith(reviewed)):
                return fail("review_bound", f"审查报告的 reviewed_commit（{reviewed or '缺失'}）与候选 {sha[:7]} 不符")
            records.append(gates.record("review_bound", "pass", sha[:12]))
            evidence["reviewed"] = sha
            return None, None, False
        message = f"KiroCrew ON：{spec.get('name') or task['id']}（{run['id']}/{task['id']} 第 {attempt['number']} 次）"
        try:
            candidate = self.request(config, "/candidate", {"run_id": run["id"], "attempt_id": attempt["id"], "mode": "commit",
                                                            "message": message, **({"parent": latest["sha"]} if latest else {})},
                                     timeout=180)
        except Error as exc:
            if exc.status != 404:
                raise
            records.extend(gates.record(name, "skip", gates.OLD_WORKER) for name in ("diff_present", "no_secrets", "checks_pass"))
            return None, None, False
        evidence["candidate"] = {key: candidate.get(key) for key in
                                 ("sha", "parent", "changed", "files", "insertions", "deletions", "rewritten")}
        if not candidate["changed"]:
            if kind in ("implement", "deliver"):
                return fail("diff_present", "任务没有改动任何文件")
            records.append(gates.record("diff_present", "skip", "没有改动（此类任务不要求）"))
        else:
            records.append(gates.record("diff_present", "pass", f"{candidate['files']} 个文件"))
        if candidate["secrets"]:
            return fail("no_secrets", "候选中有疑似凭证或 .env 文件（会话分支未移动）：" + "、".join(candidate["secrets"][:20]))
        records.append(gates.record("no_secrets", "pass"))
        commands = spec.get("verify") or []
        if not commands:
            records.append(gates.record("checks_pass", "skip", "任务没有检查命令"))
            return None, None, False
        try:
            job = self.request(config, "/verify/start", {"run_id": run["id"], "attempt_id": attempt["id"],
                                                         "commands": commands})["job"]
        except Error as exc:
            if exc.status != 404:
                raise
            records.append(gates.record("checks_pass", "skip", gates.OLD_WORKER))
            return None, None, False
        evidence["checks"] = {"job": job, "started": time.time(), "commands": len(commands)}
        return None, None, True

    def _poll_checks(self, run, config, task, attempt, evidence):
        """The verify commands of the current Attempt (D37 §5): still running, or done and the Attempt judged."""
        rid, checks = run["id"], evidence["checks"]
        try:
            status = self.request(config, "/verify/status", {"job": checks["job"]})
        except Error as exc:
            if exc.code != "check_unknown":
                raise
            if not checks.get("restarted"):   # the Worker restarted: once more, then a failure
                job = self.request(config, "/verify/start", {"run_id": rid, "attempt_id": attempt["id"],
                                                             "commands": json.loads(attempt["spec"]).get("verify", [])})["job"]
                evidence["checks"] = {**checks, "job": job, "restarted": True}
                with self.db.transaction() as db:
                    journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.checking", "检查命令中断，重新运行",
                                   event={"id": attempt["id"], "task_id": task["id"]}, evidence=dumps(evidence), updated=time.time())
                return
            status = {"state": "done", "passed": False, "failed": None, "results": [], "lost": True}
        if status["state"] != "done":
            return
        evidence["check_results"] = status["results"]
        failed = status["results"][status["failed"]] if status.get("failed") is not None else None
        if status["passed"]:
            evidence["gates"].append(gates.record("checks_pass", "pass", f"{len(status['results'])} 条命令"))
            error = None
        else:
            if status.get("lost"):
                error = "检查命令中断（Worker 重启）"
            elif failed is None:
                error = "检查命令已停止"
            elif failed.get("timeout"):
                error = f"检查命令超时：{failed['command']}"
            else:
                error = f"检查命令失败：{failed['command']}（退出码 {failed['exit']}）"
            evidence["gates"].append(gates.record("checks_pass", "fail", error))
            evidence.update(failure_type="gate", gate="checks_pass")
        self._settle(run, task, attempt, evidence, error)

    def _settle(self, run, task, attempt, evidence, error):
        """Accept or block an Attempt whose checks ended, like advance does for any other; recovery follows a block."""
        rid, state = run["id"], "blocked" if error else "accepted"
        with self.db.transaction() as db:
            current = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, task["id"])).fetchone()
            active = db.execute("SELECT desired FROM runs WHERE id=?", (rid,)).fetchone()[0] == "active"
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.state",
                           event={"id": attempt["id"], "task_id": task["id"], "status": state, "error": error,
                                  "evidence": evidence},
                           status=state, evidence=dumps(evidence), error=error, updated=time.time())
            if current["current_attempt"] == attempt["id"] and active:
                journal.change(db, "tasks", (rid, task["id"]), journal.WORKER, "task.state", status=state)
        self.update(rid, "ready" if state == "accepted" else state, error, actor=journal.WORKER)
        if state == "blocked":
            recovery.automatic(self.db, rid, attempt["id"])

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
            body = {"run_id": rid, "source": json.loads(run["source"])}
            auth = self.credentials.material(run)   # 409 credential_unavailable blocks the run with its reason
            if auth:
                body["git_auth"] = auth
            result = self.request(config, "/prepare", body, timeout=240)
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
            latest = self._last_candidate(db, rid, task["ordinal"])
        spec = json.loads(attempt["spec"])
        snapshot = json.loads(run["snapshot"])
        gated = snapshot.get("execution_contract", 1) >= 3 and task["id"] != planning.PLAN_ID   # D37
        if attempt["status"] == "dispatching":
            # The spec a Worker validates keeps its six fields (a Worker not yet upgraded runs it as before); the task
            # contract and the requirement KiroCrew completed travel beside it (D34 §6.2).
            contract = {key: spec[key] for key in CONTRACT if key in spec}
            plan = snapshot.get("planning") or {}
            if contract.get("depends_on"):
                upstream = [item for item in upstream if item["id"] in contract["depends_on"]]
            result = self.request(config, "/dispatch", {
                "run_id": rid, "attempt_id": attempt["id"], "input": run["input"], "upstream": upstream,
                "spec": {key: spec[key] for key in LEGACY if key in spec},
                **({"contract": contract} if contract else {}),
                **({"requirement": {"intent": plan.get("intent", ""), "summary": plan.get("summary", "")}}
                   if plan.get("applied") else {}),
                **({"quality": True} if snapshot.get("execution_contract", 1) >= 2 else {}),
                **({"repair": repair} if repair else {}),
                **({"candidate": {"sha": latest["sha"], "parent": latest["parent"]}}
                   if gated and spec.get("kind") == "review" and latest else {}),
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
        prior = json.loads(attempt["evidence"] or "{}")
        if prior.get("checks") and "check_results" not in prior:
            return self._poll_checks(run, config, task, attempt, prior)
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
        # An automatic retry keeps its mark through every evidence update: it is not retried again (D34 §5.1).
        retry_of = json.loads(attempt["evidence"] or "{}").get("transient_retry_of")
        if retry_of:
            evidence["transient_retry_of"] = retry_of
        state, error = ("waiting_approval" if native.get("awaiting_approval") else "running"), None
        retry = None
        if native.get("done"):
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
            # The five checks every Attempt passes, named and recorded (D37 §4).
            error, failure_type, evidence["gates"] = gates.basic(native, result, spec, evidence, report, invalid, lost,
                                                                 missing, blocked_line)
            if not error and gated:
                error, failure_type, checking = self._more_gates(run, config, task, attempt, spec, evidence, report, latest)
                if checking:   # the verify commands run; the Attempt is judged when they end
                    with self.db.transaction() as db:
                        journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.checking",
                                       event={"id": attempt["id"], "task_id": task["id"], "commands": evidence["checks"]["commands"]},
                                       status="running", evidence=dumps(evidence), result=native.get("result"), updated=time.time())
                    return self.update(rid, "running", actor=journal.WORKER)
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
            if not (retry and current["current_attempt"] == attempt["id"] and active) or retry_of:
                retry = None   # not ours to retry, or this Attempt already was the automatic retry: recovery decides
            if retry:
                evidence["transient"] = retry
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.state",
                           event={"id": attempt["id"], "task_id": task["id"], "status": state, "error": error,
                                  "evidence": evidence},
                           quiet=() if native.get("done") else ("result",),
                           status=state, evidence=dumps(evidence), result=native.get("result"), error=error, updated=time.time())
            if retry:
                why = {"runtime_cancelled": "运行时取消了原生任务", "result_missing": "原生任务没有返回结果"}[retry]
                # The retry is created here and marked, so it is never retried again; a stop or continue before it is
                # dispatched cancels or replaces it like any other Attempt.
                aid, now = uid("attempt"), time.time()
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], attempt["number"] + 1, current["spec"],
                            dumps({"transient_retry_of": attempt["id"]}), now, now))
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.retry", f"{why}，自动重试一次",
                               event={"attempt": attempt["id"], "retry": aid, "transient": retry},
                               status="dispatching", current_attempt=aid)
            elif current["current_attempt"] == attempt["id"] and active:
                journal.change(db, "tasks", (rid, task["id"]), journal.WORKER, "task.state", status=state)
        if retry:
            return self.update(rid, "ready", None, reason="瞬时失败，自动重试一次")
        self.update(rid, "ready" if state == "accepted" else state, error, actor=journal.WORKER)
        if state == "blocked":
            recovery.automatic(self.db, rid, attempt["id"])
