import json
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



class MoreClientsTests(unittest.TestCase):
    """M3-1 (2026-10-21): Codex and Kiro IDE are configured by the command too, keeping what their files already hold."""
    token = "secret-token-" + "y" * 30

    def test_codex_gets_its_server_in_config_toml_beside_what_is_there(self):
        import tomllib
        with tempfile.TemporaryDirectory() as home:
            config = Path(home, ".codex/config.toml")
            config.parent.mkdir()
            config.write_text('model = "o4"\n\n[mcp_servers.kirocrew-on]\ncommand = "old"\n\n[mcp_servers.kirocrew-on.env]\n'
                              'OCTOPUS_API_TOKEN = "old"\n\n[mcp_servers.other]\ncommand = "other"\n')
            dry = connect.connect("codex", "https://on.example/", self.token, home=home, dry_run=True)
            self.assertIn('command = "old"', config.read_text())   # a dry run writes nothing
            actions = connect.connect("codex", "https://on.example/", self.token, home=home)
            value = tomllib.loads(config.read_text())
            self.assertEqual(value["model"], "o4")
            self.assertEqual(value["mcp_servers"]["other"], {"command": "other"})
            server = value["mcp_servers"]["kirocrew-on"]
            self.assertEqual(server["args"], ["-m", "octopus", "mcp", "--url", "https://on.example/"])
            self.assertEqual(server["env"]["OCTOPUS_API_TOKEN"], self.token)
            self.assertEqual(len(list(config.parent.glob("config.toml.bak-*"))), 1)
            self.assertTrue(Path(home, ".codex/skills/kirocrew-on/SKILL.md").is_file())
            self.assertNotIn(self.token, "\n".join(dry + actions))

    def test_kiro_ide_gets_its_server_in_the_workspace_mcp_json(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            settings = Path(project, ".kiro/settings/mcp.json")
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
            actions = connect.connect("kiro-ide", "https://on.example/", self.token, home=home, project=project,
                                      run=lambda *a, **k: self.fail("Kiro IDE needs no kiro-cli"))
            value = json.loads(settings.read_text())
            self.assertEqual(set(value["mcpServers"]), {"other", "kirocrew-on"})
            self.assertEqual(value["mcpServers"]["kirocrew-on"]["env"]["OCTOPUS_API_TOKEN"], self.token)
            self.assertTrue(Path(project, ".kiro/skills/kirocrew-on/SKILL.md").is_file())
            self.assertIn(".gitignore", "\n".join(actions))
            self.assertNotIn(self.token, "\n".join(actions))

    def test_a_config_it_cannot_merge_is_said_plainly(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, ".codex").mkdir()
            Path(home, ".codex/config.toml").write_text("[[broken\n")
            done = subprocess.run([sys.executable, "-m", "octopus", "connect", "codex", "--url", "https://on.example",
                                   "--token", self.token, "--dry-run"], capture_output=True, text=True,
                                  cwd=Path(__file__).resolve().parents[1], env={**os.environ, "HOME": home})
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("无法把 MCP 服务器合并进 ~/.codex/config.toml", done.stderr)
            self.assertNotIn("Traceback", done.stderr)
            self.assertNotIn(self.token, done.stdout + done.stderr)

    def test_the_command_line_offers_the_four_clients(self):
        done = subprocess.run([sys.executable, "-m", "octopus", "connect", "--help"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[1])
        for client in ("claude", "kiro", "codex", "kiro-ide"):
            self.assertIn(client, done.stdout)

class IssueTokenTests(unittest.TestCase):
    def factory(self, admin=False, fail=False, logout_fails=False):
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
                if path == "/v1/logout" and logout_fails:
                    raise ApiError(502, "http_error", "Bad Gateway")
                return {"ok": True}
        return Fake, calls

    def issued(self, calls):
        return next(body for _, _, path, body in calls if path == "/v1/tokens")

    def test_a_thirty_day_token_with_manage_and_the_login_is_logged_out(self):
        Fake, calls = self.factory()
        self.assertEqual(connect.issue_token(Fake, URL, "claude", "alice", "pw")["token"], "oct_issued")
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

    def test_a_failed_logout_does_not_hide_the_issued_token(self):
        Fake, calls = self.factory(logout_fails=True)
        issued = connect.issue_token(Fake, URL, "claude", "alice", "pw")
        self.assertEqual(issued["token"], "oct_issued")
        self.assertIn("登出", issued["warning"])

    def test_revoking_an_issued_token_uses_its_id(self):
        Fake, calls = self.factory()
        connect.revoke(Fake, URL, {"token": "oct_issued", "id": "t1"})
        self.assertEqual(calls[-1][:3], ("oct_issued", "DELETE", "/v1/tokens/t1"))

    def cli(self, *args, env=None, path=None):
        base = {k: v for k, v in os.environ.items() if k not in {"OCTOPUS_API_TOKEN", "OCTOPUS_PASSWORD"}}
        if path:
            base["PATH"] = path + os.pathsep + base["PATH"]
        return subprocess.run([sys.executable, "-m", "octopus", "connect", *args], cwd=Path(__file__).resolve().parent.parent,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, env={**base, **(env or {})}, timeout=30)

    def test_admin_with_a_supplied_token_is_refused(self):
        result = self.cli("claude", "--url", URL, "--admin", env={"OCTOPUS_API_TOKEN": "oct_supplied"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--admin", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_failing_client_command_never_prints_the_token(self):
        with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as home:
            fake = Path(bin_dir, "claude")
            fake.write_text("#!/bin/sh\nexit 1\n")
            fake.chmod(0o755)
            result = self.cli("claude", "--url", URL, "--token", "oct_SECRETVALUE", env={"HOME": home}, path=bin_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("写入 claude 配置失败", result.stderr)
        self.assertNotIn("oct_SECRETVALUE", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_the_skill_explains_client_and_environment_errors(self):
        text = connect.skill(URL)
        self.assertIn("client_error", text)
        self.assertIn("worker_unavailable", text)

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
