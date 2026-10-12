"""D34 §6–§7: structured tasks (kind, deliverables, acceptance, verify, depends_on, paths), the requirement and task
contract passed to every task, and continuing with only the affected tasks."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client
from octopus.db import dumps
from octopus.domain import steps
from octopus.engine import Engine
from octopus.security import Error
from octopus.worker import Worker
from octopus.workflow_import import import_workflow
from tests.test_teams_workers import TeamsWorkersCase

LEGACY = {"id", "name", "tool", "model", "effort", "prompt"}
FLOW = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement",
         "acceptance": ["登录成功"], "verify": ["npm test"]},
        {"id": "docs", "name": "文档", "tool": "claude", "prompt": "写文档", "kind": "deliver", "acceptance": ["说明齐全"]},
        {"id": "check", "name": "核对", "tool": "claude", "prompt": "核对", "kind": "test", "depends_on": ["code"],
         "acceptance": ["登录成功"], "verify": ["npm test"]}]


class ContractTests(unittest.TestCase):
    base = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}

    def test_structured_fields_are_kept_only_when_given(self):
        self.assertNotIn("acceptance", steps([self.base])[0])
        step = steps([{**self.base, "kind": "implement", "acceptance": [" 登录成功 ", "登录成功"], "verify": ["npm test"],
                       "deliverables": ["src/login.ts"], "paths": ["src/**"]}])[0]
        self.assertEqual((step["acceptance"], step["verify"], step["kind"]), (["登录成功"], ["npm test"], "implement"))
        self.assertEqual((step["deliverables"], step["paths"]), (["src/login.ts"], ["src/**"]))

    def test_depends_on_must_point_to_earlier_tasks(self):
        review = {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查"}
        self.assertEqual(steps([self.base, {**review, "depends_on": ["code"]}])[1]["depends_on"], ["code"])
        for bad in (["review"], ["nope"]):
            with self.subTest(bad=bad), self.assertRaises(Error):
                steps([self.base, {**review, "depends_on": bad}])

    def test_bad_kinds_and_oversized_lists_are_refused(self):
        for extra in ({"kind": "deploy"}, {"acceptance": ["x"] * 21}, {"verify": "npm test"}, {"acceptance": ["x" * 501]},
                      {"verify": ["v"] * 11}, {"paths": [3]}):
            with self.subTest(extra=extra), self.assertRaises(Error):
                steps([{**self.base, **extra}])

    def test_imported_workflows_keep_the_fields_and_follow_renamed_ids(self):
        result = import_workflow('{"steps": [{"id": "Build", "name": "实现", "tool": "claude", "prompt": "实现", '
                                 '"kind": "implement", "acceptance": ["可运行"], "verify": ["npm test"]}, '
                                 '{"id": "Check", "name": "核对", "tool": "codex", "prompt": "核对", "kind": "test", '
                                 '"depends_on": ["Build"], "acceptance": ["测试通过"], "verify": ["npm test"]}]}')
        self.assertEqual([s["id"] for s in result["steps"]], ["build", "check"])
        self.assertEqual(result["steps"][1]["depends_on"], ["build"])
        self.assertFalse(any("忽略" in note for note in result["notes"]))
        later = import_workflow('[{"id": "a", "name": "甲", "tool": "claude", "prompt": "x", "depends_on": ["b"]}, '
                                '{"id": "b", "name": "乙", "tool": "codex", "prompt": "y", "depends_on": ["a", "甲", "nope"]}]')
        self.assertEqual([s.get("depends_on") for s in later["steps"]], [[], ["a"]])
        self.assertEqual(sum("忽略 depends_on" in note for note in later["notes"]), 2)


class DispatchContractTests(TeamsWorkersCase):
    def test_dispatch_spec_keeps_the_legacy_fields(self):
        rid = self.service.start(self.c, {"request_id": "start-0000000001", "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": False, "max_cycles": 2}, "steps": FLOW})["id"]
        with self.db.transaction() as db:   # fixture: a plan KiroCrew applied earlier
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()[0])
            snapshot["planning"] = {"mode": "agent", "applied": True, "intent": "修复登录", "summary": "## 验收\n- 登录成功"}
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (dumps(snapshot), rid))
        sent = []
        def worker(config, path, body, **kwargs):
            if path == "/prepare":
                return {"native_parent": "octopus-x", "workspace": "/w/x"}
            if path == "/dispatch":
                sent.append(body)
                return {"status": "running", "native_id": "n" + body["attempt_id"][-7:], "context_hash": "h"}
            return {"status": "running", "native": {"done": True, "result": "完成", "error": ""},
                    "evidence": {"actual_backend": "claude"}, "handoff": "交接",
                    "outcome": dumps({"decision": "pass", "summary": "通过"})}
        engine = Engine(self.db, request=worker)
        for _ in range(8):
            engine.tick()
        self.assertEqual([b["spec"]["id"] for b in sent], ["code", "docs", "check"])
        self.assertTrue(all(set(b["spec"]) == LEGACY for b in sent))
        self.assertEqual(sent[0]["contract"]["acceptance"], ["登录成功"])
        self.assertEqual(sent[0]["requirement"], {"intent": "修复登录", "summary": "## 验收\n- 登录成功"})
        self.assertEqual([u["id"] for u in sent[1]["upstream"]], ["code"])
        self.assertEqual([u["id"] for u in sent[2]["upstream"]], ["code"])   # depends_on: docs is left out


    def test_the_diagnosis_context_carries_the_requirement_and_contract(self):
        from octopus.recovery import schedule
        rid = self.service.start(self.c, {"request_id": "start-0000000002", "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "recovery": {"enabled": True, "max_cycles": 2}, "steps": FLOW})["id"]
        with self.db.transaction() as db:   # fixture: a planned run whose first task failed
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()[0])
            snapshot["planning"] = {"mode": "agent", "applied": True, "intent": "修复登录", "summary": "## 验收\n- 登录成功"}
            db.execute("UPDATE runs SET snapshot=?,status='blocked',native_parent='octopus-x' WHERE id=?", (dumps(snapshot), rid))
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,error,evidence,created,updated) "
                       "VALUES('attempt-1',?,'code',1,'{}','blocked','失败','{\"failure_type\":\"implementation\"}',0,0)", (rid,))
            db.execute("UPDATE tasks SET status='blocked',current_attempt='attempt-1' WHERE run_id=? AND id='code'", (rid,))
            run = dict(db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())
            task = db.execute("SELECT * FROM tasks WHERE run_id=? AND id='code'", (rid,)).fetchone()
            attempt = db.execute("SELECT * FROM attempts WHERE id='attempt-1'").fetchone()
            schedule(db, self.db, run, task, attempt)
            context = json.loads(db.execute("SELECT context FROM recoveries WHERE run_id=?", (rid,)).fetchone()[0])
        self.assertEqual(context["requirement"]["intent"], "修复登录")
        self.assertEqual(context["tasks"][0]["spec"]["acceptance"], ["登录成功"])


class WorkerContractTests(unittest.TestCase):
    def test_worker_instructions_include_requirement_and_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = Worker(root / "worker", "x" * 40, crew_home=root / "crew")
            rid = "run-" + "1" * 32
            def native(path, body=None, method=None):
                if path == "/api/chat/slots/octopus-" + rid and body is None:
                    raise gateway_client.GatewayError(404, '{"error":"not found"}')
                if path == "/api/chat/slots":
                    return {"key": "octopus-" + rid}
                return {"id": "abcdef12"} if path == "/api/spawn" else {"ok": True}
            node.native = native
            node.prepare({"run_id": rid, "source": {"type": "new"}})
            attempt = "attempt-" + "2" * 32
            node.dispatch({"run_id": rid, "attempt_id": attempt, "input": "需求", "upstream": [], "quality": True,
                           "spec": {"id": "check", "name": "核对", "tool": "claude", "model": "auto", "effort": "", "prompt": "核对"},
                           "contract": {"kind": "test", "acceptance": ["登录成功"], "verify": ["npm test"], "depends_on": ["code"]},
                           "requirement": {"intent": "修复登录", "summary": "## 验收\n- 登录成功"}})
            folder = node.path(rid) / "attempts" / attempt
            text = (folder / "instructions.md").read_text()
            for part in ("## 需求与验收标准（KiroCrew 规划补全）", "修复登录", "## 本任务契约", "登录成功", "npm test", '"acceptance"',
                         "suggestions"):
                self.assertIn(part, text)
            context = json.loads((folder / "context.json").read_text())
            self.assertEqual((context["contract"]["kind"], context["requirement"]["intent"]), ("test", "修复登录"))


class OutcomeTests(unittest.TestCase):
    def test_acceptance_results_and_suggestions_are_validated(self):
        from octopus.recovery import outcome
        good = outcome('{"decision":"pass","summary":"完成","acceptance":[{"item":"登录成功","result":"pass","evidence":"npm test 0"}],'
                       '"suggestions":["补 CHANGELOG"]}')
        self.assertEqual((good["suggestions"], good["acceptance"][0]["result"]), (["补 CHANGELOG"], "pass"))
        for bad in ('{"decision":"pass","summary":"x","acceptance":[{"item":"a","result":"ok"}]}',
                    '{"decision":"pass","summary":"x","acceptance":[{"result":"pass"}]}',
                    '{"decision":"pass","summary":"x","suggestions":"补"}'):
            with self.subTest(bad=bad), self.assertRaises(Error):
                outcome(bad)


if __name__ == "__main__":
    unittest.main()
