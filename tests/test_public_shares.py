"""D75: a Session's pages or its running demo shared with anyone on the Internet — behind a four-digit access code or
open. The link's token cannot be guessed; the code is a second factor with limited tries; a share ends when revoked, when
it expires, when its Session closes or its sharer's account is disabled."""
import base64
import json
import time
from unittest import mock

from octopus import public_shares
from octopus.db import dumps
from octopus.security import Error
from tests.test_access import AccessCase

PAGE = b"<!doctype html><title>demo</title><link rel=stylesheet href=./a.css>"


class PublicShareCase(AccessCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('w-bob','bob',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://worker.test", "token": "t" * 40, "capabilities": {}})))
        self.rid = self.start(self.b, worker_id="w-bob")["id"]
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed', workspace='/w', native_parent='octopus-x' WHERE id=?", (self.rid,))
        self.files = {"": {"entry": "index.html", "pages": ["index.html", "report/index.html"]},
                      "index.html": PAGE, "a.css": b"body{}", "report/index.html": b"<h1>report</h1>"}
        patcher = mock.patch("octopus.transport.worker_request", self.worker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def worker(self, config, path, body=None, timeout=35):
        assert path == "/files", path
        found = self.files.get(body["path"])
        if found is None:
            raise Error(404, "not_found", "文件不存在")
        if isinstance(found, dict):
            return found
        return {"content": base64.b64encode(found).decode()}

    def share(self, actor=None, **body):
        return self.service.create_public_share(actor or self.b, self.rid, {"kind": "pages", "mode": "code", **body})


class SharingTests(PublicShareCase):
    def test_the_creator_shares_with_a_code_or_open(self):
        coded = self.share()
        self.assertEqual((coded["kind"], coded["mode"]), ("pages", "code"))
        self.assertRegex(coded["code"], r"^[0-9]{4}$")
        self.assertRegex(coded["token"], r"^[A-Za-z0-9_-]{24}$")
        self.assertAlmostEqual(coded["expires"], time.time() + 7 * 86400, delta=5)   # seven days unless chosen
        opened = self.share(mode="open", expires="never")
        self.assertIsNone(opened["code"])
        self.assertIsNone(opened["expires"])
        listed = self.service.public_shares(self.b, self.rid)["shares"]
        self.assertEqual({s["id"] for s in listed}, {coded["id"], opened["id"]})

    def test_who_may_share(self):
        with self.db.transaction() as db:   # eve can read it as a viewer, which is not enough
            db.execute("INSERT INTO shares(id,run_id,kind,subject,role,created_by,created) VALUES('s1',?,'user',?,'viewer',?,1)",
                       (self.rid, self.e["id"], self.b["id"]))
        with self.assertRaises(Error) as caught:
            self.share(self.e)
        self.assertEqual(caught.exception.status, 403)

    def test_what_is_refused(self):
        for body in ({"kind": "video"}, {"mode": "pin"}, {"expires": "3d"}, {"entry": "../secret.html"}, {"entry": ".env"}):
            with self.subTest(body=body), self.assertRaises(Error):
                self.share(**body)

    def test_revoked_or_expired_or_closed_it_ends(self):
        share = self.share(mode="open")
        self.assertEqual(self.service.public_share_visit(share["token"], "1.1.1.1")["kind"], "pages")
        self.service.delete_public_share(self.b, self.rid, share["id"])
        with self.assertRaises(Error) as caught:
            self.service.public_share_visit(share["token"], "1.1.1.1")
        self.assertEqual(caught.exception.status, 404)
        late = self.share(mode="open", expires="1d")
        with self.assertRaises(Error):
            self.service.public_share_visit(late["token"], "1.1.1.1", now=time.time() + 2 * 86400)
        again = self.share(mode="open")
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET lifecycle='closed' WHERE id=?", (self.rid,))
        with self.assertRaises(Error):
            self.service.public_share_visit(again["token"], "1.1.1.1")

    def test_a_disabled_sharer_ends_the_share(self):
        share = self.share(mode="open")
        with self.db.transaction() as db:
            db.execute("UPDATE users SET active=0 WHERE id=?", (self.b["id"],))
        with self.assertRaises(Error):
            self.service.public_share_visit(share["token"], "1.1.1.1")


class CodeTests(PublicShareCase):
    def test_without_the_code_it_asks_and_with_it_it_opens(self):
        share = self.share()
        with self.assertRaises(Error) as caught:
            self.service.public_share_visit(share["token"], "1.1.1.1")
        self.assertEqual((caught.exception.status, caught.exception.code), (401, "share_code_required"))
        self.assertEqual(self.service.public_share_check(share["token"], share["code"], "1.1.1.1")["id"], share["id"])
        self.assertEqual(self.service.public_share_visit(share["token"], "1.1.1.1", granted=True)["kind"], "pages")

    def test_wrong_codes_are_limited_per_visitor_then_per_share(self):
        share = self.share()
        wrong = "0000" if share["code"] != "0000" else "1111"
        for _ in range(5):
            with self.assertRaises(Error) as caught:
                self.service.public_share_check(share["token"], wrong, "2.2.2.2")
            self.assertEqual(caught.exception.code, "share_code_wrong")
        with self.assertRaises(Error) as caught:   # the right code too: this visitor waits ten minutes
            self.service.public_share_check(share["token"], share["code"], "2.2.2.2")
        self.assertEqual((caught.exception.status, caught.exception.code), (429, "share_rate_limited"))
        self.assertEqual(self.service.public_share_check(share["token"], share["code"], "3.3.3.3")["id"], share["id"])
        for n in range(30):   # many visitors: the share locks for an hour
            try:
                self.service.public_share_check(share["token"], wrong, f"10.0.{n}.1")
            except Error:
                pass
        with self.assertRaises(Error) as caught:
            self.service.public_share_check(share["token"], share["code"], "4.4.4.4")
        self.assertEqual(caught.exception.code, "share_locked")
        renewed = self.service.public_share_code(self.b, self.rid, share["id"])   # a new code unlocks it
        self.assertNotIn("locked_until", {k for k, v in renewed.items() if v})
        self.assertEqual(self.service.public_share_check(share["token"], renewed["code"], "4.4.4.4")["id"], share["id"])

    def test_a_new_code_ends_what_the_old_one_granted(self):
        share = self.share()
        key = b"k" * 32
        grant = public_shares.grant(share["token"], share["code"], key)
        self.assertTrue(public_shares.granted(grant, share["token"], share["code"], key))
        renewed = self.service.public_share_code(self.b, self.rid, share["id"])
        self.assertFalse(public_shares.granted(grant, share["token"], renewed["code"], key))
        self.assertFalse(public_shares.granted(grant, "x" * 24, share["code"], key))   # another share's


class ContentTests(PublicShareCase):
    def test_pages_are_read_from_the_workspace(self):
        share = self.share(mode="open")
        visit = self.service.public_share_visit(share["token"], "1.1.1.1")
        self.assertEqual(visit["entry"], "index.html")
        self.assertEqual(self.service.public_share_file(share["token"], "a.css"), b"body{}")
        named = self.share(mode="open", entry="report/index.html")
        self.assertEqual(self.service.public_share_visit(named["token"], "1.1.1.1")["entry"], "report/index.html")
        with self.db.read() as db:
            views = db.execute("SELECT views FROM public_shares WHERE id=?", (share["id"],)).fetchone()["views"]
        self.assertEqual(views, 1)

    def test_a_demo_share_is_a_holder_of_the_running_app(self):
        share = self.share(kind="app", mode="open")
        with self.db.transaction() as db:
            db.execute("INSERT INTO apps(run_id,worker_id,status,port,path,upstream,started,started_by,last_access) "
                       "VALUES(?,'w-bob','running',47001,'/',?,1,?,1)",
                       (self.rid, dumps({"url": "http://127.0.0.1:47001", "port": 47001}), self.b["id"]))
        self.assertEqual(self.service.public_share_visit(share["token"], "1.1.1.1")["kind"], "app")
        self.assertEqual(self.service.app_auth(self.rid, share["id"])["port"], 47001)
        self.service.delete_public_share(self.b, self.rid, share["id"])
        with self.assertRaises(Error):
            self.service.app_auth(self.rid, share["id"])

    def test_a_pages_share_is_no_holder_of_the_app(self):
        share = self.share(mode="open")
        with self.db.transaction() as db:
            db.execute("INSERT INTO apps(run_id,worker_id,status,port,path,upstream,started,started_by,last_access) "
                       "VALUES(?,'w-bob','running',47001,'/',?,1,?,1)",
                       (self.rid, dumps({"url": "http://127.0.0.1:47001", "port": 47001}), self.b["id"]))
        with self.assertRaises(Error):
            self.service.app_auth(self.rid, share["id"])


if __name__ == "__main__":
    import unittest
    unittest.main()
