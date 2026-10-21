"""D56 M3-5: the session list filtered on the server by space, state, repository, branch, stage, tool, client, submitter
and time, searched in full text, and paged — so the console and Coding Agents find a Session among many."""
import calendar
import json
import unittest

from octopus.db import dumps
from octopus.security import Error, authenticate, issue_token
from tests.test_access import AccessCase

CODE = {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "implement", "acceptance": ["完成"], "verify": ["true"]}
REVIEW = {"id": "review", "name": "审查", "tool": "codex", "prompt": "审查", "kind": "review", "acceptance": ["无阻断"]}


class SearchCase(AccessCase):
    def setUp(self):
        super().setUp()
        self.n = 0

    def make(self, actor, steps=(CODE,), source=None, team=None, created=None, **extra):
        self.n += 1
        body = {"request_id": f"search-fixture-{self.n:04d}", "input": extra.pop("input", f"需求 {self.n}"),
                "source": source or {"type": "new"}, "steps": [dict(s) for s in steps],
                "recovery": {"enabled": False, "max_cycles": 2}, **({"team_id": team} if team else {}), **extra}
        rid = self.service.start(actor, body)["id"]
        if created is not None:
            with self.db.transaction() as db:
                db.execute("UPDATE runs SET created=?, updated=? WHERE id=?", (created, created, rid))
        return rid

    def ids(self, actor, **query):
        return [r["id"] for r in self.service.runs(actor, **query)]

    def accept(self, rid, task):
        with self.db.transaction() as db:
            db.execute("UPDATE tasks SET status='accepted' WHERE run_id=? AND id=?", (rid, task))


class FilterTests(SearchCase):
    def test_by_repository_whatever_the_address(self):
        a = self.make(self.b, source={"type": "git", "url": "git@github.com:o/r"})
        b = self.make(self.b, source={"type": "git", "url": "https://github.com/o/other.git"})
        self.make(self.b)
        self.assertEqual(self.ids(self.b, repo="github.com/o/r"), [a])
        self.assertEqual(self.ids(self.b, repo="O/R"), [a])
        self.assertEqual(sorted(self.ids(self.b, repo="github.com/o")), sorted([a, b]))

    def test_by_branch(self):
        a = self.make(self.b, source={"type": "git", "url": "https://github.com/o/r", "ref": "feat/login"})
        self.make(self.b, source={"type": "git", "url": "https://github.com/o/r", "ref": "main"})
        self.assertEqual(self.ids(self.b, branch="feat/log"), [a])

    def test_by_stage_tool_and_status(self):
        a = self.make(self.b, steps=(CODE, REVIEW))
        b = self.make(self.b, steps=(CODE,))
        self.accept(a, "code")
        self.accept(b, "code")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (b,))
        self.assertEqual(self.ids(self.b, stage="review"), [a])
        self.assertEqual(self.ids(self.b, stage="done"), [b])
        self.assertEqual(self.ids(self.b, tool="codex"), [a])
        self.assertEqual(self.ids(self.b, status=["completed"]), [b])
        self.assertEqual(sorted(self.ids(self.b, status=["completed", "waiting_worker"])), sorted([a, b]))
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET lifecycle='closed' WHERE id=?", (b,))
        self.assertEqual(self.ids(self.b, lifecycle="closed"), [b])
        self.assertEqual(self.ids(self.b, lifecycle="open"), [a])

    def test_the_planning_stage(self):
        rid = self.service.start(self.b, {"request_id": "search-plan-0001", "input": "规划一个功能", "source": {"type": "new"},
                                          "plan": "agent"})["id"]
        self.assertEqual(self.ids(self.b, stage="plan"), [rid])

    def test_by_client_submitter_space_and_time(self):
        with self.db.transaction() as db:
            codex = authenticate(db, issue_token(db, self.b["id"], label="codex MCP")["token"])
        tid = self.team()
        day = calendar.timegm((2026, 10, 20, 12, 0, 0))
        a = self.make(codex, created=day)
        b = self.make(self.b, team=tid, created=day + 86400)
        c = self.make(self.a, team=tid, created=day + 2 * 86400)
        with self.db.transaction() as db:   # a Session from before D55 has no Request
            db.execute("UPDATE runs SET request_id=NULL WHERE id=?", (c,))
        self.assertEqual(self.ids(self.b, client="codex"), [a])
        self.assertEqual(self.ids(self.b, client="api"), [b])
        self.assertEqual(self.ids(self.b, submitter="alice"), [c])
        self.assertEqual(self.ids(self.b, submitter=self.a["id"]), [c])
        self.assertEqual(self.ids(self.b, space="personal"), [a])
        self.assertEqual(self.ids(self.b, space=tid), [c, b])
        self.assertEqual(self.ids(self.b, since="2026-10-21"), [c, b])
        self.assertEqual(self.ids(self.b, until="2026-10-20"), [a])
        self.assertEqual(self.ids(self.b, since=str(day + 86400), until=str(day + 86400)), [b])

    def test_a_bad_filter_is_refused(self):
        for bad in ({"stage": "later"}, {"tool": "gemini"}, {"client": "x"}, {"since": "yesterday"}, {"lifecycle": "gone"},
                    {"status": ["nope"]}, {"repo": "x" * 201}, {"cursor": "!!"}):
            with self.assertRaises(Error, msg=bad) as caught:
                self.service.runs(self.b, page=True, **bad)
            self.assertEqual(caught.exception.status, 400, bad)


class FullTextTests(SearchCase):
    def test_every_word_must_match_anywhere_in_the_session(self):
        a = self.make(self.b, steps=({**CODE, "prompt": "把 Redis 缓存加到订单查询"},), input="优化订单")
        b = self.make(self.b, input="优化登录")
        with self.db.transaction() as db:
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,error,created,updated) "
                       "VALUES('attempt-x',?,'code',1,'{}','blocked',?,?,1,1)",
                       (b, dumps({"outcome": {"summary": "发现 N+1 查询"}}), "检查命令失败：pytest"))
            db.execute("UPDATE tasks SET current_attempt='attempt-x' WHERE run_id=? AND id='code'", (b,))
        self.assertEqual(self.ids(self.b, q="redis"), [a])
        self.assertEqual(self.ids(self.b, q="优化 Redis"), [a])
        self.assertEqual(self.ids(self.b, q="N+1"), [b])
        self.assertEqual(self.ids(self.b, q="pytest"), [b])
        self.assertEqual(sorted(self.ids(self.b, q="优化")), sorted([a, b]))
        self.assertEqual(self.ids(self.b, q="优化 不存在的词"), [])
        self.assertEqual(self.ids(self.e, q="优化"), [])   # access is checked as before


class PageTests(SearchCase):
    def test_pages_neither_repeat_nor_skip_when_sessions_are_created_meanwhile(self):
        made = [self.make(self.b, created=1000 + i) for i in range(5)]   # oldest first
        first = self.service.runs(self.b, page=True, limit=2)
        self.assertEqual(first["total"], 5)
        self.assertEqual([r["id"] for r in first["items"]], made[4:2:-1])
        newer = self.make(self.b, created=2000)
        second = self.service.runs(self.b, page=True, limit=2, cursor=first["next"])
        third = self.service.runs(self.b, page=True, limit=2, cursor=second["next"])
        self.assertEqual([r["id"] for r in second["items"] + third["items"]], [made[2], made[1], made[0]])
        self.assertIsNone(third["next"])
        self.assertEqual(self.service.runs(self.b, page=True, limit=50)["items"][0]["id"], newer)

    def test_without_page_the_answer_is_still_a_list(self):
        self.make(self.b)
        self.assertIsInstance(self.service.runs(self.b), list)

    def test_the_route_parses_every_filter(self):
        rid = self.make(self.b, source={"type": "git", "url": "https://github.com/o/r"})
        with self.http() as call:
            status, body = call("GET", "/v1/runs?page=1&limit=10&repo=github.com/o/r&status=waiting_worker,completed"
                                       "&lifecycle=open&tool=claude&stage=implement&submitter=bob&q=%E9%9C%80%E6%B1%82",
                                self.tokens["bob"])
            self.assertEqual(status, 200)
            self.assertEqual(([r["id"] for r in body["items"]], body["total"], body["next"]), ([rid], 1, None))
            self.assertEqual(call("GET", "/v1/runs?page=1&stage=later", self.tokens["bob"])[0], 400)
            status, plain = call("GET", "/v1/runs?view=list", self.tokens["bob"])
            self.assertIsInstance(plain, list)


if __name__ == "__main__":
    unittest.main()
