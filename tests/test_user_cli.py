"""D58 M3-2: the user's octopus commands — login keeps a CLI token, submit works from the local repository (unpushed
commits stop it unless pushed or allowed), and the others answer in Chinese (or JSON) through the same calls as MCP."""
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from octopus import cli, local
from tests.test_local import RID, LocalCase, commit, git

VIEW = {"id": RID, "name": "登录", "status": "running", "version": 4, "input": "做登录",
        "attention": {"title": "正在执行", "detail": "实现中"},
        "tasks": [{"id": "code", "status": "pending", "spec": {"name": "实现", "tool": "claude", "model": "auto", "kind": "deliver",
                                                             "acceptance": ["完成"], "verify": ["npm test"]},
                   "mapping": {"label": "KiroCrew 规划"}, "attempts": []}]}


class Api:
    def __init__(self, view=VIEW):
        self.calls, self.view, self.url = [], dict(view), "https://k.example"

    def request(self, method, path, body=None, timeout=40):
        self.calls.append((method, path, body))
        if path == "/v1/login":
            return {"token": "oct_login", "id": "token-login", "scopes": ["read", "execute", "manage"], "expires": 1}
        if path == "/v1/tokens":
            return {"token": "oct_cli", "id": "token-cli", "scopes": body["scopes"], "expires": 2}
        if path == "/v1/tokens/token-login":
            return {"ok": True}
        if path == "/v1/runs" and method == "POST":
            return {"id": RID, "session_id": RID, "version": 1, "status": "preparing", "request_id": "request-9"}
        if path.startswith("/v1/runs?"):
            return [self.view]
        if path == f"/v1/runs/{RID}":
            return self.view
        if path == f"/v1/runs/{RID}/events":
            return []
        raise AssertionError((method, path))

    def text(self, method, path, timeout=40):
        return "# 报告\n"


def run(args, api, cwd):
    out = io.StringIO()
    with redirect_stdout(out):
        cli.run(args, api, Path(cwd))
    return out.getvalue()


class LoginTests(unittest.TestCase):
    def test_login_keeps_a_cli_token_only_for_its_owner(self):
        with tempfile.TemporaryDirectory() as home:
            api = Api()
            with mock.patch.object(cli, "Client", return_value=api):
                cli.login("https://k.example", "bob", "Fixture-password-123!", Path(home))
            saved = Path(home) / ".config/kirocrew-on/cli.json"
            self.assertEqual(json.loads(saved.read_text())["token"], "oct_cli")
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
            issued = next(c for c in api.calls if c[1] == "/v1/tokens")
            self.assertEqual(issued[2]["label"], "octopus CLI")
            self.assertIn(("DELETE", "/v1/tokens/token-login", None), [c[:3] for c in api.calls])   # the login token is not kept

    def test_a_saved_token_goes_only_to_its_platform(self):   # final review: --url sent it anywhere
        with tempfile.TemporaryDirectory() as home:
            path = cli.config_path(Path(home))
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"url": "https://k.example", "token": "oct_saved"}))
            self.assertEqual(cli.client_from(None, {}, Path(home)).token, "oct_saved")
            with self.assertRaises(ValueError) as caught:
                cli.client_from("https://elsewhere.example", {}, Path(home))
            self.assertIn("octopus login", str(caught.exception))
            self.assertEqual(cli.client_from("https://elsewhere.example", {"OCTOPUS_API_TOKEN": "oct_env"}, Path(home)).token, "oct_env")

    def test_without_a_login_the_user_is_told_how(self):
        with tempfile.TemporaryDirectory() as home, self.assertRaises(ValueError) as caught:
            cli.client_from(None, {}, Path(home))
        self.assertIn("octopus login", str(caught.exception))


class EntryTests(unittest.TestCase):
    def test_octopus_dispatches_the_user_commands(self):
        from octopus.__main__ import main
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}, clear=False), \
                mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as caught:
            os.environ.pop("OCTOPUS_API_TOKEN", None)
            os.environ.pop("OCTOPUS_URL", None)
            main(["status", "--run", RID])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("octopus login", err.getvalue())


class CommandTests(LocalCase):
    def test_submit_stops_on_unpushed_commits_unless_pushed_or_allowed(self):
        commit(self.repo, {"b.txt": "b\n"})
        api = Api()
        with self.assertRaises(ValueError) as caught:
            run(["submit", "做登录"], api, self.repo)
        self.assertIn("--push-base", str(caught.exception))
        self.assertFalse([c for c in api.calls if c[1] == "/v1/runs"])
        text = run(["submit", "做登录", "--allow-unpushed"], api, self.repo)
        body = next(c[2] for c in api.calls if c[1] == "/v1/runs")
        self.assertEqual(body["source"], {"type": "git", "url": "https://github.com/o/app.git", "ref": "main"})
        self.assertNotIn("admission", body)   # D70: no plan waits for confirmation
        self.assertIn(RID, text)
        self.assertEqual(local.linked(self.repo)["run_id"], RID)
        with mock.patch.object(local, "push_base") as pushed:
            run(["submit", "再做一次", "--push-base"], api, self.repo)
        pushed.assert_called_once()

    def test_submit_work_that_needs_no_repository(self):   # D65: octopus submit --no-repo
        api = Api()
        run(["submit", "调研 JSON Schema 校验方案并写报告", "--no-repo"], api, self.repo)
        body = next(c[2] for c in api.calls if c[1] == "/v1/runs")
        self.assertEqual(body["source"], {"type": "none"})

    def test_submit_from_inside_the_repository(self):   # final review: the process's own directory is the repository
        commit(self.repo, {"b.txt": "b\n"})
        api, here = Api(), os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, here)
        run(["submit", "做登录", "--allow-unpushed"], api, self.repo)
        self.assertTrue([c for c in api.calls if c[1] == "/v1/runs"])
        git(self.repo, "switch", "-q", "-c", "feature")
        with self.assertRaises(ValueError) as caught:
            run(["submit", "新分支", "--allow-unpushed"], api, self.repo)
        self.assertIn("--push-base", str(caught.exception))

    def test_status_and_plan_use_the_linked_session(self):
        local.link(self.repo, {"run_id": RID})
        api = Api()
        status = run(["status"], api, self.repo)
        self.assertIn("正在执行", status)
        plan = run(["plan"], api, self.repo)
        self.assertIn("实现", plan)
        self.assertIn("claude", plan)
        self.assertIn("npm test", plan)
        self.assertEqual(json.loads(run(["status", "--json"], api, self.repo))["session"]["status"], "running")
        with self.assertRaises(SystemExit):   # D70: there is no plan to confirm
            run(["confirm"], api, self.repo)

    def test_search_passes_the_filters(self):
        api = Api()
        run(["search", "登录", "--repo", "github.com/o/app", "--client", "cli", "--stage", "review"], api, self.repo)
        path = next(c[1] for c in api.calls if c[1].startswith("/v1/runs?"))
        for part in ("q=%E7%99%BB%E5%BD%95", "repo=github.com%2Fo%2Fapp", "client=cli", "stage=review"):
            self.assertIn(part, path)

    def test_a_command_without_a_session_says_how_to_name_one(self):
        lonely = Path(self.tmp.name) / "nowhere"
        lonely.mkdir()
        with self.assertRaises(ValueError) as caught:
            run(["status"], Api(), lonely)
        self.assertIn("--run", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
