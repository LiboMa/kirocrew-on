"""D58 for Coding Agents: octopus_link / octopus_pull / octopus_sync run the same local work as the CLI in the user's
repository, and octopus_start stops on commits origin does not have unless they are pushed (with the user's consent)
or the submission is meant for what origin has."""
import json
import unittest
from unittest import mock

from octopus import local, mcp
from tests.test_local import RID, LocalCase, commit, git


class Agent(LocalCase):
    def setUp(self):
        super().setUp()
        self.started = []
        platform = self.platform
        original = platform.request

        def request(method, path, body=None):
            if path.endswith("/events"):
                return []
            if method == "POST" and path == "/v1/runs":
                self.started.append(body)
                return {"id": RID, "session_id": RID, "version": 1, "status": "preparing", "request_id": "request-1"}
            return original(method, path, body)
        platform.request = request
        platform.url = "https://k.example"

    def call(self, name, args):
        return mcp.call(self.platform, name, {"dir": str(self.repo), **args})


class LocalToolTests(Agent):
    def test_link_pull_and_sync(self):
        self.assertTrue(self.call("octopus_link", {"run_id": RID})["path"].endswith(".octopus/session.json"))
        pulled = self.call("octopus_pull", {"checkout": True})   # the run comes from the link
        self.assertEqual(pulled["head"], self.candidate)
        new = commit(self.repo, {"src/a.js": "two\n"})
        synced = self.call("octopus_sync", {"note": "改样式"})
        self.assertEqual((synced["head"], synced["old"]), (new, self.candidate))
        self.assertEqual(len(self.platform.uploads), 1)

    def test_status_reads_the_link(self):
        self.call("octopus_link", {"run_id": RID})
        self.assertEqual(self.call("octopus_status", {})["session"]["id"], RID)

    def test_without_a_link_the_run_id_is_asked_for(self):
        with self.assertRaises(ValueError) as caught:
            self.call("octopus_pull", {})
        self.assertIn("run_id", str(caught.exception))

    def test_the_tools_are_described_and_only_take_what_they_use(self):
        tools = {t["name"]: t for t in mcp.TOOLS}
        self.assertEqual(set(tools["octopus_sync"]["inputSchema"]["properties"]), {"run_id", "dir", "note"})
        self.assertEqual(set(tools["octopus_pull"]["inputSchema"]["properties"]), {"run_id", "dir", "checkout", "full"})
        self.assertIn("push_base", tools["octopus_start"]["inputSchema"]["properties"])


class StartTests(Agent):
    SOURCE = {"type": "git", "url": "https://github.com/o/app.git", "ref": "main"}

    def test_unpushed_commits_stop_a_submission_of_this_repository(self):
        commit(self.repo, {"b.txt": "b\n"})
        answer = self.call("octopus_start", {"request_id": "start-00000001", "input": "做登录", "source": self.SOURCE})
        self.assertEqual(answer["unpushed"], 1)
        self.assertIn("push_base", answer["hint"])
        self.assertEqual(self.started, [])
        with mock.patch.object(local, "push_base") as pushed:
            self.call("octopus_start", {"request_id": "start-00000002", "input": "做登录", "source": self.SOURCE, "push_base": True})
        pushed.assert_called_once()
        self.assertNotIn("push_base", self.started[-1])
        self.assertEqual(local.linked(self.repo)["run_id"], RID)
        self.call("octopus_start", {"request_id": "start-00000003", "input": "做登录", "source": self.SOURCE, "allow_unpushed": True})
        self.assertEqual(len(self.started), 2)

    def test_a_branch_origin_lacks_stops_a_submission_of_this_repository(self):
        git(self.repo, "switch", "-q", "-c", "feature")
        answer = self.call("octopus_start", {"request_id": "start-00000006", "input": "做登录",
                                             "source": dict(self.SOURCE, ref="feature"), "allow_unpushed": True})
        self.assertEqual(answer["missing_branch"], "feature")
        self.assertEqual(self.started, [])

    def test_unrecognised_repositories_are_not_the_same_one(self):
        git(self.repo, "remote", "set-url", "origin", "../somewhere")   # no host/owner/repo to compare
        commit(self.repo, {"b.txt": "b\n"})
        self.call("octopus_start", {"request_id": "start-00000007", "input": "x", "source": {"type": "git", "url": "../other"}})
        self.assertEqual(len(self.started), 1)

    def test_another_repository_or_a_new_project_is_submitted_as_given(self):
        commit(self.repo, {"b.txt": "b\n"})
        self.call("octopus_start", {"request_id": "start-00000004", "input": "别的仓库",
                                    "source": {"type": "git", "url": "https://github.com/o/other.git"}})
        self.call("octopus_start", {"request_id": "start-00000005", "input": "新项目", "source": {"type": "new"}})
        self.assertEqual(len(self.started), 2)
        self.assertNotIn("dir", self.started[0])


if __name__ == "__main__":
    unittest.main()
