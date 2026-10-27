"""D43: waiting that ends on its own — the platform reads each Worker's health, keeps its capabilities current and continues
a Session once what it waited for is back (a Kiro login, an enabled Worker, its environment, a usable credential)."""
import copy
import json
import tempfile
import unittest

from octopus import recovery
from octopus.db import Database, dumps
from octopus.domain import Service
from octopus.engine import Engine
from octopus.security import Error, authenticate, create_user, issue_token
from tests.old_worker import as_old_worker

TOOLS = {"kiro": {"available": True, "login": "ok"}, "claude": {"available": True}}
EXPIRED = {"done": True, "result": "", "error": "Kiro session expired: not logged in"}


def step(sid, tool):
    return {"id": sid, "name": sid, "tool": tool, "prompt": "完成", "kind": "deliver", "acceptance": ["完成"]}


class WaitingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.service = Service(self.db)
        with self.db.transaction() as db:
            user = create_user(db, "owner", "Fixture-password-123!", True)
            self.actor = authenticate(db, issue_token(db, user["id"])["token"])
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('worker','W',?,?)",
                       (user["id"], dumps({"url": "fixture", "token": "fixture",
                                            "capabilities": {"boundary": "b1", "tools": TOOLS, "tool_auth": {"kiro": "ok"}}})))
        self.calls, self.bodies = [], {}
        self.login, self.boundary, self.health_error, self.tools, self.health_extra = "ok", "b1", None, TOOLS, {}
        self.expired, self.running, self.denied = set(), set(), set()
        self.now = 1_000_000.0
        self.engine = Engine(self.db, self.request, wall=lambda: self.now)

    def start(self, steps, **body):
        return self.service.start(self.actor, {"request_id": "waiting-" + str(len(self.calls)), "input": "完成并验证",
                                               "steps": steps, "source": {"type": "new"}, "worker_id": "worker",
                                               "recovery": {"enabled": True, "max_cycles": 2}, **body})["id"]

    def request(self, config, path, body, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/health":
            if self.health_error:
                raise Error(503, "worker_unavailable", self.health_error)
            return {"boundary": self.boundary, "tools": self.tools, "tool_auth": {"kiro": self.login}, **self.health_extra}
        if path == "/prepare":
            return {"native_parent": "parent", "workspace": "/w"}
        if path == "/dispatch":
            self.bodies.setdefault(body["attempt_id"], copy.deepcopy(body))
            return {"status": "running", "native_id": body["attempt_id"][-8:], "context_hash": "h"}
        as_old_worker(path)
        record = self.bodies[body["attempt_id"]]
        spec = record["spec"]
        number = sum(1 for b in self.bodies.values() if not b.get("coordination") and b["spec"]["id"] == spec["id"])
        if (spec["id"], number) in self.running:
            return {"native": {"done": False}, "evidence": {"actual_backend": spec["tool"]}}
        native = dict(EXPIRED) if (spec["id"], number) in self.expired else {"done": True, "result": "完成"}
        outcome = {"decision": "pass", "summary": spec["id"] + " 完成", "failure_type": "none", "findings": []}
        if (spec["id"], number) in self.denied:   # the Agent itself reports a permission problem, not a login
            outcome = {"decision": "blocked", "summary": "GitHub token 无 push 权限", "failure_type": "auth", "findings": []}
        return {"native": native, "evidence": {"actual_backend": spec["tool"], "handoff_path": "/w/handoff.md"}, "handoff": "交接",
                "outcome": dumps(outcome)}

    def tick(self, rid, count=20):
        for _ in range(count):
            self.engine.tick()
        return self.service.run(self.actor, rid)

    def attempts(self, run, task):
        return sorted({t["id"]: t for t in run["tasks"]}[task]["attempts"], key=lambda a: a["number"])

    def events(self, rid, kind):
        with self.db.read() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind=?", (rid, kind))]

    def capabilities(self):
        with self.db.read() as db:
            return json.loads(db.execute("SELECT config FROM workers WHERE id='worker'").fetchone()["config"])["capabilities"]

    def health_calls(self):
        return sum(1 for path, _ in self.calls if path == "/health")


class KiroLoginTests(WaitingCase):
    def test_a_kiro_login_that_expired_and_came_back_resumes_the_task(self):
        self.expired = {("code", 1)}
        rid = self.start([step("code", "kiro"), step("deliver", "claude")])
        run = self.tick(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["attention"]["title"], "等待 Kiro 登录恢复")
        # D53 / 0.2.0 P2 Task 5: the boundary's owner signs Kiro in from the console and authorizes the device code
        self.assertIn("边界主人在「我的设置 › 执行环境」里点「登录 Kiro」", run["attention"]["action"])
        self.assertIn("设备码", run["attention"]["action"])
        self.login = "expired"
        self.engine.probe()
        self.assertEqual(self.tick(rid)["status"], "blocked")   # still expired
        self.login, self.now = "ok", self.now + 61
        self.engine.probe()
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.attempts(run, "code")), 2)
        self.assertEqual(run["recovery"]["history"], [])
        self.assertEqual([e.get("reason") for e in self.events(rid, "run.resumed")], ["Kiro 登录已恢复，自动继续"])

    def test_an_auth_failure_with_a_good_login_waits_for_a_person(self):
        self.expired = {("code", 1)}
        rid = self.start([step("code", "kiro")])
        self.tick(rid)
        for _ in range(3):
            self.now += 61
            self.engine.probe()
        run = self.tick(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(self.attempts(run, "code")), 1)
        self.assertEqual(self.events(rid, "run.resumed"), [])

    def test_a_claude_auth_failure_is_not_waited_on(self):
        self.expired = {("code", 1)}
        rid = self.start([step("code", "claude")])
        run = self.tick(rid)
        self.assertNotEqual(run["attention"]["title"], "等待 Kiro 登录恢复")
        self.login = "expired"
        self.engine.probe()
        self.login, self.now = "ok", self.now + 61
        self.engine.probe()
        run = self.tick(rid)
        self.assertEqual((run["status"], len(self.attempts(run, "code"))), ("blocked", 1))

    def test_an_agent_reported_auth_failure_is_not_a_kiro_login(self):
        self.denied = {("code", 1)}
        rid = self.start([step("code", "kiro")])
        run = self.tick(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["attention"]["title"], "等待处理认证、权限或模型配置")
        self.login = "expired"
        self.engine.probe()
        self.login, self.now = "ok", self.now + 61
        self.engine.probe()
        run = self.tick(rid)
        self.assertEqual((run["status"], len(self.attempts(run, "code"))), ("blocked", 1))

    def test_a_good_login_seen_after_the_failure_hands_it_to_a_person(self):
        self.expired = {("code", 1)}
        rid = self.start([step("code", "kiro")])
        self.tick(rid)
        self.engine.probe()                     # the login is fine already: fixed before the check, or never the cause
        run = self.service.run(self.actor, rid)
        self.assertEqual(run["attention"]["title"], "Kiro 登录正常，需要处理")
        self.assertIn("不会自动重跑", run["attention"]["action"])
        self.now += 61
        self.engine.probe()
        self.assertEqual(self.health_calls(), 1)   # nobody waits on it any more: the ten-minute rhythm

    def test_a_long_wait_does_not_use_up_the_time_budget(self):
        self.expired = {("code", 1)}
        rid = self.start([step("code", "kiro"), step("deliver", "claude")], recovery={"enabled": True, "max_cycles": 2, "budget_minutes": 30})
        self.tick(rid)
        with self.db.transaction() as db:   # it started 40 minutes ago and has waited for the login for 35
            db.execute("UPDATE runs SET created=created-?, updated=updated-? WHERE id=?", (40 * 60, 35 * 60, rid))
        self.login = "expired"
        self.engine.probe()
        self.login, self.now = "ok", self.now + 61
        self.engine.probe()
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertLess(run["recovery"]["budget"]["minutes"]["used"], 30)
        self.assertIsNone(run["recovery"]["budget"]["exhausted"])

    def test_resuming_is_not_a_users_word(self):
        self.expired = {("code", 1)}
        rid = self.start([step("code", "kiro")])
        self.tick(rid)
        self.login = "expired"
        self.engine.probe()
        self.login, self.now = "ok", self.now + 61
        self.engine.probe()
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertFalse(run["recovery"]["budget"]["reset"])   # the budget still counts from the start


class WorkerTests(WaitingCase):
    def enable(self, value):
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=? WHERE id='worker'", (int(value),))

    def test_a_worker_enabled_again_resumes_the_running_session(self):
        self.running = {("code", 1)}
        rid = self.start([step("code", "claude")])
        self.assertEqual(self.tick(rid, 4)["status"], "running")
        self.enable(False)
        run = self.tick(rid, 2)
        self.assertEqual((run["status"], run["error"]), ("blocked", "Worker 已停用"))
        self.assertEqual(run["attention"]["title"], "执行环境已停用")
        self.running = set()   # the native task went on while the platform could not look
        self.enable(True)
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.attempts(run, "code")), 1)   # the same Attempt, looked at again
        self.assertEqual([e.get("reason") for e in self.events(rid, "run.resumed")], ["执行环境已重新启用，自动继续"])

    def test_a_session_waiting_for_its_disabled_worker_continues_once_enabled(self):
        rid = self.start([step("code", "claude")])
        self.enable(False)
        run = self.tick(rid, 2)
        self.assertEqual(run["status"], "waiting_worker")
        self.enable(True)
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual([e.get("reason") for e in self.events(rid, "run.resumed")], ["执行环境已重新服务此空间，自动继续"])

    def test_a_session_without_a_worker_keeps_waiting_for_an_assignment(self):
        self.enable(False)
        rid = self.service.start(self.actor, {"request_id": "waiting-none-1", "input": "完成并验证", "steps": [step("code", "claude")],
                                              "source": {"type": "new"}})["id"]
        self.enable(True)
        run = self.tick(rid)
        self.assertEqual(run["status"], "waiting_worker")   # no environment of its own yet: a person assigns one
        self.assertEqual(self.events(rid, "run.resumed"), [])


class ProbeTests(WaitingCase):
    def test_the_probe_saves_capabilities_only_for_the_same_boundary(self):
        self.tools = {**TOOLS, "codex": {"available": True}}
        self.engine.probe()
        saved = self.capabilities()
        self.assertIn("codex", saved["tools"])
        self.assertEqual(saved["checked_at"], self.now)
        self.boundary, self.tools, self.now = "b2", TOOLS, self.now + 601
        self.engine.probe()
        self.assertIn("codex", self.capabilities()["tools"])   # another boundary is the administrator's to check

    def test_the_probe_does_not_save_another_isolation_or_host(self):
        with self.db.transaction() as db:
            config = json.loads(db.execute("SELECT config FROM workers WHERE id='worker'").fetchone()["config"])
            config["capabilities"].update(isolation="dedicated", host_principal="p1")
            db.execute("UPDATE workers SET config=? WHERE id='worker'", (dumps(config),))
        self.health_extra = {"isolation": "trusted-local", "host_principal": "p1"}
        self.tools = {**TOOLS, "codex": {"available": True}}
        self.engine.probe()
        self.assertNotIn("codex", self.capabilities()["tools"])   # the same boundary id, but no longer the same isolation
        self.health_extra, self.now = {"isolation": "dedicated", "host_principal": "p2"}, self.now + 601
        self.engine.probe()
        self.assertNotIn("codex", self.capabilities()["tools"])
        self.health_extra, self.now = {"isolation": "dedicated", "host_principal": "p1"}, self.now + 601
        self.engine.probe()
        self.assertIn("codex", self.capabilities()["tools"])

    def test_an_unreachable_worker_is_only_logged(self):
        self.health_error = "Worker 未返回可核实回执"
        with self.assertLogs(level="WARNING") as logs:
            self.engine.probe()
        self.assertTrue(any("health" in line for line in logs.output))
        self.assertNotIn("checked_at", self.capabilities())

    def test_a_worker_nobody_waits_on_is_checked_every_ten_minutes(self):
        self.engine.probe()
        self.now += 60
        self.engine.probe()
        self.assertEqual(self.health_calls(), 1)
        self.now += 541
        self.engine.probe()
        self.assertEqual(self.health_calls(), 2)

    def test_a_worker_a_session_waits_on_is_checked_every_minute(self):
        self.expired, self.login = {("code", 1)}, "expired"
        rid = self.start([step("code", "kiro")])
        self.tick(rid)
        self.engine.probe()
        self.now += 61
        self.engine.probe()
        self.assertEqual(self.health_calls(), 2)


if __name__ == "__main__":
    unittest.main()
