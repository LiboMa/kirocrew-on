"""D67 (owner 2026-10-22: 「配置支持 Cookie，以及登录 Session 持久化」「增加 Session 持久化，这个是 Web 应用最基础的功能了」):
a login also sets an HttpOnly, Secure, SameSite=Strict session cookie — kept 30 days when the person stays signed in (the
console asks by default), until the browser closes otherwise — so a new tab or a reopened browser is still signed in. The Authorization header
still comes first; a write made on the cookie alone must come from the console's own origin; logging out ends both."""
import json
import time
import urllib.error
import urllib.request

from tests.test_access import PASSWORD, AccessCase

DAY = 86400


class CookieLoginTests(AccessCase):
    def raw(self, call, method, path, body=None, headers=None):
        request = urllib.request.Request(call.base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={**({"Content-Type": "application/json"} if body is not None else {}),
                                                  **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, response.headers, json.load(response)

    def login(self, call, **extra):
        status, headers, body = self.raw(call, "POST", "/v1/login", {"username": "bob", "password": PASSWORD, **extra})
        self.assertEqual(status, 200)
        return headers.get("Set-Cookie", ""), body

    def cookie(self, set_cookie):
        return set_cookie.split(";", 1)[0]   # octopus_session=<token>

    def test_staying_signed_in_is_a_thirty_day_cookie(self):
        with self.http() as call:
            set_cookie, body = self.login(call, remember=True)
        for flag in ("octopus_session=", "HttpOnly", "Secure", "SameSite=Strict", "Path=/", f"Max-Age={30 * DAY}"):
            self.assertIn(flag, set_cookie)
        self.assertAlmostEqual(body["expires"], time.time() + 30 * DAY, delta=60)

    def test_otherwise_the_cookie_ends_with_the_browser(self):
        with self.http() as call:
            set_cookie, body = self.login(call, remember=False)
        self.assertIn("HttpOnly", set_cookie)
        self.assertNotIn("Max-Age", set_cookie)
        self.assertAlmostEqual(body["expires"], time.time() + DAY, delta=60)

    def test_a_login_that_does_not_say_keeps_its_old_lifetime(self):   # octopus login / bootstrap / connect send no remember
        with self.http() as call:
            set_cookie, body = self.login(call)
        self.assertNotIn("Max-Age", set_cookie)
        self.assertAlmostEqual(body["expires"], time.time() + DAY, delta=60)

    def test_remember_is_a_yes_or_no(self):
        with self.http() as call:
            status, _, body = self.raw(call, "POST", "/v1/login", {"username": "bob", "password": PASSWORD, "remember": "yes"})
        self.assertEqual(status, 400)

    def test_a_new_tab_is_signed_in_by_its_cookie(self):
        with self.http() as call:
            set_cookie, _ = self.login(call)
            status, _, me = self.raw(call, "GET", "/v1/me", headers={"Cookie": "other=1; " + self.cookie(set_cookie)})
        self.assertEqual((status, me["username"]), (200, "bob"))

    def test_a_write_on_the_cookie_alone_must_come_from_the_console(self):   # CSRF
        with self.http() as call:
            set_cookie, _ = self.login(call)
            host = call.base.split("//", 1)[1]
            body = {"request_id": "cookie-flow-0001", "name": "Cookie 工作流",
                    "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]}
            foreign = self.raw(call, "POST", "/v1/workflows", body, {"Cookie": self.cookie(set_cookie), "Origin": "https://evil.example"})
            missing = self.raw(call, "POST", "/v1/workflows", body, {"Cookie": self.cookie(set_cookie)})
            own = self.raw(call, "POST", "/v1/workflows", body, {"Cookie": self.cookie(set_cookie), "Origin": "http://" + host})
        self.assertEqual((foreign[0], foreign[2].get("code")), (403, "origin"))
        self.assertEqual((missing[0], missing[2].get("code")), (403, "origin"))
        self.assertIn(own[0], (200, 201))

    def test_the_authorization_header_comes_first(self):
        with self.http() as call:
            set_cookie, _ = self.login(call)
            status, _, me = self.raw(call, "GET", "/v1/me", headers={"Cookie": self.cookie(set_cookie),
                                                                    "Authorization": "Bearer " + self.tokens["eve"]})
        self.assertEqual(me["username"], "eve")

    def test_a_token_by_header_needs_no_origin(self):   # API clients and the CLI: no cookie, so no cross-site risk
        with self.http() as call:
            status, _ = call("POST", "/v1/workflows", self.tokens["bob"],
                             {"request_id": "cookie-flow-0002", "name": "头部 Token 工作流",
                              "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})
        self.assertIn(status, (200, 201))

    def test_logging_out_ends_the_cookie_and_its_token(self):
        with self.http() as call:
            set_cookie, _ = self.login(call)
            host = call.base.split("//", 1)[1]
            status, headers, _ = self.raw(call, "POST", "/v1/logout", {}, {"Cookie": self.cookie(set_cookie), "Origin": "http://" + host})
            self.assertEqual(status, 200)
            self.assertIn("Max-Age=0", headers.get("Set-Cookie", ""))
            self.assertEqual(self.raw(call, "GET", "/v1/me", headers={"Cookie": self.cookie(set_cookie)})[0], 401)


if __name__ == "__main__":
    import unittest
    unittest.main()
