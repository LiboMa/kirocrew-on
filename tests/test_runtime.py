"""D34 §5: transient native failures retry once without a recovery cycle; an expired login waits instead of failing."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from octopus import gateway_client
from octopus.db import dumps
from octopus.engine import Engine
from octopus.security import Error
from octopus.worker import Worker
from tests.test_teams_workers import TeamsWorkersCase
from tests.old_worker import as_old_worker

CANCELLED = {"done": True, "result": "", "error": "cancelled (stop_reason=cancelled): turn cancelled by the runtime — partial result preserved"}
# EC2, a Gateway restart while a task ran (M2 fault injection): the Agent's ACP process was ended under it
EXITED = {"done": True, "result": "", "error": "kiro_crew.acp.client.AcpError: ACP process exited (code=-15, SIGTERM, external) — "
                                                "[session/create] sessionId=3cb392c9 phase=validate-cwd durationMs=0"}
# EC2 run-f7670bfb…: a deploy restarted the runtime during a prompt (KiroCrew acp/session_handle.py:5216)
DIED = {"done": True, "result": "", "error": "kiro_crew.acp.client.AcpProcessDied: Runtime process died during prompt — process "
        "exited (rc=-15 (signal SIGTERM)) [returncode=-15] stderr_tail: <none>"}
# EC2 run-6d7c132b…: the Agent replied, then KiroCrew could not clean up a process its tree left behind (process_identity.py)
CLEANUP = {"done": True, "result": "已完成：README.md 增加一节。", "error": "kill failed: pid 762595 exited and its process group "
           "762595 still has members, but none could be verified as this run's; not signalled"}


class RuntimeCase(TeamsWorkersCase):
    def setUp(self):
        super().setUp()
        self.calls, self.natives, self.inspected = [], [], 0
        self.outcome, self.after_inspect = dumps({"decision": "pass", "summary": "通过"}), None

    def worker(self, config, path, body, **kwargs):
        as_old_worker(path)
        self.calls.append(path)
        if path == "/prepare":
            return {"native_parent": "octopus-" + body["run_id"], "workspace": "/w/" + body["run_id"]}
        if path == "/dispatch":
            return {"status": "running", "native_id": "n" + body["attempt_id"][-7:], "context_hash": "h"}
        if path == "/inspect":
            self.inspected += 1
            native = self.natives.pop(0) if self.natives else {"done": True, "result": "完成", "error": ""}
            reply = {"status": "running", "native": native, "evidence": {"actual_backend": "claude"},
                     "handoff": "交接", "outcome": self.outcome}
            if self.after_inspect:
                self.after_inspect()
            return reply
        raise AssertionError(path)

    def session(self, recovery=True):
        return self.service.start(self.c, {"request_id": "start-0000000001", "input": "研发需求与真实验证", "source": {"type": "new"},
                                           "team_id": "team-p", "recovery": {"enabled": recovery, "max_cycles": 2},
                                           "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})["id"]

    def tick(self, times):
        engine = Engine(self.db, request=self.worker)
        for _ in range(times):
            engine.tick()

    def detail(self, rid):
        value = self.service.run(self.c, rid)
        return value, value["tasks"][0]["attempts"]

    def kinds(self, rid):
        with self.db.read() as db:
            return [r["kind"] for r in db.execute("SELECT kind FROM events WHERE run_id=? ORDER BY id", (rid,))]


class TransientTests(RuntimeCase):
    def test_runtime_cancellation_is_retried_once_without_a_recovery_cycle(self):
        rid = self.session()
        self.natives = [CANCELLED]
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "runtime_cancelled")
        self.assertEqual((run["status"], run["recovery"]["used"]), ("completed", 0))
        self.assertIn("attempt.retry", self.kinds(rid))

    def test_an_acp_process_that_exited_is_retried_once_without_a_recovery_cycle(self):
        rid = self.session()
        self.natives = [EXITED]
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "runtime_exited")   # design §8.1 L0: ACP 进程退出
        self.assertEqual((run["status"], run["recovery"]["used"]), ("completed", 0))

    def test_a_runtime_process_that_died_during_a_prompt_is_retried_once_without_a_recovery_cycle(self):
        rid = self.session()
        self.natives = [DIED]
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "runtime_exited")
        self.assertEqual((run["status"], run["recovery"]["used"]), ("completed", 0))

    def test_a_process_group_the_runtime_could_not_clean_up_is_retried_once_without_a_recovery_cycle(self):
        rid = self.session()
        self.natives = [CLEANUP]
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "runtime_cleanup")
        self.assertEqual((run["status"], run["recovery"]["used"]), ("completed", 0))

    def test_a_second_transient_failure_goes_to_recovery(self):
        rid = self.session()
        self.natives = [CANCELLED, CANCELLED]
        self.tick(5)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "blocked"])
        self.assertNotIn("transient", attempts[1]["evidence"])
        self.assertEqual((run["status"], run["recovery"]["used"]), ("diagnosing", 1))

    def test_a_fresh_attempt_after_continue_is_retried_again(self):
        rid = self.session()
        self.natives = [CANCELLED]
        self.tick(3)                                    # prepare, dispatch, inspect: cancelled → automatic retry queued
        with self.db.transaction() as db:               # the user stops before the retry is dispatched
            db.execute("UPDATE runs SET status='stopped',desired='active' WHERE id=?", (rid,))
        version = self.service.run(self.c, rid)["version"]
        self.service.operate(self.c, rid, "continue", {"request_id": "continue-000009", "expected_version": version, "task_id": "code"})
        self.natives = [CANCELLED]                      # the user's fresh run is cancelled by the runtime too
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual(run["recovery"]["used"], 0)
        self.assertEqual(attempts[-1]["status"], "accepted")
        self.assertTrue(any(a["evidence"].get("transient_retry_of") for a in attempts))

    def test_a_deliberate_native_stop_is_not_retried(self):
        rid = self.session(recovery=False)
        self.natives = [{**CANCELLED, "stopped": True}]
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], len(attempts)), ("blocked", 1))
        self.assertEqual(attempts[0]["evidence"]["failure_type"], "native_stopped")

    def test_a_running_task_with_a_growing_reply_records_no_event_per_poll(self):
        rid = self.session()
        self.natives = [{"done": False, "result": "第 1 段"}, {"done": False, "result": "第 1 段\n第 2 段"},
                        {"done": False, "result": "第 1 段\n第 2 段\n第 3 段"}]
        self.tick(5)                                    # prepare, dispatch, three polls while running
        self.assertLessEqual(self.kinds(rid).count("attempt.state"), 1)

    def test_a_cancellation_after_stop_is_not_retried(self):
        rid = self.session()
        self.natives = [CANCELLED]
        def stop():
            with self.db.transaction() as db:   # the user's stop commits while the result is in flight
                db.execute("UPDATE runs SET desired='stop',status='stopping' WHERE id=?", (rid,))
        self.tick(2)
        self.after_inspect = stop
        self.tick(1)
        run, attempts = self.detail(rid)
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("transient", attempts[0]["evidence"])
        self.assertNotIn("attempt.retry", self.kinds(rid))

    def test_missing_result_with_a_passing_outcome_is_accepted(self):
        rid = self.session()
        self.natives = [{"done": True, "result": "_No response._", "error": ""}]
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], [a["status"] for a in attempts]), ("completed", ["accepted"]))
        self.assertTrue(attempts[0]["evidence"]["result_missing"])

    def test_missing_result_without_outcome_is_retried(self):
        rid = self.session()
        self.natives, self.outcome = [{"done": True, "result": "", "error": ""}], ""
        def restore():
            self.outcome = dumps({"decision": "pass", "summary": "通过"})
        self.after_inspect = restore
        self.tick(6)
        run, attempts = self.detail(rid)
        self.assertEqual([a["status"] for a in attempts], ["blocked", "accepted"])
        self.assertEqual(attempts[0]["evidence"]["transient"], "result_missing")
        self.assertEqual(run["recovery"]["used"], 0)

    def test_an_explicit_blocked_reply_is_not_transient(self):
        rid = self.session(recovery=False)
        self.natives, self.outcome = [{"done": True, "result": "无法继续\nBLOCKED 需要仓库权限", "error": ""}], ""
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], len(attempts)), ("blocked", 1))

    def test_expired_login_is_an_auth_failure_without_automatic_recovery(self):
        rid = self.session()
        self.natives = [{"done": True, "result": "", "error": "kiro_crew.acp.client.AcpError: Your session has expired. Code 401"}]
        self.tick(4)
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], len(attempts)), ("blocked", 1))
        self.assertEqual(attempts[0]["evidence"]["failure_type"], "auth")
        self.assertEqual(run["recovery"]["used"], 0)


EXPIRED = "Worker 上 kiro-cli 登录已过期：请管理员执行 deploy.sh kiro-login"


class LoginPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def worker(self, kiro_cli="/opt/kiro/bin/kiro-cli"):
        node = Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew", kiro_cli=kiro_cli)
        rid = "run-" + "1" * 32

        def native(path, body=None, method=None):
            if path == "/api/chat/slots/octopus-" + rid and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": "octopus-" + rid}
            if path == "/api/spawn":
                return {"id": "abcdef12"}
            return {"ok": True}

        node.native = native
        node.prepare({"run_id": rid, "source": {"type": "new"}})
        def body(tool, number):
            return {"run_id": rid, "attempt_id": "attempt-" + str(number) * 32, "input": "需求", "upstream": [],
                    "spec": {"id": "code", "name": "编码", "tool": tool, "model": "auto", "effort": "", "prompt": "实现"}}
        return node, rid, body

    def test_expired_login_refuses_kiro_dispatch_before_recording_it(self):
        node, rid, body = self.worker()
        expired = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"not logged in")
        with patch("octopus.worker.subprocess.run", return_value=expired) as whoami:
            with self.assertRaises(Error) as caught:
                node.dispatch(body("kiro", 2))
            self.assertEqual((caught.exception.status, caught.exception.code), (503, "tool_auth_expired"))
            self.assertFalse((node.path(rid) / "attempts" / ("attempt-" + "2" * 32) / "dispatch.json").exists())
            self.assertEqual(node.dispatch(body("claude", 3))["status"], "running")
            self.assertEqual(node.health()["tool_auth"]["kiro"], "expired")
            self.assertEqual(whoami.call_count, 1)   # cached for a minute

    def test_only_a_recognisable_logout_counts_as_expired(self):
        node, rid, body = self.worker()
        replies = {"ok": (0, b"Logged in with IAM Identity Center (https://example)"),
                   "expired": (1, b"Not logged in. Run kiro-cli login"),
                   "unknown": (2, b"error: unrecognized subcommand 'whoami'")}
        for state, (code, out) in replies.items():
            node._login = (0.0, None)
            done = subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=b"")
            with self.subTest(state=state), patch("octopus.worker.subprocess.run", return_value=done) as whoami:
                self.assertEqual(node.tool_auth(), state)
                self.assertEqual(whoami.call_args.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_an_unknown_login_state_does_not_block(self):
        node, rid, body = self.worker(kiro_cli=None)
        with patch("octopus.worker.shutil.which", return_value=None):
            self.assertEqual(node.tool_auth(), "unknown")
            self.assertEqual(node.dispatch(body("kiro", 4))["status"], "running")


class EngineLoginTests(RuntimeCase):
    def test_a_conflicting_redispatch_is_reconciled_instead_of_resent(self):
        rid = self.session()
        def worker(config, path, body, **kwargs):
            if path == "/dispatch":
                self.calls.append(path)
                raise Error(409, "conflict", "Attempt ID 对应不同请求")
            return self.worker(config, path, body, **kwargs)
        engine = Engine(self.db, request=worker)
        for _ in range(4):
            engine.tick()
        run, attempts = self.detail(rid)
        self.assertEqual(self.calls.count("/dispatch"), 1)   # never sent again
        self.assertIn("/inspect", self.calls)                 # reconciled from what the Worker recorded
        self.assertEqual((attempts[0]["status"], run["status"]), ("accepted", "completed"))

    def test_an_expired_login_keeps_the_session_waiting_then_resumes(self):
        rid = self.session()
        refuse = [True]
        def worker(config, path, body, **kwargs):
            if path == "/dispatch" and refuse[0]:
                raise Error(503, "tool_auth_expired", EXPIRED)
            return self.worker(config, path, body, **kwargs)
        engine = Engine(self.db, request=worker)
        engine.tick()          # prepare
        engine.tick()          # dispatch refused
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], run["error"]), ("ready", EXPIRED))
        self.assertEqual([a["status"] for a in attempts], ["dispatching"])
        refuse[0] = False
        engine = Engine(self.db, request=worker)
        for _ in range(3):
            engine.tick()
        run, attempts = self.detail(rid)
        self.assertEqual((run["status"], [a["status"] for a in attempts]), ("completed", ["accepted"]))


if __name__ == "__main__":
    unittest.main()
