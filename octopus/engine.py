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
from . import gates, journal, planning, recovery, waiting
from .security import Error
from .domain import CONTRACT, awaits_confirmation, unserved
from .gates import BLOCKED

LEGACY = ("id", "name", "tool", "model", "effort", "prompt")


CANCELLED = re.compile(r"stop_reason=cancelled|turn cancelled by the runtime", re.I)
# The Agent's ACP process ended under the task (a Gateway restart, a killed process): design §8.1 L0, not the task's fault.
EXITED = re.compile(r"ACP process exited", re.I)


def transient(native, error, missing, blocked_line):
    """A failure the native runtime caused rather than the task (D34 §5.1): retried once without a recovery cycle. A stop
    someone made in the native KiroCrew page is theirs to resume, never retried."""
    if native.get("stopped"):
        return None
    if CANCELLED.search(native.get("error") or ""):
        return "runtime_cancelled"
    if EXITED.search(native.get("error") or ""):
        return "runtime_exited"
    if error == BLOCKED and missing and not blocked_line:
        return "result_missing"
    return None


def handoff(row):
    """What a task is told of an accepted task before it (D40): its conclusion and findings, its candidate and checks, and
    where its full handoff is on the Worker — not its raw result and evidence (several KB each, mostly noise for the next
    task). The Agent reads the handoff file when it needs the details."""
    spec, evidence = json.loads(row.get("spec") or "{}"), json.loads(row.get("evidence") or "{}")
    outcome = evidence.get("outcome") or {}
    clip = lambda text, limit: (lambda s: s if len(s) <= limit else s[:limit] + "…")(str(text or ""))
    item = {"id": row["id"], "name": spec.get("name") or row["id"], "kind": spec.get("kind"),
            "backend": evidence.get("actual_backend"), "decision": outcome.get("decision"),
            "summary": clip(outcome.get("summary") or row.get("result"), 1500)}
    findings = [clip(f if isinstance(f, str) else json.dumps(f, ensure_ascii=False), 200) for f in outcome.get("findings") or []]
    if findings:
        item["findings"] = findings[:10]
    if outcome.get("suggestions"):
        item["suggestions"] = [clip(s, 200) for s in outcome["suggestions"][:5]]
    if evidence.get("handoff_path"):
        item["handoff"] = evidence["handoff_path"]
    candidate = evidence.get("candidate") or {}
    if candidate.get("sha"):
        item["candidate"] = {"sha": str(candidate["sha"])[:12], "files": (candidate.get("names") or [])[:30]}
    if evidence.get("check_results"):
        item["checks"] = [{"command": clip(r.get("command"), 200), "exit": r.get("exit")} for r in evidence["check_results"][:10]]
    return item


class Engine:
    def __init__(self, database, request=worker_request, workers=1, retry_unavailable=0.0, clock=time.monotonic,
                 credentials_key_file=None, credentials_private_hosts=(), wall=time.time, poll_backoff=False):
        self.db, self.request = database, request
        # A running Attempt is looked at less often the longer it runs (D40): not every second for an hour-long task.
        self.poll_backoff, self.due = poll_backoff, {}
        self.wall = wall   # Unix time, compared with KiroCrew's state.json and the Attempt's creation (D39)
        from .credentials import CredentialStore
        self.credentials = CredentialStore(database, credentials_key_file, credentials_private_hosts)   # opened only for a clone (D35 §5.1)
        self.stopping = threading.Event()
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="octopus-session")
        self.inflight, self.futures, self.guard = set(), set(), threading.Lock()
        self.retry_unavailable, self.clock, self.backoff = retry_unavailable, clock, {}
        self.turns, self.turn = {}, 0  # last submission order per Session, for round-robin fairness
        self.probed = {}   # Worker id → when its health was last read (D43)
    def start(self):
        self.lease = (self.db.root / "engine.lock").open("a")
        fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        thread = threading.Thread(target=self.loop, daemon=True, name="octopus-engine")
        thread.start()
        # Health checks wait on the network: their own thread, so a slow Worker never holds up the Sessions' cycle.
        threading.Thread(target=self.probe_loop, daemon=True, name="octopus-probe").start()
        return thread

    def probe_loop(self):
        while not self.stopping.is_set():
            try:
                self.probe()
            except Exception as exc:
                logging.error("Octopus health check cycle failed (%s)", type(exc).__name__)
            self.stopping.wait(60)

    PROBE_WAITING, PROBE_IDLE = 60, 600

    def probe(self, now=None):
        """Read each enabled Worker's health (D43 §3.1) — every minute where a Session waits for its Kiro login, every ten
        minutes otherwise. The same boundary's health is saved as its capabilities, as a refresh does; another boundary is
        the administrator's to check."""
        now = self.wall() if now is None else now
        with self.db.read() as db:
            urgent = {item["worker_id"] for item in waiting.kiro_waiting(db)
                      if not waiting.handed_over(item["evidence"].get("waiting") or {})}
            rows = [dict(r) for r in db.execute("SELECT id, config FROM workers WHERE enabled=1 AND retired=0")]
        for row in rows:
            last = self.probed.get(row["id"])
            if last is not None and now - last < (self.PROBE_WAITING if row["id"] in urgent else self.PROBE_IDLE):
                continue
            self.probed[row["id"]] = now
            try:
                health = self.request(json.loads(row["config"]), "/health", None, timeout=5)
            except Exception as exc:   # error class only: messages may carry transport details
                logging.warning("Worker %s health check failed (%s)", row["id"], type(exc).__name__)
                continue
            with self.db.transaction() as db:
                config = json.loads(db.execute("SELECT config FROM workers WHERE id=?", (row["id"],)).fetchone()["config"])
                saved = config.get("capabilities") or {}
                # The boundary's identity, as the native entry checks it: any of these changed is the administrator's to check.
                if health.get("boundary") != saved.get("boundary") or any(
                        key in saved and health.get(key) != saved[key] for key in ("host_principal", "isolation")):
                    logging.warning("Worker %s health check: another execution boundary, capabilities not saved", row["id"])
                    continue
                db.execute("UPDATE workers SET config=? WHERE id=?",
                           (dumps({**config, "capabilities": {**health, "checked_at": now}}), row["id"]))
                waiting.observe(db, row["id"], (health.get("tool_auth") or {}).get("kiro"), now)

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
        with self.db.transaction() as db:   # waits that are over continue (D43)
            waiting.resume(db, skip=busy)
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
            if code in {"model_unavailable", "model_transport_unsupported"}:
                return self._refused(run, current["id"], json.loads(current["evidence"] or "{}"), message, code)
            if code in {"invalid_request", "not_prepared"}:
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

    def _watchdog(self, run, config, task, attempt, result, snapshot):
        """A native task with no progress for too long, or running too long (D39 §3.2): stopped, the Attempt judged here (a
        stop seen later reads as someone's stop, never retried), retried once without a recovery cycle, then diagnosed.
        True when it acted this time (or must not run the normal update)."""
        prior = json.loads(attempt["evidence"] or "{}")
        mark = prior.get("watchdog") or {}
        if mark.get("unsupported"):
            return False
        settings = recovery.policy(snapshot.get("recovery"), legacy=True)
        idle = settings.get("idle_minutes", recovery.IDLE_MINUTES)
        total = settings.get("attempt_minutes", recovery.ATTEMPT_MINUTES)
        progress, now = result.get("progress") or {}, self.wall()
        # Measured from when it last could make progress: its dispatch, or the end of a wait for approval (state.json is
        # not written while it waits).
        began = max(attempt["created"], float(prior.get("approval_seen") or 0))
        quiet = None   # no progress signal (no state.json): only the total limit applies
        if isinstance(progress.get("idle_seconds"), (int, float)):
            quiet = min(progress["idle_seconds"], now - began)   # the Worker's own clock
        elif isinstance(progress.get("updated_at"), (int, float)):
            quiet = now - max(progress["updated_at"], began)
        tool = str(progress.get("last_tool") or "")[:80]
        if now - began > total * 60:
            reason = f"原生任务运行超过 {total} 分钟，看门狗已停止"
        elif quiet is not None and quiet > idle * 60:
            reason = f"原生任务 {int(quiet // 60)} 分钟没有进展" + (f"（最后工具：{tool}）" if tool else "") + "，看门狗已停止"
        else:
            return False
        if mark.get("stop_failed_at") and now - mark["stop_failed_at"] < 60:
            return True   # a stop that failed is tried again a minute later, not on every poll
        info = {"reason": reason, "idle_minutes": idle, "attempt_minutes": total, "turns": progress.get("turns"),
                "last_tool": tool, "quiet_seconds": quiet, "stop_errors": mark.get("stop_errors", 0)}
        # The intent first: a stop that outlives this request (KiroCrew reaps a wedged process for 30 s or more) or an
        # Engine restart is still judged as the watchdog's when the stopped task is seen later.
        self._mark_watchdog(attempt, prior, {**info, "stopping": True})
        try:
            answer = self.request(config, "/attempt/stop", {"run_id": run["id"], "attempt_id": attempt["id"]}, timeout=180)
        except Error as exc:
            if exc.status == 404 and exc.code == "not_found":   # an older Worker: leave the task to finish or someone's stop
                self._mark_watchdog(attempt, prior, {"unsupported": True})
                return True
            # Not stopped: never start another Agent on the same workspace; try again a minute later.
            self._mark_watchdog(attempt, prior, {**info, "stopping": True, "stop_errors": info["stop_errors"] + 1,
                                                 "stop_failed_at": now, "stop_error": str(exc)[:200]})
            return True
        if answer.get("finished"):   # it ended on its own meanwhile: its result is judged as usual on the next look
            self._mark_watchdog(attempt, prior, None)
            return True
        return self._stalled(run, task, attempt, {**prior, **(result.get("evidence") or {})}, info, settings)

    def _mark_watchdog(self, attempt, prior, value):
        evidence = {key: item for key, item in prior.items() if key != "watchdog"}
        if value is not None:
            evidence["watchdog"] = value
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt["id"], journal.ENGINE, "attempt.watchdog", quiet=("evidence",),
                           evidence=dumps(evidence), updated=time.time())

    def _stalled(self, run, task, attempt, evidence, info, settings):
        """The Attempt the watchdog stopped: blocked as a native error, retried once, then diagnosed (D39 §3.2)."""
        rid, reason = run["id"], info["reason"]
        prior = json.loads(attempt["evidence"] or "{}")
        retry, over = settings["enabled"] and not prior.get("transient_retry_of"), None
        if retry:
            with self.db.read() as db:   # D42: within the Session's automatic retries and time
                over = recovery.refused(db, run, "retry")
            retry = not over
        info = {key: item for key, item in info.items() if key not in {"stopping", "stop_failed_at", "stop_error"}}
        evidence = {**evidence, "failure_type": "native_error", "watchdog": {**info, "stopped_at": self.wall()},
                    **({"transient": "stalled"} if retry else {}),
                    **({"budget_refused": over} if over else {})}
        with self.db.transaction() as db:
            current = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, task["id"])).fetchone()
            active = db.execute("SELECT desired FROM runs WHERE id=?", (rid,)).fetchone()[0] == "active"
            journal.change(db, "attempts", attempt["id"], journal.ENGINE, "attempt.state", reason,
                           event={"id": attempt["id"], "task_id": task["id"], "status": "blocked", "error": reason},
                           status="blocked", error=reason, evidence=dumps(evidence), updated=time.time())
            if current["current_attempt"] != attempt["id"] or not active:
                return True
            if retry:
                aid, created = uid("attempt"), time.time()
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], attempt["number"] + 1, current["spec"],
                            dumps({"transient_retry_of": attempt["id"],
                                   **({"switched_from": prior["switched_from"]} if prior.get("switched_from") else {})}),
                            created, created))
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.retry", "原生任务卡住，自动重试一次",
                               event={"attempt": attempt["id"], "retry": aid, "transient": "stalled"},
                               status="dispatching", current_attempt=aid)
            else:
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "task.state", status="blocked")
        if retry:
            self.update(rid, "ready", None, reason="原生任务卡住，自动重试一次")
        else:
            self.update(rid, "blocked", reason, actor=journal.ENGINE)
            recovery.automatic(self.db, rid, attempt["id"])
        return True

    def _refused(self, run, attempt_id, evidence, message, code, actor=journal.WORKER):
        """A tool the task cannot run on (D38 §3.2): the Attempt is blocked as the tool's failure and goes to diagnosis,
        where the Coordinator may recommend another tool."""
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt_id, actor, "attempt.state",
                           event={"id": attempt_id, "status": "blocked", "error": message},
                           status="blocked", error=message, evidence=dumps({**evidence, "failure_type": "tool", "refused": code}),
                           updated=time.time())
            journal.change_rows(db, "tasks", "current_attempt=?", (attempt_id,), actor, "task.state", status="blocked")
        self.update(run["id"], "blocked", message, actor=actor)
        recovery.automatic(self.db, run["id"], attempt_id)

    @staticmethod
    def _head_candidate(db, rid):
        """The candidate the Session's branch holds (D37 §3): the newest that moved it, whichever task made it — the
        next candidate's parent and what a review is bound to (task order would be wrong after a partial continue)."""
        for row in db.execute("SELECT evidence FROM attempts WHERE run_id=? AND evidence LIKE '%\"candidate\"%' "
                              "ORDER BY updated DESC, created DESC", (rid,)):
            candidate = json.loads(row["evidence"] or "{}").get("candidate")
            if candidate and candidate.get("sha") and candidate.get("moved", True):
                return candidate
        return None

    @staticmethod
    def _task_start(db, rid, task_id):
        """Where a task started (D37 §11, review fix): the newest candidate another task left on the branch. Its own failed
        Attempts move the branch too, so measuring from the previous Attempt would count a retry's revert as a change."""
        for row in db.execute("SELECT evidence FROM attempts WHERE run_id=? AND task_id!=? AND evidence LIKE '%\"candidate\"%' "
                              "ORDER BY updated DESC, created DESC", (rid, task_id)):
            candidate = json.loads(row["evidence"] or "{}").get("candidate")
            if candidate and candidate.get("sha") and candidate.get("moved", True):
                return candidate["sha"]
        return None

    def _more_gates(self, run, config, task, attempt, spec, evidence, report, latest, start=None):
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
                if exc.status == 404:
                    records.append(gates.record("review_bound", "skip", gates.OLD_WORKER))
                    return None, None, False
                if 400 <= exc.status < 500:   # refused for this workspace: the Agent can fix it, recovery tells it how
                    return fail("review_bound", f"无法核对审查的工作区：{exc}")
                raise
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
                                                            "message": message, **({"parent": latest["sha"]} if latest else {}),
                                                            **({"since": start} if start else {})},
                                     timeout=180)
        except Error as exc:
            if exc.status == 404:
                records.extend(gates.record(name, "skip", gates.OLD_WORKER) for name in ("diff_present", "no_secrets", "checks_pass"))
                return None, None, False
            if 400 <= exc.status < 500:   # e.g. the Agent left the Session's branch: a failure recovery can fix
                return fail("diff_present", f"无法生成候选：{exc}")
            raise
        evidence["candidate"] = {**{key: candidate.get(key) for key in
                                    ("sha", "parent", "changed", "files", "insertions", "deletions", "rewritten")},
                                 "moved": not candidate["secrets"], "start": start,
                                 **{key: candidate[key] for key in ("names", "names_truncated") if key in candidate}}
        if not candidate["changed"]:
            if kind in ("implement", "deliver"):
                return fail("diff_present", "任务没有改动任何文件")
            records.append(gates.record("diff_present", "skip", "没有改动（此类任务不要求）"))
        else:
            records.append(gates.record("diff_present", "pass", f"{candidate['files']} 个文件"))
        if candidate["secrets"]:
            return fail("no_secrets", "候选中有疑似凭证或 .env 文件（会话分支未移动）：" + "、".join(candidate["secrets"][:20]))
        records.append(gates.record("no_secrets", "pass"))
        locked = self._locked(run, task, spec, candidate)
        if locked:
            status, detail = locked
            if status == "fail":
                return fail("artifacts_locked", detail)
            records.append(gates.record("artifacts_locked", status, detail))
        commands = spec.get("verify") or []
        if not commands:
            records.append(gates.record("checks_pass", "skip", "任务没有检查命令"))
            return None, None, False
        try:
            job = self.request(config, "/verify/start", {"run_id": run["id"], "attempt_id": attempt["id"],
                                                         "commands": commands})["job"]
        except Error as exc:
            if exc.status == 404:
                records.append(gates.record("checks_pass", "skip", gates.OLD_WORKER))
                return None, None, False
            if 400 <= exc.status < 500:
                return fail("checks_pass", f"无法运行检查命令：{exc}")
            raise
        evidence["checks"] = {"job": job, "started": time.time(), "commands": len(commands)}
        return None, None, True

    def _locked(self, run, task, spec, candidate):
        """artifacts_locked (D37 §11.2): (status, detail), or None when the candidate changed nothing."""
        if not candidate.get("changed"):
            return None
        if "names" not in candidate:
            return "skip", gates.OLD_WORKER
        if not spec.get("paths"):
            return "skip", "任务未声明可改动路径（paths）"
        owners, generated = {}, set()
        with self.db.read() as db:
            # What the platform's own check runs left in the workspace (.coverage, reports) is nobody's deliverable.
            for row in db.execute("SELECT evidence FROM attempts WHERE run_id=? AND evidence LIKE '%_artifacts\"%'", (run["id"],)):
                value = json.loads(row["evidence"] or "{}")
                generated.update(value.get("check_artifacts") or [], value.get("baseline_artifacts") or [])
            for row in db.execute("SELECT t.spec tspec, a.evidence FROM tasks t JOIN attempts a ON a.id=t.current_attempt "
                                  "WHERE t.run_id=? AND t.ordinal<? AND t.status='accepted' ORDER BY t.ordinal",
                                  (run["id"], task["ordinal"])):
                names = (json.loads(row["evidence"] or "{}").get("candidate") or {}).get("names") or []
                for name in names:
                    if name not in generated:
                        owners.setdefault(name, json.loads(row["tspec"]).get("name") or "此前的任务")
        if not owners:
            return "skip", "此前的已验收任务没有记录文件"
        broken = {}
        for name in candidate["names"]:
            if name in owners and name not in generated and not gates.covered(name, spec["paths"]):
                broken.setdefault(owners[name], []).append(name)
        if not broken:
            return "pass", None
        listed = "；".join(f"改动了已验收任务「{owner}」的交付物：{'、'.join(files[:10])}" for owner, files in broken.items())
        return "fail", listed + "；如需修改，请在本任务的 paths 中列出"

    def _start_baseline(self, run, config, attempt, evidence):
        """tests_fail_on_baseline (D37 §11.1): the second run of the verify commands, on the Session's baseline."""
        candidate = evidence["candidate"]
        commands = json.loads(attempt["spec"]).get("verify", [])
        skip = lambda detail: (evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", detail)), False)[1]
        if not candidate.get("start"):   # e.g. tests for code the Session found: nothing to compare with
            return skip("会话中此前没有源码改动：测试无法与改动之前比较")
        installing = [c for c in commands if gates.INSTALLS.search(c)]
        if installing:
            return skip(f"检查命令会安装依赖（{installing[0][:80]}）：在基线上运行会改动依赖目录，不在基线上运行")
        try:
            started = self.request(config, "/verify/start", {"run_id": run["id"], "attempt_id": attempt["id"], "commands": commands,
                                                             "baseline": {"candidate": candidate["sha"], "start": candidate["start"]}})
        except Error as exc:
            if not 400 <= exc.status < 500:
                raise
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", f"无法在基线上运行：{exc}"))
            return False
        if not started.get("baseline"):   # an older Worker ran them on the candidate: that says nothing about the baseline
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", gates.OLD_WORKER))
            return False
        evidence["baseline"] = {"job": started["job"], "base": started.get("base"), "started": time.time()}
        return True

    def _poll_baseline(self, run, config, task, attempt, evidence):
        try:
            status = self.request(config, "/verify/status", {"job": evidence["baseline"]["job"]})
        except Error as exc:
            if exc.code != "check_unknown":
                raise
            status = {"state": "done", "lost": True, "results": []}
        if status["state"] != "done":
            return
        evidence["baseline_results"] = status.get("results") or []
        evidence["baseline_artifacts"] = status.get("artifacts") or []
        base = str(evidence["baseline"].get("base") or "")[:7]
        last = evidence["baseline_results"][-1] if evidence["baseline_results"] else {}
        unjudged = (status.get("error") or ("基线检查中断（Worker 重启）" if status.get("lost") else None)
                    or ("基线检查已停止" if last.get("stopped") or (not status.get("passed") and status.get("failed") is None) else None)
                    or (f"基线上的检查命令超时：{last.get('command', '')}" if last.get("timeout") else None)
                    or ("基线上的检查命令无法运行" if last and last.get("exit") is None else None))
        if unjudged:   # nothing about the tests: never a pass or a fail
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", unjudged))
            return self._settle(run, task, attempt, evidence, None)
        if status.get("passed"):
            error = f"新增的测试在改动之前（基线 {base}）也通过，不能证明新行为"
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "fail", error))
            evidence.update(failure_type="gate", gate="tests_fail_on_baseline")
            return self._settle(run, task, attempt, evidence, error)
        failed = evidence["baseline_results"][status["failed"]] if status.get("failed") is not None else {}
        evidence["gates"].append(gates.record("tests_fail_on_baseline", "pass",
                                              f"基线上失败：{failed.get('command', '')}（退出码 {failed.get('exit')}）"))
        return self._settle(run, task, attempt, evidence, None)

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
        evidence["check_artifacts"] = status.get("artifacts") or []
        failed = status["results"][status["failed"]] if status.get("failed") is not None else None
        if status["passed"]:
            evidence["gates"].append(gates.record("checks_pass", "pass", f"{len(status['results'])} 条命令"))
            error = None
            if json.loads(attempt["spec"]).get("kind") == "test" and not (evidence.get("candidate") or {}).get("changed"):
                evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", "测试任务没有改动"))
            elif json.loads(attempt["spec"]).get("kind") == "test":
                started = self._start_baseline(run, config, attempt, evidence)
                if started:
                    with self.db.transaction() as db:
                        journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.checking", "在会话基线上运行测试",
                                       event={"id": attempt["id"], "task_id": task["id"]}, evidence=dumps(evidence), updated=time.time())
                    return
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
        if self.poll_backoff and run["status"] in {"running", "waiting_approval"}:
            due = self.due.get(rid)
            if due and self.clock() < due[0] and due[1] == run["version"]:
                return
        else:
            self.due.pop(rid, None)
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
            replan = json.loads(fresh["snapshot"]).get("replan") or {}
            if attempt is None and task["id"] == replan.get("task") and replan.get("applied"):
                # A rework of an earlier task resets the replan task too; its plan is already applied: it stands as it was
                # (D41) — a new planning run would only be discarded.
                done = db.execute("SELECT id FROM attempts WHERE run_id=? AND task_id=? AND status='accepted' "
                                  "ORDER BY number DESC LIMIT 1", (rid, task["id"])).fetchone()
                if done:
                    journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "task.state", "重新规划已应用，不再运行",
                                   status="accepted", current_attempt=done["id"])
                    return
            if attempt is None and awaits_confirmation(json.loads(fresh["snapshot"]), task["id"]):
                journal.change(db, "runs", rid, journal.ENGINE, "run.awaiting_confirmation", "计划待确认",
                               event={"status": "awaiting_confirmation"}, status="awaiting_confirmation", updated=time.time())
                return
            if attempt is None:
                aid, now = uid("attempt"), time.time()
                number = db.execute("SELECT count(*) FROM attempts WHERE run_id=? AND task_id=?", (rid, task["id"])).fetchone()[0] + 1
                switched = recovery.switched_from(db, rid, task["id"], json.loads(task["spec"]))
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], number, task["spec"], dumps({"switched_from": switched} if switched else {}),
                            now, now))
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.created",
                               event={"id": aid, "task_id": task["id"], "number": number}, current_attempt=aid, status="dispatching")
                attempt = db.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
            task, attempt = dict(task), dict(attempt)
            upstream = [handoff(dict(t)) for t in db.execute(
                "SELECT t.id,t.spec,a.result,a.evidence FROM tasks t JOIN attempts a ON a.id=t.current_attempt "
                "WHERE t.run_id=? AND t.ordinal<? AND t.status='accepted' ORDER BY t.ordinal", (rid, task["ordinal"]))]
            repair = recovery.feedback(db, run, attempt)
            latest = self._head_candidate(db, rid)
            start = self._task_start(db, rid, task["id"])
        spec = json.loads(attempt["spec"])
        snapshot = json.loads(run["snapshot"])
        gated = snapshot.get("execution_contract", 1) >= 3 and not planning.planning_task(task["id"], snapshot)   # D37
        if attempt["status"] == "dispatching":
            # The platform's record of this Worker's tools (D38 §3.1): a tool it cannot start is not dispatched.
            state = ((config.get("capabilities") or {}).get("tools") or {}).get(spec["tool"])
            if task["id"] != planning.PLAN_ID and isinstance(state, dict) and state.get("available") is False:
                return self._refused(run, attempt["id"], json.loads(attempt["evidence"] or "{}"),
                                     f"{spec['tool']} 在此执行环境不可用：{state.get('reason') or '原因未报告'}"
                                     "（平台保存的执行环境能力；已安装时请在「执行环境」中刷新模型）", "tool_unavailable",
                                     actor=journal.ENGINE)
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
        if prior.get("baseline") and "baseline_results" not in prior:
            return self._poll_baseline(run, config, task, attempt, prior)
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
        # An automatic retry and a Coordinator's switch keep their marks through every evidence update (D34 §5.1, D38 §3.3).
        retry_of = prior.get("transient_retry_of")
        for key in ("transient_retry_of", "switched_from", "watchdog", "approval_seen"):   # marks kept across updates
            if prior.get(key):
                evidence[key] = prior[key]
        if native.get("done") and not native.get("stopped") and (evidence.get("watchdog") or {}).get("stopping"):
            del evidence["watchdog"]   # its stop never took: it finished on its own and is judged as usual
        state, error = ("waiting_approval" if native.get("awaiting_approval") else "running"), None
        if native.get("awaiting_approval"):
            evidence["approval_seen"] = self.wall()   # the watchdog measures from the end of the wait (D39)
        elif not native.get("done") and self._watchdog(run, config, task, attempt, result, snapshot):
            return
        if native.get("done") and native.get("stopped") and (prior.get("watchdog") or {}).get("stopping"):
            # the watchdog's stop, finished after the platform stopped waiting for it (or across a restart)
            mark = prior["watchdog"]
            return self._stalled(run, task, attempt, evidence, {k: v for k, v in mark.items()},
                                 recovery.policy(snapshot.get("recovery"), legacy=True))
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
                error, failure_type, checking = self._more_gates(run, config, task, attempt, spec, evidence, report, latest,
                                                                 start)
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
            if state == "accepted" and current["current_attempt"] == attempt["id"] and active and \
                    planning.planning_task(task["id"], snapshot):
                fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
                replanning = task["id"] != planning.PLAN_ID   # D41: the Coordinator's replan
                if planning.pending(fresh, task["id"]):
                    try:   # the KiroCrew plan is applied (or refused) before the planning Attempt's outcome is written
                        planning.expand(db, rid, native.get("result") or "", config.get("capabilities", {}).get("models"),
                                        replan=replanning)
                    except (Error, ValueError, TypeError) as exc:
                        state, error = "blocked", "KiroCrew 规划结果未通过校验：" + str(exc)
                        evidence["failure_type"] = "contract"
            if not (retry and current["current_attempt"] == attempt["id"] and active) or retry_of:
                retry = None   # not ours to retry, or this Attempt already was the automatic retry: recovery decides
            elif over := recovery.refused(db, run, "retry"):
                retry, evidence["budget_refused"] = None, over   # past the budget (D42): recovery decides, and says why
            if retry:
                evidence["transient"] = retry
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.state",
                           event={"id": attempt["id"], "task_id": task["id"], "status": state, "error": error,
                                  "evidence": evidence},
                           quiet=() if native.get("done") else ("result",),
                           status=state, evidence=dumps(evidence), result=native.get("result"), error=error, updated=time.time())
            if retry:
                why = {"runtime_cancelled": "运行时取消了原生任务", "runtime_exited": "原生运行时进程退出",
                       "result_missing": "原生任务没有返回结果"}[retry]
                # The retry is created here and marked, so it is never retried again; a stop or continue before it is
                # dispatched cancels or replaces it like any other Attempt.
                aid, now = uid("attempt"), time.time()
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], attempt["number"] + 1, current["spec"],
                            dumps({"transient_retry_of": attempt["id"],
                                   **({"switched_from": evidence["switched_from"]} if evidence.get("switched_from") else {})}),
                            now, now))
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
        if self.poll_backoff and not native.get("done"):
            age = self.wall() - attempt["created"]
            pause = 2 if age < 120 else 5 if age < 600 else 10 if age < 1800 else 20
            self.due[rid] = (self.clock() + pause, run["version"])
