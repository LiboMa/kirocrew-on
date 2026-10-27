"""Peer review 2026-10-27: an administrator's API / MCP token (`octopus connect --admin`) let a Coding Agent create an
account or reset anyone's password, the owner's too, and then sign in as them. A token now records how it was issued:
'login' only from POST /v1/login (someone who typed the password), 'api' for every other way. Creating an account and
resetting a password need a console login; resetting one's own password goes through /v1/me/password with the current
one. Taking access away (deactivating, revoking tokens) still works through the API, for incident response."""
from octopus.security import issue_token
from tests.test_access import PASSWORD, AccessCase

NEW = "Another-password-456!"


class TokenKindTests(AccessCase):
    def login(self, call, name):
        status, made = call("POST", "/v1/login", None, {"username": name, "password": PASSWORD})
        self.assertEqual(status, 200)
        return made["token"]

    def kind(self, token_id):
        with self.db.read() as db:
            return db.execute("SELECT kind FROM tokens WHERE id=?", (token_id,)).fetchone()[0]

    def test_only_the_login_issues_a_login_token(self):
        with self.http() as call:
            console = self.login(call, "alice")
            status, me = call("GET", "/v1/me", console)
            status, made = call("POST", "/v1/tokens", console, {"label": "claude MCP admin"})
            self.assertEqual(status, 201)
        with self.db.read() as db:
            kinds = dict(db.execute("SELECT label, kind FROM tokens WHERE user_id=?", (self.a["id"],)).fetchall())
        self.assertEqual((kinds["Web login"], kinds["claude MCP admin"]), ("login", "api"))
        self.assertEqual(kinds["API"], "api")   # the fixture's token, as every token from before this change

    def test_an_api_token_cannot_create_accounts_or_reset_passwords(self):
        with self.http() as call:
            status, answer = call("POST", "/v1/users", self.tokens["alice"], {"username": "mallory", "password": PASSWORD})
            self.assertEqual((status, answer["code"]), (403, "console_login_required"))
            status, answer = call("POST", f"/v1/users/{self.b['id']}/password", self.tokens["alice"], {"password": NEW})
            self.assertEqual((status, answer["code"]), (403, "console_login_required"))
            console = self.login(call, "alice")
            self.assertEqual(call("POST", "/v1/users", console, {"username": "carol", "password": PASSWORD})[0], 201)
            self.assertEqual(call("POST", f"/v1/users/{self.b['id']}/password", console, {"password": NEW})[0], 200)

    def test_no_one_resets_their_own_password_without_the_current_one(self):
        with self.http() as call:
            console = self.login(call, "alice")
            status, answer = call("POST", f"/v1/users/{self.a['id']}/password", console, {"password": NEW})
            self.assertEqual((status, answer["code"]), (400, "use_me_password"))

    def test_taking_access_away_still_works_through_the_api_giving_it_back_does_not(self):
        with self.http() as call:
            self.assertEqual(call("POST", f"/v1/users/{self.e['id']}/active", self.tokens["alice"], {"active": False})[0], 200)
            status, answer = call("POST", f"/v1/users/{self.e['id']}/active", self.tokens["alice"], {"active": True})
            self.assertEqual((status, answer["code"]), (403, "console_login_required"))
            console = self.login(call, "alice")
            self.assertEqual(call("POST", f"/v1/users/{self.e['id']}/active", console, {"active": True})[0], 200)

    def test_granting_admin_needs_a_console_login_but_taking_it_away_does_not(self):
        # an existing account whose password someone else holds must not be raised through an Agent's token (peer review)
        with self.http() as call:
            status, answer = call("POST", f"/v1/users/{self.e['id']}/admin", self.tokens["alice"], {"admin": True})
            self.assertEqual((status, answer["code"]), (403, "console_login_required"))
            console = self.login(call, "alice")
            self.assertEqual(call("POST", f"/v1/users/{self.e['id']}/admin", console, {"admin": True})[0], 200)
            self.assertEqual(call("POST", f"/v1/users/{self.e['id']}/admin", self.tokens["alice"], {"admin": False})[0], 200)

    def test_regenerating_a_login_token_from_an_api_token_yields_an_api_token(self):
        with self.http() as call:
            console = self.login(call, "alice")
            with self.db.read() as db:
                login_id = db.execute("SELECT id FROM tokens WHERE user_id=? AND kind='login'", (self.a["id"],)).fetchone()[0]
            status, fresh = call("POST", f"/v1/tokens/{login_id}/regenerate", self.tokens["alice"], {})
            self.assertEqual(status, 200)
            self.assertEqual(self.kind(login_id), "api")
            status, answer = call("POST", "/v1/users", fresh["token"], {"username": "mallory", "password": PASSWORD})
            self.assertEqual((status, answer["code"]), (403, "console_login_required"))
            self.assertIsNotNone(console)

    def test_the_change_journal_names_the_console_by_how_the_token_was_issued(self):
        with self.db.transaction() as db:
            disguised = issue_token(db, self.b["id"], label="Web login ")["token"]   # an API token dressed as the console
        with self.db.read() as db:
            from octopus.security import authenticate
            self.assertEqual(authenticate(db, disguised)["client"], "api")
        with self.http() as call:
            console = self.login(call, "bob")
        with self.db.read() as db:
            from octopus.security import authenticate
            self.assertEqual(authenticate(db, console)["client"], "console")
