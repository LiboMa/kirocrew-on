"""Peer finding 2026-10-27: a token's label decides where its holder's actions are shown to come from (the change journal's
client, a Request's client). 「Web login」 is the console's own login; anyone holding a token — a Coding Agent with an MCP
token included — must not be able to mint one under that name and pass its actions off as the console's."""
from octopus.journal import client
from tests.test_access import PASSWORD, AccessCase


class TokenLabelTests(AccessCase):
    def test_the_console_login_label_is_reserved(self):
        with self.http() as call:
            for label in ("Web login", "web login", "  Web login  ", "WEB LOGIN"):
                with self.subTest(label=label):
                    status, answer = call("POST", "/v1/tokens", self.tokens["bob"], {"label": label})
                    self.assertEqual((status, answer["code"]), (400, "label_reserved"))
            status, made = call("POST", "/v1/tokens", self.tokens["bob"], {"label": "claude MCP"})   # what connect issues
            self.assertEqual(status, 201)
            self.assertEqual(call("POST", "/v1/login", None, {"username": "bob", "password": PASSWORD})[0], 200)
        with self.db.read() as db:
            labels = [r[0] for r in db.execute("SELECT label FROM tokens WHERE user_id=?", (self.b["id"],))]
        # only the console's own login carries it
        self.assertEqual([l for l in labels if l.strip().lower() == "web login"], ["Web login"])
        # the console is named by how a token was issued, never by its label (test_token_kind)
        self.assertEqual((client("Web login", "login"), client("Web login"), client("claude MCP")), ("console", "api", "mcp"))
