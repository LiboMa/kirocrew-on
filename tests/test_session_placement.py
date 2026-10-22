"""D49 phase 2: where a Session runs. A Team Session runs on its submitter's own boundary; without one it falls back to the
Team's environment with a hint (owner 2026-10-19, option A); whether an environment still serves a Session follows its
creator; the platform alone answers where a new Session would go."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

from octopus import domain
from octopus.db import Database, dumps
from octopus.domain import Service, unserved
from octopus.security import Error, authenticate, create_user, issue_token

STEPS = [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["完成"]}]


class PlacementCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Database(self.temporary.name)
        self.service = Service(self.db)
        self.config = dumps({"url": "fixture", "token": "fixture", "capabilities": {"isolation": "dedicated"}})
        with self.db.transaction() as db:
            self.admin = authenticate(db, issue_token(db, create_user(db, "admin", "admin-password-123", admin=True)["id"])["token"])
            users = {name: create_user(db, name, name + "-password-123", admin=False) for name in ("alice", "bob", "dave")}
            self.ids = {name: row["id"] for name, row in users.items()}
            self.tokens = {name: issue_token(db, row["id"])["token"] for name, row in users.items()}
            self.people = {name: authenticate(db, token) for name, token in self.tokens.items()}
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-a','研发组',1)")
            for name, role in (("alice", "member"), ("bob", "member"), ("dave", "owner")):
                db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-a',?,?)", (self.ids[name], role))
            db.execute("INSERT INTO workers(id,name,team_id,config,scope) VALUES('teamenv','研发组环境','team-a',?,'dedicated')",
                       (self.config,))
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('shared','personal',?,?,'shared')",
                       (self.admin["id"], self.config))
            for name in ("alice", "bob"):
                db.execute("INSERT INTO worker_spaces(worker_id,user_id) VALUES('shared',?)", (self.ids[name],))
        self.own("alice-b", "alice")
        self.requests = 0

    def own(self, wid, person):
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES(?,?,?,?,'dedicated')",
                       (wid, wid, self.ids[person], self.config))

    def start(self, person, team="team-a", **body):
        self.requests += 1
        created = self.service.start(self.people[person], {
            "request_id": f"placement-fixture-{self.requests}", "input": "实现登录功能", "source": {"type": "new"}, "steps": STEPS,
            **({"team_id": team} if team else {}), **body})
        return created, self.service.run(self.people[person], created["id"])

    def row(self, rid):
        with self.db.read() as db:
            return dict(db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())


class RuleTests(PlacementCase):
    def test_a_team_session_runs_on_the_submitters_own_boundary(self):
        created, run = self.start("alice")
        self.assertEqual(run["worker_id"], "alice-b")
        self.assertEqual(run["placement"], {"kind": "own"})
        self.assertEqual(created["placement"], {"kind": "own"})

    def test_without_one_it_falls_back_to_the_teams_environment_with_a_hint(self):
        _, run = self.start("bob")
        self.assertEqual(run["worker_id"], "teamenv")
        self.assertEqual(run["placement"]["kind"], "team")
        self.assertIn("请平台管理员", run["placement"]["hint"])   # D53: the user is pointed to the administrator,
        self.assertNotIn("deploy.sh", run["placement"]["hint"])   # never handed an ops command

    def test_a_team_without_an_environment_takes_a_member_with_a_boundary(self):
        with self.db.transaction() as db:
            db.execute("DELETE FROM workers WHERE id='teamenv'")
        self.assertEqual(self.start("alice")[1]["worker_id"], "alice-b")
        with self.assertRaises(Error) as caught:
            self.start("bob")
        self.assertEqual(caught.exception.code, "team_without_worker")
        self.assertIn("自己的执行边界", str(caught.exception))

    def test_another_members_boundary_cannot_be_named(self):
        with self.assertRaises(Error) as caught:
            self.start("bob", worker_id="alice-b")
        self.assertEqual(caught.exception.status, 403)

    def test_two_own_boundaries_wait_in_a_team_too(self):
        self.own("alice-b2", "alice")
        _, run = self.start("alice")
        self.assertIsNone(run["worker_id"])
        self.assertEqual(run["status"], "waiting_worker")

    def test_a_disabled_own_boundary_is_named_in_the_hint(self):   # review minor: "you have none" sent them for a second
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='alice-b'")
        _, run = self.start("alice")
        self.assertEqual(run["worker_id"], "teamenv")
        self.assertIn("已停用", run["placement"]["hint"])
        self.assertNotIn("还没有自己的执行边界", run["placement"]["hint"])
        with self.db.transaction() as db:   # a Team with no environment at all
            db.execute("INSERT INTO teams(id,name,created) VALUES('team-b','空组',1)")
            db.execute("INSERT INTO members(team_id,user_id,role) VALUES('team-b',?,'member')", (self.ids["alice"],))
        with self.assertRaises(Error) as caught:
            self.start("alice", team="team-b")
        self.assertEqual(caught.exception.code, "team_without_worker")
        self.assertIn("已停用", str(caught.exception))

    def test_a_named_environment_is_kept_and_recorded(self):
        _, run = self.start("alice", worker_id="teamenv")
        self.assertEqual((run["worker_id"], run["placement"]), ("teamenv", {"kind": "requested"}))

    def test_personal_sessions_are_unchanged(self):
        self.assertEqual(self.start("alice", team=None)[1]["placement"], {"kind": "own"})
        _, run = self.start("bob", team=None)
        self.assertEqual((run["worker_id"], run["placement"]), ("shared", {"kind": "only"}))

    def test_the_timeline_records_the_placement(self):
        created, _ = self.start("bob")
        with self.db.read() as db:
            event = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='run.created'", (created["id"],)).fetchone()
        self.assertEqual(json.loads(event["payload"])["placement"], "team")


class ServingTests(PlacementCase):
    def test_the_creator_leaving_the_team_makes_it_wait(self):
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("DELETE FROM members WHERE team_id='team-a' AND user_id=?", (self.ids["alice"],))
            self.assertIn("会话创建者已不在该 Team", unserved(db, self.row(created["id"])))

    def test_a_disabled_boundary_waits_and_can_move_to_the_team(self):
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='alice-b'")
            self.assertEqual(unserved(db, self.row(created["id"])), "执行环境已停用")
            db.execute("UPDATE runs SET status='waiting_worker' WHERE id=?", (created["id"],))
        self.service.operate(self.people["dave"], created["id"], "assign-worker",
                             {"request_id": "assign-fixture-1", "worker_id": "teamenv",
                              "expected_version": self.row(created["id"])["version"]})
        self.assertEqual(self.row(created["id"])["worker_id"], "teamenv")

    def test_assigning_records_where_it_now_runs(self):   # review minor: it still said "own" on the Team's environment
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id='alice-b'")
            db.execute("UPDATE runs SET status='waiting_worker' WHERE id=?", (created["id"],))
        self.service.operate(self.people["dave"], created["id"], "assign-worker",
                             {"request_id": "assign-fixture-2", "worker_id": "teamenv",
                              "expected_version": self.row(created["id"])["version"]})
        self.assertEqual(self.service.run(self.people["alice"], created["id"])["placement"], {"kind": "team"})

    def test_a_team_environment_change_leaves_member_boundaries_alone(self):
        created, _ = self.start("alice")
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET scope='spare', team_id=NULL, owner_id=?, last_team='team-a' WHERE id='teamenv'",
                       (self.admin["id"],))   # as Service.set_environment leaves the Team's former host
            self.assertIsNone(unserved(db, self.row(created["id"])))

    def test_the_space_rule_is_one(self):   # the start, the check before dispatch and the assignment ask the same function
        with self.db.read() as db:
            names = {w["id"] for w in domain.space_workers(db, self.ids["alice"], "team-a")}
            others = {w["id"] for w in domain.space_workers(db, self.ids["bob"], "team-a")}
        self.assertEqual((names, others), ({"alice-b", "teamenv"}, {"teamenv"}))


class InterfaceTests(PlacementCase):
    def test_the_placement_endpoint_answers_the_default(self):
        mine = self.service.placement(self.people["alice"], "team-a")
        self.assertEqual((mine["space"], mine["default"]["id"], mine["default"]["placement"], mine["hint"]),
                         ("team-a", "alice-b", "own", None))
        self.assertEqual({(o["id"], o["own"]) for o in mine["options"]}, {("alice-b", True), ("teamenv", False)})
        theirs = self.service.placement(self.people["bob"], "team-a")
        self.assertEqual((theirs["default"]["id"], theirs["default"]["placement"]), ("teamenv", "team"))
        self.assertIn("请平台管理员", theirs["hint"])   # D53: the user is pointed to the administrator,
        self.assertNotIn("deploy.sh", theirs["hint"])   # never handed an ops command
        self.assertEqual(self.service.placement(self.people["alice"])["default"]["placement"], "own")
        with self.db.transaction() as db:
            eve = authenticate(db, issue_token(db, create_user(db, "eve", "eve-password-123", admin=False)["id"])["token"])
        with self.assertRaises(Error) as caught:
            self.service.placement(eve, "team-a")
        self.assertIn(caught.exception.status, {403, 404})

    def test_an_archived_team_has_no_place_for_a_new_session(self):   # as start() refuses one there
        with self.db.transaction() as db:
            db.execute("UPDATE teams SET archived=1 WHERE id='team-a'")
        with self.assertRaises(Error) as caught:
            self.service.placement(self.people["alice"], "team-a")
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "team_archived"))

    def test_the_options_say_what_auto_runs(self):   # D51 R5: codex publishes no list; its "auto" is a configured model
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET config=? WHERE id='teamenv'", (dumps({"url": "fixture", "token": "fixture", "capabilities": {
                "isolation": "dedicated", "model_defaults": {"codex": "openai.gpt-6-astra"}}}),))
        option = next(o for o in self.service.placement(self.people["bob"], "team-a")["options"] if o["id"] == "teamenv")
        self.assertEqual(option["defaults"], {"codex": "openai.gpt-6-astra"})

    def test_the_options_for_a_session_follow_its_creator(self):
        created, _ = self.start("alice")
        self.own("dave-b", "dave")   # the Team owner's own boundary is not the Session's to move to
        answer = self.service.placement(self.people["dave"], rid=created["id"])
        self.assertEqual({o["id"] for o in answer["options"]}, {"alice-b", "teamenv"})
        self.assertEqual((answer["default"]["id"], answer["default"]["placement"]), ("alice-b", "own"))

    def test_only_the_boundary_owner_opens_the_native_entry(self):
        from octopus import crew_entry
        created, _ = self.start("alice")
        with self.db.transaction() as db:   # prepared: the native Session exists on alice's boundary
            db.execute("UPDATE runs SET native_parent='octopus-x', status='ready' WHERE id=?", (created["id"],))
        with patch("octopus.crew_entry.worker_request", side_effect=AssertionError("no network")):
            with self.assertRaises(Error) as caught:
                crew_entry.create(self.service, self.people["bob"], created["id"])
        self.assertEqual(caught.exception.status, 403)

    def test_the_console_offers_the_native_entry_only_to_the_boundary_owner(self):   # R7: as the server enforces it
        created, _ = self.start("alice")
        self.assertTrue(self.service.run(self.people["alice"], created["id"])["permissions"]["native"])
        theirs = self.service.run(self.people["bob"], created["id"])["permissions"]
        self.assertFalse(theirs["native"])
        self.assertIn("alice 的执行边界", theirs["native_reason"])

    def test_other_members_see_the_session_and_its_report(self):
        from octopus import report
        created, _ = self.start("alice")
        run = self.service.run(self.people["bob"], created["id"])
        self.assertEqual(run["worker_id"], "alice-b")
        self.assertIn("实现登录功能", report.markdown(run))

    def test_the_mcp_lists_environments_with_the_default(self):
        from octopus import mcp
        seen, answer = [], self.service.placement(self.people["bob"], "team-a")

        class Client:
            def request(self, method, path, body=None):
                seen.append((method, path))
                return answer
        listed = mcp.call(Client(), "octopus_environments", {"team_id": "team-a"})
        self.assertEqual(seen[-1], ("GET", "/v1/placement?team_id=team-a"))
        self.assertEqual([(e["id"], e["default"]) for e in listed["environments"]], [("teamenv", True)])
        self.assertIn("请平台管理员", listed["hint"])   # D53: the user is pointed to the administrator,
        self.assertNotIn("deploy.sh", listed["hint"])   # never handed an ops command
        mcp.call(Client(), "octopus_environments", {"run_id": "run-1"})
        self.assertEqual(seen[-1], ("GET", "/v1/placement?run_id=run-1"))

    def test_the_compact_status_carries_the_placement(self):
        from octopus import mcp
        created, _ = self.start("bob")
        view = mcp.compact_status(self.service.run(self.people["bob"], created["id"]), [])["session"]
        self.assertEqual(view["placement"]["kind"], "team")
        self.assertIn("请平台管理员", view["placement"]["hint"])   # D53: the user is pointed to the administrator,
        self.assertNotIn("deploy.sh", view["placement"]["hint"])   # never handed an ops command

    def test_the_skill_lists_a_waiting_sessions_environments_by_run(self):   # final review: team_id gave 404 / 403
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "skills/kirocrew-on/SKILL.md").read_text()
        row = next(line for line in text.splitlines() if line.startswith("| `waiting_worker` |"))
        self.assertIn("`octopus_environments`（传 `run_id`）", row)
        self.assertNotIn("Team 会话传 `team_id`", row)

    def test_the_route_and_schema(self):
        from octopus.schema import openapi
        from octopus.server import required_scope, server
        self.assertEqual(required_scope("GET", "/v1/placement"), "read")
        self.assertIn("/v1/placement", set(openapi()["paths"]))
        api = server(self.db, 0)
        threading.Thread(target=api.serve_forever, daemon=True).start()
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{api.server_address[1]}/v1/placement?team_id=team-a",
                                         headers={"Authorization": "Bearer " + self.tokens["alice"]})
        with urllib.request.urlopen(request, timeout=10) as answer:
            self.assertEqual(json.load(answer)["default"]["id"], "alice-b")


class DirectionTests(PlacementCase):
    """R10 (final review 2026-10-20): on a person's own boundary only they give the work its instructions; the Agent there
    runs as them. Others look, stop and publish; a Team owner or an administrator moves it to the Team's environment."""
    def blocked(self, person="alice"):
        created, _ = self.start(person)
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='blocked', native_parent='octopus-x' WHERE id=?", (created["id"],))
        return created["id"]

    def operate(self, person, rid, action, **body):
        self.requests += 1
        return self.service.operate(self.admin if person == "admin" else self.people[person], rid, action,
                                    {"request_id": f"direct-fixture-{self.requests}", "expected_version": self.row(rid)["version"], **body})

    def test_only_the_boundary_owner_continues_or_diagnoses_there(self):   # D70: there is no plan to confirm
        rid = self.blocked()
        for person in ("bob", "dave", "admin"):
            for action, body in (("continue", {"task_id": "code"}), ("recover", {})):
                with self.subTest(person=person, action=action), self.assertRaises(Error) as caught:
                    self.operate(person, rid, action, **body)
                self.assertEqual(caught.exception.code, "boundary_owner_only")
        self.operate("alice", rid, "continue", task_id="code")   # the owner herself goes on

    def test_notes_and_approvals_follow_the_same_rule(self):
        rid = self.blocked()
        with self.assertRaises(Error) as steered:
            self.service.steer(self.people["dave"], rid, {"task_id": "code", "message": "先只改 README"})
        with self.assertRaises(Error) as approved:
            self.service.answer_approval(self.people["dave"], rid, {"origin": "native", "request_id": "r-1", "request_mid": "m-1",
                                                                    "decision": "approve"})
        self.assertEqual((steered.exception.code, approved.exception.code), ("boundary_owner_only", "boundary_owner_only"))

    def test_others_still_stop_it(self):
        rid = self.blocked()
        self.operate("bob", rid, "stop")
        self.assertEqual(self.row(rid)["desired"], "stop")

    def test_a_stopped_session_stays_where_it_is(self):   # R10 review: a move left it stuck and its work behind
        rid = self.blocked()
        with self.assertRaises(Error) as caught:
            self.operate("dave", rid, "assign-worker", worker_id="teamenv")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.row(rid)["worker_id"], "alice-b")

    def test_only_the_owner_puts_work_on_their_boundary(self):   # R10 review: waiting, any member could pick alice-b
        rid = self.blocked()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='waiting_worker', worker_id=NULL, native_parent=NULL WHERE id=?", (rid,))
        for person in ("bob", "dave", "admin"):
            with self.subTest(person=person), self.assertRaises(Error) as caught:
                self.operate(person, rid, "assign-worker", worker_id="alice-b")
            self.assertEqual(caught.exception.code, "boundary_owner_only")
        self.operate("dave", rid, "assign-worker", worker_id="teamenv")   # the Team's environment: as before
        self.assertEqual(self.row(rid)["worker_id"], "teamenv")
        other = self.blocked()
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='waiting_worker', worker_id=NULL, native_parent=NULL WHERE id=?", (other,))
        self.operate("alice", other, "assign-worker", worker_id="alice-b")   # she herself may
        self.assertEqual(self.row(other)["worker_id"], "alice-b")

    def test_the_console_is_told(self):
        rid = self.blocked()
        self.assertTrue(self.service.run(self.people["alice"], rid)["permissions"]["direct"])
        theirs = self.service.run(self.people["bob"], rid)["permissions"]
        self.assertFalse(theirs["direct"])
        self.assertIn("只有边界主人", theirs["direct_reason"])
        self.assertTrue(self.service.run(self.people["bob"], self.start("bob")[0]["id"])["permissions"]["direct"])   # Team env


if __name__ == "__main__":
    unittest.main()
