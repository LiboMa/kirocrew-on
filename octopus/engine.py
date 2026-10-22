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
from . import crew_entry, evaluation, gates, journal, knowledge, planning, quotas, recovery, waiting
from .security import Error
from .domain import (CONTRACT, GENERAL_KINDS, awaits_confirmation, required_checks, runs_checks, unserved,
                     worker_source)
from .gates import BLOCKED

LEGACY = ("id", "name", "tool", "model", "effort", "prompt")


# The native runtime's own faults (cancelled, exited, cleanup, sandbox) are recovery.FAULTS: a diagnosis is retried on them too.
# KiroCrew ended a run its admission gate never let start (D60, EC2 2026-10-21 "never started: waiting for memory").
NEVER_STARTED = re.compile(r"^\s*never started", re.I)
QUEUE_POLL = 20   # seconds between looks at a queued native run (D60): not every tick on a Gateway short of memory


def transient(native, error, missing, blocked_line, entered=True):
    """A failure the native runtime caused rather than the task (D34 §5.1): retried once without a recovery cycle. A stop
    someone may have made in the native KiroCrew page (``entered``: crew_entry.entered) is theirs to resume, never retried;
    one nobody could have made is KiroCrew's teardown of a running child (D57 §6.3)."""
    kind = recovery.fault(native, entered)
    if kind or native.get("stopped"):
        return kind
    if NEVER_STARTED.search(native.get("error") or ""):
        return "never_started"
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
    elif evidence.get("handoff_text"):   # D57 §4: the platform wrote it (no file on the Worker): given whole
        item["handoff_text"] = evidence["handoff_text"]
    candidate = evidence.get("candidate") or {}
    if candidate.get("sha"):
        item["candidate"] = {"sha": str(candidate["sha"])[:12], "files": (candidate.get("names") or [])[:30]}
    if evidence.get("check_results"):
        item["checks"] = [{"command": clip(r.get("command"), 200), "exit": r.get("exit")} for r in evidence["check_results"][:10]]
    # D57 §4: what its gates only warned of (an accepted task's files it changed, no change, …), for the review that follows
    warned = [{"name": g.get("name"), **({"detail": clip(g["detail"], 200)} if g.get("detail") else {}),
               **({"files": list(g["files"])[:30]} if g.get("files") else {})} for g in gates.warnings(evidence.get("gates"))]
    if warned:
        item["warnings"] = warned[:10]
    return item


GENERATED = "（平台生成：执行者没有写交接文件）"


def generated_handoff(task, attempt, evidence, native):
    """D57 §4: the handoff the platform writes when the executor wrote none — what ``handoff`` tells a later task of it,
    from the native result, under a first line that says who wrote it. Kept in the evidence as ``handoff_text``: a
    later task, the recovery context and the Coordinator read it there, as they read a real one at ``handoff_path``."""
    item = handoff({"id": task["id"], "spec": attempt["spec"], "evidence": dumps(evidence), "result": native.get("result")})
    return GENERATED + "\n" + json.dumps(item, ensure_ascii=False, indent=2)


BROKEN = frozenset({"native_error", "native_stopped", "auth", "tool", "model"})   # the conversation itself failed


def continuation(previous, attempt, spec, repair, changed):
    """D46 P5a: a rework the platform arranged (``repair``) resumes the task's previous native conversation when that
    Attempt ran natively with the same tool, model and effort and its work fell short (not a conversation that broke:
    a native error, a stop, the watchdog, an expired login or the wrong backend), this one is neither the Coordinator's
    switch nor the automatic retry, and the user did not continue the Session since (``changed``). The conversation is
    the chain's first: the one the first Attempt started."""
    if not repair or changed or not previous or not previous.get("native_id"):
        return None
    evidence = json.loads(attempt.get("evidence") or "{}")
    if evidence.get("switched_from") or evidence.get("transient_retry_of"):
        return None
    before_evidence = json.loads(previous.get("evidence") or "{}")
    if before_evidence.get("failure_type") in BROKEN or before_evidence.get("watchdog"):
        return None
    before = json.loads(previous.get("spec") or "{}")
    if any((before.get(k) or "") != (spec.get(k) or "") for k in ("tool", "model", "effort")) or spec.get("effort"):
        return None   # an explicit effort: /continue cannot set it, so the run would ignore it
    chain = (before_evidence.get("continued") or {}).get("conversation")
    return {"conversation": chain or previous["native_id"], "from": previous["id"]}


def refused(resumed):
    """The timeline's reason when the Worker could not continue the conversation (D46 P5a): the rework starts afresh."""
    code = resumed.get("continue_refused")
    return f"未能续接上一轮对话（{str(code)[:120]}），本轮从新对话开始" if code else None

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
                "SELECT r.*,w.config worker_config,w.enabled worker_enabled,w.scope worker_scope FROM runs r "
                "JOIN workers w ON w.id=r.worker_id WHERE r.id=? AND r.lifecycle!='deleted' AND "
                "r.status IN ('preparing','ready','running','waiting_approval','dispatch_unknown','stopping','reopening','diagnosing')",
                (rid,)).fetchone()
        return dict(row) if row else None
    @staticmethod
    def _continuation(db, run, task, attempt, repair, config):
        """The conversation a platform rework resumes (D46 P5a), or None for a fresh spawn."""
        if not repair or json.loads(run["snapshot"]).get("execution_contract", 1) < 4 \
                or not (config.get("capabilities") or {}).get("continue"):
            return None
        previous = db.execute("SELECT * FROM attempts WHERE run_id=? AND task_id=? AND number<? AND native_id IS NOT NULL "
                              "ORDER BY number DESC LIMIT 1", (run["id"], task["id"], attempt["number"])).fetchone()
        if previous is None:
            return None
        changed = any(json.loads(r["content"]).get("kind") == "continued" for r in db.execute(
            "SELECT content FROM revisions WHERE run_id=? AND created>?", (run["id"], previous["created"])))
        return continuation(dict(previous), dict(attempt), json.loads(attempt["spec"]), repair, changed)

    def _knowledge_attempt(self, run, attempt_id):
        with self.db.read() as db:
            row = db.execute("SELECT task_id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        return bool(row) and knowledge.platform(json.loads(run["snapshot"]), row["task_id"])

    def _skip_knowledge(self, run, attempt_id, evidence, reason, actor=journal.WORKER):
        """D46 P4: the platform's Knowledge task that cannot run or finish is passed over with its reason — never blocked,
        never diagnosed — so a Session the Evaluator passed completes (R5)."""
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt_id, actor, "attempt.state", "Knowledge 沉淀未完成",
                           event={"id": attempt_id, "status": "accepted", "skipped": reason},
                           status="accepted", error=None, evidence=dumps({**evidence, "knowledge_skipped": reason,
                                                                          "warnings": gates.warnings(evidence.get("gates"))}),
                           updated=time.time())
            journal.change_rows(db, "tasks", "current_attempt=?", (attempt_id,), actor, "task.state", status="accepted")
        self.update(run["id"], "ready", None, actor=actor)

    def _knowledge(self, run, config):
        """Write what an accepted Knowledge task listed (D46 P4 R5) — once for each of its Attempts, before the Session
        completes. A refusal or an older Worker is recorded and never holds the Session; an unavailable environment is
        waited for up to knowledge.RETRY seconds. True when it acted this time."""
        rid, snapshot = run["id"], json.loads(run["snapshot"])
        added = snapshot.get("knowledge_added") or {}
        if not added.get("tasks"):
            return False
        marks = ",".join("?" * len(added["tasks"]))
        with self.db.read() as db:
            rows = db.execute(f"SELECT t.id, a.id attempt, a.evidence FROM tasks t JOIN attempts a ON a.id=t.current_attempt "
                              f"WHERE t.run_id=? AND t.status='accepted' AND t.id IN ({marks}) ORDER BY t.ordinal",
                              (rid, *added["tasks"])).fetchall()
            # every platform Knowledge task's Attempt once: a later one (after a continue changed code again) as well
            row = next((r for r in rows if r["attempt"] not in (added.get("written") or [])), None)
            if row is None:
                return False
            source = json.loads(run["source"])
            repo = knowledge.key(source, knowledge.space(run))
            evidence = json.loads(row["evidence"] or "{}")
            skipped = str(evidence.get("knowledge_skipped") or "")[:300] or None
            keep, rest = ((), ()) if skipped else knowledge.items(
                evidence.get("outcome"), knowledge.known(db, run["worker_id"], repo) if repo else None)
        results = []
        if keep and run.get("worker_scope") == "shared":   # D52 R4: for the repository's reviewed knowledge only
            results = [{"result": "kept", "reason": knowledge.KEPT} for _ in keep]
        elif keep and repo:
            if self.wall() < (added.get("next") or 0):
                return True   # waiting for the environment: tried again knowledge.BACKOFF seconds after the last
            try:
                # the Worker writes the items one by one, up to 30 s each (gateway_client.request)
                answer = self.request(config, "/knowledge/write", {"run_id": rid, "scope": repo, "items": keep},
                                      timeout=knowledge.LIMIT * 30 + 30)
                results = answer.get("results") if isinstance(answer, dict) else None
                results = results if isinstance(results, list) else []
            except Error as exc:
                since = added.get("waiting") or self.wall()
                if exc.code in {"worker_unavailable", "native_unavailable"} and self.wall() - since < knowledge.RETRY:
                    with self.db.transaction() as db:
                        fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
                        fresh["knowledge_added"] = {**(fresh.get("knowledge_added") or {}), "waiting": since,
                                                    "next": self.wall() + knowledge.BACKOFF}
                        journal.change(db, "runs", rid, journal.ENGINE, "run.knowledge", "执行环境暂不可用，稍后写入知识",
                                       quiet=("snapshot",), snapshot=dumps(fresh))
                    return True
                reason = ("执行环境不支持写入知识（Worker 尚未升级）" if exc.status == 404 and exc.code == "not_found"
                          else ("执行环境暂不可用，未写入" if exc.code in {"worker_unavailable", "native_unavailable"}
                                else "未写入：" + str(exc)[:160]))
                results = [{"result": "skipped", "reason": reason} for _ in keep]
        rows = []
        for item, answer in zip(keep, results + [{}] * (len(keep) - len(results))):
            answer = answer if isinstance(answer, dict) else {}
            result = answer.get("result") if answer.get("result") in {"written", "refused", "superseded", "skipped", "kept"} else "skipped"
            reason = str(answer.get("reason") or ("" if result == "written" else "执行环境未报告结果"))[:200] or None
            rows.append({**item, "result": result, "reason": reason})
        rows += rest
        with self.db.transaction() as db:
            if repo:
                knowledge.record(db, rid, row["id"], run["worker_id"], repo, rows)
            fresh = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            state = {k: v for k, v in (fresh.get("knowledge_added") or {}).items() if k not in {"waiting", "next", "skipped"}}
            fresh["knowledge_added"] = {**state, "written": [*(state.get("written") or []), row["attempt"]],
                                        **({"skipped": skipped} if skipped else {})}
            journal.change(db, "runs", rid, journal.ENGINE, "run.knowledge", quiet=("snapshot",), snapshot=dumps(fresh))
            counts = {result: sum(r["result"] == result for r in rows)
                      for result in ("written", "refused", "superseded", "skipped", "kept")}
            journal.record(db, rid, "run.knowledge", journal.ENGINE,
                           reason="Knowledge 沉淀未完成：" + skipped if skipped else "沉淀知识：" + ("、".join(f"{label} {counts[k]} 条" for k, label in
                                                (("written", "写入"), ("superseded", "替代"), ("refused", "被拒"), ("skipped", "未写入"),
                                                 ("kept", "发布时提到仓库"))
                                                if counts[k]) or "没有可沉淀的知识"),
                           task=row["id"], **counts)
        return True

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
            if code in {"tool_auth_expired", "model_unavailable", "model_transport_unsupported", "invalid_request",
                        "not_prepared"} and knowledge.platform(json.loads(run["snapshot"]), current["task_id"]):
                # D46 P4: the platform's Knowledge task does not wait for a login or go to diagnosis
                return self._skip_knowledge(run, current["id"], json.loads(current["evidence"] or "{}"), message)
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
                wanted = (json.loads(current["spec"] or "{}").get("requested") or {}).get("model")
                with self.db.read() as db:
                    row = db.execute("SELECT spec FROM tasks WHERE run_id=? AND current_attempt=?", (run["id"], current["id"])).fetchone()
                wanted = wanted or ((json.loads(row["spec"]).get("requested") or {}).get("model") if row else None)
                if wanted:   # D45: the Worker refused the model the requirement named — said, not replaced or diagnosed
                    return self._refused(run, current["id"], {**json.loads(current["evidence"] or "{}"), "requested": True},
                                         f"需求指定的模型 {wanted} 未由执行环境公布（Worker：{message}）", code)
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

    def _quota_wait(self, db, run):
        """D61: before a Session's next task starts — in the write that would create its Attempt, so two Sessions cannot
        take one place — the person's and the Team's running tasks are counted. Full, or a Session that waited longer has
        room now: it waits (recorded once, refreshed now and then for the order). Its turn: the wait ends, and its time is
        not counted in the time budget. True while it waits."""
        snapshot, now = json.loads(run["snapshot"]), time.time()
        mark = quotas.waiting(snapshot, run)
        full = quotas.full(db, run)
        if full is None and not quotas.ahead(db, run, mark.get("since", now), now):
            if mark:   # what another wait already credited (D43 _resumed) is not credited again (final review I2)
                since = max(snapshot.get("budget_since") or run["created"], mark.get("credited") or 0)
                snapshot["budget_waited"] = (snapshot.get("budget_waited") or 0) + max(0.0, now - max(mark["since"], since))
            if "quota_wait" in snapshot:
                del snapshot["quota_wait"]
                if mark:   # recorded whatever the error already reads: a wait for its Worker may have cleared it
                    journal.change(db, "runs", run["id"], journal.ENGINE, "run.quota_wait", quiet=("snapshot", "error"),
                                   snapshot=dumps(snapshot), error=None, updated=now)
                    journal.record(db, run["id"], "run.quota_go", journal.ENGINE, reason="有空位，开始下一个任务",
                                   waited=round(now - mark["since"]))
                else:   # a wait from before a stop or a continue: it is not this turn's
                    journal.change(db, "runs", run["id"], journal.ENGINE, "run.quota_wait", quiet=("snapshot",),
                                   snapshot=dumps(snapshot))
            return False
        if full is None:
            return True   # a place, but someone waited longer: it takes it first
        text = waiting.quota_title(full, bool(run["team_id"]))
        if not mark:
            snapshot["quota_wait"] = {"since": now, "seen": now, "version": run["version"], **full}
            journal.change(db, "runs", run["id"], journal.ENGINE, "run.quota_wait", text, event=full, quiet=("snapshot",),
                           snapshot=dumps(snapshot), error=text, updated=now)
        elif now - mark.get("seen", 0) >= quotas.REFRESH or run["error"] != text:
            snapshot["quota_wait"] = {**mark, **full, "seen": now}
            journal.change(db, "runs", run["id"], journal.ENGINE, "run.quota_wait", quiet=("snapshot", "error"),
                           snapshot=dumps(snapshot), error=text)   # seen again: nothing new to record
        return True

    def _queue(self, run, task, attempt, prior, queued):
        """A native run KiroCrew deferred (D60): shown on the Session as a queue — the kind of wait, not the host's numbers —
        and not judged while it waits; when it starts, the end of the queue is recorded (the watchdog counts from it)."""
        mark, text = prior.get("queued") or {}, waiting.queue_title(queued.get("reason"))
        if self.poll_backoff:
            self.due[run["id"]] = (self.clock() + QUEUE_POLL, run["version"])
        if mark.get("since") and not mark.get("until"):
            if run["error"] != text:   # another error took its place meanwhile (a failed look): it still waits
                self.update(run["id"], "running", text, actor=journal.WORKER)
            return
        mark = {"since": self.wall(), "reason": queued.get("reason"), "resuming": bool(queued.get("resuming"))}
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.queued", text,
                           event={"id": attempt["id"], "task_id": task["id"], "cause": queued.get("reason"),
                                  "detail": queued.get("detail")}, evidence=dumps({**prior, "queued": mark}), updated=time.time())
        self.update(run["id"], "running", text, actor=journal.WORKER)

    def _dequeued(self, run, task, attempt, prior, native):
        """The end of a queue (D60): recorded on the Attempt, and the time it waited is not counted in the Session's time
        budget (like a wait for a person, D43). Returns the Attempt's marks with the end."""
        rid, now = run["id"], self.wall()
        mark = {**prior["queued"], "until": now}
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.dequeued",
                           "排队结束：原生任务没有开始" if native.get("done") else "排队结束，开始执行",
                           event={"id": attempt["id"], "task_id": task["id"], "waited": round(now - mark["since"])},
                           evidence=dumps({**prior, "queued": mark}), updated=time.time())
            row = db.execute("SELECT snapshot, created FROM runs WHERE id=?", (rid,)).fetchone()
            snapshot = json.loads(row["snapshot"])
            since = snapshot.get("budget_since") or row["created"]
            snapshot["budget_waited"] = (snapshot.get("budget_waited") or 0) + max(0.0, now - max(mark["since"], since))
            journal.change(db, "runs", rid, journal.ENGINE, "run.budget", quiet=("snapshot",), snapshot=dumps(snapshot))
        if not native.get("done"):
            self.update(rid, "running", None, actor=journal.WORKER)
        return {**prior, "queued": mark}

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
        began = max(attempt["created"], float(prior.get("approval_seen") or 0),
                    float((prior.get("queued") or {}).get("until") or 0))   # D60: a queue is not a stall
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
        return self._stalled(run, task, attempt, {**prior, **(result.get("evidence") or {})}, info, settings, config)

    def _mark_watchdog(self, attempt, prior, value):
        evidence = {key: item for key, item in prior.items() if key != "watchdog"}
        if value is not None:
            evidence["watchdog"] = value
        with self.db.transaction() as db:
            journal.change(db, "attempts", attempt["id"], journal.ENGINE, "attempt.watchdog", quiet=("evidence",),
                           evidence=dumps(evidence), updated=time.time())

    def _stalled(self, run, task, attempt, evidence, info, settings, config=None):
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
        if config is not None:   # D57 §4: what a stopped review changed is put back, as for any review that did not pass
            self._put_back_review(run, config, attempt, evidence)
        if not retry and knowledge.platform(json.loads(run["snapshot"]), task["id"]):   # D46 P4: skipped, not diagnosed
            self._skip_knowledge(run, attempt["id"], evidence, reason, journal.ENGINE)
            return True
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
        if self._knowledge_attempt(run, attempt_id):
            return self._skip_knowledge(run, attempt_id, evidence, message, actor)
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

    def _read_only(self, run, config, attempt, latest, records, fail):
        """The platform's Knowledge task runs after the Evaluator and changes nothing (D46 P4): what it changed in tracked
        files is put back, so what is published is what the gates passed; the task is then skipped."""
        body = {"run_id": run["id"], "attempt_id": attempt["id"], "expected": latest["sha"]}
        try:
            check = self.request(config, "/candidate", {**body, "mode": "check"})
        except Error as exc:
            if exc.status == 404:
                records.append(gates.record("read_only", "skip", gates.OLD_WORKER))
                return None, None, False
            if 400 <= exc.status < 500:
                return fail("read_only", f"无法核对 Knowledge 任务的工作区：{exc}")
            raise
        if check["head"] == latest["sha"] and not check["changed_files"]:
            records.append(gates.record("read_only", "pass"))
            return None, None, False
        if check["head"] != latest["sha"]:
            return fail("read_only", f"Knowledge 任务移动了会话分支（HEAD 不是候选 {latest['sha'][:7]}），未沉淀")
        files = "、".join(check["changed_files"][:10])
        try:
            self.request(config, "/candidate", {**body, "mode": "restore"})
            done = "已还原"
        except Error as exc:
            done = "未能还原：" + str(exc)[:160]
        return fail("read_only", f"Knowledge 任务修改了 {files}（{done}），未沉淀")

    @staticmethod
    def _reverted(rewound, sha):
        """What a Worker's rewind put back (D57 §4), as the notes a review_bound record shows, and the files."""
        reverted, notes = [str(name) for name in rewound.get("reverted") or []], []
        if reverted:
            notes.append("审查改动的已跟踪文件已由平台撤回：" + "、".join(reverted[:10])
                         + (f" 等 {len(reverted)} 个" if len(reverted) > 10 else ""))
        if rewound.get("rewound"):
            notes.append(f"审查期间的 {rewound['rewound']} 个提交已撤回，会话分支回到候选 {sha[:7]}")
        if not notes:   # e.g. only what a candidate leaves out changed: still say what the platform did
            notes.append(f"会话分支已由平台恢复到候选 {sha[:7]}")
        return notes, reverted

    def _entered(self, run, attempt):
        """Whether a person may have stopped this Attempt's native task in the native page (crew_entry.entered)."""
        with self.db.read() as db:
            return crew_entry.entered(db, run["worker_id"], attempt["created"])

    def _put_back_review(self, run, config, attempt, evidence):
        """A review that ended without passing — a rejection, an error, the watchdog's stop — leaves the workspace as the
        reviewer left it, and the rework's candidate would take its edits in: they are put back all the same (D57 §4).
        Recorded as review_bound beside the failure, which stays the Attempt's: its error, failure_type and gate are what
        recovery sees. warn: put back; skip: no tracked file changed (or a Worker that cannot); fail: putting back failed.
        Only tracked files are put back (``sources.rewind``: untracked files the reviewer created stay). A review its gates
        judged (review_bound already recorded) was put back there."""
        spec, snapshot = json.loads(attempt["spec"]), json.loads(run["snapshot"])
        if spec.get("kind") != "review" or snapshot.get("execution_contract", 1) < 3 \
                or planning.planning_task(attempt["task_id"], snapshot) \
                or any(g.get("name") == "review_bound" for g in evidence.get("gates") or []):
            return
        with self.db.read() as db:
            latest = self._head_candidate(db, run["id"])
        if latest is None:
            return
        sha = latest["sha"]
        body = {"run_id": run["id"], "attempt_id": attempt["id"], "expected": sha}
        record = lambda status, detail, **extra: evidence.setdefault("gates", []).append(
            {**gates.record("review_bound", status, detail), **extra})
        try:
            check = self.request(config, "/candidate", {**body, "mode": "check"})
        except Error as exc:
            if not 400 <= exc.status < 500:
                raise
            return record("skip", gates.OLD_WORKER) if exc.status == 404 else \
                record("fail", f"无法核对审查的工作区，未能撤回审查的改动：{exc}")
        if not check["changed_files"] and check["head"] == sha:
            return record("skip", "审查没有改动已跟踪的文件")
        what = "审查改动的 " + "、".join(check["changed_files"][:10]) if check["changed_files"] else \
            f"审查期间被改动的会话分支（HEAD 不是候选 {sha[:7]}）"
        try:
            rewound = self.request(config, "/candidate", {**body, "mode": "rewind"}, timeout=180)
        except Error as exc:
            if not 400 <= exc.status < 500:
                raise
            return record("fail", f"平台未能撤回{what}：{exc}") if exc.status == 409 else \
                record("skip", f"未能撤回{what}：{gates.OLD_WORKER}")
        if rewound.get("head") != sha or not rewound.get("clean"):
            return record("fail", f"平台撤回{what}后，工作区仍不是候选 {sha[:7]}："
                          + "、".join((rewound.get("changed_files") or [])[:10]))
        notes, reverted = self._reverted(rewound, sha)
        record("warn", "；".join(notes), **({"files": reverted[:30]} if reverted else {}))

    def _more_gates(self, run, config, task, attempt, spec, evidence, report, latest, start=None):
        """diff_present, no_secrets and the start of checks_pass for a code task; review_bound for a review (D37 §4).
        (error, failure_type, checking): ``checking`` when the verify commands were started."""
        records, kind = evidence["gates"], spec.get("kind")

        def fail(name, detail):
            records.append(gates.record(name, "fail", detail))
            evidence["gate"] = name
            return detail, "gate", False
        if kind == "design":
            if latest and knowledge.platform(json.loads(run["snapshot"]), task["id"]):   # D46 P4: after the Evaluator
                return self._read_only(run, config, attempt, latest, records, fail)
            return None, None, False
        if kind == "review":
            if latest is None:
                records.append(gates.record("review_bound", "skip", "此前没有候选"))
                return None, None, False
            body = {"run_id": run["id"], "attempt_id": attempt["id"], "expected": latest["sha"]}
            try:
                check = self.request(config, "/candidate", {**body, "mode": "check"})
            except Error as exc:
                if exc.status == 404:
                    records.append(gates.record("review_bound", "skip", gates.OLD_WORKER))
                    return None, None, False
                if 400 <= exc.status < 500:   # refused for this workspace: the Agent can fix it, recovery tells it how
                    return fail("review_bound", f"无法核对审查的工作区：{exc}")
                raise
            sha, reviewed = latest["sha"], str((report or {}).get("reviewed_commit") or "")
            notes, reverted = [], []
            if check["changed_files"] or check["head"] != sha:
                # D57 §4: what the review changed is put back by the platform, its branch back on the candidate
                try:
                    rewound = self.request(config, "/candidate", {**body, "mode": "rewind"}, timeout=180)
                except Error as exc:
                    if exc.status == 409:   # refused (HEAD not on top of the candidate, another branch) or failed: not bound
                        what = "审查改动了 " + "、".join(check["changed_files"][:10]) if check["changed_files"] else \
                            f"审查期间会话分支被改动（HEAD 不是候选 {sha[:7]}）"
                        return fail("review_bound", f"{what}，平台未能撤回：{exc}")
                    if 400 <= exc.status < 500:   # a Worker from before D57 cannot put it back: judged as before
                        if check["changed_files"]:
                            return fail("review_bound", "审查不应修改代码，修改应交给实现任务：" + "、".join(check["changed_files"][:10]))
                        return fail("review_bound", f"审查期间会话分支被改动（HEAD 不是候选 {sha[:7]}）")
                    raise
                if rewound.get("head") != sha or not rewound.get("clean"):
                    return fail("review_bound", f"平台撤回审查的改动后，工作区仍不是候选 {sha[:7]}："
                                + "、".join((rewound.get("changed_files") or [])[:10]))
                notes, reverted = self._reverted(rewound, sha)
            if not (len(reviewed) >= 7 and sha.startswith(reviewed)):
                notes.append(f"审查报告写的版本（{reviewed[:12] or '缺失'}）与候选 {sha[:7]} 不符")
            if notes:   # D57 §13.4: the verdict counts, bound to the candidate
                records.append(gates.record("review_bound", "warn", "；".join(notes) + "；审查结论照常采用"))
                if reverted:
                    records[-1]["files"] = reverted[:30]
            else:
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
            if kind in ("implement", "deliver", *GENERAL_KINDS):   # D57 §4: a warning, not a failure; D65 general work too
                records.append(gates.record("diff_present", "warn",
                                            "本任务没有改动入库文件。如果它本来就只产出补丁或报告，可以忽略。"))
            else:
                records.append(gates.record("diff_present", "skip", "没有改动（此类任务不要求）"))
        else:
            records.append(gates.record("diff_present", "pass", f"{candidate['files']} 个文件"))
        if candidate["secrets"]:
            return fail("no_secrets", "候选中有疑似凭证或 .env 文件（会话分支未移动）：" + "、".join(candidate["secrets"][:20]))
        records.append(gates.record("no_secrets", "pass"))
        locked = self._locked(run, task, spec, candidate)
        if locked:
            status, detail, *files = locked
            records.append(gates.record("artifacts_locked", status, detail))
            if files:   # D57 §4: the accepted tasks' files it changed, named for the review that follows
                records[-1]["files"] = files[0]
        commands = (spec.get("verify") or []) if runs_checks(spec) else []   # never a review's or a design's (D57 §13.1)
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
        """artifacts_locked (D37 §11.2): (status, detail), with the files it names for a warning, or None when the candidate
        changed nothing."""
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
        # D57 §4: a warning — the change stays, for the review that follows to look at
        return "warn", listed + "（本任务的 paths 未列出，请审查时确认）", sorted(n for files in broken.values() for n in files)

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
        last = evidence["baseline_results"][-1] if evidence["baseline_results"] else {}
        unjudged = (status.get("error") or ("基线检查中断（Worker 重启）" if status.get("lost") else None)
                    or ("基线检查已停止" if last.get("stopped") or (not status.get("passed") and status.get("failed") is None) else None)
                    or (f"基线上的检查命令超时：{last.get('command', '')}" if last.get("timeout") else None)
                    or ("基线上的检查命令无法运行" if last and last.get("exit") is None else None))
        if unjudged:   # nothing about the tests: never a pass or a fail
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", unjudged))
            return self._settle(run, task, attempt, evidence, None)
        if status.get("passed"):   # D57 §4: a warning, not a failure
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "warn", "测试在改动前也能通过，可能没测到改动"))
            return self._settle(run, task, attempt, evidence, None)
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
        spec = json.loads(attempt["spec"])
        # D57 §5: only a required check's own failure blocks (a 「本地同步」's checks are judged the same way)
        verdict, detail = gates.checks_verdict(spec.get("verify") or [], status, required_checks(run, spec))
        evidence["gates"].append(gates.record("checks_pass", verdict, detail))
        error = None
        if verdict == "fail":
            error = detail
            evidence.update(failure_type="gate", gate="checks_pass")
        elif spec.get("kind") == "test" and not (evidence.get("candidate") or {}).get("changed"):
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", "测试任务没有改动"))
        elif spec.get("kind") == "test" and verdict == "warn":   # compared with the baseline only when all of them pass
            evidence["gates"].append(gates.record("tests_fail_on_baseline", "skip", "检查命令没有全部通过，不在基线上比较"))
        elif spec.get("kind") == "test":
            started = self._start_baseline(run, config, attempt, evidence)
            if started:
                with self.db.transaction() as db:
                    journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.checking", "在会话基线上运行测试",
                                   event={"id": attempt["id"], "task_id": task["id"]}, evidence=dumps(evidence), updated=time.time())
                return
        self._settle(run, task, attempt, evidence, error)

    def _settle(self, run, task, attempt, evidence, error):
        """Accept or block an Attempt whose checks ended, like advance does for any other; recovery follows a block."""
        rid, state = run["id"], "blocked" if error else "accepted"
        if state == "accepted":
            evidence["warnings"] = gates.warnings(evidence.get("gates"))   # D57 §4: listed, not blocking
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
            body = {"run_id": rid, "source": worker_source(json.loads(run["source"]))}   # D65: none is a new project there
            auth = self.credentials.material(run)   # 409 credential_unavailable blocks the run with its reason
            if auth:
                body["git_auth"] = auth
            if knowledge.applies(json.loads(run["snapshot"]), body["source"], run.get("worker_scope"), config) \
                    and knowledge.key(body["source"], knowledge.space(run)):   # D46 P4: the mark its lessons are scoped to
                body["knowledge_scope"] = knowledge.key(body["source"], knowledge.space(run))
            result = self.request(config, "/prepare", body, timeout=240)
            self.update(rid, "ready", actor=journal.WORKER, native_parent=result["native_parent"], workspace=result["workspace"])
            return
        if run["status"] == "ready" and self._knowledge(run, config):   # D46 P4: before the Session completes
            return
        with self.db.transaction() as db:
            fresh = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if fresh["desired"] != "active":
                return
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND status!='accepted' ORDER BY ordinal LIMIT 1", (rid,)).fetchone()
            if task is None:
                if evaluation.append(db, rid, json.loads(fresh["snapshot"]), config):   # D46 P3: a verdict before completion
                    return
                if knowledge.append(db, rid, fresh, run.get("worker_scope"), config):   # D46 P4: then what it learned
                    return
                journal.change(db, "runs", rid, journal.ENGINE, "run.completed",
                               event={"quality": "原生执行与交接核对完成；不是自动安全认证"}, status="completed", updated=time.time())
                return
            if task["status"] in {"failed", "blocked", "stopped"}:
                return
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone()
            replan = next((r for r in planning.replans(json.loads(fresh["snapshot"])) if r.get("task") == task["id"]), {})
            if attempt is None and replan.get("applied"):
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
            if attempt is None and self._quota_wait(db, fresh):
                return   # D61: the person's or the Team's running tasks are at their limit
            if attempt is None:
                if evaluation.refresh(db, rid, task, json.loads(fresh["snapshot"]), config):   # D46 P3: today's items
                    task = db.execute("SELECT * FROM tasks WHERE run_id=? AND id=?", (rid, task["id"])).fetchone()
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
            resume = self._continuation(db, run, task, attempt, repair, config) if attempt["status"] == "dispatching" else None
            latest = self._head_candidate(db, rid)
            start = self._task_start(db, rid, task["id"])
        spec = json.loads(attempt["spec"])
        snapshot = json.loads(run["snapshot"])
        gated = snapshot.get("execution_contract", 1) >= 3 and not planning.planning_task(task["id"], snapshot)   # D37
        if attempt["status"] == "dispatching":
            # The platform's record of this Worker's tools (D38 §3.1): a tool it cannot start is not dispatched.
            state = ((config.get("capabilities") or {}).get("tools") or {}).get(spec["tool"])
            wanted = json.loads(task["spec"]).get("requested") or {}
            if wanted:   # D45: the requirement named it; not available here → said and stopped, never replaced
                published = ((config.get("capabilities") or {}).get("models") or {}).get(spec["tool"])
                who = f"需求指定的 {spec['tool']}" if wanted.get("tool") else f"此任务的 {spec['tool']}"
                why = (f"{who} 在此执行环境不可用：{state.get('reason') or '原因未报告'}"
                       + ("" if wanted.get("tool") else f"（需求指定了模型 {wanted.get('model')}）")
                       if isinstance(state, dict) and state.get("available") is False else
                       f"需求指定的模型 {spec['model']} 未由执行环境公布"
                       if wanted.get("model") and spec["model"] not in {"auto", "default"} and published is not None
                       and spec["model"] not in published else None)
                if why:
                    return self._refused(run, attempt["id"], {**json.loads(attempt["evidence"] or "{}"), "requested": True},
                                         why, "requested_unavailable", actor=journal.ENGINE)
            if task["id"] != planning.PLAN_ID and isinstance(state, dict) and state.get("available") is False:
                return self._refused(run, attempt["id"], json.loads(attempt["evidence"] or "{}"),
                                     f"{spec['tool']} 在此执行环境不可用：{state.get('reason') or '原因未报告'}"
                                     "（平台保存的执行环境能力；已安装时请在「执行环境」中刷新模型）", "tool_unavailable",
                                     actor=journal.ENGINE)
            # The spec a Worker validates keeps its six fields (a Worker not yet upgraded runs it as before); the task
            # contract and the requirement KiroCrew completed travel beside it (D34 §6.2).
            contract = {key: spec[key] for key in CONTRACT if key in spec}
            contract.pop("verify_required", None)   # D57: the platform judges which must pass; an older Worker refuses it
            if not (config.get("capabilities") or {}).get("role_cards"):   # D46 P2: an older Worker refuses the field
                contract.pop("role", None)
            have = (config.get("capabilities") or {}).get("skills")
            if contract.get("skills"):   # D50: only what the Worker's pack has (it refuses a name it lacks)
                contract["skills"] = [name for name in contract["skills"] if isinstance(have, list) and name in have]
            if not contract.get("skills"):   # an older Worker, without the pack, refuses the field itself
                contract.pop("skills", None)
            plan = snapshot.get("planning") or {}
            if contract.get("depends_on"):
                upstream = [item for item in upstream if item["id"] in contract["depends_on"]]
            result = self.request(config, "/dispatch", {
                "run_id": rid, "attempt_id": attempt["id"], "input": run["input"], "upstream": upstream,
                "spec": {key: spec[key] for key in LEGACY if key in spec},
                **({"contract": contract} if contract else {}),
                **({"requirement": {"intent": plan.get("intent", ""), "summary": plan.get("summary", ""),
                                    **({"outdated": True} if plan.get("outdated") else {})}}
                   if plan.get("applied") else {}),
                # D46: planning is analysis — its instructions do not tell it to develop
                **({"stage": "planning"} if planning.planner_task(task["id"], snapshot) else {}),
                **({"quality": True} if snapshot.get("execution_contract", 1) >= 2 else {}),
                **({"repair": repair} if repair else {}),
                **({"continue": resume} if resume else {}),   # D46 P5a: the rework resumes its conversation
                **({"candidate": {"sha": latest["sha"], "parent": latest["parent"]}}
                   if gated and spec.get("kind") == "review" and latest else {}),
                # D66: a repair carries why the last recommendation was not a workflow
                **({"design": {**snapshot["design"], **({"repair": fix} if (fix := json.loads(attempt["evidence"] or "{}")
                                                                              .get("design_repair")) else {})}}
                   if snapshot.get("purpose") == "workflow_design" else {}),
                # D46 P4: the repository's lessons, only on a dedicated environment (the one it runs on now)
                **({"lessons": True} if knowledge.applies(snapshot, json.loads(run["source"]), run.get("worker_scope"), config)
                   else {})})
            state = result["status"]
            resumed = {k: result[k] for k in ("continued", "continue_refused") if result.get(k)}
            with self.db.transaction() as db:
                journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.dispatched", refused(resumed),
                               event={"id": attempt["id"], "task_id": task["id"], "status": state, **resumed},
                               status=state, native_id=result.get("native_id"), context_hash=result.get("context_hash"),
                               error=result.get("error"), updated=time.time(),
                               **({"evidence": dumps({**json.loads(attempt["evidence"] or "{}"), **resumed})} if resumed else {}))
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
        if native and result.get("queued"):
            return self._queue(run, task, attempt, prior, result["queued"])
        if native and (prior.get("queued") or {}).get("since") and not prior["queued"].get("until"):
            prior = self._dequeued(run, task, attempt, prior, native)
            if not native.get("done"):
                return   # the watchdog counts from the end of the queue, from the next look on
        if not native:
            resumed = {k: result[k] for k in ("continued", "continue_refused") if result.get(k)}   # a reconciled dispatch
            with self.db.transaction() as db:
                journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.running", refused(resumed),
                               status="running", native_id=result["native_id"],
                               **({"evidence": dumps({**json.loads(attempt["evidence"] or "{}"), **resumed})} if resumed else {}))
            self.update(rid, "running", actor=journal.WORKER)
            return
        evidence = result.get("evidence", {})
        # An automatic retry and a Coordinator's switch keep their marks through every evidence update (D34 §5.1, D38 §3.3).
        retry_of = prior.get("transient_retry_of")
        for key in ("transient_retry_of", "switched_from", "watchdog", "approval_seen", "continued",
                    "continue_refused", "queued", "never_started", "design_repair"):   # marks kept across updates
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
                                 recovery.policy(snapshot.get("recovery"), legacy=True), config)
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
            if report is not None and report["decision"] == "blocked" and evaluation.applies(snapshot, spec, config):
                # D46 P3 (EC2 run-346afc30…): an Evaluator that left items to a person and called the whole outcome blocked
                # has done its part; its verdicts decide, and the Session waits for the person
                problem, waiting = evaluation.verdicts(spec, report)
                if problem is None and waiting:
                    report = evidence["outcome"] = {**report, "decision": "pass", "failure_type": "none", "stated": "blocked"}
                    blocked_line = False
            # The five checks every Attempt passes, named and recorded (D37 §4).
            error, failure_type, evidence["gates"] = gates.basic(native, result, spec, evidence, report, invalid, lost,
                                                                 missing, blocked_line)
            if any(g["name"] == "handoff_present" and g["status"] == "warn" for g in evidence["gates"]):
                evidence["handoff_generated"] = True   # D57 §4: the executor wrote none; the platform did
                result["handoff"] = evidence["handoff_text"] = generated_handoff(task, attempt, evidence, native)
            if not error and gated:
                error, failure_type, checking = self._more_gates(run, config, task, attempt, spec, evidence, report, latest,
                                                                 start)
                if checking:   # the verify commands run; the Attempt is judged when they end
                    with self.db.transaction() as db:
                        journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.checking",
                                       event={"id": attempt["id"], "task_id": task["id"], "commands": evidence["checks"]["commands"]},
                                       status="running", evidence=dumps(evidence), result=native.get("result"), updated=time.time())
                    return self.update(rid, "running", actor=journal.WORKER)
            if not error and gated and evaluation.applies(snapshot, spec, config):   # D46 P3: the Evaluator's verdicts
                error, pending = evaluation.verdicts(spec, report)
                evidence["gates"].append(gates.record("evaluated", "fail" if error else "pass",
                                                      error or (f"{len(pending)} 项待人确认" if pending else None)))
                if error:   # a failed verdict is reworked; a missing one or one without evidence is the outcome's form (R3)
                    failure_type = "implementation" if error.startswith(evaluation.FAILED) else "contract"
                    evidence["gate"] = "evaluated"
                elif pending:
                    evidence["evaluation"] = {"pending": pending}
            if error:   # D57 §4: a review that did not pass is put back too; its failure stays what recovery sees
                self._put_back_review(run, config, attempt, evidence)
            if lost and not error:
                evidence["result_missing"] = True
            if error:
                evidence["failure_type"] = failure_type
                retry = transient(native, error, missing, blocked_line,
                                  self._entered(run, attempt) if native.get("stopped") else True)
                if retry == "never_started" and (evidence.get("queued") or {}).get("resuming"):
                    retry = None   # it had begun before its queue: not a lack of resources (D60 §3.4)
            state = "blocked" if error else "accepted"
            if state == "accepted" and invalid and knowledge.platform(snapshot, task["id"]):
                evidence["knowledge_skipped"] = invalid   # D57 §4: accepted with the warning, but its list cannot be read
            if state == "accepted" and (evidence.get("evaluation") or {}).get("pending"):
                state = "awaiting_acceptance"   # D46 P3: what the Evaluator left to a person
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
                                        tools=config.get("capabilities", {}).get("tools"),
                                        replan=replanning)
                    except (Error, ValueError, TypeError) as exc:
                        state, error = "blocked", "KiroCrew 规划结果未通过校验：" + str(exc)
                        evidence["failure_type"] = "contract"
            design_fix = None
            if state == "accepted" and snapshot.get("purpose") == "workflow_design" and active \
                    and current["current_attempt"] == attempt["id"] and not prior.get("design_repair"):
                from .designs import explain, parse_recommendation
                try:
                    parse_recommendation(native.get("result") or "")
                except (Error, ValueError, TypeError, KeyError) as exc:   # D66: KiroCrew fixes its own answer, once
                    design_fix = explain(native.get("result") or "", exc)
                    state, error = "blocked", "推荐未通过平台校验：" + design_fix + "（已自动请 KiroCrew 修正一次）"
            starts = prior.get("never_started", 0)   # how many Attempts before this one in a row did not start
            if not (retry and current["current_attempt"] == attempt["id"] and active):
                retry = None   # not ours to retry
            elif retry == "never_started":   # D60 §3.4: not the automatic retry and not the budget; the third in a row
                retry = retry if starts < 2 else None   # goes to recovery
            elif retry_of:
                retry = None   # this Attempt already was the automatic retry: recovery decides
            elif over := recovery.refused(db, run, "retry"):
                retry, evidence["budget_refused"] = None, over   # past the budget (D42): recovery decides, and says why
            if retry:
                evidence["transient"] = retry
            elif state == "blocked" and current["current_attempt"] == attempt["id"] and active \
                    and knowledge.platform(snapshot, task["id"]):   # D46 P4: the platform's side task never holds a Session
                evidence["knowledge_skipped"] = error
                state, error = "accepted", None
            if state in {"accepted", "awaiting_acceptance"}:   # D57 §4: listed, not blocking; kept when a person accepts
                evidence["warnings"] = gates.warnings(evidence.get("gates"))
            journal.change(db, "attempts", attempt["id"], journal.WORKER, "attempt.state",
                           event={"id": attempt["id"], "task_id": task["id"], "status": state, "error": error,
                                  "evidence": evidence},
                           quiet=() if native.get("done") else ("result",),
                           status=state, evidence=dumps(evidence), result=native.get("result"), error=error, updated=time.time())
            if retry:
                why = {**recovery.WHY, "result_missing": "原生任务没有返回结果",
                       "never_started": "原生任务未能开始（执行环境资源不足）"}[retry]
                # The retry is created here and marked, so it is never retried again; a stop or continue before it is
                # dispatched cancels or replaces it like any other Attempt.
                aid, now = uid("attempt"), time.time()
                # A run that never started is dispatched again as it was: the one automatic retry stays for later.
                mark = ({"never_started": starts + 1, **({"transient_retry_of": retry_of} if retry_of else {})}
                        if retry == "never_started" else {"transient_retry_of": attempt["id"]})
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], attempt["number"] + 1, current["spec"],
                            dumps({**mark,
                                   **({"switched_from": evidence["switched_from"]} if evidence.get("switched_from") else {})}),
                            now, now))
                again = "自动重新派发" if retry == "never_started" else "自动重试一次"
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.retry", f"{why}，{again}",
                               event={"attempt": attempt["id"], "retry": aid, "transient": retry},
                               status="dispatching", current_attempt=aid)
            elif design_fix:   # the reason travels with the new Attempt (dispatch → design.repair)
                aid, now = uid("attempt"), time.time()
                db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                           "VALUES(?,?,?,?,?,'dispatching',?,?,?)",
                           (aid, rid, task["id"], attempt["number"] + 1, current["spec"],
                            dumps({"design_repair": {"of": attempt["id"], "error": design_fix}}), now, now))
                journal.change(db, "tasks", (rid, task["id"]), journal.ENGINE, "attempt.retry",
                               f"推荐未通过平台校验（{design_fix}），自动请 KiroCrew 修正一次",
                               event={"attempt": attempt["id"], "retry": aid, "transient": "design_invalid"},
                               status="dispatching", current_attempt=aid)
            elif current["current_attempt"] == attempt["id"] and active:
                journal.change(db, "tasks", (rid, task["id"]), journal.WORKER, "task.state", status=state)
                if state == "awaiting_acceptance":
                    journal.record(db, rid, "run.awaiting_acceptance", journal.ENGINE, "验收待确认",
                                   pending=len(evidence["evaluation"]["pending"]))
        if design_fix:
            return self.update(rid, "ready", None, reason="推荐未通过平台校验，自动请 KiroCrew 修正一次")
        if retry:
            return self.update(rid, "ready", None, reason="未能开始，自动重新派发" if retry == "never_started" else
                               "瞬时失败，自动重试一次")
        self.update(rid, "ready" if state == "accepted" else state, error, actor=journal.WORKER)
        if state == "blocked":
            recovery.automatic(self.db, rid, attempt["id"])
        if self.poll_backoff and not native.get("done"):
            age = self.wall() - attempt["created"]
            pause = 2 if age < 120 else 5 if age < 600 else 10 if age < 1800 else 20
            self.due[rid] = (self.clock() + pause, run["version"])
