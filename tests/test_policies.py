"""D55 M2-5: a person's or a Team's admission rules by repository — a trusted repository starts at once, the rest wait
for their plan's confirmation (the platform default is now confirm). Order: submitted → personal → Team → default."""
import json
import unittest

from octopus import policies
from octopus.domain import Service
from octopus.security import Error
from tests.test_access import AccessCase

APP = {"type": "git", "url": "https://github.com/o/app.git"}


class AdmissionTests(AccessCase):
    def rule(self, actor, repos, mode, scope="personal", team=None):
        body = {"kind": "admission", "scope": scope, "rule": {"repos": repos, "mode": mode}, **({"team_id": team} if team else {})}
        return self.service.create_policy(actor, body)

    def decide(self, actor, source, submitted=None, team=None, default="confirm"):
        with self.db.read() as db:
            return policies.admission(db, actor["id"], team, source, submitted, default)

    def test_a_repository_rule_lets_a_trusted_repository_start_at_once(self):
        made = self.rule(self.b, "github.com/o/*", "auto")
        decided = self.decide(self.b, APP)
        self.assertEqual((decided["mode"], decided["basis"], decided["policy_id"], decided["pattern"], decided["scope"]),
                         ("auto", "rule", made["id"], "github.com/o/*", "personal"))
        self.assertEqual(self.decide(self.b, {"type": "git", "url": "git@github.com:other/app"}),
                         {"mode": "confirm", "basis": "default"})
        self.assertEqual(self.decide(self.e, APP)["basis"], "default")   # someone else's rule is not yours

    def test_every_form_of_the_address_matches(self):
        self.rule(self.b, "github.com/o/r", "auto")
        for url in ("https://github.com/o/r.git", "git@github.com:o/r", "https://GitHub.com/O/R", "ssh://git@github.com/o/r.git",
                    "https://github.com/o/r/"):
            self.assertEqual(policies.repo_of({"type": "git", "url": url}), "github.com/o/r", url)
            self.assertEqual(self.decide(self.b, {"type": "git", "url": url})["basis"], "rule", url)
        self.assertEqual(self.decide(self.b, {"type": "git", "url": "https://github.com/o/r2"})["basis"], "default")

    def test_a_new_project_matches_no_rule(self):
        self.rule(self.b, "*", "auto")
        self.assertIsNone(policies.repo_of({"type": "new"}))
        self.assertEqual(self.decide(self.b, {"type": "new"}), {"mode": "confirm", "basis": "default"})

    def test_order_submitted_personal_team_default(self):
        tid = self.team()
        self.rule(self.a, "github.com/o/*", "auto", "team", tid)
        self.assertEqual(self.decide(self.b, APP, team=tid)["scope"], "team")
        self.assertEqual(self.decide(self.b, APP)["basis"], "default")   # a Team's rule is for its Sessions only
        mine = self.rule(self.b, "github.com/o/app", "confirm")
        decided = self.decide(self.b, APP, team=tid)
        self.assertEqual((decided["mode"], decided["policy_id"]), ("confirm", mine["id"]))
        self.assertEqual(self.decide(self.b, APP, submitted="auto", team=tid), {"mode": "auto", "basis": "submitted"})

    def test_the_most_specific_rule_wins(self):
        self.rule(self.b, "*", "confirm")
        exact = self.rule(self.b, "github.com/o/app", "auto")
        self.rule(self.b, "github.com/o/*", "confirm")
        self.assertEqual(self.decide(self.b, APP)["policy_id"], exact["id"])

    def test_a_bad_rule_is_refused(self):
        for rule in ({"repos": "github.com/o/*"}, {"repos": "github.com/o/*", "mode": "later"}, {"repos": "", "mode": "auto"},
                     {"repos": "https://github.com/o/*", "mode": "auto"}, {"repos": "a b", "mode": "auto"},
                     {"repos": "github.com/o/*", "mode": "auto", "extra": 1}):
            with self.assertRaises(Error) as caught:
                self.service.create_policy(self.b, {"kind": "admission", "scope": "personal", "rule": rule})
            self.assertEqual(caught.exception.status, 400, rule)
        with self.assertRaises(Error):
            self.service.create_policy(self.b, {"kind": "budget", "scope": "personal", "rule": {}})
        with self.assertRaises(Error):
            self.service.create_policy(self.b, {"kind": "admission", "scope": "team", "rule": {"repos": "*", "mode": "auto"}})

    def test_only_the_team_owner_writes_team_rules(self):
        tid = self.team()
        with self.assertRaises(Error) as caught:
            self.rule(self.b, "github.com/o/*", "auto", "team", tid)
        self.assertEqual(caught.exception.status, 403)
        made = self.rule(self.a, "github.com/o/*", "auto", "team", tid)
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
            self.rule(self.a, "github.com/o/*", "auto", "team", tid)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "team_archived"))
        mine = self.rule(self.b, "github.com/o/*", "auto")
        with self.assertRaises(Error) as caught:
            self.service.delete_policy(self.e, mine["id"])
        self.assertEqual(caught.exception.status, 404)
        with self.db.read() as db:
            actions = [r["action"] for r in db.execute("SELECT action FROM audit WHERE kind='policy' ORDER BY id")]
        self.assertEqual(actions, ["policy.create", "policy.delete", "policy.create"])

    def test_the_session_says_why_it_waits_or_starts(self):
        self.service = Service(self.db, default_admission="confirm")
        made = self.rule(self.b, "github.com/o/*", "auto")
        run = self.service.run(self.b, self.start(self.b, source=APP)["id"])
        self.assertEqual(run["admission"], "auto")
        self.assertEqual(run["admission_basis"]["basis"], "rule")
        request = self.service.request(self.b, run["request"]["id"])
        self.assertEqual(request["policy"]["admission"]["policy_id"], made["id"])
        other = self.service.run(self.b, self.start(self.b, "start-0000000002")["id"])
        self.assertEqual((other["admission"], other["admission_basis"]), ("confirm", {"mode": "confirm", "basis": "default"}))


class PolicyRouteTests(AccessCase):
    def test_the_routes(self):
        with self.http() as call:
            body = {"kind": "admission", "scope": "personal", "rule": {"repos": "github.com/o/*", "mode": "auto"}}
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
