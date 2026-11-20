"""D55 M2-1: every submission is a Request — who sent it from which client, what they chose explicitly and the policy it
started under — and its Session names it. One Request, one Session for now; a Session from before has none."""
import json
import time
import unittest

from octopus import requests
from octopus.db import dumps
from octopus.security import Error, authenticate, issue_token
from tests.test_access import AccessCase
from tests.test_confirmation import ConfirmationCase

ONE = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "deliver", "acceptance": ["完成"]}]


class RequestTests(ConfirmationCase):
    def test_a_submission_is_a_request_its_session_names(self):
        rid = self.service.start(self.actor, {"request_id": "request-fixture-1", "input": "实现登录页",
                                              "source": {"type": "new"}, "worker_id": "worker", "steps": ONE})["id"]
        run = self.service.run(self.actor, rid)
        self.assertTrue(run["request"]["id"].startswith("request-"))
        found = self.service.request(self.actor, run["request"]["id"])
        self.assertEqual(found["input"], "实现登录页")
        self.assertEqual([s["id"] for s in found["sessions"]], [rid])
        self.assertEqual(found["status"], run["status"])
        self.assertEqual(found["owner"]["id"], self.actor["id"])

    def test_the_same_request_id_is_one_request(self):
        body = {"request_id": "request-fixture-2", "input": "实现登录页", "source": {"type": "new"},
                "worker_id": "worker", "steps": ONE}
        first = self.service.start(self.actor, dict(body))
        second = self.service.start(self.actor, dict(body))
        self.assertEqual(first["id"], second["id"])
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 1)

    def test_the_client_is_the_tokens(self):
        self.assertEqual(requests.client_of("Web login", "login"), "console")   # by how the token was issued
        self.assertEqual(requests.client_of("Web login"), "api")   # never by its label (peer review 2026-10-27)
        self.assertEqual(requests.client_of("codex MCP"), "codex")
        self.assertEqual(requests.client_of("claude MCP admin"), "claude")
        self.assertEqual(requests.client_of("kiro-ide MCP"), "kiro-ide")
        self.assertEqual(requests.client_of("CI"), "api")
        self.assertEqual(requests.client_of(None), "api")
        self.assertEqual(self.actor["token_label"], "API")

    def test_explicit_choices_and_the_policy_are_kept(self):
        rid = self.start(steps=ONE, admission="confirm")   # D70: an older client's confirm is not used
        found = self.service.request(self.actor, self.service.run(self.actor, rid)["request"]["id"])
        self.assertEqual(found["explicit"], {"steps": 1})
        self.assertEqual(found["policy"]["admission"], {"mode": "auto", "basis": "default"})
        self.assertEqual(found["client"], "api")
        self.assertEqual(found["source"], {"type": "new"})
        rid = self.start(steps=ONE)
        found = self.service.request(self.actor, self.service.run(self.actor, rid)["request"]["id"])
        self.assertEqual(found["policy"]["admission"], {"mode": "auto", "basis": "default"})

    def test_someone_who_cannot_read_its_sessions_cannot_read_it(self):
        rid = self.start(steps=ONE)
        request = self.service.run(self.actor, rid)["request"]["id"]
        with self.assertRaises(Error) as caught:
            self.service.request(self.other, request)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.service.requests(self.other), [])
        self.assertEqual([r["id"] for r in self.service.requests(self.actor)], [request])

    def test_an_administrator_reading_someone_elses_request_is_audited(self):   # final review: as for Sessions (A2)
        rid = self.service.start(self.other, {"request_id": "request-fixture-9", "input": "实现登录页", "source": {"type": "new"},
                                              "steps": ONE})["id"]
        request = self.service.run(self.other, rid)["request"]["id"]
        self.service.request(self.other, request)
        self.service.request(self.actor, request)
        self.service.request(self.actor, request)
        with self.db.read() as db:
            rows = db.execute("SELECT actor_id,action,target FROM audit WHERE kind='request'").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(self.actor["id"], "request.view", request)])

    def test_requests_are_filtered_like_sessions(self):
        self.start(steps=ONE, input="实现登录页")
        self.start(steps=ONE, input="修复导出")
        self.assertEqual([r["input"] for r in self.service.requests(self.actor, q="导出")], ["修复导出"])
        self.assertEqual(len(self.service.requests(self.actor, limit=1)), 1)
        self.assertEqual(self.service.requests(self.actor, client="codex"), [])
        for bad in ({"client": "x"}, {"limit": 0}, {"q": ""}, {"status": "nope"}):
            with self.assertRaises(Error):
                self.service.requests(self.actor, **bad)

    def test_an_old_session_has_no_request(self):
        now = time.time()
        with self.db.transaction() as db:
            db.execute("INSERT INTO runs(id,owner_id,team_id,worker_id,workflow_id,snapshot,input,source,status,created,updated) "
                       "VALUES('run-old',?,NULL,'worker',NULL,?,?,?,?,?,?)",
                       (self.actor["id"], dumps({"name": "旧"}), "旧需求", dumps({"type": "new"}), "completed", now, now))
        self.assertIsNone(self.service.run(self.actor, "run-old")["request"])
        self.assertNotIn("request_id", self.service.run(self.actor, "run-old"))


class RequestRouteTests(AccessCase):
    def test_the_routes_show_the_client_and_hide_others_requests(self):
        with self.db.transaction() as db:
            raw = issue_token(db, self.b["id"], label="codex MCP")["token"]
            codex = authenticate(db, raw)
        rid = self.start(codex)["id"]
        with self.http() as call:
            status, listed = call("GET", "/v1/requests?client=codex&limit=5", raw)
            self.assertEqual(status, 200)
            self.assertEqual([r["sessions"][0]["id"] for r in listed], [rid])
            self.assertEqual(listed[0]["client"], "codex")
            status, one = call("GET", "/v1/requests/" + listed[0]["id"], raw)
            self.assertEqual((status, one["id"]), (200, listed[0]["id"]))
            self.assertEqual(call("GET", "/v1/requests/" + listed[0]["id"], self.tokens["eve"])[0], 404)
            self.assertEqual(call("GET", "/v1/requests", self.tokens["eve"]), (200, []))
            self.assertEqual(call("GET", "/v1/requests?limit=x", raw)[0], 400)
            self.assertEqual(call("POST", "/v1/requests", raw, {})[0], 405)


if __name__ == "__main__":
    unittest.main()
