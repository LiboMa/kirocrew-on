"""D58 M3-4 on the user's machine: a local repository linked to a Session, the Session's branch and context pulled into it,
local commits synced back, and unpushed commits detected before a submission — shared by the CLI and the MCP tools."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from octopus import local

RID = "run-" + "1" * 32
BRANCH = "octopus/" + RID
AGENT = ["-c", "user.email=a@a", "-c", "user.name=agent"]


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, files, message="change"):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, *AGENT, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class Platform:
    """What the API answers for one Session whose workspace is ``remote`` (the Worker's repository)."""
    def __init__(self, remote, base):
        self.remote, self.base, self.downloads, self.uploads, self.version = remote, base, [], [], 3

    def request(self, method, path, body=None):
        if method == "POST" and path == f"/v1/runs/{RID}/bundle":
            return {"url": f"/bundle/ticket/{RID}.bundle", "expires_in": 600}
        if method == "GET" and path == f"/v1/runs/{RID}":
            return {"id": RID, "version": self.version, "input": "做登录", "name": "登录",
                    "planning": {"summary": "补全的需求"}, "request": {"id": "request-1", "client": "cli"},
                    "tasks": [{"id": "code", "status": "accepted", "spec": {"name": "实现", "tool": "claude", "kind": "deliver",
                                                                         "acceptance": ["完成"], "verify": ["true"]},
                               "attempts": [{"number": 1, "result": "交接：已实现登录"}]}]}
        raise AssertionError((method, path))

    def text(self, method, path):
        assert path == f"/v1/runs/{RID}/report?format=md", path
        return "# 报告\n"

    def download(self, path, file):
        self.downloads.append(path)
        full = "full=1" in path
        target = self.remote.parent / "out.bundle"
        git(self.remote, "bundle", "create", "-q", str(target), "refs/heads/" + BRANCH, *([] if full else ["^" + self.base]))
        data = target.read_bytes()
        file.write(data)
        return len(data)

    def upload(self, path, data):
        self.uploads.append((path, data))
        query = parse_qs(urlsplit(path).query)
        return {"id": RID, "version": self.version + 1, "head": query["head"][0], "commits": 1, "task_id": "code",
                "attempt_id": "attempt-x", "status": "ready"}


class LocalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root / "remote"
        self.remote.mkdir()
        git(self.remote, "init", "-q", "-b", "main")
        self.base = commit(self.remote, {"README.md": "x\n"}, "base")
        git(self.remote, "switch", "-q", "-c", BRANCH)
        self.candidate = commit(self.remote, {"src/a.js": "one\n"}, "candidate")
        git(self.remote, "switch", "-q", "main")
        self.repo = root / "local"
        subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "main", str(self.remote), str(self.repo)], check=True,
                       capture_output=True)
        git(self.repo, "remote", "set-url", "origin", "https://github.com/o/app.git")
        self.platform = Platform(self.remote, self.base)


class SourceTests(LocalCase):
    def test_the_source_is_origin_and_the_current_branch(self):
        self.assertEqual(local.repository(self.repo / "sub" if False else self.repo), self.repo.resolve())
        self.assertEqual(local.source(self.repo), {"type": "git", "url": "https://github.com/o/app.git", "ref": "main"})
        self.assertIsNone(local.repository(Path(self.tmp.name)))

    def test_an_ssh_origin_is_submitted_as_https(self):   # the execution environment clones over HTTPS only (EC2 run-d4d7…)
        for address in ("git@github.com:o/app.git", "ssh://git@github.com/o/app.git"):
            git(self.repo, "remote", "set-url", "origin", address)
            self.assertEqual(local.source(self.repo)["url"], "https://github.com/o/app.git", address)
        self.assertEqual(local.https("https://gitlab.example.com/g/s/r"), "https://gitlab.example.com/g/s/r")

    def test_unpushed_commits_are_counted(self):
        self.assertEqual(local.unpushed(self.repo), 0)
        commit(self.repo, {"b.txt": "b\n"})
        self.assertEqual(local.unpushed(self.repo), 1)
        git(self.repo, "switch", "-q", "-c", "feature")
        commit(self.repo, {"c.txt": "c\n"})
        self.assertEqual(local.unpushed(self.repo), 2)   # a branch origin does not have: every commit no remote has

    def test_the_link_is_personal(self):
        local.link(self.repo, {"run_id": RID, "url": "https://k.example", "head": self.candidate})
        self.assertEqual(local.linked(self.repo)["run_id"], RID)
        exclude = (self.repo / ".git/info/exclude").read_text()
        self.assertIn(".octopus/session.json", exclude)
        self.assertIn(".octopus/sessions/", exclude)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        local.link(self.repo, {"run_id": RID, "url": "https://k.example", "head": self.candidate})
        self.assertEqual((self.repo / ".git/info/exclude").read_text().count(".octopus/session.json"), 1)


class PullTests(LocalCase):
    def test_the_branch_and_the_context_come_back(self):
        result = local.pull(self.platform, RID, self.repo)
        self.assertEqual((result["branch"], result["head"], result["full"]), (BRANCH, self.candidate, False))
        self.assertEqual(git(self.repo, "rev-parse", BRANCH), self.candidate)
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")   # not checked out unless asked
        context = self.repo / ".octopus/sessions" / RID
        self.assertEqual((context / "report.md").read_text(), "# 报告\n")
        self.assertEqual(json.loads((context / "plan.json").read_text())["tasks"][0]["verify"], ["true"])
        self.assertIn("已实现登录", (context / "handoffs/code.md").read_text())
        self.assertEqual(local.linked(self.repo)["head"], self.candidate)
        local.pull(self.platform, RID, self.repo, checkout=True)
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), BRANCH)

    def test_without_the_baseline_the_whole_branch_is_fetched(self):
        lonely = Path(self.tmp.name) / "lonely"
        lonely.mkdir()
        git(lonely, "init", "-q", "-b", "main")
        commit(lonely, {"x": "x\n"}, "unrelated")
        result = local.pull(self.platform, RID, lonely)
        self.assertTrue(result["full"])
        self.assertEqual(self.platform.downloads[-1], f"/bundle/ticket/{RID}.bundle?full=1")
        self.assertEqual(git(lonely, "rev-parse", BRANCH), self.candidate)

    def test_local_commits_on_the_branch_are_never_overwritten(self):
        local.pull(self.platform, RID, self.repo, checkout=True)
        commit(self.repo, {"src/a.js": "mine\n"})
        with self.assertRaises(ValueError) as caught:
            local.pull(self.platform, RID, self.repo)
        self.assertIn("sync", str(caught.exception))
        git(self.remote, "switch", "-q", BRANCH)
        moved = commit(self.remote, {"src/b.js": "remote\n"})
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "branch", "-q", "-f", BRANCH, self.candidate)   # the local branch is behind, no commits of its own
        self.assertEqual(local.pull(self.platform, RID, self.repo)["head"], moved)


class SyncTests(LocalCase):
    def test_the_commits_since_the_session_head_are_sent(self):
        local.pull(self.platform, RID, self.repo, checkout=True)
        new = commit(self.repo, {"src/a.js": "two\n"})
        result = local.sync(self.platform, RID, self.repo, note="改了样式")
        path, data = self.platform.uploads[-1]
        query = parse_qs(urlsplit(path).query)
        self.assertEqual((query["old"][0], query["head"][0], query["expected_version"][0], query["note"][0]),
                         (self.candidate, new, "3", "改了样式"))
        self.assertTrue(query["request_id"][0].startswith("sync-"))
        bundle = Path(self.tmp.name) / "sent.bundle"
        bundle.write_bytes(data)
        self.assertIn(new, git(self.repo, "bundle", "list-heads", str(bundle)))
        self.assertEqual((result["head"], local.linked(self.repo)["head"]), (new, new))

    def test_refusals(self):
        local.pull(self.platform, RID, self.repo, checkout=True)
        with self.assertRaises(ValueError) as caught:
            local.sync(self.platform, RID, self.repo)
        self.assertIn("没有新的本地提交", str(caught.exception))
        commit(self.repo, {"src/a.js": "two\n"})
        (self.repo / "src/a.js").write_text("uncommitted\n")
        with self.assertRaises(ValueError) as caught:
            local.sync(self.platform, RID, self.repo)
        self.assertIn("未提交", str(caught.exception))
        git(self.repo, "checkout", "-q", "--", "src/a.js")
        git(self.repo, "switch", "-q", "main")
        with self.assertRaises(ValueError) as caught:
            local.sync(self.platform, RID, self.repo)
        self.assertIn("pull", str(caught.exception))
        self.assertEqual(self.platform.uploads, [])


if __name__ == "__main__":
    unittest.main()
