"""D46 P4: a code-changing Session from a repository, on a dedicated execution environment, ends with a Knowledge task the
platform appends after the Evaluator; what it lists is checked and written as KiroCrew lessons of that repository, and the
repository's later tasks are given them. A shared environment gathers and injects nothing."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from octopus import knowledge
from octopus.db import dumps
from octopus.security import Error
from tests.test_evaluator import CAPABILITIES, TEAM, EvaluatorCase

CAPS = {**CAPABILITIES, "knowledge": True}
SOURCE = {"type": "git", "url": "https://github.com/O/r.git"}
SHA = "a" * 40
READ_ONLY = [{"id": "look", "name": "分析", "tool": "claude", "prompt": "分析", "kind": "design", "acceptance": ["结论"]}]


class KnowledgeCase(EvaluatorCase):
    def setUp(self):
        super().setUp()
        self.configure(CAPS)
        self.prepared, self.writes, self.forgets, self.write_errors = [], [], [], []
        self.knowledge = [{"rule": "依赖用 pnpm 安装", "avoid": "不要用 npm install"}, {"rule": "接口测试在 tests/api/"}]
        self.knowledge_outcome, self.knowledge_running, self.knowledge_dispatch_error = {}, False, None
        self.candidates, self.knowledge_check, self.restores = False, None, []

    def configure(self, capabilities, scope="dedicated"):
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=?, scope=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": capabilities}), scope))
            if scope == "shared":
                db.execute("INSERT OR IGNORE INTO worker_spaces(worker_id,user_id) VALUES('worker',?)", (self.actor["id"],))

    def request(self, config, path, body, **kwargs):
        if path == "/prepare":
            self.prepared.append(copy.deepcopy(body))
        if path == "/knowledge/write":
            self.writes.append(copy.deepcopy(body))
            if self.write_errors:
                raise self.write_errors.pop(0)
            return {"results": [{"rule": item["rule"], "result": "written"} for item in body["items"]]}
        if path == "/knowledge/forget":
            self.forgets.append(copy.deepcopy(body))
            return {"ok": True}
        role = lambda: (self.bodies[body["attempt_id"]].get("contract") or {}).get("role")
        if path == "/dispatch" and (body.get("contract") or {}).get("role") == "knowledge" and self.knowledge_dispatch_error:
            raise self.knowledge_dispatch_error
        if path == "/candidate" and self.candidates:   # a Worker of D37: candidates and the review's check
            if body["mode"] == "commit":
                return {"sha": SHA, "parent": "b" * 40, "changed": True, "files": 1, "insertions": 1, "deletions": 0, "secrets": []}
            if body["mode"] == "restore":
                self.restores.append(copy.deepcopy(body))
                return {"head": SHA, "clean": True, "changed_files": []}
            if role() == "knowledge" and self.knowledge_check:
                return self.knowledge_check
            return {"head": SHA, "clean": True, "changed_files": []}
        if path == "/inspect" and role() == "knowledge" and self.knowledge_running:
            return {"native_id": "n1", "native": {}}
        result = super().request(config, path, body, **kwargs)
        if path == "/inspect" and role() == "knowledge":
            outcome = json.loads(result["outcome"])
            outcome.update(knowledge=self.knowledge, **self.knowledge_outcome)
            result["outcome"] = dumps(outcome)
        if path == "/inspect" and role() == "evaluator" and self.candidates:
            outcome = json.loads(result["outcome"])
            outcome["reviewed_commit"] = SHA
            result["outcome"] = dumps(outcome)
        return result

    def run_(self, steps=TEAM, source=SOURCE, **body):
        return super().run_(steps=steps, source=source, **body)

    def tasks(self, run):
        return [t["id"] for t in run["tasks"]]

    def work(self):
        """The task dispatches (no coordination)."""
        return [b for b in self.bodies.values() if not b.get("coordination")]


class ScopeTests(KnowledgeCase):
    def test_a_dedicated_repository_session_is_marked_and_given_its_lessons(self):
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")
        scope = self.prepared[0]["knowledge_scope"]
        self.assertRegex(scope, r"^github\.com_o_r\.[0-9a-f]{12}$")
        self.assertTrue(self.work())
        self.assertEqual({b.get("lessons") for b in self.work()}, {True})

    def test_new_or_unsupported_sessions_gather_nothing(self):   # a shared environment gathers for the repository (D52)
        cases = {"new source": (CAPS, "dedicated", {"type": "new"}),
                 "older Worker": (CAPABILITIES, "dedicated", SOURCE)}
        for name, (capabilities, scope, source) in cases.items():
            with self.subTest(name):
                self.configure(capabilities, scope)
                self.prepared.clear()
                self.bodies.clear()
                _, run = self.run_(source=source)
                self.assertEqual(run["status"], "completed")
                self.assertNotIn("knowledge_scope", self.prepared[0])
                self.assertEqual({b.get("lessons") for b in self.work()}, {None})
                self.assertNotIn("knowledge", self.tasks(run))
                self.assertEqual(self.writes, [])

    def test_a_shared_session_says_its_items_go_to_the_repository_only(self):   # D52 R4
        self.configure(CAPS, "shared")
        _, run = self.run_()
        self.assertEqual(run["knowledge"]["note"], knowledge.SHARED)
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"kept"})

    def test_older_contracts_do_not_apply(self):
        self.assertFalse(knowledge.applies({"execution_contract": 3}, SOURCE, "dedicated", {"capabilities": CAPS}))
        self.assertTrue(knowledge.applies({"execution_contract": 4}, SOURCE, "dedicated", {"capabilities": CAPS}))

    def test_injection_follows_the_current_environment(self):
        rid = self.start(steps=TEAM, source=SOURCE, recovery={"enabled": False, "max_cycles": 2})
        self.engine.tick()   # prepared on the dedicated environment
        self.assertIn("knowledge_scope", self.prepared[0])
        self.configure(CAPS, "shared")
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual({b.get("lessons") for b in self.work()}, {None})
        self.assertEqual(self.writes, [])   # gathered for the repository only (D52 R4)


class KeyTests(unittest.TestCase):
    def test_the_repository_key_is_the_same_for_every_form_of_its_address(self):
        keys = {knowledge.key({"type": "git", "url": url}, "user-1") for url in (
            "https://github.com/O/r.git", "https://github.com/o/r", "git@github.com:o/r.git", "ssh://git@github.com/o/r.git",
            "https://token@github.com/o/r.git/")}
        self.assertEqual(len(keys), 1)
        self.assertRegex(keys.pop(), r"^github\.com_o_r\.[0-9a-f]{12}$")

    def test_another_space_gets_another_key(self):
        self.assertNotEqual(knowledge.key(SOURCE, "team-a"), knowledge.key(SOURCE, "team-b"))

    def test_the_key_is_what_the_worker_accepts(self):
        key = knowledge.key({"type": "git", "url": "https://gitlab.example.com/" + "group/" * 40 + "r.git"}, "user-1")
        self.assertRegex(key, r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
        self.assertIsNone(knowledge.key({"type": "git", "url": "not a url"}, "user-1"))


class TaskTests(KnowledgeCase):
    def test_the_knowledge_task_comes_after_the_evaluator(self):
        _, run = self.run_()
        self.assertEqual(self.tasks(run), ["code", "check", "knowledge"])
        task = run["tasks"][-1]
        self.assertEqual((task["spec"]["role"], task["spec"]["kind"], task["spec"]["tool"]), ("knowledge", "design", "kiro"))
        self.assertEqual(task["spec"]["acceptance"], ["列出可复用的项目知识（可为空）"])
        self.assertEqual(task["mapping"]["label"], "平台追加（沉淀项目知识）")
        knowledge_dispatch = next(b for b in self.work() if b["spec"]["id"] == "knowledge")
        self.assertEqual(knowledge_dispatch["contract"]["role"], "knowledge")

    def test_without_kiro_the_first_available_tool_gathers(self):
        self.configure({**CAPS, "tools": {"kiro": {"available": False}, "claude": {"available": True}, "codex": {"available": True}}})
        _, run = self.run_()
        self.assertEqual(run["tasks"][-1]["spec"]["tool"], "claude")

    def test_a_read_only_session_gets_no_knowledge_task(self):
        _, run = self.run_(steps=READ_ONLY)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.tasks(run), ["look"])

    def test_a_continue_reruns_the_knowledge_task_and_writes_again(self):
        rid, run = self.run_()
        self.knowledge = [{"rule": "登录接口返回 401 而非 403"}]
        self.service.operate(self.actor, rid, "continue", {"request_id": "knowledge-continue-1", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补充登录失败的处理"})
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.tasks(run), ["code", "check", "knowledge"])
        self.assertEqual([[i["rule"] for i in w["items"]] for w in self.writes],
                         [["依赖用 pnpm 安装", "接口测试在 tests/api/"], ["登录接口返回 401 而非 403"]])

    def test_a_change_after_knowledge_gets_another(self):
        code, gather = {"id": "code", "kind": "implement"}, {"id": "knowledge", "role": "knowledge", "kind": "design"}
        self.assertTrue(knowledge.needed([code], []))
        self.assertFalse(knowledge.needed([code, gather], ["knowledge"]))
        self.assertTrue(knowledge.needed([code, gather, {"id": "more", "kind": "deliver"}], ["knowledge"]))
        self.assertTrue(knowledge.needed([code, gather], []))   # a knowledge task the platform did not add is not its own
        self.assertFalse(knowledge.needed([{"id": "look", "kind": "design"}], []))


class WriteTests(KnowledgeCase):
    def test_checked_items_are_written_and_recorded(self):
        rid, run = self.run_()
        self.assertEqual(len(self.writes), 1)
        write = self.writes[0]
        self.assertEqual(write["run_id"], rid)
        self.assertEqual(write["scope"], self.prepared[0]["knowledge_scope"])
        self.assertEqual(write["items"], [{"rule": "依赖用 pnpm 安装", "avoid": "不要用 npm install"}, {"rule": "接口测试在 tests/api/"}])
        items = run["knowledge"]["items"]
        self.assertEqual([(i["rule"], i["result"], i["task_id"]) for i in items],
                         [("依赖用 pnpm 安装", "written", "knowledge"), ("接口测试在 tests/api/", "written", "knowledge")])
        self.assertEqual(items[0]["avoid"], "不要用 npm install")
        self.assertIsNone(run["knowledge"]["note"])
        with self.db.read() as db:
            event = db.execute("SELECT * FROM events WHERE run_id=? AND kind='run.knowledge'", (rid,)).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(run["status"], "completed")

    def test_too_many_too_long_or_secret_items_are_not_written(self):
        self.knowledge = ([{"rule": "x" * 301}, {"rule": "令牌 ghp_" + "a" * 36}, {"rule": ""}, "构建用 make build"]
                          + [{"rule": f"约定 {n}"} for n in range(6)])
        _, run = self.run_()
        self.assertEqual([i["rule"] for i in self.writes[0]["items"]], ["构建用 make build", "约定 0", "约定 1", "约定 2", "约定 3"])
        skipped = [i for i in run["knowledge"]["items"] if i["result"] == "skipped"]
        self.assertEqual(len(skipped), 5)
        self.assertFalse(any("ghp_" in (i["rule"] + (i["reason"] or "")) for i in run["knowledge"]["items"]))
        self.assertTrue(all(i["reason"] for i in skipped))

    def test_nothing_listed_writes_nothing(self):
        self.knowledge = []
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.writes, [])
        self.assertEqual(run["knowledge"]["items"], [])

    def test_what_this_environment_already_holds_is_skipped(self):
        self.run_()
        _, run = self.run_()
        self.assertEqual(len(self.writes), 1)   # the second Session had nothing new to write
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"skipped"})

    def test_refusals_are_recorded_as_the_worker_reports_them(self):
        original = self.request

        def answer(config, path, body, **kwargs):
            if path == "/knowledge/write":
                self.writes.append(body)
                return {"results": [{"rule": body["items"][0]["rule"], "result": "refused", "reason": "volatile_session_fact"},
                                    {"rule": body["items"][1]["rule"], "result": "superseded", "reason": "替代了：旧规则"}]}
            return original(config, path, body, **kwargs)
        self.engine.request = answer
        _, run = self.run_()
        self.assertEqual([(i["result"], i["reason"]) for i in run["knowledge"]["items"]],
                         [("refused", "volatile_session_fact"), ("superseded", "替代了：旧规则")])

    def test_an_older_worker_skips_the_write(self):
        self.write_errors = [Error(404, "not_found", "接口不存在")]
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"skipped"})
        self.assertIn("不支持", run["knowledge"]["items"][0]["reason"])

    def test_an_unavailable_worker_is_waited_for(self):
        self.write_errors = [Error(503, "native_unavailable", "KiroCrew Gateway 暂不可用")]
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.writes), 2)
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"written"})

    def test_a_worker_unavailable_too_long_does_not_hold_the_session(self):
        self.write_errors = [Error(503, "native_unavailable", "暂不可用")] * 50
        clock = [1000.0]
        self.engine.wall = lambda: clock[0]
        rid = self.start(steps=TEAM, source=SOURCE, recovery={"enabled": False, "max_cycles": 2})
        run = self.tick(rid)
        self.assertNotEqual(run["status"], "completed")   # waits for the environment
        clock[0] += knowledge.RETRY + 1
        run = self.tick(rid, 3)
        self.assertEqual(run["status"], "completed")
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"skipped"})


class NeverHoldsTests(KnowledgeCase):
    """The platform's own side task never holds a Session the Evaluator passed (R5; review 2026-10-19)."""

    def start_(self):
        return self.start(steps=TEAM, source=SOURCE, recovery={"enabled": True, "max_cycles": 2})

    def assert_completed_without_knowledge(self, run, why):
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.writes, [])
        self.assertEqual(self.decisions, [])   # no Coordinator was asked
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM recoveries WHERE run_id=?", (run["id"],)).fetchone()[0], 0)
        self.assertIn(why, run["knowledge"]["note"])
        self.assertEqual(next(t for t in run["tasks"] if t["id"] == "knowledge")["status"], "accepted")

    def test_a_blocked_knowledge_outcome_completes_the_session(self):
        self.knowledge_outcome = {"decision": "blocked", "failure_type": "input", "summary": "没有可读的交接"}
        run = self.tick(self.start_())
        self.assert_completed_without_knowledge(run, "未完成")

    def test_a_knowledge_task_without_an_outcome_completes_the_session(self):
        self.knowledge_outcome = {"decision": "maybe"}
        run = self.tick(self.start_())
        self.assert_completed_without_knowledge(run, "未完成")

    def test_an_expired_kiro_login_completes_the_session(self):
        self.knowledge_dispatch_error = Error(503, "tool_auth_expired", "Worker 上 kiro-cli 登录已过期")
        run = self.tick(self.start_())
        self.assert_completed_without_knowledge(run, "kiro-cli 登录已过期")

    def test_a_tool_no_longer_available_completes_the_session(self):
        rid = self.start_()
        for _ in range(30):
            self.engine.tick()
            if any(t["id"] == "knowledge" for t in self.service.run(self.actor, rid)["tasks"]):
                break
        self.configure({**CAPS, "tools": {"kiro": {"available": False, "reason": "未安装"}, "claude": {"available": True},
                                          "codex": {"available": True}}})
        run = self.tick(rid)
        self.assert_completed_without_knowledge(run, "不可用")

    def test_a_stalled_knowledge_task_completes_the_session(self):
        self.knowledge_running = True
        rid = self.start_()
        self.tick(rid)
        with self.db.read() as db:
            row = dict(db.execute("SELECT r.*,w.config worker_config,w.enabled worker_enabled,w.scope worker_scope FROM runs r "
                                  "JOIN workers w ON w.id=r.worker_id WHERE r.id=?", (rid,)).fetchone())
            task = dict(db.execute("SELECT * FROM tasks WHERE run_id=? AND id='knowledge'", (rid,)).fetchone())
            attempt = dict(db.execute("SELECT * FROM attempts WHERE id=?", (task["current_attempt"],)).fetchone())
        self.assertEqual(attempt["status"], "running")
        self.engine._stalled(row, task, attempt, {}, {"reason": "原生任务 30 分钟没有进展"}, {"enabled": False})
        self.knowledge_running = False
        self.assert_completed_without_knowledge(self.tick(rid), "没有进展")

    def test_a_transient_failure_is_still_retried_once(self):
        answers = iter([{"native": {"done": True, "result": "", "error": "AcpProcessDied: Runtime process died"}}])
        original = KnowledgeCase.request

        def flaky(config, path, body, **kwargs):
            if path == "/inspect" and (self.bodies[body["attempt_id"]].get("contract") or {}).get("role") == "knowledge":
                answer = next(answers, None)
                if answer:
                    return {**answer, "evidence": {"actual_backend": "kiro"}}
            return original(self, config, path, body, **kwargs)
        self.engine.request = flaky
        run = self.tick(self.start_())
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(next(t for t in run["tasks"] if t["id"] == "knowledge")["attempts"]), 2)
        self.assertEqual(len(self.writes), 1)


class ReadOnlyTests(KnowledgeCase):
    def test_what_the_knowledge_task_changed_is_restored_and_nothing_is_written(self):
        self.candidates = True
        self.knowledge_check = {"head": SHA, "clean": False, "changed_files": ["README.md"]}
        run = self.tick(self.start(steps=TEAM, source=SOURCE, recovery={"enabled": True, "max_cycles": 2}))
        self.assertEqual(run["status"], "completed")
        self.assertEqual([(r["mode"], r["expected"]) for r in self.restores], [("restore", SHA)])
        self.assertEqual(self.writes, [])
        self.assertIn("README.md", run["knowledge"]["note"])
        self.assertIn("已还原", run["knowledge"]["note"])

    def test_a_read_only_knowledge_task_is_written_as_usual(self):
        self.candidates = True
        run = self.tick(self.start(steps=TEAM, source=SOURCE, recovery={"enabled": False, "max_cycles": 2}))
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.restores, [])
        self.assertEqual(len(self.writes), 1)


class TimeoutTests(KnowledgeCase):
    def test_the_platform_waits_longer_than_the_worker_takes(self):
        seen = []
        original = KnowledgeCase.request

        def timed(config, path, body, **kwargs):
            if path == "/knowledge/write":
                seen.append(kwargs.get("timeout"))
            return original(self, config, path, body, **kwargs)
        self.engine.request = timed
        self.run_()
        self.assertGreaterEqual(seen[0], 5 * 30 + 15)   # five KiroCrew writes of up to 30 s each


class ContinueTests(KnowledgeCase):
    def test_a_selective_continue_gathers_again(self):
        rid, run = self.run_()
        self.knowledge = [{"rule": "错误密码返回 401"}]
        self.service.operate(self.actor, rid, "continue", {"request_id": "knowledge-continue-2", "expected_version": run["version"],
                                                           "task_id": "code", "input": "补充错误密码提示", "rerun": []})
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual([[i["rule"] for i in w["items"]] for w in self.writes][-1], ["错误密码返回 401"])

    def test_the_rerun_rule_names_the_knowledge_task(self):
        from octopus.domain import rerun_plan
        rows = [{"id": "code", "status": "accepted", "spec": dumps({"kind": "implement"})},
                {"id": "knowledge", "status": "accepted", "spec": dumps({"kind": "design", "role": "knowledge"})},
                {"id": "look", "status": "accepted", "spec": dumps({"kind": "design"})}]
        rerunning, kept, reasons = rerun_plan(rows, 0, [])
        self.assertEqual((rerunning, kept, reasons), (["code", "knowledge"], ["look"], {"knowledge": "knowledge"}))


class StatusTests(KnowledgeCase):
    def test_the_compact_status_lists_what_was_written(self):
        from octopus import mcp
        self.knowledge = [{"rule": "依赖用 pnpm 安装"}, {"rule": "x" * 301}]
        _, run = self.run_()
        view = mcp.compact_status(run, [])["session"]["knowledge"]
        self.assertEqual([(i["rule"], i["result"]) for i in view["items"]][0], ("依赖用 pnpm 安装", "written"))
        self.assertIn("reason", view["items"][1])
        self.assertEqual(set(view["items"][0]), {"id", "rule", "result"})

    def test_a_shared_sessions_compact_status_says_why(self):
        from octopus import mcp
        self.configure(CAPS, "shared")
        _, run = self.run_()
        view = mcp.compact_status(run, [])["session"]["knowledge"]   # D52 R4: kept for the repository only
        self.assertEqual(view["note"], knowledge.SHARED)
        self.assertEqual([(i["result"], i["reason"]) for i in view["items"]], [("kept", knowledge.KEPT)] * 2)


    def test_a_shared_sessions_timeline_counts_what_is_kept_for_the_repository(self):
        self.configure(CAPS, "shared")
        rid, _ = self.run_()
        with self.db.read() as db:
            payloads = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind='run.knowledge'", (rid,))]
        reason = next(p["reason"] for p in payloads if "沉淀" in p.get("reason", ""))
        self.assertEqual(reason, "沉淀知识：发布时提到仓库 2 条")   # D52 R4, not 「没有可沉淀的知识」

class ForgetTests(KnowledgeCase):
    def forget(self, actor, rid, kid):
        with patch("octopus.transport.worker_request", side_effect=lambda config, path, body, **k: self.request(config, path, body)):
            return self.service.forget_knowledge(actor, rid, {"id": kid})

    def test_the_creator_deletes_one_item(self):
        rid, run = self.run_()
        item = run["knowledge"]["items"][0]
        self.forget(self.actor, rid, item["id"])
        self.assertEqual(self.forgets[-1]["rule"], "依赖用 pnpm 安装")
        self.assertEqual(self.forgets[-1]["scope"], self.prepared[0]["knowledge_scope"])
        run = self.service.run(self.actor, rid)
        self.assertIsNotNone(run["knowledge"]["items"][0]["removed"])
        self.assertEqual(run["knowledge"]["items"][0]["removed_by"], "owner")
        with self.assertRaises(Error) as again:
            self.forget(self.actor, rid, item["id"])
        self.assertEqual(again.exception.status, 409)

    def test_a_deleted_item_is_not_written_again(self):
        rid, run = self.run_()
        self.forget(self.actor, rid, run["knowledge"]["items"][0]["id"])
        _, again = self.run_()
        self.assertEqual(len(self.writes), 1)   # the next Session listing it again does not write it back
        item = next(i for i in again["knowledge"]["items"] if i["rule"] == "依赖用 pnpm 安装")
        self.assertEqual((item["result"], item["reason"]), ("skipped", knowledge.REMOVED))

    def test_forgetting_is_checked(self):
        rid, run = self.run_()
        item = run["knowledge"]["items"][0]
        with self.assertRaises(Error) as other:
            self.forget(self.other, rid, item["id"])
        self.assertIn(other.exception.status, {403, 404})
        with self.assertRaises(Error) as missing:
            self.forget(self.actor, rid, "knowledge-" + "0" * 32)
        self.assertEqual(missing.exception.status, 404)
        self.assertEqual(self.forgets, [])

    def test_the_api_documents_and_serves_the_delete(self):
        from octopus.schema import openapi
        from octopus.server import required_scope
        self.assertIn("/v1/runs/{id}/knowledge/forget", openapi()["paths"])
        self.assertEqual(required_scope("POST", "/v1/runs/run-1/knowledge/forget"), "execute")

    def test_the_route_reaches_the_service(self):
        import threading
        import urllib.error
        import urllib.request
        from octopus.security import issue_token
        from octopus.server import server
        rid, _ = self.run_()
        with self.db.transaction() as db:
            token = issue_token(db, self.actor["id"])["token"]
        app = server(self.db, 0)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (app.shutdown(), app.server_close(), thread.join(2)))
        request = urllib.request.Request(f"http://127.0.0.1:{app.server_address[1]}/v1/runs/{rid}/knowledge/forget", method="POST",
                                         data=b'{"id": "knowledge-none"}', headers={"Authorization": "Bearer " + token,
                                                                                    "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        with caught.exception as response:
            self.assertEqual((response.code, json.load(response)["error"]), (404, "知识条目不存在"))

    def test_forget_goes_to_the_environment_that_wrote_it(self):
        rid, run = self.run_()
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config) VALUES('other-worker','W2',?,?)",
                       (self.actor["id"], dumps({"url": "elsewhere", "token": "t", "capabilities": CAPS})))
            db.execute("UPDATE runs SET worker_id='other-worker' WHERE id=?", (rid,))
        seen = []
        with patch("octopus.transport.worker_request", side_effect=lambda config, path, body, **k: seen.append(config["url"]) or {"ok": True}):
            self.service.forget_knowledge(self.actor, rid, {"id": run["knowledge"]["items"][0]["id"]})
        self.assertEqual(seen, ["fixture"])


class RoleTests(unittest.TestCase):
    def test_the_knowledge_card_is_short_and_not_offered_to_the_planner(self):
        from octopus import roles
        self.assertEqual(roles.KINDS["knowledge"], ("design",))
        self.assertLessEqual(len(roles.card("knowledge")), 600)
        self.assertNotIn("knowledge（", roles.catalogue())

    def test_a_planned_knowledge_task_loses_the_role(self):
        from octopus.workflow_import import normalize
        notes = []
        planned = normalize([{"id": "k", "name": "整理", "tool": "kiro", "prompt": "整理", "kind": "design", "role": "knowledge"}],
                            notes, planned=True)
        self.assertNotIn("role", planned[0])
        self.assertTrue(any("knowledge" in note for note in notes))

    def test_the_knowledge_task_is_told_the_outcome_field(self):
        from pathlib import Path
        from octopus.recovery import quality_instructions
        text = quality_instructions(Path("/w"), {"role": "knowledge", "kind": "design", "acceptance": [knowledge.ITEM]})
        self.assertIn('"knowledge"', text)
        self.assertTrue(re.search(r"rule.*300", text) and re.search(r"avoid.*200", text))


if __name__ == "__main__":
    unittest.main()
