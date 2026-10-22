"""D55: a person's or a Team's policies — mapping preferences for the planner. Admission rules by repository are gone
(D70, owner 2026-10-22: every Session starts once planned): new ones are refused, old ones have no effect."""
import json
import unittest

from octopus import planning, policies
from octopus.domain import mapping
from octopus.domain import Service
from octopus.security import Error
from tests.test_access import AccessCase

APP = {"type": "git", "url": "https://github.com/o/app.git"}


class AdmissionTests(AccessCase):
    def test_an_admission_rule_is_refused(self):   # D70: no plan waits for confirmation, so there is nothing to decide
        for scope, actor, extra in (("personal", self.b, {}), ("team", self.a, {"team_id": self.team()})):
            with self.assertRaises(Error) as caught:
                self.service.create_policy(actor, {"kind": "admission", "scope": scope,
                                                   "rule": {"repos": "github.com/o/*", "mode": "auto"}, **extra})
            self.assertEqual((caught.exception.status, caught.exception.code), (400, "admission_removed"))
        with self.assertRaises(Error):
            self.service.create_policy(self.b, {"kind": "budget", "scope": "personal", "rule": {}})

    def test_every_form_of_the_address_is_one_repository(self):   # still how listing and requests name a repository
        for url in ("https://github.com/o/r.git", "https://github.com/O/R", "git@github.com:o/r.git", "ssh://git@github.com/o/r"):
            self.assertEqual(policies.repo_of({"type": "git", "url": url}), "github.com/o/r", url)
        self.assertIsNone(policies.repo_of({"type": "new"}))

    def test_only_the_team_owner_writes_team_rules(self):
        tid = self.team()
        rule = lambda actor, scope="team": self.service.create_policy(
            actor, {"kind": "mapping", "scope": scope, "rule": {"role": "evaluator", "tool": "codex"},
                    **({"team_id": tid} if scope == "team" else {})})
        with self.assertRaises(Error) as caught:
            rule(self.b)
        self.assertEqual(caught.exception.status, 403)
        made = rule(self.a)
        self.assertEqual([p["id"] for p in self.service.policies(self.b, tid)], [made["id"]])
        with self.assertRaises(Error) as caught:
            self.service.policies(self.e, tid)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.service.policies(self.e), [])
        with self.assertRaises(Error) as caught:
            self.service.delete_policy(self.b, made["id"])
        self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(Error) as caught:
            self.service.delete_policy(self.e, made["id"])
        self.assertEqual(caught.exception.status, 404)
        self.service.delete_policy(self.a, made["id"])
        with self.db.transaction() as db:
            db.execute("UPDATE teams SET archived=1 WHERE id=?", (tid,))
        with self.assertRaises(Error) as caught:
            rule(self.a)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "team_archived"))
        mine = rule(self.b, "personal")
        with self.assertRaises(Error) as caught:
            self.service.delete_policy(self.e, mine["id"])
        self.assertEqual(caught.exception.status, 404)
        with self.db.read() as db:
            actions = [r["action"] for r in db.execute("SELECT action FROM audit WHERE kind='policy' ORDER BY id")]
        self.assertEqual(actions, ["policy.create", "policy.delete", "policy.create"])

    def test_a_rule_written_before_stays_deletable_and_has_no_effect(self):
        with self.db.transaction() as db:
            db.execute("INSERT INTO policies(id,kind,owner_id,team_id,rule,created_by,created) VALUES('old','admission',?,NULL,?,?,1)",
                       (self.b["id"], json.dumps({"repos": "*", "mode": "confirm"}), self.b["id"]))
        run = self.service.run(self.b, self.start(self.b, source=APP)["id"])
        self.assertEqual((run["admission"], run["admission_basis"]), ("auto", {"mode": "auto", "basis": "default"}))
        self.assertEqual([p["id"] for p in self.service.policies(self.b)], ["old"])
        self.service.delete_policy(self.b, "old")


class PreferenceTests(AccessCase):
    def prefer(self, actor, scope="personal", team=None, **rule):
        return self.service.create_policy(actor, {"kind": "mapping", "scope": scope, "rule": rule, **({"team_id": team} if team else {})})

    def test_preferences_reach_the_planner(self):
        self.prefer(self.b, role="evaluator", tool="codex")
        rid = self.service.start(self.b, {"request_id": "prefer-00000001", "input": "实现登录页并验证", "source": {"type": "new"},
                                          "plan": "agent"})["id"]
        run = self.service.run(self.b, rid)
        prompt = run["tasks"][0]["spec"]["prompt"]
        self.assertIn("用户的映射偏好", prompt)
        self.assertIn("- 个人：role evaluator → codex", prompt)
        with self.db.read() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
        self.assertEqual([(p["scope"], p["role"], p["tool"]) for p in snapshot["preferences"]], [("personal", "evaluator", "codex")])
        plain = self.service.start(self.e, {"request_id": "prefer-00000002", "input": "实现登录页并验证", "source": {"type": "new"},
                                            "plan": "agent"})["id"]
        self.assertNotIn("用户的映射偏好", self.service.run(self.e, plain)["tasks"][0]["spec"]["prompt"])

    def test_personal_overrides_team_on_the_same_role(self):
        tid = self.team()
        self.prefer(self.a, "team", tid, kind="review", tool="kiro")
        self.prefer(self.a, "team", tid, kind="test", tool="claude")
        mine = self.prefer(self.b, kind="review", tool="codex", model="gpt-5")
        with self.db.read() as db:
            found = policies.preferences(db, self.b["id"], tid)
            alone = policies.preferences(db, self.b["id"], None)
        self.assertEqual([(p["scope"], p["kind"], p["tool"]) for p in found], [("personal", "review", "codex"), ("team", "test", "claude")])
        self.assertEqual((found[0]["id"], found[0]["model"]), (mine["id"], "gpt-5"))
        self.assertEqual([p["id"] for p in alone], [mine["id"]])   # a Team's preferences are for its Sessions

    def test_a_planned_task_that_follows_a_preference_says_so(self):
        snapshot = {"planning": {"mode": "agent", "tasks": ["t1"]}, "replan": {"task": "replan-1", "tasks": ["t2"]},
                    "preferences": [{"scope": "personal", "kind": "review", "tool": "codex"},
                                    {"scope": "team", "role": "evaluator", "tool": "kiro"}]}
        task = lambda tid, **spec: {"id": tid, "spec": {"tool": "claude", **spec}}
        self.assertEqual(mapping(snapshot, task("t1", kind="review", tool="codex")),
                         {"source": "planner", "label": "KiroCrew 规划（按个人偏好）"})
        self.assertEqual(mapping(snapshot, task("t1", kind="review", tool="kiro"))["label"], "KiroCrew 规划")
        self.assertEqual(mapping(snapshot, task("t1", kind="review", role="evaluator", tool="kiro"))["label"],
                         "KiroCrew 规划（按 Team 偏好）")   # the role's preference comes first
        self.assertEqual(mapping(snapshot, task("t1", kind="review", role="evaluator", tool="codex"))["label"], "KiroCrew 规划")
        self.assertEqual(mapping(snapshot, task("t2", kind="review", tool="codex"))["label"], "KiroCrew 重新规划（按个人偏好）")
        self.assertEqual(mapping({**snapshot, "preferences": None}, task("t1", kind="review", tool="codex"))["label"], "KiroCrew 规划")
        requested = task("t1", kind="review", tool="codex", requested={"tool": "codex", "quote": "审查用 codex"})
        self.assertEqual(mapping(snapshot, requested)["source"], "requested")   # what the requirement named comes first

    def test_a_bad_preference_is_refused(self):
        for rule in ({"tool": "codex"}, {"kind": "review", "role": "evaluator", "tool": "codex"}, {"kind": "review", "tool": "gemini"},
                     {"kind": "review"}, {"kind": "plan", "tool": "codex"}, {"role": "boss", "tool": "codex"},
                     {"kind": "review", "tool": "codex", "model": "m" * 201}, {"kind": "review", "tool": "codex", "model": ""},
                     {"kind": "review", "tool": "codex", "extra": 1}):
            with self.assertRaises(Error) as caught:
                self.service.create_policy(self.b, {"kind": "mapping", "scope": "personal", "rule": rule})
            self.assertEqual(caught.exception.status, 400, rule)

    def test_the_section_is_clipped_and_states_its_rules(self):
        self.assertEqual(planning.preference_section([], 600), "")
        section = planning.preference_section([{"id": "policy-x", "scope": "personal", "kind": "review", "tool": "codex"}] * 40, 300)
        self.assertIn("## 用户的映射偏好（个人 / Team）", section)
        for rule in ("交接", "requested", "available 为 false"):
            self.assertIn(rule, section)
        self.assertLess(len(section), 300 + 200)
        self.assertNotIn("policy-x", section)   # the planner needs what is preferred, not the rule's id


class PolicyRouteTests(AccessCase):
    def test_the_routes(self):
        with self.http() as call:
            body = {"kind": "mapping", "scope": "personal", "rule": {"role": "evaluator", "tool": "codex"}}
            self.assertEqual(call("POST", "/v1/policies", self.tokens["bob"],
                                  {"kind": "admission", "scope": "personal", "rule": {"repos": "*", "mode": "auto"}})[0], 400)
            status, made = call("POST", "/v1/policies", self.tokens["bob"], body)
            self.assertEqual(status, 201)
            self.assertEqual(call("GET", "/v1/policies", self.tokens["bob"])[1][0]["id"], made["id"])
            self.assertEqual(call("GET", "/v1/policies", self.tokens["eve"]), (200, []))
            self.assertEqual(call("POST", "/v1/policies", self.token("bob", ["read"]), body)[0], 403)
            self.assertEqual(call("DELETE", "/v1/policies/" + made["id"], self.tokens["eve"])[0], 404)
            self.assertEqual(call("DELETE", "/v1/policies/" + made["id"], self.tokens["bob"])[0], 200)
            self.assertEqual(call("GET", "/v1/policies", self.tokens["bob"]), (200, []))


if __name__ == "__main__":
    unittest.main()
