"""D65: tasks are not bound to Git — a Session may have no code repository (source ``none``) and its tasks may be general
work, research (调研) and write (撰写), judged like a delivery; code Sessions stay as they were."""
import json
import unittest

from octopus import evaluation
from octopus.engine import Engine
from octopus.security import Error
from tests.test_engine_gates import BASE, IMPLEMENT, FakeWorker
from tests.test_teams_workers import TeamsWorkersCase

RESEARCH = {"id": "research", "name": "调研", "tool": "codex", "prompt": "调研主题并列出来源", "kind": "research",
            "acceptance": ["列出至少 3 个来源"]}
WRITE = {"id": "report", "name": "撰写报告", "tool": "claude", "prompt": "根据调研写报告", "kind": "write",
         "acceptance": ["报告包含结论与依据"], "deliverables": ["report.md"]}


class GeneralCase(TeamsWorkersCase):
    def session(self, steps, source=None, request="start-0000000001", **extra):
        return self.service.start(self.c, {"request_id": request, "input": "调研某主题并写一份报告",
                                           "source": source or {"type": "none"}, "team_id": "team-p",
                                           "recovery": {"enabled": False, "max_cycles": 2}, "steps": steps, **extra})["id"]

    def run_engine(self, worker, ticks=12):
        engine = Engine(self.db, request=worker)
        for _ in range(ticks):
            engine.tick()
        return engine

    def evidence(self, rid, task):
        with self.db.read() as db:
            row = db.execute("SELECT a.evidence FROM attempts a JOIN tasks t ON t.current_attempt=a.id WHERE t.run_id=? AND t.id=?",
                             (rid, task)).fetchone()
        return json.loads(row["evidence"] or "{}")

    def gates(self, rid, task):
        return {g["name"]: g["status"] for g in self.evidence(rid, task).get("gates", [])}


class SourceTests(GeneralCase):
    def test_a_session_needs_no_repository(self):
        rid = self.session([WRITE])
        self.assertEqual(self.service.run(self.c, rid)["source"], {"type": "none"})
        with self.assertRaises(Error):
            self.session([WRITE], source={"type": "none", "url": "https://example.com/x.git"}, request="start-0000000002")

    def test_the_worker_prepares_it_as_a_new_project(self):   # an older Worker knows new, not none
        worker = FakeWorker()
        self.session([WRITE])
        self.run_engine(worker, ticks=2)
        self.assertEqual(next(b for p, b in worker.calls if p == "/prepare")["source"], {"type": "new"})

    def test_research_and_write_are_kinds(self):
        self.session([RESEARCH, WRITE])
        with self.assertRaises(Error):
            self.session([{**WRITE, "kind": "chat"}], request="start-0000000003")

    def test_a_session_without_a_repository_is_not_published(self):
        rid = self.session([WRITE])
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed', workspace='/w/x' WHERE id=?", (rid,))
        with self.assertRaises(Error) as caught:
            self.service.publish(self.c, rid, {})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "no_repository"))
        with self.assertRaises(Error) as caught:
            self.session([WRITE], request="start-0000000004", publish={"auto": True})
        self.assertEqual(caught.exception.status, 400)


class GateTests(GeneralCase):
    def test_a_write_task_that_wrote_nothing_is_only_noted(self):
        worker = FakeWorker()
        worker.candidate = lambda body: {"sha": BASE, "parent": BASE, "changed": False, "files": 0, "insertions": 0,
                                         "deletions": 0, "rewritten": False, "secrets": []}
        rid = self.session([WRITE])
        self.run_engine(worker)
        self.assertEqual(self.gates(rid, "report")["diff_present"], "warn")
        self.assertEqual(self.service.run(self.c, rid)["tasks"][0]["status"], "accepted")

    def test_a_secret_in_a_report_still_blocks(self):
        worker = FakeWorker()
        worker.candidate = lambda body: {"sha": BASE, "parent": BASE, "changed": True, "files": 1, "insertions": 1,
                                         "deletions": 0, "rewritten": False, "secrets": [".env"]}
        rid = self.session([WRITE])
        self.run_engine(worker)
        self.assertEqual(self.gates(rid, "report")["no_secrets"], "fail")

    def test_no_checks_run_without_verify(self):
        worker = FakeWorker()
        self.session([RESEARCH, WRITE])
        self.run_engine(worker, ticks=16)
        self.assertNotIn("/verify/start", worker.paths())

    def test_only_a_session_that_changes_code_gets_an_evaluator(self):
        snapshot, config = {"execution_contract": 4}, {"capabilities": {"role_cards": True}}
        self.assertFalse(evaluation.needed([RESEARCH, WRITE], snapshot, config))
        self.assertTrue(evaluation.needed([RESEARCH, IMPLEMENT], snapshot, config))   # a mixed Session: as today


class WordingTests(GeneralCase):
    """D65 Task 2 (§5, §6a-1): general work is told it is a task in a working directory, with its own ending; the platform
    says so to the Worker (work: general), whose instructions for code are word for word as before."""
    def test_the_platform_says_general_work_to_the_worker(self):
        worker = FakeWorker()
        none = self.session([WRITE])
        code = self.session([IMPLEMENT], source={"type": "new"}, request="start-0000000002")
        self.run_engine(worker, ticks=6)
        sent = {body["run_id"]: body for body in worker.dispatched.values()}
        self.assertEqual(sent[none].get("work"), "general")
        self.assertNotIn("work", sent[code])

    def test_a_research_task_in_a_code_session_is_general_work_too(self):
        worker = FakeWorker()
        rid = self.session([RESEARCH, IMPLEMENT], source={"type": "new"})
        self.run_engine(worker, ticks=6)
        first = next(body for body in worker.dispatched.values() if body["run_id"] == rid)
        self.assertEqual((first["spec"]["id"], first.get("work")), ("research", "general"))

    def test_the_agent_reads_a_task_in_a_working_directory(self):
        from pathlib import Path
        from octopus.worker import envelope
        args = ("需求", None, {"kind": "write"}, "写报告", [], Path("/w/project"), Path("/w/handoff.md"))
        general = envelope(*args, work="general")
        self.assertIn("# 本次任务", general)
        self.assertNotIn("本次研发任务", general)
        self.assertIn("工作目录：/w/project", general)
        self.assertIn("写成工作目录中的文件", general)
        research = envelope("需求", None, {"kind": "research"}, "调研", [], Path("/w/project"), Path("/w/h.md"), work="general")
        self.assertIn("依据", research)

    def test_code_work_reads_as_before(self):
        from pathlib import Path
        from octopus.worker import envelope
        args = ("需求", None, {"kind": "implement"}, "实现", [], Path("/w/project"), Path("/w/handoff.md"))
        self.assertEqual(envelope(*args), envelope(*args, work=None))
        self.assertIn("# 本次研发任务", envelope(*args))
        self.assertIn("源码目录：/w/project", envelope(*args))


class PlanningTests(GeneralCase):
    """D65 §5: the planner is told when there is no repository, and how a general task's contract is written."""
    def test_a_session_without_a_repository_is_said_to_the_planner(self):
        from octopus import planning
        plain, general = planning.plan_step()["prompt"], planning.plan_step(general=True)["prompt"]
        self.assertNotIn("本会话没有代码仓库", plain)
        self.assertIn("本会话没有代码仓库", general)
        for prompt in (plain, general):
            self.assertIn("一般任务（research / write）：deliverables 写产出文件名", prompt)
            self.assertLessEqual(len(prompt), 7400)

    def test_an_agent_planned_session_without_a_repository_plans_as_one(self):
        rid = self.service.start(self.c, {"request_id": "start-0000000009", "input": "调研某主题并写一份报告",
                                          "source": {"type": "none"}, "team_id": "team-p", "plan": "agent"})["id"]
        with self.db.read() as db:
            spec = json.loads(db.execute("SELECT spec FROM tasks WHERE run_id=? AND id='plan'", (rid,)).fetchone()[0])
        self.assertIn("本会话没有代码仓库", spec["prompt"])


if __name__ == "__main__":
    unittest.main()
