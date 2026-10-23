"""D37 §5 (G1): the platform runs a task's verify commands itself — in order, every one of them even after a failure
(D57 §13.3), each and all of them time-limited with their whole process group ended, the output's tail kept with
credentials masked; a commit a check makes has an identity, and a failure the environment caused is told apart (D57 §5)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from octopus import checks as checks_module
from octopus.checks import Checks
from octopus.security import Error
from tests.test_archive import RID, make_worker
from tests.test_secret_rules import private_key


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
        # D57 §3.3: a credential in the output is shown by its first four and last two characters only, and what was
        # masked is listed in the result (kind, line in the tail, masked form, hash) — its text nowhere
        token = "ghp_" + "A1b2C3d4" * 5
        checks = Checks()
        value = finished(checks, checks.start("run-a", "attempt-1", self.project,
                                              [f"head -c 10000 /dev/zero | tr '\\0' x; echo; echo token={token}"])["job"])
        result = value["results"][0]
        self.assertLessEqual(len(result["tail"].encode()), 4096)
        self.assertTrue(result["tail"].endswith("\ntoken=ghp_…d4\n"), result["tail"][-60:])
        self.assertEqual(result["findings"], [{"kind": "github", "line": 2, "masked": "ghp_…d4",
                                               "hash": hashlib.sha256(token.encode()).hexdigest()}])
        self.assertNotIn(token[4:-2], json.dumps([result["tail"], result["findings"]]))   # the command is the plan's
        clean = finished(checks, checks.start("run-a", "attempt-2", self.project, ["echo fine"])["job"])
        self.assertNotIn("findings", clean["results"][0])

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
            ({"exit": 127, "tail": "sh: 1: pytest: not found\n"}, "命令不存在：pytest"),   # dash: a PATH miss, bare name
            # a wrapper that could not start a command it looked up on PATH: the 127 fallback still holds
            ({"exit": 127, "tail": "timeout: failed to run command 'pytest': No such file or directory\n"}, "命令不存在（退出码 127）"),
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
            # bash reports a PATH miss as "command not found", never so: a bare name here is a file argument, a
            # "source" target or a redirect that is not there
            {"exit": 127, "tail": "bash: run_tests.sh: No such file or directory\n"},   # bash run_tests.sh
            {"exit": 127, "tail": "sh: run_tests.sh: No such file or directory\n"},     # sh run_tests.sh
            {"exit": 1, "tail": "bash: line 1: setup.sh: No such file or directory\n"},  # source setup.sh
            {"exit": 2, "tail": "/bin/dash: 0: cannot open run_tests.sh: No such file\n"},
            {"exit": 127, "tail": "timeout: failed to run command './run_tests.sh': No such file or directory\n"},
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
        # an empty HOME, so that no login profile slows the ten shells down on a loaded machine (as in run_outputs)
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            checks = Checks()
            value = finished(checks, checks.start("run-a", "attempt-1", self.project, [
                "d57-no-such-command --version",
                f"{sys.executable} -c 'import d57_no_such_module'",
                "git init -q repo && cd repo && GIT_AUTHOR_NAME= GIT_COMMITTER_NAME= git commit -q --allow-empty -m x",
                f"{sys.executable} -c 'assert 1 == 2'",
                f"{sys.executable} -c 'import myapp.models'",
                "./d57-never-written.sh",
                "sh -c scripts/d57-never-written.sh",
                "bash d57-never-written.sh",
                "sh d57-never-written.sh",
                "true"])["job"], 120)
        expected = ["命令不存在：d57-no-such-command", "缺少 Python 模块：d57_no_such_module", "检查环境没有 git 提交者身份",
                    None, None, None, None, None, None, None]
        tails = [r["tail"] for r in value["results"]]
        exits = [r["exit"] for r in value["results"]]
        self.assertEqual(exits[5:8] + exits[9:], [127, 127, 127, 0], tails)
        # "sh file": 127 where sh is bash (macOS, AL2023), 2 where it is dash ("cannot open", ubuntu); the project's either way
        self.assertIn(exits[8], (127, 2), tails)
        self.assertEqual([checks_module.unrunnable(r) for r in value["results"]], expected, tails)
        # the Worker records its answer, made with the project at hand, in each failed result
        self.assertEqual([r.get("unrunnable", "absent") for r in value["results"]], [*expected[:9], "absent"], tails)

    def run_outputs(self, outputs, limit=120):
        """The results of commands that print each of ``outputs`` (text, exit code). With an empty HOME, so that no login
        profile slows the shells down."""
        commands = []
        for n, (text, code) in enumerate(outputs):
            (self.project / f"out-{n}.txt").write_text(text)
            commands.append(f"cat out-{n}.txt; exit {code}")
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            checks = Checks()
            return finished(checks, checks.start("run-a", "attempt-1", self.project, commands)["job"], limit)["results"]

    def test_masking_never_changes_what_unrunnable_answers(self):
        # D57 §3.3, §5: the Worker judges a failure on the output as the command printed it, before masking, so a key
        # header, a key, its last lines or an END line near the error never turns an environment warning into a failure; what it
        # stores is masked, the reason too
        key, body = private_key("RSA", 640)
        header, end = "-----BEGIN RSA PRIVATE" + " KEY-----", "-----END RSA PRIVATE" + " KEY-----"
        messages = [("python3: No module named pytest", 1), ("python3.11: No module named pytest", 1),
                    ("/usr/bin/python3: No module named pytest", 1),
                    ("/usr/bin/env: 'node': No such file or directory", 127),
                    ("/usr/bin/env: ‘node’: No such file or directory", 127),
                    ("/bin/sh: 1: pytest: not found", 127), ("/bin/bash: line 1: pytest: command not found", 127),
                    ("ModuleNotFoundError: No module named 'requests'", 1)]
        contexts = ["{}\n", header + "\n{}\n", key + "\n{}\n", "\n".join(body.splitlines()[-6:]) + "\n" + end + "\n{}\n",
                    "{}\nhint: expected " + end + "\n",
                    "key.pem:" + end + "\n{}\n", "x" * 5000 + "\n{}\nhint: expected " + end + "\n",
                    "x" * 9000 + "\n{}\n" + end + "\n"]   # the last: its head dropped, so masked from the cut to the END
        outputs = [(context.format(message), code) for message, code in messages for context in contexts]
        token = "ghp_" + "Z9y8X7w6" * 5
        outputs += [(f"Error: Cannot find module '{token}'\n", 1),
                    ("Error: Cannot find module 'https://u:pw@registry.example.com/x'\n", 1)]
        results = self.run_outputs(outputs)
        self.assertEqual(len(results), len(outputs))
        for n, (message, code) in enumerate(messages):
            judged = results[n * len(contexts):(n + 1) * len(contexts)]
            reason = checks_module.unrunnable({"command": "c", "exit": code, "seconds": 0.1, "tail": message + "\n"})
            self.assertIsNotNone(reason, message)
            with self.subTest(message):
                self.assertEqual([r["unrunnable"] for r in judged], [reason] * len(contexts),
                                 [r["tail"][-200:] for r in judged])
                self.assertTrue(all(line not in r["tail"] for r in judged for line in body.splitlines()))
                self.assertEqual([r["tail"] for r in judged[1:3]], ["----…--\n" + message + "\n"] * 2)   # masked
                self.assertNotIn(message, judged[-1]["tail"])   # hidden in what is shown, still judged
                self.assertEqual([checks_module.unrunnable(r) for r in judged], [reason] * len(contexts))
        self.assertEqual(results[-2]["unrunnable"], "缺少 Node 模块：ghp_…w6")
        self.assertEqual(results[-1]["unrunnable"], "缺少 Node 模块：https://registry.example.com/x")
        self.assertNotIn(token[4:-2], json.dumps(results, ensure_ascii=False))
        self.assertNotIn("u:pw", json.dumps(results, ensure_ascii=False))

    def test_an_end_line_is_masked_as_a_cut_off_keys_only_once_output_was_dropped(self):
        # the reader knows whether it dropped the head of the output: 8 KB exactly is all of it, one byte more is not
        end = "-----END RSA PRIVATE" + " KEY-----\n"
        size = 2 * checks_module.TAIL
        filler = lambda n: ("x " * n)[:n]   # no run of base64 (next to a key's marker, one of 40 or more is masked)
        results = self.run_outputs([(filler(size - len(end)) + end, 1), (filler(size + 1 - len(end)) + end, 1)])
        self.assertNotIn("findings", results[0])
        self.assertTrue(results[0]["tail"].endswith(end))
        self.assertEqual([f["kind"] for f in results[1]["findings"]], ["private_key"])

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

    def test_a_check_that_prints_the_workers_token_shows_it_masked(self):
        # D57 §3.3: no rule knows the Worker's own token; an Agent there could still have written it where a check prints it
        with tempfile.TemporaryDirectory() as directory:
            node, project = make_worker(Path(directory))
            (project / "token.txt").write_text(f"Bearer {node.token}\n")
            job = node.verify_start({"run_id": RID, "attempt_id": "attempt-1", "commands": ["cat token.txt; exit 3"]})["job"]
            result = finished(node.checks, job)["results"][0]
            self.assertNotIn(node.token, json.dumps(result))
            self.assertTrue(result["tail"].endswith(f"Bearer {node.token[:4]}…{node.token[-2:]}\n"), result["tail"])
            self.assertEqual([f["kind"] for f in result["findings"]], ["worker_token"])
            self.assertEqual((result["exit"], result["command"]), (3, "cat token.txt; exit 3"))   # the command as planned


if __name__ == "__main__":
    unittest.main()
