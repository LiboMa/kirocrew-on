"""D62 (owner 2026-10-22): an API token lasts 7, 30 or 365 days, or never expires — chosen when it is issued. A token that
carries admin lasts at most 30 days (the security floor). A token that never expires reads expires: null."""
import tempfile
import time
import unittest
from unittest import mock

from octopus import cli, connect
from octopus.security import NEVER, Error, authenticate, issue_token
from tests.test_access import AccessCase

DAY = 86400


class IssueTests(AccessCase):
    def issued(self, **kwargs):
        with self.db.transaction() as db:
            return issue_token(db, self.b["id"], ["read", "execute", "manage"], "Coding client", **kwargs)

    def test_the_four_choices(self):
        for choice, days in (("7d", 7), ("30d", 30), ("365d", 365)):
            with self.subTest(choice):
                self.assertAlmostEqual(self.issued(expires_in=choice)["expires"], time.time() + days * DAY, delta=60)
        token = self.issued(expires_in="never")
        self.assertIsNone(token["expires"])
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT expires FROM tokens WHERE id=?", (token["id"],)).fetchone()[0], NEVER)
            self.assertEqual(authenticate(db, token["token"])["id"], self.b["id"])

    def test_anything_else_is_refused(self):
        for choice in ("1y", "90d", 7, ""):
            with self.subTest(choice), self.assertRaises(Error) as caught:
                self.issued(expires_in=choice)
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_an_admin_token_lasts_thirty_days_at_most(self):
        with self.db.transaction() as db:
            for choice in ("365d", "never"):
                with self.subTest(choice), self.assertRaises(Error) as caught:
                    issue_token(db, self.a["id"], ["read", "admin"], "admin client", expires_in=choice)
                self.assertEqual((caught.exception.status, caught.exception.code), (403, "forbidden"))
            self.assertAlmostEqual(issue_token(db, self.a["id"], ["read", "admin"], "admin", expires_in="30d")["expires"],
                                   time.time() + 30 * DAY, delta=60)

    def test_the_old_ttl_still_works_as_before(self):   # clients from before D62 send ttl in seconds, at most 30 days
        self.assertAlmostEqual(self.issued(ttl=7 * DAY)["expires"], time.time() + 7 * DAY, delta=60)
        self.assertAlmostEqual(self.issued(ttl=400 * DAY)["expires"], time.time() + 30 * DAY, delta=60)

    def test_over_http_and_in_the_lists(self):
        with self.http() as call:
            status, token = call("POST", "/v1/tokens", self.tokens["bob"], {"label": "Kiro CLI", "scopes": ["read"],
                                                                            "expires_in": "never", "ttl": 30 * DAY})
            self.assertEqual((status, token["expires"]), (201, None))
            listed = {t["id"]: t["expires"] for t in call("GET", "/v1/tokens", self.tokens["bob"])[1]}
            self.assertIsNone(listed[token["id"]])
            self.assertEqual(call("GET", "/v1/me", token["token"])[0], 200)
            status, tokens = call("GET", f"/v1/admin/users/{self.b['id']}/tokens", self.tokens["alice"])
            self.assertEqual(status, 200)
            self.assertIsNone(next(t for t in tokens if t["id"] == token["id"])["expires"])
            self.assertEqual(call("POST", "/v1/tokens", self.tokens["bob"], {"expires_in": "forever"})[0], 400)

    def test_the_api_description_offers_the_choices(self):
        from octopus.schema import openapi
        field = openapi()["components"]["schemas"]["Token"]["properties"]["expires_in"]
        self.assertEqual(field["enum"], ["7d", "30d", "365d", "never"])


class ClientTests(unittest.TestCase):
    def fake(self, calls):
        class Fake:
            def __init__(self, url, token):
                self.token = token

            def request(self, method, path, body=None):
                calls.append((method, path, body))
                if path == "/v1/login":
                    return {"token": "oct_login", "id": "t-login", "scopes": ["read", "execute", "manage"]}
                if path == "/v1/me":
                    return {"admin": 1}
                if path == "/v1/tokens":
                    return {"token": "oct_issued", "id": "t1", "expires": None}
                return {"ok": True}
        return Fake

    def test_connect_asks_for_the_chosen_expiry_and_keeps_ttl_for_older_platforms(self):
        calls = []
        connect.issue_token(self.fake(calls), "https://p", "claude", "bob", "pw", expires="never")
        self.assertEqual(next(b for _, p, b in calls if p == "/v1/tokens"),
                         {"label": "claude MCP", "scopes": ["read", "execute", "manage"], "ttl": 30 * DAY, "expires_in": "never"})
        with self.assertRaisesRegex(ValueError, "30 天"):
            connect.issue_token(self.fake([]), "https://p", "claude", "bob", "pw", admin=True, expires="365d")

    def test_connect_says_how_long_the_token_lasts(self):   # final review C1: it printed 30 days for a never-expiring one
        issued = connect.issue_token(self.fake([]), "https://p", "claude", "bob", "pw", expires="never")
        self.assertIsNone(issued["expires"])
        self.assertEqual(connect.lifetime(None), "Token 永不过期")
        self.assertEqual(connect.lifetime(time.mktime((2027, 1, 31, 12, 0, 0, 0, 0, -1))), "Token 有效期至 2027-01-31")

    def test_cli_login_asks_for_the_chosen_expiry(self):
        calls = []
        with tempfile.TemporaryDirectory() as home, mock.patch.object(cli, "Client", self.fake(calls)):
            result = cli.login("https://p", "bob", "pw", home=home, expires="365d")
        self.assertEqual(next(b for _, p, b in calls if p == "/v1/tokens")["expires_in"], "365d")
        self.assertIsNone(result["expires"])

    def test_the_commands_take_expires(self):
        self.assertEqual(cli.parser().parse_args(["login", "--expires", "never"]).expires, "never")
        self.assertEqual(cli.parser().parse_args(["login"]).expires, "30d")
        from octopus.__main__ import build_parser, settings_from_argv
        settings, _ = settings_from_argv([], environ={})
        args = build_parser(settings, environ={}).parse_args(["connect", "claude", "--url", "https://p", "--expires", "7d"])
        self.assertEqual(args.expires, "7d")


if __name__ == "__main__":
    unittest.main()
