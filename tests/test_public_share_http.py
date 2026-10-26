"""D75 over HTTP: a public share on the preview domain — the access-code page and its form, the grant cookie for the
link's path, the pages served sandboxed, the demo entered through the remote preview's own gate. Requests come as the
preview domain's nginx sends them (loopback, X-Preview-Host)."""
from contextlib import contextmanager
import http.client
import json
import threading
from urllib.parse import urlencode

from octopus.db import dumps
from octopus.server import server
from tests.test_public_shares import PAGE, PublicShareCase

DOMAIN = "preview.test"


class HttpCase(PublicShareCase):
    @contextmanager
    def preview(self):
        app = server(self.db, 0, preview_domain=DOMAIN)
        app.service.create_public_share = self.service.create_public_share   # same database, same behaviour
        self.service.public_base = app.service.public_base
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        port = app.server_address[1]

        def call(method, path, host=None, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            head = {"Host": f"127.0.0.1:{port}", "X-Preview-Host": host or f"{self.rid}.{DOMAIN}", **(headers or {})}
            data = None
            if body is not None:
                data = urlencode(body).encode()
                head.update({"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(data))})
            conn.request(method, path, body=data, headers=head)
            response = conn.getresponse()
            try:
                return response.status, dict(response.getheaders()), response.read()
            finally:
                conn.close()
        try:
            yield call
        finally:
            app.shutdown()
            app.server_close()
            thread.join(2)

    def link(self, share):
        return "/_kirocrew/s/" + share["token"]


class CodePageTests(HttpCase):
    def test_a_coded_share_asks_then_opens_its_pages(self):
        with self.preview() as call:
            share = self.share()
            status, headers, page = call("GET", self.link(share))
            self.assertEqual(status, 200)
            self.assertIn("输入授权码".encode(), page)
            self.assertIn("form-action 'self'", headers["Content-Security-Policy"])
            self.assertIn("noindex", headers["X-Robots-Tag"])
            origin = {"Origin": f"https://{self.rid}.{DOMAIN}"}
            wrong = "0000" if share["code"] != "0000" else "1111"
            status, _, page = call("POST", self.link(share), body={"code": wrong}, headers=origin)
            self.assertEqual(status, 401)
            self.assertIn("授权码不正确".encode(), page)
            status, headers, _ = call("POST", self.link(share), body={"code": share["code"]}, headers=origin)
            self.assertEqual((status, headers["Location"]), (303, self.link(share)))
            cookie = headers["Set-Cookie"]
            self.assertIn(f"Path={self.link(share)};", cookie)
            self.assertIn("HttpOnly", cookie)
            jar = {"Cookie": cookie.split(";")[0]}
            status, headers, _ = call("GET", self.link(share), headers=jar)
            self.assertEqual((status, headers["Location"]), (302, self.link(share) + "/index.html"))
            status, headers, data = call("GET", self.link(share) + "/index.html", headers=jar)
            self.assertEqual((status, data), (200, PAGE))
            self.assertTrue(headers["Content-Security-Policy"].startswith("sandbox "))   # an opaque origin, as 预览产出
            self.assertIn("noindex", headers["X-Robots-Tag"])

    def test_without_the_grant_files_are_refused(self):
        with self.preview() as call:
            share = self.share()
            status, headers, _ = call("GET", self.link(share) + "/a.css", headers={"Sec-Fetch-Dest": "style"})
            self.assertEqual(status, 403)
            status, headers, _ = call("GET", self.link(share) + "/index.html", headers={"Sec-Fetch-Dest": "document"})
            self.assertEqual((status, headers["Location"]), (302, self.link(share)))

    def test_an_open_share_needs_no_code(self):
        with self.preview() as call:
            share = self.share(mode="open")
            status, headers, _ = call("GET", self.link(share))
            self.assertEqual((status, headers["Location"]), (302, self.link(share) + "/index.html"))
            self.assertEqual(call("GET", self.link(share) + "/a.css")[2], b"body{}")

    def test_another_sessions_host_or_site_is_refused(self):
        with self.preview() as call:
            share = self.share()
            self.assertEqual(call("GET", self.link(share), host=f"run-{'0' * 32}.{DOMAIN}")[0], 404)
            status, _, _ = call("POST", self.link(share), body={"code": share["code"]},
                                headers={"Origin": "https://evil.example"})
            self.assertEqual(status, 403)

    def test_an_unknown_or_revoked_link_says_it_is_gone(self):
        with self.preview() as call:
            share = self.share(mode="open")
            self.service.delete_public_share(self.b, self.rid, share["id"])
            status, _, page = call("GET", self.link(share))
            self.assertEqual(status, 404)
            self.assertIn("分享链接不存在或已失效".encode(), page)


class DemoTests(HttpCase):
    def test_a_demo_share_enters_the_running_app_through_the_preview_gate(self):
        with self.preview() as call:
            share = self.share(kind="app", mode="open")
            status, _, page = call("GET", self.link(share))
            self.assertEqual(status, 409)   # not running: a visitor never starts it
            self.assertIn("演示当前没有在运行".encode(), page)
            with self.db.transaction() as db:
                db.execute("INSERT INTO apps(run_id,worker_id,status,port,path,upstream,started,started_by,last_access) "
                           "VALUES(?,'w-bob','running',47001,'/',?,1,?,1)",
                           (self.rid, dumps({"url": "http://127.0.0.1:47001", "port": 47001}), self.b["id"]))
            status, headers, _ = call("GET", self.link(share))
            self.assertEqual(status, 302)
            self.assertTrue(headers["Location"].startswith("/_kirocrew/open/"))
            status, headers, _ = call("GET", headers["Location"].replace("/open/", "/enter/"))
            self.assertEqual(status, 302)
            preview = {"Cookie": headers["Set-Cookie"].split(";")[0]}
            status, headers, _ = call("GET", "/v1/preview-auth", headers=preview)
            self.assertEqual((status, headers["X-Preview-Port"]), (204, "47001"))
            self.service.delete_public_share(self.b, self.rid, share["id"])   # revoked: the next request is refused
            self.assertEqual(call("GET", "/v1/preview-auth", headers=preview)[0], 403)


class ManageTests(HttpCase):
    def test_the_link_is_on_the_sessions_own_preview_origin(self):
        with self.preview():
            share = self.share()
        self.assertEqual(share["url"], f"https://{self.rid}.{DOMAIN}/_kirocrew/s/{share['token']}")


if __name__ == "__main__":
    import unittest
    unittest.main()
