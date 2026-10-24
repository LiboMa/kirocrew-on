"""Owner 2026-10-24 (控制台改版): an existing API token can be regenerated — the same entry (name, scopes) gets a new secret,
shown once; the old one stops at once. Its expiry stays unless a new lifetime is chosen. The token the request itself
signs in with is not regenerated: that would sign the person out mid-request; the list says which one it is."""
import time
import unittest

from octopus.security import Error, authenticate
from tests.test_access import AccessCase


class RegenerateTests(AccessCase):
    def setUp(self):
        super().setUp()
        with self.http() as call:
            status, made = call("POST", "/v1/tokens", self.tokens["bob"], {"label": "Claude Code", "scopes": ["read", "execute"],
                                                                         "expires_in": "30d"})
        self.assertEqual(status, 201)
        self.made = made

    def test_the_same_entry_gets_a_new_secret_and_the_old_one_stops(self):
        with self.http() as call:
            status, again = call("POST", f"/v1/tokens/{self.made['id']}/regenerate", self.tokens["bob"], {})
            self.assertEqual(status, 200)
            self.assertEqual((again["id"], again["label"], again["scopes"]), (self.made["id"], "Claude Code", ["read", "execute"]))
            self.assertNotEqual(again["token"], self.made["token"])
            self.assertEqual(again["expires"], self.made["expires"])   # the expiry stays unless a lifetime is chosen
            self.assertEqual(call("GET", "/v1/me", self.made["token"])[0], 401)
            self.assertEqual(call("GET", "/v1/me", again["token"])[0], 200)
        with self.db.read() as db:
            with self.assertRaises(Error):
                authenticate(db, self.made["token"])

    def test_a_new_lifetime_may_be_chosen(self):
        with self.http() as call:
            status, again = call("POST", f"/v1/tokens/{self.made['id']}/regenerate", self.tokens["bob"], {"expires_in": "7d"})
        self.assertEqual(status, 200)
        self.assertAlmostEqual(again["expires"], time.time() + 7 * 86400, delta=60)

    def test_the_token_signing_in_now_is_not_regenerated(self):
        with self.http() as call:
            listed = call("GET", "/v1/tokens", self.tokens["bob"])[1]
            current = next(t for t in listed if t["current"])
            self.assertFalse(next(t for t in listed if t["id"] == self.made["id"])["current"])
            status, body = call("POST", f"/v1/tokens/{current['id']}/regenerate", self.tokens["bob"], {})
        self.assertEqual((status, body["code"]), (409, "token_in_use"))

    def test_a_narrower_token_cannot_regenerate_a_wider_one(self):
        # Review 2026-10-24: alice's manage-only token must not turn her admin token into a value it can use (A3)
        with self.http() as call:
            status, narrow = call("POST", "/v1/tokens", self.tokens["alice"], {"label": "agent", "scopes": ["read", "execute", "manage"],
                                                                              "expires_in": "30d"})
            self.assertEqual(status, 201)
            status, wide = call("POST", "/v1/tokens", self.tokens["alice"], {"label": "ops", "scopes": ["read", "admin"], "expires_in": "7d"})
            self.assertEqual(status, 201)
            status, body = call("POST", f"/v1/tokens/{wide['id']}/regenerate", narrow["token"], {})
            self.assertEqual((status, body["code"]), (403, "forbidden"))
            self.assertEqual(call("GET", "/v1/me", wide["token"])[0], 200)   # refused: the old value still works
            status, same = call("POST", f"/v1/tokens/{self.made['id']}/regenerate", self.tokens["bob"], {})
            self.assertEqual(status, 200)   # within its own scopes it may
            status, other = call("POST", "/v1/tokens", self.tokens["alice"], {"label": "reader", "scopes": ["read"], "expires_in": "7d"})
            self.assertEqual(call("POST", f"/v1/tokens/{other['id']}/regenerate", narrow["token"], {})[0], 200)

    def test_someone_elses_or_an_unknown_token_is_not_found(self):
        with self.http() as call:
            self.assertEqual(call("POST", f"/v1/tokens/{self.made['id']}/regenerate", self.tokens["eve"], {})[0], 404)
            self.assertEqual(call("POST", "/v1/tokens/token-unknown/regenerate", self.tokens["bob"], {})[0], 404)
            self.assertEqual(call("POST", f"/v1/tokens/{self.made['id']}/regenerate", self.tokens["bob"],
                                  {"expires_in": "forever"})[0], 400)


if __name__ == "__main__":
    unittest.main()
