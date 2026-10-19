"""D46 P2: a task's role chooses the card its Agent works by (inlined at the top of its instructions); kind keeps deciding
the gates. Cards are KiroCrew-style SKILL.md files (owner 2026-10-18: a KiroCrew skill cannot be chosen per spawn)."""
import re
import unittest
from pathlib import Path

from octopus import revisions, roles
from octopus.domain import steps
from octopus.security import Error
from octopus.worker import envelope

ROOT = Path(__file__).resolve().parents[1]


def step(**extra):
    return {"id": extra.pop("id", "task-1"), "name": extra.pop("name", "任务"), "tool": extra.pop("tool", "claude"),
            "prompt": "做", **extra}


class CardTests(unittest.TestCase):
    def test_every_role_has_a_kirocrew_style_card(self):
        self.assertEqual(set(roles.KINDS), {"spec", "sde", "integration_test", "security_test", "challenge", "evaluator",
                                            "knowledge"})
        for role in roles.KINDS:
            with self.subTest(role):
                text = (ROOT / "octopus/roles" / role / "SKILL.md").read_text()
                front = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
                fields = dict(line.split(": ", 1) for line in front.splitlines())
                self.assertEqual(set(fields), {"name", "description"})
                self.assertEqual(fields["name"], role)
                self.assertTrue(0 < len(roles.card(role)) <= 600)

    def test_the_catalogue_names_every_role_and_its_kind(self):
        text = roles.catalogue()
        for role, kinds in roles.KINDS.items():
            if role in roles.PLATFORM:   # D46 P4: added by the platform, never planned
                self.assertNotIn(f"{role}（", text)
                continue
            self.assertIn(f"{role}（{roles.LABELS[role]}，kind {'/'.join(kinds)}）", text)


class RoleFieldTests(unittest.TestCase):
    def test_a_role_is_kept_and_infers_its_kind(self):
        self.assertEqual(steps([step(role="security_test", tool="codex")])[0]["kind"], "review")
        self.assertEqual(steps([step(role="sde", kind="deliver")])[0]["role"], "sde")

    def test_an_unknown_role_or_an_incompatible_kind_is_refused(self):
        for bad in (step(role="tester"), step(role="sde", kind="review"), step(role="spec", kind="implement")):
            with self.subTest(bad), self.assertRaises(Error):
                steps([bad])

    def test_a_submitted_reviewer_on_the_authors_tool_is_refused(self):
        plan = [step(id="code", role="sde", tool="claude"), step(id="check", role="evaluator", tool="claude")]
        with self.assertRaises(Error) as caught:
            steps(plan)
        self.assertIn("审查不独立", str(caught.exception))
        steps([plan[0], {**plan[1], "tool": "codex"}])   # another tool is fine
        self.assertEqual(len(roles.unindependent(steps(plan, planned=True))), 1)   # a planned one is noted, not refused

    def test_without_a_role_nothing_changes(self):
        self.assertNotIn("role", steps([step(kind="review")])[0])

    def test_a_continue_that_changes_the_role_is_checked(self):
        from octopus import domain
        self.assertIn("role", domain.CONTRACT)   # the override's allowed fields are {..., *CONTRACT}; it is checked by steps()
        with self.assertRaises(Error):
            steps([step(kind="implement", role="evaluator")])

    def test_the_legacy_runtime_drops_the_role(self):
        import workflow
        flow = workflow.validate({"schema": 1, "id": "flow", "name": "流程",
                                  "steps": [step(id="code", role="sde", kind="implement")]})
        self.assertNotIn("role", flow["steps"][0])

    def test_a_role_change_shows_in_the_revisions(self):
        self.assertIn("role", revisions.CONTRACT)
        self.assertEqual(revisions.LABELS["role"], "角色")


class InstructionsTests(unittest.TestCase):
    def render(self, contract):
        return envelope("需求", None, contract, "任务说明", [], "/w", "/w/handoff.md")

    def test_a_task_with_a_role_starts_with_its_card(self):
        text = self.render({"kind": "review", "role": "security_test"})
        self.assertTrue(text.startswith("# 你的角色：安全测试\n" + roles.card("security_test")))
        self.assertIn("角色：安全测试", text.split("## 本任务契约", 1)[1])

    def test_a_task_without_a_role_is_unchanged(self):
        self.assertTrue(self.render({"kind": "review"}).startswith("# 本次研发任务"))


class PlannerTests(unittest.TestCase):
    def test_the_planner_is_told_the_roles_and_independence(self):
        from octopus import planning
        for prompt in (planning.plan_step()["prompt"],
                       planning.replan_step("replan-1", [], "失败", {"reason": "r", "instructions": "i"})["prompt"]):
            self.assertIn("security_test（安全测试，kind review）", prompt)
            self.assertIn("不得使用被审查改动的作者工具", prompt)
            self.assertIn('"role"', prompt)


PLANNED = """```json
{"intent": "实现并审查", "summary": "需求", "name": "计划", "steps": [
 {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "role": "sde", "acceptance": ["完成"], "verify": ["true"]},
 {"id": "check", "name": "评估", "tool": "claude", "prompt": "评估", "kind": "review", "role": "evaluator", "acceptance": ["完成"]}]}
```"""

from tests.test_confirmation import ConfirmationCase   # noqa: E402
from tests.test_replan import ReplanCase   # noqa: E402

REPLANNED = """```json
{"intent": "补上评估", "summary": "剩余工作", "name": "剩余工作", "steps": [
 {"id": "check", "name": "复评", "tool": "claude", "prompt": "复评", "kind": "review", "role": "evaluator", "acceptance": ["无阻断问题"]}]}
```"""


class PlannedRoleTests(ConfirmationCase):
    def test_a_planned_reviewer_on_the_authors_tool_is_applied_with_a_note(self):
        import tests.test_confirmation as confirmation
        original, confirmation.PLAN = confirmation.PLAN, PLANNED
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertEqual([t["spec"].get("role") for t in run["tasks"] if t["id"] != "plan"], ["sde", "evaluator"])
        self.assertTrue(any("审查不独立" in note and "评估" in note for note in run["planning"]["notes"]))


class ReplannedRoleTests(ReplanCase):
    def test_a_replan_notes_a_reviewer_on_the_authors_tool(self):
        self.plan = REPLANNED   # the kept task "code" (claude, implement) wrote the change
        run = self.tick(self.start(), 40)
        self.assertTrue(any("审查不独立" in note and "复评" in note for note in run["replan"]["notes"]), run["replan"])


class VisibleTests(unittest.TestCase):
    def test_the_console_has_a_title_for_every_role(self):
        text = (ROOT / "web/src/lib/labels.ts").read_text()
        block = re.search(r"ROLE_LABELS[^=]*=\s*\{(.*?)\}", text, re.S).group(1)
        self.assertEqual(dict(re.findall(r'(\w+):\s*"([^"]+)"', block)), roles.LABELS)

    def test_the_compact_status_and_the_report_carry_the_role(self):
        from octopus import mcp, report
        task = {"id": "check", "status": "accepted", "spec": {"name": "安全审查", "tool": "codex", "kind": "review",
                                                              "role": "security_test"}, "attempts": []}
        view = mcp.compact_status({"id": "run-1", "status": "completed", "tasks": [task]}, [])["session"]
        self.assertEqual(view["tasks"][0]["role"], "security_test")
        self.assertEqual(report.model({"id": "run-1", "tasks": [task]})["tasks"][0]["kind"], "review（安全测试）")


TEAM = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "role": "sde",
         "acceptance": ["完成"], "verify": ["true"]},
        {"id": "check", "name": "评估", "tool": "codex", "prompt": "评估", "kind": "review", "role": "evaluator", "acceptance": ["完成"]}]


class FinalReviewTests(ConfirmationCase):
    def contracts(self):
        return [b.get("contract") or {} for b in self.bodies.values() if not b.get("coordination")]

    def test_a_worker_that_does_not_know_roles_gets_no_role(self):
        rid = self.start(steps=TEAM, recovery={"enabled": False, "max_cycles": 2})
        self.tick(rid)
        self.assertTrue(self.contracts())
        self.assertTrue(all("role" not in c for c in self.contracts()))   # an older Worker refuses a contract field it lacks

    def test_a_worker_that_knows_roles_gets_them(self):
        from octopus.db import dumps
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='worker'",
                       (dumps({"url": "fixture", "token": "fixture", "capabilities": {"role_cards": True}}),))
        rid = self.start(steps=TEAM, recovery={"enabled": False, "max_cycles": 2})
        self.tick(rid)
        self.assertIn("sde", [c.get("role") for c in self.contracts()])

    def test_the_worker_says_it_knows_roles(self):
        import inspect
        from octopus.worker import Worker
        self.assertIn('"role_cards": True', inspect.getsource(Worker.health))

    def test_an_invalid_planned_role_is_dropped_with_a_note(self):
        import tests.test_confirmation as confirmation
        plan = PLANNED.replace('"role": "sde"', '"role": ""').replace('"role": "evaluator"', '"role": "integration_test"')
        original, confirmation.PLAN = confirmation.PLAN, plan
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        tasks = [t for t in run["tasks"] if t["id"] != "plan"]
        self.assertEqual([t["spec"].get("role") for t in tasks], [None, None])
        self.assertEqual([t["spec"]["kind"] for t in tasks], ["implement", "review"])
        self.assertEqual(sum("角色" in note and "已忽略" in note for note in run["planning"]["notes"]), 2)

    def test_a_continue_that_puts_the_reviewer_on_the_authors_tool_is_refused(self):
        rid = self.start(steps=TEAM, recovery={"enabled": False, "max_cycles": 2})
        run = self.tick(rid)
        with self.assertRaises(Error) as caught:
            self.service.operate(self.actor, rid, "continue", {"request_id": "roles-continue-1", "expected_version": run["version"],
                                                               "task_id": "check", "execution": {"tool": "claude"}})
        self.assertIn("审查不独立", str(caught.exception))
        self.service.operate(self.actor, rid, "continue", {"request_id": "roles-continue-2", "expected_version": run["version"],
                                                           "task_id": "check", "execution": {"prompt": "再评估"}})


class SchemaAndConsoleTests(unittest.TestCase):
    def test_the_openapi_step_accepts_a_role(self):
        from octopus.schema import openapi
        step_schema = openapi()["components"]["schemas"]["Step"]["properties"]
        self.assertEqual(set(step_schema["role"]["enum"]), set(roles.KINDS))

    def test_the_revision_diff_names_the_role(self):
        text = (ROOT / "web/src/features/sessions/RevisionsPanel.tsx").read_text()
        block = re.search(r"const FIELD[^=]*=\s*\{(.*?)\};", text, re.S).group(1)
        self.assertEqual(dict(re.findall(r'(\w+):\s*"([^"]+)"', block)).get("role"), "角色")


if __name__ == "__main__":
    unittest.main()
