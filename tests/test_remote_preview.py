"""D36 §5.2: the remote preview's platform side — the record of each Session's app, the single-use opening link and the
preview domain's cookie, and auth_request re-checking every request (nginx asks /v1/preview-auth)."""
from contextlib import contextmanager
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from octopus import remote_preview
from octopus.db import dumps
from octopus.security import Error
from octopus.server import archive_run, archive_ticket, preview_key, preview_run, preview_ticket, required_scope, server
from tests.test_access import AccessCase

TOKEN = "x" * 40


class FakeAppWorker(BaseHTTPRequestHandler):
    """Answers POST /app like a Worker (Task 1); ``old`` answers 404 like a Worker from before the remote preview."""
    calls, mode, via, instance = [], "ok", None, "i1"
    state = {"status": "stopped", "port": None, "path": None, "error": None, "started": None, "source": None}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAppWorker.calls.append(body)
        if self.path != "/app" or FakeAppWorker.mode == "old":
            status, answer = 404, {"code": "not_found", "error": "接口不存在"}
        else:
            status = 200
            if body["action"] == "start":
                FakeAppWorker.state = {"status": "starting", "port": 47001, "path": "/", "error": None, "started": 1.0,
                                       "source": "declared"}
            if body["action"] == "stop":
                FakeAppWorker.state = {**FakeAppWorker.state, "status": "stopped"}
                answer = {"status": "stopped"}
            else:
                answer = {**FakeAppWorker.state, "log": ["== 启动 app：npm run dev（端口 47001）"], "via": FakeAppWorker.via,
                          "instance": FakeAppWorker.instance}
        data = json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class PreviewCase(AccessCase):
    def setUp(self):
        super().setUp()
        FakeAppWorker.calls, FakeAppWorker.mode, FakeAppWorker.via, FakeAppWorker.instance = [], "ok", None, "i1"
        FakeAppWorker.state = {"status": "stopped", "port": None, "path": None, "error": None, "started": None, "source": None}
        self.worker = ThreadingHTTPServer(("127.0.0.1", 0), FakeAppWorker)
        threading.Thread(target=self.worker.serve_forever, daemon=True).start()
        self.addCleanup(self.worker.server_close)
        self.addCleanup(self.worker.shutdown)
        self.tid = self.team(environment=False)
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,team_id,config) VALUES('w-z','z',?,?)",
                       (self.tid, dumps({"url": f"http://127.0.0.1:{self.worker.server_address[1]}", "token": TOKEN,
                                         "capabilities": {"isolation": "dedicated"}})))
        self.rid = self.start(self.a, team_id=self.tid, worker_id="w-z", name="订单系统")["id"]
        self.settle(self.rid)
        self.key = preview_key(self.tmp.name)

    def settle(self, rid, status="completed"):
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET workspace='/w/x',native_parent='octopus-x',status=? WHERE id=?", (status, rid))

    def running(self):
        """Start the app (as bob) and let the Worker report it ready."""
        self.service.app_start(self.b, self.rid)
        FakeAppWorker.state = {**FakeAppWorker.state, "status": "running"}
        return self.service.app(self.b, self.rid)

    def row(self, rid=None):
        with self.db.read() as db:
            return db.execute("SELECT * FROM apps WHERE run_id=?", (rid or self.rid,)).fetchone()

    def events(self, kind):
        with self.db.read() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind=? ORDER BY id",
                                                         (self.rid, kind))]

    @contextmanager
    def platform(self, **options):
        app = server(self.db, 0, **options)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        try:
            yield app.server_address[1]
        finally:
            app.shutdown()
            app.server_close()

    def raw(self, port, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                               headers={"Host": f"127.0.0.1:{port}", **({"Content-Type": "application/json"} if body is not None
                                                                        else {}), **(headers or {})})
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()


class TicketTests(PreviewCase):
    def test_open_tickets_and_cookies_have_their_own_purpose(self):
        ticket = remote_preview.open_ticket(self.rid, self.b["id"], self.key)
        rid, user, nonce = remote_preview.open_run(ticket, self.key)
        self.assertEqual((rid, user), (self.rid, self.b["id"]))
        self.assertRegex(nonce, r"^[0-9a-f]{16}$")
        self.assertNotEqual(nonce, remote_preview.open_run(remote_preview.open_ticket(self.rid, self.b["id"], self.key),
                                                           self.key)[2])
        for wrong, code in ((archive_ticket(self.rid, self.b["id"], "console", self.key), "app_link_invalid"),
                            (preview_ticket(self.rid, self.b["id"], self.key), "app_link_invalid"),
                            (ticket[:-2] + ("AA" if not ticket.endswith("AA") else "BB"), "app_link_invalid"),
                            (remote_preview.open_ticket(self.rid, self.b["id"], self.key, now=time.time() - 3600),
                             "app_link_expired")):
            with self.assertRaises(Error) as caught:
                remote_preview.open_run(wrong, self.key)
            self.assertEqual((caught.exception.status, caught.exception.code), (403, code))
        for use in (lambda: archive_run(ticket, self.key), lambda: preview_run(ticket, self.key)):
            with self.assertRaises(Error):
                use()
        value = remote_preview.cookie(self.rid, self.b["id"], self.key)
        self.assertEqual(remote_preview.cookie_run(value, self.key), (self.rid, self.b["id"]))
        for wrong in (preview_ticket(self.rid, self.b["id"], self.key), "",
                      remote_preview.cookie(self.rid, self.b["id"], self.key, now=time.time() - 9 * 3600)):
            with self.assertRaises(Error) as caught:
                remote_preview.cookie_run(wrong, self.key)
            self.assertEqual((caught.exception.status, caught.exception.code), (401, "app_cookie_invalid"))

    def test_the_upstream_of_a_loopback_worker_and_of_a_worker_host(self):
        self.assertEqual(remote_preview.upstream({"url": "http://127.0.0.1:8928", "token": TOKEN}, 47001, None),
                         {"url": "http://127.0.0.1:47001", "port": 47001})
        host = remote_preview.upstream({"url": "https://10.42.1.50:8943", "token": TOKEN}, 47002,
                                       {"port": 8945, "name": "w1.worker.kirocrew-on"})
        expected = hmac.new(TOKEN.encode(), b"kirocrew-preview", hashlib.sha256).hexdigest()
        self.assertEqual(host, {"url": "https://10.42.1.50:8945", "name": "w1.worker.kirocrew-on", "verify": expected,
                                "port": 47002})
        self.assertEqual(remote_preview.verify_value(TOKEN), expected)
        with self.assertRaises(Error) as caught:
            remote_preview.upstream({"url": "https://10.42.1.50:8943", "token": TOKEN}, 47002, None)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "app_unreachable"))

    def test_routes_and_scopes(self):
        self.assertEqual(required_scope("GET", "/v1/runs/r1/app"), "read")
        self.assertEqual(required_scope("POST", "/v1/runs/r1/app"), "execute")
        self.assertEqual(required_scope("POST", "/v1/runs/r1/app/open"), "read")


class AppTests(PreviewCase):
    def test_who_may_start_and_what_is_recorded(self):
        started = self.service.app_start(self.b, self.rid)
        self.assertEqual(started["status"], "starting")
        self.assertIn({"run_id": self.rid, "action": "start"}, FakeAppWorker.calls)
        view = self.running()
        self.assertEqual((view["status"], view["openable"], view["port"]), ("running", True, 47001))
        self.assertEqual(view["started_by"]["username"], "bob")
        self.assertTrue(view["log"])
        self.assertGreater(view["idle_stop_at"], time.time() + 1700)
        (record,) = self.events("app.started")
        self.assertEqual((record["actor"]["username"], record["port"]), ("bob", 47001))
        with self.assertRaises(Error) as caught:
            self.service.app_start(self.e, self.rid)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.service.app_stop(self.b, self.rid), {"status": "stopped"})
        self.assertEqual(self.row()["status"], "stopped")
        self.assertEqual(self.events("app.stopped")[0]["reason"], "user")

    def test_a_read_only_token_cannot_start(self):
        with self.platform() as port:
            status, _, body = self.raw(port, "POST", f"/v1/runs/{self.rid}/app",
                                       {"Authorization": "Bearer " + self.token("bob", ["read"])}, {"action": "start"})
            self.assertEqual(status, 403, body)
            status, _, body = self.raw(port, "POST", f"/v1/runs/{self.rid}/app",
                                       {"Authorization": "Bearer " + self.tokens["bob"]}, {"action": "start"})
            self.assertEqual((status, json.loads(body)["status"]), (200, "starting"))
            status, _, body = self.raw(port, "GET", f"/v1/runs/{self.rid}/app", {"Authorization": "Bearer " + self.token("bob", ["read"])})
            self.assertEqual((status, json.loads(body)["status"]), (200, "starting"))

    def test_start_is_refused_while_busy_at_the_limit_or_on_an_old_worker(self):
        self.settle(self.rid, "running")
        with self.assertRaises(Error) as caught:
            self.service.app_start(self.b, self.rid)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "app_busy"))
        self.settle(self.rid)
        others = [self.start(self.a, f"start-000000010{i}", team_id=self.tid, worker_id="w-z")["id"] for i in range(3)]
        with self.db.transaction() as db:
            for rid in others:
                db.execute("INSERT INTO apps(run_id,worker_id,status,port,upstream,started,last_access) VALUES(?,?,?,?,?,?,?)",
                           (rid, "w-z", "running", 47100, dumps({"instance": "i1"}), time.time(), time.time()))
        with self.assertRaises(Error) as caught:
            self.service.app_start(self.b, self.rid)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "app_limit"))
        with self.db.transaction() as db:
            db.execute("DELETE FROM apps")
        FakeAppWorker.mode = "old"
        with self.assertRaises(Error) as caught:
            self.service.app_start(self.b, self.rid)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "app_unsupported"))
        self.assertEqual(self.service.app(self.b, self.rid)["status"], "unsupported")

    def test_a_worker_host_without_a_preview_port_is_refused_before_anything_starts(self):
        with patch.object(remote_preview, "LOOPBACK", frozenset()):   # this Worker now counts as a Worker host
            with self.assertRaises(Error) as caught:
                self.service.app_start(self.b, self.rid)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "app_unreachable"))
        self.assertNotIn("start", [call["action"] for call in FakeAppWorker.calls])

    def test_the_record_follows_the_worker(self):
        self.running()
        FakeAppWorker.state = {**FakeAppWorker.state, "status": "failed", "error": "进程 app 已退出（代码 1）"}
        view = self.service.app(self.b, self.rid)
        self.assertEqual((view["status"], view["error"], view["openable"]), ("failed", "进程 app 已退出（代码 1）", False))
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.events("app.stopped")[-1]["reason"], "worker")

    def test_open_needs_a_ready_preview_and_a_preview_domain(self):
        with self.platform() as port:
            status, _, body = self.raw(port, "POST", f"/v1/runs/{self.rid}/app/open",
                                       {"Authorization": "Bearer " + self.tokens["bob"]}, {})
            self.assertEqual((status, json.loads(body)["code"]), (409, "app_preview_unconfigured"))
        with self.platform(preview_url="https://preview.example") as port:
            self.service.app_start(self.b, self.rid)
            status, _, body = self.raw(port, "POST", f"/v1/runs/{self.rid}/app/open",
                                       {"Authorization": "Bearer " + self.tokens["bob"]}, {})
            self.assertEqual((status, json.loads(body)["code"]), (409, "app_not_ready"))
            FakeAppWorker.state = {**FakeAppWorker.state, "status": "running"}
            status, _, body = self.raw(port, "POST", f"/v1/runs/{self.rid}/app/open",
                                       {"Authorization": "Bearer " + self.token("bob", ["read"])}, {})
            answer = json.loads(body)
            self.assertEqual((status, answer["expires_in"]), (200, 600))
            self.assertTrue(answer["url"].startswith("https://preview.example/_kirocrew/open/"), answer)
            self.assertEqual(self.raw(port, "POST", f"/v1/runs/{self.rid}/app/open",
                                      {"Authorization": "Bearer " + self.tokens["eve"]}, {})[0], 404)


class PreviewDomainTests(PreviewCase):
    def test_opening_sets_a_cookie_for_the_preview_domain_once(self):
        self.running()
        ticket = remote_preview.open_ticket(self.rid, self.b["id"], self.key)
        with self.platform() as port:
            status, headers, page = self.raw(port, "GET", "/_kirocrew/open/" + ticket)
            self.assertEqual(status, 200)
            self.assertEqual(headers["clear-site-data"], '"cache", "cookies", "storage", "executionContexts"')
            self.assertIn(f"/_kirocrew/enter/{ticket}".encode(), page)
            status, headers, _ = self.raw(port, "GET", "/_kirocrew/enter/" + ticket)
            self.assertEqual((status, headers["location"]), (302, "/"))
            cookie = headers["set-cookie"]
            for part in ("__Host-kirocrew_preview=", "Path=/", "Secure", "HttpOnly", "SameSite=Lax", "Max-Age=28800"):
                self.assertIn(part, cookie)
            value = cookie.split(";")[0].split("=", 1)[1]
            status, _, page = self.raw(port, "GET", "/_kirocrew/enter/" + ticket)
            self.assertEqual(status, 403)
            self.assertIn("已使用".encode(), page)
            status, headers, body = self.raw(port, "GET", "/v1/preview-auth",
                                             {"Cookie": f"theme=dark; __Host-kirocrew_preview={value}"})
            self.assertEqual((status, body), (204, b""))
            self.assertEqual((headers["x-preview-upstream"], headers["x-preview-port"]), ("http://127.0.0.1:47001", "47001"))
            self.assertNotIn("x-preview-verify", headers)
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth")[0], 401)
            status, _, page = self.raw(port, "GET", "/_kirocrew/denied")
            self.assertEqual(status, 403)
            self.assertIn("远程预览".encode(), page)

    def test_a_worker_host_upstream_carries_the_check_header(self):
        FakeAppWorker.via = {"port": 8945, "name": "w1.worker.kirocrew-on"}
        with patch.object(remote_preview, "LOOPBACK", frozenset()):
            self.running()
            value = remote_preview.cookie(self.rid, self.b["id"], self.key)
            with self.platform() as port:
                status, headers, _ = self.raw(port, "GET", "/v1/preview-auth", {"Cookie": "__Host-kirocrew_preview=" + value})
        self.assertEqual(status, 204)
        self.assertEqual((headers["x-preview-upstream"], headers["x-preview-port"], headers["x-preview-name"]),
                         ("https://127.0.0.1:8945", "47001", "w1.worker.kirocrew-on"))
        self.assertEqual(headers["x-preview-verify"], hmac.new(TOKEN.encode(), b"kirocrew-preview", hashlib.sha256).hexdigest())

    def test_preview_auth_is_only_for_the_local_proxy(self):
        self.running()
        value = remote_preview.cookie(self.rid, self.b["id"], self.key)
        secret = "s" * 40
        with self.platform(origin_secret=secret, allowed_hosts=("on.example",)) as port:
            for headers in ({"X-Origin-Verify": secret},                                  # through CloudFront
                            {"X-Origin-Verify": secret, "Host": "on.example"},
                            {"Host": "on.example"}):
                for path in ("/v1/preview-auth", "/_kirocrew/open/x", "/_kirocrew/denied"):
                    status, answer, _ = self.raw(port, "GET", path, {"Cookie": "__Host-kirocrew_preview=" + value, **headers})
                    self.assertIn(status, {403, 404}, (path, headers))
                    self.assertFalse([h for h in answer if h.startswith("x-preview")], (path, headers))
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth", {"Cookie": "__Host-kirocrew_preview=" + value,
                                                                     "X-Preview-Gate": remote_preview.gate_value(secret)})[0], 204)

    def test_a_cookie_stops_working_when_access_is_revoked(self):
        self.running()
        self.assertEqual(self.service.app_auth(self.rid, self.b["id"])["port"], 47001)
        self.service.membership(self.a, self.tid, {"username": "bob"}, remove=True)
        with self.assertRaises(Error) as caught:
            self.service.app_auth(self.rid, self.b["id"])
        self.assertEqual((caught.exception.status, caught.exception.code), (403, "app_forbidden"))
        self.assertEqual(self.service.app_auth(self.rid, self.a["id"])["port"], 47001)
        self.service.app_stop(self.a, self.rid)
        with self.assertRaises(Error) as caught:
            self.service.app_auth(self.rid, self.a["id"])
        self.assertEqual((caught.exception.status, caught.exception.code), (403, "app_stopped"))

    def test_access_is_written_at_most_once_a_minute(self):
        self.running()
        now = time.time() + 100
        self.service.app_auth(self.rid, self.b["id"], now=now)
        self.assertEqual(self.row()["last_access"], now)
        self.service.app_auth(self.rid, self.b["id"], now=now + 30)
        self.assertEqual(self.row()["last_access"], now)
        self.service.app_auth(self.rid, self.b["id"], now=now + 61)
        self.assertEqual(self.row()["last_access"], now + 61)


class SessionOriginTests(PreviewCase):
    """D36 phase 3: each Session's preview on its own origin, https://<run-id>.<preview_domain>/ (one wildcard certificate);
    the host names the Session, and the cookie and the opening link must name the same one."""
    DOMAIN = "preview.example"

    def host(self, rid=None):
        return f"{rid or self.rid}.{self.DOMAIN}"

    def test_the_host_names_the_session(self):
        rid = self.rid
        self.assertEqual(remote_preview.session_host(f"{rid}.preview.example", self.DOMAIN), rid)
        self.assertEqual(remote_preview.session_host(f"{rid.upper()}.Preview.Example:443", self.DOMAIN), rid)
        for other in ("d12ngj1vip1ugn.cloudfront.net", "", None, "preview.example.evil.test", "xpreview.example"):
            self.assertIsNone(remote_preview.session_host(other, self.DOMAIN), other)
        self.assertIsNone(remote_preview.session_host(f"{rid}.preview.example", ""))   # no per-session origins configured
        for wrong in (f"a.{rid}.preview.example", "run-zz.preview.example", "preview.example", ".preview.example"):
            with self.assertRaises(Error, msg=wrong) as caught:
                remote_preview.session_host(wrong, self.DOMAIN)
            self.assertEqual(caught.exception.status, 404, wrong)
        self.assertEqual(remote_preview.session_url(self.DOMAIN, rid), f"https://{rid}.preview.example")

    def test_the_preview_domain_is_a_plain_lowercase_name(self):
        from pathlib import Path
        from octopus.config import ConfigError, _layer
        self.assertEqual(_layer({"preview_domain": "preview.tinyboat.blog"}, Path("/")), {"preview_domain": "preview.tinyboat.blog"})
        for wrong in ("https://preview.tinyboat.blog", "*.preview.tinyboat.blog", "Preview.Example", "preview", "a..b"):
            with self.assertRaises(ConfigError, msg=wrong):
                _layer({"preview_domain": wrong}, Path("/"))

    def test_the_opening_link_goes_to_the_sessions_own_origin(self):
        FakeAppWorker.state = {**FakeAppWorker.state, "status": "running"}
        self.service.app_start(self.b, self.rid)
        FakeAppWorker.state = {**FakeAppWorker.state, "status": "running"}
        with self.platform(preview_domain=self.DOMAIN) as port:
            status, _, body = self.raw(port, "POST", f"/v1/runs/{self.rid}/app/open",
                                       {"Authorization": "Bearer " + self.tokens["bob"]}, {})
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["url"].startswith(f"https://{self.host()}/_kirocrew/open/"), body)

    def test_the_cookie_works_only_on_its_sessions_host(self):
        self.running()
        other = self.start(self.a, request="start-0000000077", team_id=self.tid, worker_id="w-z", name="别的")["id"]
        value = remote_preview.cookie(self.rid, self.b["id"], self.key)
        cookie = {"Cookie": "__Host-kirocrew_preview=" + value}
        with self.platform(preview_domain=self.DOMAIN) as port:
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth", {**cookie, "X-Preview-Host": self.host()})[0], 204)
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth", {**cookie, "X-Preview-Host": self.host(other)})[0], 403)
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth",
                                      {**cookie, "X-Preview-Host": "a." + self.host()})[0], 403)
            # the distribution's own cloudfront.net name keeps the single-origin mode
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth",
                                      {**cookie, "X-Preview-Host": "d12ngj1vip1ugn.cloudfront.net"})[0], 204)

    def test_an_opening_link_is_followed_only_on_its_sessions_host(self):
        self.running()
        other = self.start(self.a, request="start-0000000078", team_id=self.tid, worker_id="w-z", name="别的")["id"]
        ticket = remote_preview.open_ticket(self.rid, self.b["id"], self.key)
        with self.platform(preview_domain=self.DOMAIN) as port:
            for path in ("/_kirocrew/open/", "/_kirocrew/enter/"):
                status, headers, _ = self.raw(port, "GET", path + ticket, {"X-Preview-Host": self.host(other)})
                self.assertEqual(status, 403, path)
                self.assertNotIn("set-cookie", headers)
            status, headers, page = self.raw(port, "GET", "/_kirocrew/open/" + ticket, {"X-Preview-Host": self.host()})
            self.assertEqual(status, 200)
            self.assertNotIn("clear-site-data", headers)   # the origin is this Session's own: its app keeps its storage
            status, headers, _ = self.raw(port, "GET", "/_kirocrew/enter/" + ticket, {"X-Preview-Host": self.host()})
            self.assertEqual(status, 302)
            self.assertTrue(headers["set-cookie"].startswith("__Host-kirocrew_preview="))
            ticket = remote_preview.open_ticket(self.rid, self.b["id"], self.key)   # the shared origin still clears
            status, headers, _ = self.raw(port, "GET", "/_kirocrew/open/" + ticket,
                                          {"X-Preview-Host": "d12ngj1vip1ugn.cloudfront.net"})
            self.assertEqual(status, 200)
            self.assertIn("storage", headers["clear-site-data"])


class LifecycleTests(PreviewCase):
    def test_an_idle_preview_is_stopped(self):
        self.running()
        last = self.row()["last_access"]
        self.service.sweep_apps(now=last + 29 * 60)
        self.assertEqual(self.row()["status"], "running")
        self.service.sweep_apps(now=last + 31 * 60)
        self.assertEqual(self.row()["status"], "stopped")
        self.assertEqual(FakeAppWorker.calls[-1]["action"], "stop")
        (record,) = self.events("app.stopped")
        self.assertEqual((record["reason"], record["actor"]["kind"]), ("idle", "engine"))

    def test_closing_the_session_stops_its_preview(self):
        self.running()
        self.service.operate(self.a, self.rid, "close", {"request_id": "close-00000001", "expected_version": 1})
        self.assertEqual(self.row()["status"], "stopped")
        self.assertEqual(self.events("app.stopped")[-1]["reason"], "session")

    def test_continuing_the_session_stops_its_preview(self):
        self.running()
        self.service.operate(self.b, self.rid, "continue", {"request_id": "continue-0000001", "expected_version": 1,
                                                            "task_id": "code"})
        self.assertEqual(self.row()["status"], "stopped")
        self.assertEqual(FakeAppWorker.calls[-1]["action"], "stop")

    def test_an_unreachable_worker_does_not_block_closing(self):
        self.running()
        self.worker.shutdown()
        self.worker.server_close()
        result = self.service.operate(self.a, self.rid, "close", {"request_id": "close-00000002", "expected_version": 1})
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.row()["status"], "running")   # retried by the next sweep (the session is closing)


class ReviewFixTests(PreviewCase):
    """The final review's findings on the platform side (D36 phase 2)."""

    def other(self):
        rid = self.start(self.a, "start-0000000200", team_id=self.tid, worker_id="w-z", name="另一个")["id"]
        self.settle(rid)
        return rid

    def test_a_worker_restart_never_routes_an_old_cookie_to_another_app(self):
        self.running()
        other = self.other()
        FakeAppWorker.instance = "i2"   # the Worker restarted: its previews are gone, the port is free again
        FakeAppWorker.state = {**FakeAppWorker.state, "status": "stopped"}
        self.service.app_start(self.b, other)
        self.assertEqual(self.row()["status"], "stopped")
        self.assertEqual(self.events("app.stopped")[-1]["reason"], "worker")
        with self.assertRaises(Error) as caught:
            self.service.app_auth(self.rid, self.b["id"])
        self.assertEqual(caught.exception.code, "app_stopped")
        self.assertEqual(json.loads(self.row(other)["upstream"])["instance"], "i2")

    def test_the_sweep_notices_a_worker_restart(self):
        self.running()
        FakeAppWorker.instance = "i2"
        FakeAppWorker.state = {**FakeAppWorker.state, "status": "stopped"}
        self.service.sweep_apps()
        self.assertEqual(self.row()["status"], "stopped")

    def test_a_retired_workers_previews_end_without_calling_it(self):
        self.running()
        with self.db.transaction() as db:
            db.execute("UPDATE workers SET retired=1 WHERE id='w-z'")
        FakeAppWorker.calls = []
        self.service.sweep_apps()
        self.assertEqual(self.row()["status"], "stopped")
        self.assertEqual(self.service.app(self.b, self.rid)["status"], "stopped")
        self.assertEqual(FakeAppWorker.calls, [])

    def test_a_closing_session_cuts_its_preview_even_with_its_worker_away(self):
        self.running()
        self.worker.shutdown()
        self.worker.server_close()
        self.service.operate(self.a, self.rid, "delete", {"request_id": "delete-0000001", "expected_version": 1,
                                                          "confirmation": self.rid})
        with self.assertRaises(Error) as caught:
            self.service.app_auth(self.rid, self.b["id"])
        self.assertEqual(caught.exception.code, "app_stopped")

    def test_what_a_worker_reports_is_checked(self):
        loopback = {"url": "http://127.0.0.1:8928", "token": TOKEN}
        host = {"url": "https://10.42.1.50:8943", "token": TOKEN}
        for config, port, via in ((loopback, 8918, None), (loopback, "47001", None), (loopback, 48000, None),
                                  (host, 47001, {"port": 8945, "name": "w1\r\nX-Evil: 1"}),
                                  (host, 47001, {"port": "8945; x", "name": "w1.worker.kirocrew-on"})):
            with self.assertRaises(Error) as caught:
                remote_preview.upstream(config, port, via)
            self.assertEqual(caught.exception.code, "worker_invalid", (port, via))

    def test_status_and_stop_calls_do_not_wait_long(self):
        from octopus import transport
        seen = []
        real = transport.worker_request

        def watch(config, path, body=None, timeout=35):
            seen.append((body["action"], timeout))
            return real(config, path, body, timeout=timeout)
        with patch("octopus.transport.worker_request", side_effect=watch):
            self.running()
            self.service.app_stop(self.b, self.rid)
        timeouts = dict(seen)
        self.assertLessEqual(timeouts["status"], 5)
        self.assertLessEqual(timeouts["stop"], 10)

    def test_the_local_endpoints_need_the_proxys_gate_once_an_origin_secret_is_set(self):
        self.running()
        value, secret = remote_preview.cookie(self.rid, self.b["id"], self.key), "s" * 40
        with self.platform(origin_secret=secret) as port:
            cookie = {"Cookie": "__Host-kirocrew_preview=" + value}
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth", cookie)[0], 404)   # an agent on this host
            self.assertEqual(self.raw(port, "GET", "/v1/preview-auth", {**cookie, "X-Preview-Gate": "wrong"})[0], 404)
            status, headers, _ = self.raw(port, "GET", "/v1/preview-auth",
                                          {**cookie, "X-Preview-Gate": remote_preview.gate_value(secret)})
            self.assertEqual((status, headers["x-preview-port"]), (204, "47001"))
            self.assertEqual(self.raw(port, "GET", "/_kirocrew/denied")[0], 404)


if __name__ == "__main__":
    unittest.main()
