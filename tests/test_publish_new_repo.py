"""D48: a new-source Session publishes to a repository the platform creates (or one that is empty): the baseline becomes
`main`, the work arrives on octopus/<run> as a Draft PR. The host side here; the Session side in later classes."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from octopus import publish
from octopus.security import Error
from tests.test_private_clone import GitHost
from tests.test_publish import SECRET, FakeApi, PublishCase


class RepositoryApiTests(PublishCase):
    def setUp(self):
        super().setUp()
        FakeApi.repos = {"owner/app"}

    def test_a_repository_of_the_credentials_own_account_is_made_under_the_user(self):
        url = publish.create_repository("github_pat", self.host, {"owner": "Owner", "name": "fresh", "private": True}, SECRET)
        self.assertEqual(url, "https://forge.example/owner/fresh")
        where, body = FakeApi.made[0]
        self.assertEqual((where, body["name"], body["private"], body["auto_init"]), ("/user/repos", "fresh", True, False))

    def test_an_organisations_repository_is_made_under_the_organisation(self):
        publish.create_repository("github_pat", self.host, {"owner": "acme", "name": "tool", "private": False}, SECRET)
        self.assertEqual((FakeApi.made[0][0], FakeApi.made[0][1]["private"]), ("/orgs/acme/repos", False))

    def test_gitlab_makes_a_project_in_the_namespace(self):
        publish.create_repository("gitlab_token", self.host, {"owner": "group", "name": "svc", "private": True}, SECRET)
        where, body = FakeApi.made[0]
        self.assertEqual((where, body["namespace_id"], body["visibility"], body["initialize_with_readme"]),
                         ("/api/v4/projects", 7, "private", False))

    def test_a_repository_is_found_or_not(self):
        self.assertIsNotNone(publish.find_repository("github_pat", self.host, "owner/app", SECRET))
        self.assertIsNone(publish.find_repository("github_pat", self.host, "owner/none", SECRET))

    def test_a_credential_that_may_not_create_says_what_it_needs(self):
        FakeApi.refuse = 403
        with self.assertRaises(Error) as caught:
            publish.create_repository("github_pat", self.host, {"owner": "owner", "name": "fresh", "private": True}, SECRET)
        self.assertEqual(caught.exception.code, "credential_permission")
        self.assertIn("Administration", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_names_are_checked(self):
        for bad in ({"owner": "a/b", "name": "x"}, {"owner": "a", "name": "../x"}, {"owner": "a", "name": "x.git"},
                    {"owner": "a", "name": ""}, {"owner": "a", "name": "x" * 101}, {"owner": "-a", "name": "x"},
                    {"owner": "a", "name": "x", "private": "yes"}, {"owner": "a", "name": "x", "extra": 1}, "a/x"):
            with self.subTest(bad), self.assertRaises(Error):
                publish.check_repository(bad)
        self.assertEqual(publish.check_repository({"owner": "LiboMa", "name": "repo-create_test.1"}),
                         {"owner": "LiboMa", "name": "repo-create_test.1", "private": True})

    def test_main_starts_at_the_baseline_only_in_an_empty_repository(self):
        publish.create_repository("github_pat", self.host, {"owner": "owner", "name": "fresh", "private": True}, SECRET)
        work = Path(tempfile.mkdtemp(dir=self.tmp.name))
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "base"], check=True)
        base = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        auth = {"kind": "github_pat", "host": self.host, "username": "x-access-token", "secret": SECRET}
        url = f"https://{self.host}/owner/fresh.git"
        publish.push_baseline(work, url, base, auth)
        main = subprocess.run(["git", "-C", str(GitHost.root / "owner/fresh.git"), "rev-parse", "refs/heads/main"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(main, base)
        with self.assertRaises(Error) as caught:
            publish.push_baseline(work, url, base, auth)
        self.assertEqual(caught.exception.code, "repository_not_empty")


if __name__ == "__main__":
    unittest.main()
