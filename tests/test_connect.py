import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from octopus import connect
from octopus.client import ApiError

URL = "https://on.example"


class ConnectTest(unittest.TestCase):
    def test_both_clients_get_the_rendered_skill_and_the_token_stays_out_of_output(self):
        token = "secret-token-" + "x" * 30
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            calls = []
            run = lambda command, **kwargs: calls.append((command, kwargs.get("cwd")))
            actions = connect.connect("claude", "https://on.example/", token, home=home, run=run)
            actions += connect.connect("kiro", "https://on.example/", token, home=home, project=project, run=run)
            for path in (Path(home, ".claude/skills/kirocrew-on/SKILL.md"), Path(project, ".kiro/skills/kirocrew-on/SKILL.md")):
                text = path.read_text()
                self.assertTrue(text.startswith("---\nname: kirocrew-on\n"))
                self.assertIn("connect kiro --url https://on.example --username <用户名> --project", text)
                self.assertNotIn("{{OCTOPUS_URL}}", text)
                self.assertNotIn(token, text)
            kiro = next(command for command, _ in calls if command[0] == "kiro-cli")
            self.assertEqual(kiro[kiro.index("--scope") + 1], "workspace")
            self.assertEqual(calls[-1][1], Path(project))
            self.assertNotIn(token, "\n".join(actions))

    def test_install_skill_backs_up_a_different_existing_copy(self):
        with tempfile.TemporaryDirectory() as dest:
            target = Path(dest, "kirocrew-on/SKILL.md")
            target.parent.mkdir()
            target.write_text("old")
            connect.install_skill("https://on.example", dest)
            self.assertIn("https://on.example/?run=", target.read_text())
            self.assertEqual([p.read_text() for p in target.parent.glob("SKILL.md.bak-*")], ["old"])


class IssueTokenTests(unittest.TestCase):
    def factory(self, admin=False, fail=False):
        calls = []

        class Fake:
            def __init__(self, url, token):
                self.url, self.token = url, token

            def request(self, method, path, body=None):
                calls.append((self.token, method, path, body))
                if path == "/v1/login":
                    return {"token": "oct_login"}
                if path == "/v1/me":
                    return {"id": "u1", "username": "alice", "admin": int(admin), "scopes": ["read", "execute", "manage"]}
                if path == "/v1/tokens":
                    if fail:
                        raise ApiError(403, "forbidden", "不能扩大 Token scope")
                    return {"token": "oct_issued", "id": "t1"}
                return {"ok": True}
        return Fake, calls

    def issued(self, calls):
        return next(body for _, _, path, body in calls if path == "/v1/tokens")

    def test_a_thirty_day_token_with_manage_and_the_login_is_logged_out(self):
        Fake, calls = self.factory()
        self.assertEqual(connect.issue_token(Fake, URL, "claude", "alice", "pw"), "oct_issued")
        self.assertEqual(self.issued(calls), {"label": "claude MCP", "scopes": ["read", "execute", "manage"], "ttl": 30 * 86400})
        self.assertEqual(calls[-1][:3], ("oct_login", "POST", "/v1/logout"))

    def test_admin_tokens_need_an_admin_account_and_last_seven_days(self):
        Fake, calls = self.factory(admin=True)
        connect.issue_token(Fake, URL, "kiro", "alice", "pw", admin=True)
        self.assertEqual(self.issued(calls), {"label": "kiro MCP admin", "scopes": ["read", "execute", "manage", "admin"],
                                              "ttl": 7 * 86400})
        Fake, calls = self.factory(admin=False)
        with self.assertRaisesRegex(ValueError, "--admin 需要管理员账号"):
            connect.issue_token(Fake, URL, "kiro", "alice", "pw", admin=True)
        self.assertFalse(any(path == "/v1/tokens" for _, _, path, _ in calls))
        self.assertEqual(calls[-1][2], "/v1/logout")

    def test_the_login_token_is_logged_out_even_when_issuing_fails(self):
        Fake, calls = self.factory(fail=True)
        with self.assertRaises(ApiError):
            connect.issue_token(Fake, URL, "claude", "alice", "pw")
        self.assertEqual(calls[-1][:3], ("oct_login", "POST", "/v1/logout"))

    def test_connect_without_a_username_and_no_terminal_says_how(self):
        env = {k: v for k, v in os.environ.items() if k not in {"OCTOPUS_API_TOKEN", "OCTOPUS_PASSWORD"}}
        result = subprocess.run([sys.executable, "-m", "octopus", "connect", "claude", "--url", URL],
                                cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, env=env, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--username", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
