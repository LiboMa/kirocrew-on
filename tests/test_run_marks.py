"""Owner 2026-10-22: 「历史 Session，请增加收藏、置顶、删除、分享功能」. Pinning and starring are a person's own marks on a
Session they can read: the list says them, ``pinned=1`` lists the pinned (the console shows them on top) and ``starred=1``
the starred; nobody else sees them."""
import unittest

from octopus.security import Error
from tests.test_access import AccessCase


class MarkTests(AccessCase):
    def setUp(self):
        super().setUp()
        self.shared_env(users=("bob", "eve"))
        self.mine = self.start(self.b, "start-0000000001")["id"]
        self.other = self.start(self.b, "start-0000000002")["id"]

    def listed(self, actor, **filters):
        return {r["id"]: r.get("marks") for r in self.service.runs(actor, view="list", **filters)}

    def test_pin_and_star_are_listed_and_filter_the_list(self):
        self.assertEqual(self.service.mark_run(self.b, self.mine, {"pinned": True}), {"pinned": True, "starred": False})
        self.assertEqual(self.service.mark_run(self.b, self.other, {"starred": True}), {"pinned": False, "starred": True})
        listed = self.listed(self.b)
        self.assertEqual(listed[self.mine], {"pinned": True, "starred": False})
        self.assertEqual(listed[self.other], {"pinned": False, "starred": True})
        self.assertEqual(set(self.listed(self.b, pinned=True)), {self.mine})
        self.assertEqual(set(self.listed(self.b, starred=True)), {self.other})
        self.service.mark_run(self.b, self.mine, {"pinned": False})
        self.assertEqual(set(self.listed(self.b, pinned=True)), set())

    def test_marks_are_ones_own(self):
        with self.db.transaction() as db:   # eve may read bob's Session (a share)
            db.execute("INSERT INTO shares(id,run_id,kind,subject,role,created_by,created) VALUES('s1',?,'user',?,'viewer',?,1)",
                       (self.mine, self.e["id"], self.b["id"]))
        self.service.mark_run(self.e, self.mine, {"starred": True})
        self.assertEqual(self.listed(self.b)[self.mine], {"pinned": False, "starred": False})
        self.assertEqual({r["id"] for r in self.service.runs(self.e, view="list", starred=True, shared=True)}, {self.mine})

    def test_a_session_one_cannot_read_cannot_be_marked(self):
        with self.assertRaises(Error) as caught:
            self.service.mark_run(self.e, self.other, {"pinned": True})
        self.assertEqual(caught.exception.status, 404)

    def test_a_mark_is_a_yes_or_no(self):
        for body in ({}, {"pinned": "yes"}, {"starred": 1}, {"pinned": True, "color": "red"}):
            with self.subTest(body=body), self.assertRaises(Error) as caught:
                self.service.mark_run(self.b, self.mine, body)
            self.assertEqual(caught.exception.status, 400)

    def test_the_routes(self):
        with self.http() as call:
            status, marks = call("POST", f"/v1/runs/{self.mine}/marks", self.tokens["bob"], {"pinned": True})
            self.assertEqual((status, marks), (200, {"pinned": True, "starred": False}))
            status, page = call("GET", "/v1/runs?pinned=1&page=1&view=list", self.tokens["bob"])
            self.assertEqual([r["id"] for r in page["items"]], [self.mine])
            self.assertEqual(call("POST", f"/v1/runs/{self.mine}/marks", self.tokens["eve"], {"pinned": True})[0], 404)


if __name__ == "__main__":
    unittest.main()
