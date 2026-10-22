"""D37 §5 (G1): the platform runs a task's verify commands itself — in order, every one of them even after a failure
(D57 §13.3), each and all of them time-limited with their whole process group ended, the output's tail kept with
credentials hidden; a commit a check makes has an identity, and a failure the environment caused is told apart (D57 §5)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from octopus import sources
from octopus import checks as checks_module
from octopus.checks import Checks
from octopus.security import Error
from tests.test_archive import RID, make_worker


def finished(checks, job, limit=30):
    deadline = time.time() + limit
    while time.time() < deadline:
        value = checks.status(job)
        if value["state"] == "done":
            return value
        time.sleep(0.1)
    raise AssertionError("checks did not finish")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)

    def test_every_command_runs_even_after_a_failure(self):
        # D57 §13.3: an advisory check that fails must not keep a required one after it from running
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["false", "echo ok > ran.txt"])["job"])
        self.assertEqual([(r["command"], r["exit"]) for r in value["results"]], [("false", 1), ("echo ok > ran.txt", 0)])
        self.assertTrue((self.project / "ran.txt").exists())
        self.assertEqual((value["passed"], value["failed"]), (False, 0))   # failed: the first that failed, as before

    def test_commands_run_in_order_and_failed_names_the_first_failure(self):
        checks = Checks()
        job = checks.start("run-a", "attempt-1", self.project, ["true", "echo out; exit 3", "exit 4", "echo $((1+1)) > last"])["job"]
        value = finished(checks, job)
        self.assertEqual([r["exit"] for r in value["results"]], [0, 3, 4, 0])
        self.assertEqual((value["passed"], value["failed"]), (False, 1))
        self.assertIn("out", value["results"][1]["tail"])
        self.assertEqual((self.project / "last").read_text(), "2\n")

    def test_a_baseline_run_still_stops_at_its_first_failure(self):
        # Its only question is whether the tests fail before the change: the first failure answers it, and the project
        # stays on the baseline (previews, downloads and publishing wait) until the run ends.
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["true", "exit 3", "touch never"],
                                              kind="baseline")["job"])
        self.assertEqual([r["exit"] for r in value["results"]], [0, 3])
        self.assertEqual((value["passed"], value["failed"]), (False, 1))
        self.assertFalse((self.project / "never").exists())

    def bare_git(self, **extra):
        """A host whose git has no identity: an empty HOME, no system config, no identity in the environment."""
        home = self.project / "home"
        home.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.project / "repo")], check=True)
        bare = {key: value for key, value in os.environ.items()
                if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG")) and key != "EMAIL"}
        bare.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), GIT_CONFIG_NOSYSTEM="1", **extra)
        return home, patch.dict(os.environ, bare, clear=True)

    def run_git(self, commands, environment):
        with environment:
            checks = Checks()
            value = finished(checks, checks.start("run-a", "attempt-1", self.project / "repo", commands)["job"])
        self.assertEqual([r["exit"] for r in value["results"]], [0] * len(commands), [r["tail"] for r in value["results"]])
        return [r["tail"] for r in value["results"]]

    def test_git_commits_work_without_a_configured_identity(self):
        # On EC2 a verify "git am …" failed: "Committer identity unknown … empty ident name … not allowed"
        _, environment = self.bare_git()
        tails = self.run_git(["git commit -q --allow-empty -m x", "git log -1 --format='%an <%ae>|%cn <%ce>'"], environment)
        self.assertIn("KiroCrew ON <kirocrew-on@localhost>|KiroCrew ON <kirocrew-on@localhost>", tails[1])

    def test_the_projects_own_identity_wins_over_the_default(self):
        # e.g. a test suite that commits as "-c user.name=agent" and checks the author
        _, environment = self.bare_git()
        subprocess.run(["git", "-C", str(self.project / "repo"), "config", "user.name", "Repo Owner"], check=True)
        subprocess.run(["git", "-C", str(self.project / "repo"), "config", "user.email", "owner@example.com"], check=True)
        tails = self.run_git(["git commit -q --allow-empty -m repo", "git log -1 --format='%an <%ae>|%cn <%ce>'",
                              "git -c user.name=agent -c user.email=a@a commit -q --allow-empty -m c",
                              "git log -1 --format='%an <%ae>|%cn <%ce>'"], environment)
        self.assertIn("Repo Owner <owner@example.com>|Repo Owner <owner@example.com>", tails[1])
        self.assertIn("agent <a@a>|agent <a@a>", tails[3])

    def test_the_users_own_global_git_config_still_applies(self):
        home, environment = self.bare_git()
        (home / ".config" / "git").mkdir(parents=True)
        (home / ".config" / "git" / "config").write_text("[alias]\n\txdg = !echo from-xdg\n\tboth = !echo xdg-wins\n")
        (home / ".gitconfig").write_text("[user]\n\tname = Home User\n[alias]\n\thello = !echo from-home\n\tboth = !echo home-wins\n")
        tails = self.run_git(["git hello", "git xdg", "git both", "git commit -q --allow-empty -m h",
                              "git log -1 --format='%an <%ae>'"], environment)
        self.assertEqual([t.strip().splitlines()[-1] for t in tails[:3]], ["from-home", "from-xdg", "home-wins"])
        self.assertIn("Home User <kirocrew-on@localhost>", tails[4])   # the name is the user's; the email, absent, the default

    def test_a_global_config_the_caller_chose_is_kept(self):
        chosen = self.project / "chosen.gitconfig"
        chosen.write_text("[user]\n\tname = Ops\n\temail = ops@example.com\n")
        _, environment = self.bare_git(GIT_CONFIG_GLOBAL=str(chosen))
        tails = self.run_git(['echo "[$GIT_CONFIG_GLOBAL]"', "git commit -q --allow-empty -m o", "git log -1 --format='%an <%ae>'"],
                             environment)
        self.assertIn(f"[{chosen}]", tails[0])
        self.assertIn("Ops <ops@example.com>", tails[2])

    def test_the_default_identity_is_a_file_outside_the_project_removed_afterwards(self):
        _, environment = self.bare_git()
        tails = self.run_git(['echo "[$GIT_CONFIG_GLOBAL]"', 'echo "[${GIT_AUTHOR_NAME:-}${GIT_COMMITTER_NAME:-}]"'], environment)
        path = Path(tails[0].strip().splitlines()[-1].strip("[]"))
        self.assertTrue(path.is_absolute(), tails[0])
        self.assertFalse(path.resolve().is_relative_to(self.project.resolve()))
        self.assertFalse(path.exists())   # removed when the job ended
        self.assertIn("[]", tails[1])     # no identity variables: they would win over the project's own

    def test_all_passing_commands_pass(self):
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["true", "pwd"])["job"])
        self.assertEqual((value["passed"], value["failed"]), (True, None))
        self.assertIn(str(self.project.resolve()), value["results"][1]["tail"])   # runs in the project

    def test_output_tail_is_kept_and_scrubbed(self):
        token = "ghp_" + "A1b2C3d4" * 5
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project,
                                              [f"head -c 10000 /dev/zero | tr '\\0' x; echo; echo token={token}"])["job"])
        tail = value["results"][0]["tail"]
        self.assertLessEqual(len(tail.encode()), 4096)
        self.assertNotIn(token, tail)
        self.assertIn(sources.HIDDEN, tail)

    def test_a_hanging_command_and_its_children_are_ended(self):
        checks = Checks(per_command=1)
        started = time.time()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["sleep 31.5 & sleep 31.6"])["job"])
        self.assertLess(time.time() - started, 10)
        self.assertEqual((value["passed"], value["results"][0]["exit"], value["results"][0].get("timeout")), (False, None, True))
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "sleep 31.[56]"], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [])

    def test_a_background_process_left_by_a_command_is_ended(self):
        # e.g. "uvicorn app:app & sleep 3 && curl -f localhost:8000/health": the shell exits, its server must not stay
        checks = Checks(per_command=30)
        started = time.time()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["sleep 31.7 & echo started"])["job"])
        self.assertLess(time.time() - started, 10)
        self.assertEqual((value["passed"], value["results"][0]["exit"]), (True, 0))
        self.assertIn("started", value["results"][0]["tail"])
        time.sleep(0.5)
        self.assertEqual(subprocess.run(["pgrep", "-f", "sleep 31.7"], capture_output=True, text=True).stdout.split(), [])

    def test_the_workers_pythonpath_is_not_the_projects(self):
        with patch.dict(os.environ, {"PYTHONPATH": "/opt/kirocrew-on/current"}):
            checks = Checks()
            value = finished(checks, checks.start("run-a", "attempt-1", self.project, ['echo "[${PYTHONPATH:-}]"'])["job"])
        self.assertIn("[]", value["results"][0]["tail"])

    def test_a_command_that_cannot_start_fails_instead_of_running_forever(self):
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project / "gone", ["true"])["job"], limit=10)
        self.assertEqual((value["passed"], value["results"][0]["exit"]), (False, None))
        self.assertIn("无法运行", value["results"][0]["tail"])

    def test_the_total_limit_ends_the_rest(self):
        checks = Checks(per_command=30, total=1)
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["sleep 3", "touch never", "true"])["job"])
        self.assertEqual(len(value["results"]), 3)
        self.assertTrue(value["results"][0].get("timeout"))
        self.assertEqual(value["results"][1:], [{"command": "touch never", "exit": None, "seconds": 0.0, "tail": "", "skipped": "total_timeout"},
                                                {"command": "true", "exit": None, "seconds": 0.0, "tail": "", "skipped": "total_timeout"}])
        self.assertEqual((value["passed"], value["failed"]), (False, 0))
        self.assertFalse((self.project / "never").exists())

    def test_commands_the_total_limit_leaves_are_skipped_not_passed_and_not_failed(self):
        for kind in ("verify", "baseline"):
            with self.subTest(kind=kind):
                checks = Checks(total=0)
                value = finished(checks, checks.start("run-a", "attempt-1", self.project, ["touch never", "true"], kind=kind)["job"])
                self.assertEqual([r.get("skipped") for r in value["results"]], ["total_timeout", "total_timeout"])
                self.assertEqual((value["passed"], value["failed"]), (False, None))
                self.assertFalse((self.project / "never").exists())

    def test_unrunnable_recognises_environment_failures(self):
        environment = [
            ({"exit": 127, "tail": "bash: line 1: pytest: command not found\n"}, "命令不存在：pytest"),
            ({"exit": 127, "tail": "bash: pytest: command not found\n"}, "命令不存在：pytest"),   # bash 3.2
            # a path a login profile sources that is gone says nothing about the check's own missing command
            ({"exit": 127, "tail": "/Users/u/.bash_profile: line 5: /opt/x/env.sh: No such file or directory\n"
                                   "bash: line 1: pytest: command not found\n"}, "命令不存在：pytest"),
            ({"exit": 1, "tail": "bash: pytest: command not found\n"}, "命令不存在：pytest"),
            ({"exit": 127, "tail": "sh: 1: jest: not found\n"}, "命令不存在：jest"),
            ({"exit": 127, "tail": "zsh: command not found: go\n"}, "命令不存在：go"),
            ({"exit": 127, "tail": "/usr/bin/env: ‘node’: No such file or directory\n"}, "命令不存在：node"),
            ({"exit": 2, "tail": "make: pytest: No such file or directory\nmake: *** [Makefile:2: test] Error 127\n"}, "命令不存在：pytest"),
            ({"exit": 127, "tail": ""}, "命令不存在（退出码 127）"),
            ({"exit": 1, "tail": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'yaml'\n"},
             "缺少 Python 模块：yaml"),
            ({"exit": 1, "tail": "/usr/bin/python3: No module named pytest\n"}, "缺少 Python 模块：pytest"),
            ({"exit": 1, "tail": "Error: Cannot find module 'express'\nRequire stack:\n- /p/app.js\n"}, "缺少 Node 模块：express"),
            ({"exit": 1, "tail": "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'vitest' imported from /p/a.js\n"},
             "缺少 Node 模块：vitest"),
            ({"exit": 128, "tail": "Committer identity unknown\n\n*** Please tell me who you are.\n"
                                   "fatal: empty ident name (for <crew-owner@ip-10-0-1-2.ec2.internal>) not allowed\n"},
             "检查环境没有 git 提交者身份"),
            ({"exit": 128, "tail": "Author identity unknown\nfatal: unable to auto-detect email address (got 'u@h.(none)')\n"},
             "检查环境没有 git 提交者身份"),
            ({"exit": 1, "tail": "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?\n"},
             "Docker 服务不可用"),
        ]
        not_environment = [
            {"exit": 1, "tail": "AssertionError: 1 != 2\n"},
            {"exit": 0, "tail": "bash: foo: command not found\n"},   # it passed
            {"exit": None, "tail": "zsh: command not found: x\n", "timeout": True},
            {"exit": None, "tail": "", "skipped": "total_timeout"},
            {"exit": None, "tail": "无法运行检查命令（FileNotFoundError）"},
            {"exit": 1, "tail": "Error: Cannot find module './routes/users'\n"},   # the project's own file
            # a script the project does not have (never written): the project's error, though the shell exits 127
            {"exit": 127, "tail": "bash: line 1: ./run_tests.sh: No such file or directory\n"},
            {"exit": 127, "tail": "bash: ./run_tests.sh: No such file or directory\n"},
            {"exit": 127, "tail": "bash: scripts/test.sh: No such file or directory\n"},
            {"exit": 127, "tail": "/bin/sh: 1: ./scripts/lint.sh: not found\n"},   # dash
            {"exit": 127, "tail": "sh: 1: scripts/lint.sh: not found\n"},
            {"exit": 2, "tail": "/bin/sh: 1: ./scripts/lint.sh: not found\nmake: *** [Makefile:2: lint] Error 127\n"},
            {"exit": 127, "tail": "zsh: no such file or directory: ./x.sh\n"},
            {"exit": 127, "tail": "scripts/run.sh: line 2: ./helper.sh: No such file or directory\n"},   # its helper
            {"exit": 127, "tail": "bash: line 1: pip: command not found\nbash: line 1: ./run.sh: No such file or directory\n"},
            {"exit": 2, "tail": "make: *** No rule to make target 'test'.  Stop.\n"},
            {"exit": 1, "tail": 'npm ERR! Missing script: "test"\n'},
            # what a login profile the check's shell sources prints (this machine's does) says nothing about the check
            {"exit": 1, "tail": "/Users/u/.config/envman/ENV.env: line 3: setopt: command not found\nAssertionError: 1 != 2\n"},
            {"exit": 1, "tail": "SKIPPED [1] tests/test_a.py:3: could not import 'yaml': No module named 'yaml'\n"
                                "FAILED tests/test_b.py::test_total - assert 1 == 2\n"},
            {"exit": 2, "tail": "src/a.ts(1,19): error TS2307: Cannot find module '@/utils' or its corresponding type declarations.\n"},
        ]
        for result, reason in environment:
            with self.subTest(result=result):
                self.assertEqual(checks_module.unrunnable({"command": "c", "seconds": 0.1, **result}), reason)
                self.assertEqual(checks_module.unrunnable({"command": "c", "seconds": 0.1, **result}, self.project), reason)
        for result in not_environment:
            with self.subTest(result=result):
                self.assertIsNone(checks_module.unrunnable({"command": "c", "seconds": 0.1, **result}))
        self.assertTrue(all(isinstance(template, str) for _, template in checks_module.UNRUNNABLE))

    def test_a_module_the_project_has_itself_is_the_projects_error(self):
        # an Agent's import that is wrong ("from myapp.models import …") must not pass for a missing package
        (self.project / "myapp").mkdir()
        (self.project / "tool.py").write_text("")
        (self.project / "src" / "lib").mkdir(parents=True)
        (self.project / "src" / "single.py").write_text("")
        (self.project / "src" / "utils").mkdir()
        (self.project / "src" / "helpers.ts").write_text("")
        python = "Traceback (most recent call last):\nModuleNotFoundError: No module named '{}'\n"
        node = "Error: Cannot find module '{}'\nRequire stack:\n- /p/app.js\n"
        cases = [(python.format("yaml"), "缺少 Python 模块：yaml"), (python.format("myapp.models"), None),
                 (python.format("myapp"), None), (python.format("tool"), None), (python.format("lib.x"), None),
                 (python.format("single"), None), ("/usr/bin/python3: No module named pytest\n", "缺少 Python 模块：pytest"),
                 (node.format("left-pad"), "缺少 Node 模块：left-pad"), (node.format("./util"), None),
                 (node.format("../shared/x"), None), (node.format("/p/server.js"), None), (node.format("utils"), None),
                 (node.format("helpers"), None), (node.format("lodash/fp"), "缺少 Node 模块：lodash/fp")]
        for tail, reason in cases:
            with self.subTest(tail=tail):
                self.assertEqual(checks_module.unrunnable({"command": "c", "exit": 1, "seconds": 0.1, "tail": tail}, self.project), reason)
        # without the project (the platform's side), what the Worker recorded with it is the answer
        own = {"command": "c", "exit": 1, "seconds": 0.1, "tail": python.format("myapp.models")}
        self.assertEqual(checks_module.unrunnable(own), "缺少 Python 模块：myapp.models")
        self.assertIsNone(checks_module.unrunnable({**own, "unrunnable": None}))
        self.assertEqual(checks_module.unrunnable({**own, "unrunnable": "命令不存在：x"}), "命令不存在：x")

    def test_unrunnable_reads_what_the_shell_python_and_git_really_print(self):
        (self.project / "myapp").mkdir()
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project, [
            "d57-no-such-command --version",
            f"{sys.executable} -c 'import d57_no_such_module'",
            "git init -q repo && cd repo && GIT_AUTHOR_NAME= GIT_COMMITTER_NAME= git commit -q --allow-empty -m x",
            f"{sys.executable} -c 'assert 1 == 2'",
            f"{sys.executable} -c 'import myapp.models'",
            "./d57-never-written.sh",
            "sh -c scripts/d57-never-written.sh",
            "true"])["job"])
        expected = ["命令不存在：d57-no-such-command", "缺少 Python 模块：d57_no_such_module", "检查环境没有 git 提交者身份",
                    None, None, None, None, None]
        tails = [r["tail"] for r in value["results"]]
        self.assertEqual([r["exit"] for r in value["results"]][5:], [127, 127, 0], tails)
        self.assertEqual([checks_module.unrunnable(r) for r in value["results"]], expected, tails)
        # the Worker records its answer, made with the project at hand, in each failed result
        self.assertEqual([r.get("unrunnable", "absent") for r in value["results"]], [*expected[:7], "absent"], tails)

    def test_cancel_ends_a_running_job(self):
        checks = Checks()
        job = checks.start("run-a", "attempt-1", self.project, ["sleep 30"])["job"]
        time.sleep(0.3)
        checks.cancel("run-a")
        value = finished(checks, job, limit=10)
        self.assertEqual((value["passed"], value["results"][0].get("stopped")), (False, True))

    def test_the_same_attempt_gets_the_same_job_and_unknown_jobs_are_404(self):
        checks = Checks()
        first = checks.start("run-a", "attempt-1", self.project, ["true"])["job"]
        self.assertEqual(checks.start("run-a", "attempt-1", self.project, ["true"])["job"], first)
        self.assertNotEqual(checks.start("run-a", "attempt-2", self.project, ["true"])["job"], first)
        with self.assertRaises(Error) as caught:
            checks.status("nope")
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "check_unknown"))


class WorkerCheckTests(unittest.TestCase):
    def test_the_worker_runs_checks_in_the_project_and_stopping_the_session_ends_them(self):
        with tempfile.TemporaryDirectory() as directory:
            node, project = make_worker(Path(directory))
            job = node.verify_start({"run_id": RID, "attempt_id": "attempt-1", "commands": ["pwd"]})["job"]
            value = finished(node.checks, job)
            self.assertIn(str(project.resolve()), value["results"][0]["tail"])
            for bad in ("npm test", ["x" * 501], ["a"] * 11, [3]):
                with self.subTest(bad=bad), self.assertRaises(Error):
                    node.verify_start({"run_id": RID, "attempt_id": "attempt-2", "commands": bad})
            job = node.verify_start({"run_id": RID, "attempt_id": "attempt-3", "commands": ["sleep 30"]})["job"]
            time.sleep(0.3)
            node.checks.cancel(RID)   # what the session's stop / close / delete does
            self.assertTrue(finished(node.checks, job, limit=10)["results"][0].get("stopped"))
            self.assertEqual(node.verify_status({"job": job})["state"], "done")


if __name__ == "__main__":
    unittest.main()
