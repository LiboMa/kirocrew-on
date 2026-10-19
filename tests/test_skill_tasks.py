"""D50: a task's contract names the skills it works by; the planner is told the pack; the Worker puts each chosen skill
in the Attempt's folder and lists it; the evidence records what was given; an older Worker gets none."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from octopus import gateway_client as crew_api, planning, skills
from octopus.db import dumps
from octopus.domain import steps
from octopus.security import Error
from octopus.workflow_import import normalize
from octopus.worker import Worker
from tests.test_confirmation import ConfirmationCase

RID = "run-" + "1" * 32
FIRST, SECOND = "attempt-" + "a" * 32, "attempt-" + "b" * 32
SPEC = {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现加法并运行真实测试"}
STEP = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["完成"],
        "verify": ["true"]}


class ContractTests(unittest.TestCase):
    def test_a_task_may_name_up_to_four_skills_of_the_pack(self):
        item = steps([{**STEP, "skills": ["test-driven-development", "frontend-design", "test-driven-development"]}])[0]
        self.assertEqual(item["skills"], ["test-driven-development", "frontend-design"])
        for bad in (["make-it-pretty"], list(skills.CATALOG), "frontend-design", [3]):
            with self.subTest(bad=bad), self.assertRaises(Error):
                steps([{**STEP, "skills": bad}])
        self.assertNotIn("skills", steps([{**STEP, "skills": []}])[0])

    def test_the_planner_is_told_the_skills(self):
        text = planning.rules(4, "")
        for name in skills.CATALOG:
            self.assertIn(name, text)
        self.assertIn("skills", json.dumps(planning.EXAMPLE))

    def test_the_api_describes_the_field(self):
        from octopus.schema import openapi
        field = openapi()["components"]["schemas"]["Step"]["properties"]["skills"]
        self.assertEqual((field["items"]["enum"], field["maxItems"]), (list(skills.CATALOG), skills.LIMIT))

    def test_an_unknown_planned_skill_is_dropped_with_a_note(self):
        notes = []
        planned = normalize([{**STEP, "skills": ["frontend-design", "make-it-pretty"]}], notes, planned=True)
        self.assertEqual(planned[0]["skills"], ["frontend-design"])
        self.assertTrue(any("make-it-pretty" in note for note in notes))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node = Worker(Path(self.tmp.name) / "worker", "x" * 40, crew_home=Path(self.tmp.name) / "crew")
        ids = iter(["aaaa1111", "cccc3333"])

        def native(path, body=None, method=None, timeout=None, headers=None):
            if path.startswith("/api/chat/slots/octopus-") and body is None:
                raise crew_api.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is not None:
                return {"id": next(ids)}
            if path.endswith("/continue"):
                return {"id": "bbbb2222", "conversation": "aaaa1111", "status": "spawned"}
            return {"ok": True}
        self.node.native = native
        self.node.prepare({"run_id": RID, "source": {"type": "new"}})

    def dispatch(self, attempt, chosen=("test-driven-development", "verification-before-completion"), **extra):
        self.node.dispatch({"run_id": RID, "attempt_id": attempt, "input": "需求", "spec": SPEC, "upstream": [],
                            "contract": {"kind": "implement", "skills": list(chosen)}, **extra})
        return self.node.path(RID) / "attempts" / attempt

    def test_the_worker_copies_the_skills_and_lists_them(self):
        folder = self.dispatch(FIRST)
        for name in ("test-driven-development", "verification-before-completion"):
            self.assertEqual((folder / "skills" / (name + ".md")).read_bytes(), skills.path(name).read_bytes())
        text = (folder / "instructions.md").read_text()
        self.assertIn("## 方法", text)
        self.assertIn(str(folder / "skills/test-driven-development.md"), text)
        self.assertNotIn("frontend-design", text)

    def test_the_evidence_records_name_and_hash(self):
        folder = self.dispatch(FIRST)
        record = json.loads((folder / "dispatch.json").read_text())
        self.assertEqual(record["skills"], [{"name": n, "sha256": hashlib.sha256(skills.path(n).read_bytes()).hexdigest()}
                                            for n in ("test-driven-development", "verification-before-completion")])

    def test_each_attempt_has_its_own_copy(self):
        first, second = self.dispatch(FIRST), self.dispatch(SECOND, ("frontend-design",))
        self.assertTrue((first / "skills/test-driven-development.md").is_file())
        self.assertEqual(sorted(p.name for p in (second / "skills").iterdir()), ["frontend-design.md"])

    def test_the_attempt_evidence_says_what_it_was_given(self):
        self.dispatch(FIRST, ("frontend-design",))
        parent, base = self.node.binding(RID)[1]["parent"], self.node.native
        self.node.native = lambda path, body=None, **kw: ({"agents": [{"id": "aaaa1111", "parent": parent, "done": False}]}
                                                          if path == "/api/spawn" and body is None else base(path, body, **kw))
        evidence = self.node.inspect({"run_id": RID, "attempt_id": FIRST})["evidence"]
        self.assertEqual(evidence["skills"], [{"name": "frontend-design", "sha256": skills.sha256("frontend-design")}])

    def test_a_continued_rework_has_its_skills(self):
        self.dispatch(FIRST)
        folder = self.dispatch(SECOND, ("systematic-debugging",), **{"continue": {"conversation": "aaaa1111", "from": FIRST}})
        self.assertTrue((folder / "skills/systematic-debugging.md").is_file())

    def test_without_skills_nothing_is_added(self):
        folder = self.dispatch(FIRST, ())
        self.assertFalse((folder / "skills").exists())
        self.assertNotIn("## 方法", (folder / "instructions.md").read_text())

    def test_the_worker_says_which_skills_it_has(self):
        from unittest.mock import patch
        with patch.object(Worker, "tools", lambda self: {}), patch.object(Worker, "tool_auth", lambda self: "ok"):
            self.assertEqual(self.node.health()["skills"], list(skills.CATALOG))


class EngineTests(ConfirmationCase):
    def caps(self, capabilities):
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": capabilities}),))

    def contracts(self, rid):
        return [b.get("contract") or {} for b in self.bodies.values() if b["run_id"] == rid]

    def test_an_older_worker_gets_no_skills(self):
        self.caps({})
        rid = self.start(steps=[{**STEP, "skills": ["test-driven-development"]}])
        self.assertEqual(self.tick(rid)["status"], "completed")
        self.assertTrue(all("skills" not in c for c in self.contracts(rid)))

    def test_a_worker_with_the_pack_gets_them(self):
        self.caps({"skills": list(skills.CATALOG)})
        rid = self.start(steps=[{**STEP, "skills": ["test-driven-development"]}])
        self.tick(rid)
        self.assertEqual(self.contracts(rid)[0]["skills"], ["test-driven-development"])


if __name__ == "__main__":
    unittest.main()
