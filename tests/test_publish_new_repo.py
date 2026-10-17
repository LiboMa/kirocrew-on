"""D48: a new-source Session publishes to a repository the platform creates (or one that is empty): the baseline becomes
`main`, the work arrives on octopus/<run> as a Draft PR. The host side here; the Session side in later classes."""
import json
import subprocess
import tempfile
import unittest
import unittest.mock
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


class NewRepositoryCase(PublishCase):
    def setUp(self):
        super().setUp()
        FakeApi.repos = {"owner/app"}
        self.writer = self.service.credentials.create(self.b, {"name": "bob 建仓库", "kind": "github_pat", "host": self.host,
                                                               "scope": "user", "access": "write", "repos": ["owner/*"],
                                                               "secret": SECRET})

    def new_session(self, request="start-0000000801", **body):
        rid = self.service.start(self.b, {"request_id": request, "input": "新建仓库并推 README", "team_id": self.tid,
                                          "worker_id": "w-p", "source": {"type": "new"},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                          "recovery": {"enabled": False, "max_cycles": 2}, **body})["id"]
        result = self.node.prepare({"run_id": rid, "source": {"type": "new"}})
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET workspace=?,native_parent='octopus-x',status='completed' WHERE id=?",
                       (result["workspace"], rid))
        (Path(result["workspace"]) / "README.md").write_text("# fresh\n")
        return rid

    def head(self, path, branch):
        out = subprocess.run(["git", "-C", str(GitHost.root / (path + ".git")), "rev-parse", "--verify", "-q",
                              "refs/heads/" + branch], capture_output=True, text=True)
        return out.stdout.strip() or None

    def target(self, name="fresh", **extra):
        return {"repository": {"owner": "owner", "name": name}, "credential": self.writer["id"], **extra}

    def events(self, rid, kind):
        with self.db.read() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind=?", (rid, kind))]


class NewRepositoryTests(NewRepositoryCase):
    def test_a_new_project_is_published_to_a_repository_the_platform_makes(self):
        rid = self.new_session()
        record = self.service.publish(self.b, rid, self.target())
        base = self.head("owner/fresh", "main")
        self.assertIsNotNone(base)
        self.assertEqual(self.head("owner/fresh", "octopus/" + rid), record["commit"])
        self.assertNotEqual(base, record["commit"])   # main is the baseline; the work is on the Session's branch
        pull = FakeApi.created[-1]
        self.assertEqual((pull["head"], pull["base"], pull["draft"]), ("octopus/" + rid, "main", True))
        self.assertEqual(record["repository"], "https://forge.example/owner/fresh")
        view = self.service.run(self.b, rid)
        self.assertEqual((view["publish_target"]["path"], view["publish_target"]["created"]), ("owner/fresh", True))
        self.assertEqual(len(self.events(rid, "run.repository")), 1)
        with self.db.read() as db:
            uses = [json.loads(r["detail"]) for r in db.execute("SELECT detail FROM audit WHERE action='credential.use'")]
        self.assertTrue(any(u.get("action") == "repo.create" and u["result"] == "created" for u in uses))

    def test_publishing_again_goes_to_the_same_repository(self):
        rid = self.new_session()
        first = self.service.publish(self.b, rid, self.target())
        (Path(self.service.run(self.b, rid)["workspace"]) / "more.txt").write_text("2")
        second = self.service.publish(self.b, rid, {})
        self.assertEqual((second["number"], second["reused"]), (first["number"], True))
        self.assertEqual(len(FakeApi.made), 1)
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, self.target(name="other"))
        self.assertEqual(caught.exception.code, "publish_target_fixed")

    def test_a_publish_that_failed_after_the_repository_was_made_continues_there(self):
        rid = self.new_session()
        original, calls = publish.push, []
        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise Error(409, "publish_failed", "网络中断")
            return original(*args, **kwargs)
        with unittest.mock.patch("octopus.publish.push", failing):
            with self.assertRaises(Error):
                self.service.publish(self.b, rid, self.target())
            self.assertIsNotNone(self.service.run(self.b, rid)["publish_target"]["baseline"])
            record = self.service.publish(self.b, rid, {})
        self.assertEqual(len(FakeApi.made), 1)
        self.assertEqual(self.head("owner/fresh", "octopus/" + rid), record["commit"])

    def test_an_empty_repository_that_already_exists_is_published_to(self):
        FakeApi.repos = {"owner/app", "owner/fresh"}
        bare = GitHost.root / "owner/fresh.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        subprocess.run(["git", "-C", str(bare), "config", "http.receivepack", "true"], check=True)
        rid = self.new_session()
        self.service.publish(self.b, rid, self.target())
        self.assertEqual(FakeApi.made, [])
        self.assertFalse(self.service.run(self.b, rid)["publish_target"]["created"])
        self.assertIsNotNone(self.head("owner/fresh", "main"))

    def test_an_existing_repository_with_content_is_left_alone(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, self.target(name="app"))
        self.assertEqual(caught.exception.code, "repository_not_empty")
        self.assertIsNone(self.head("owner/app", "octopus/" + rid))
        self.assertIsNone(self.service.run(self.b, rid).get("publish_target"))
        self.assertEqual(FakeApi.created, [])

    def test_a_credential_that_does_not_cover_the_new_repository_is_refused_before_any_call(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {"repository": {"owner": "other", "name": "x"}, "credential": self.writer["id"]})
        self.assertEqual(caught.exception.code, "credential_scope")
        self.assertEqual(FakeApi.seen, [])

    def test_a_read_only_credential_or_none_is_refused(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, {"repository": {"owner": "owner", "name": "x"}})
        self.assertEqual(caught.exception.code, "publish_target_missing")
        self.service.credentials.update(self.b, self.writer["id"], {"access": "read"})
        with self.assertRaises(Error) as caught:
            self.service.publish(self.b, rid, self.target())
        self.assertEqual(caught.exception.code, "credential_read_only")

    def test_a_session_from_a_repository_does_not_take_another_target(self):
        rid, _ = self.session(cred=self.cred["id"])
        with self.assertRaises(Error):
            self.service.publish(self.b, rid, self.target())


class NewRepositoryPolicyTests(NewRepositoryCase):
    def test_a_target_given_at_the_start_is_published_on_completion(self):
        rid = self.new_session(publish={"auto": True, **self.target()})
        self.service.auto_publish_pending()
        view = self.service.run(self.b, rid)
        self.assertTrue(view["publish_auto"]["ok"], view["publish_auto"])
        self.assertEqual(view["publish_target"]["path"], "owner/fresh")

    def test_a_target_for_a_session_from_a_repository_is_refused_at_the_start(self):
        with self.assertRaises(Error):
            self.service.start(self.b, {"request_id": "start-0000000809", "input": "x", "team_id": self.tid, "worker_id": "w-p",
                                        "source": {"type": "git", "url": self.url}, "publish": {"auto": True, **self.target()},
                                        "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}]})

    def test_turning_on_the_automatic_publish_needs_a_repository_for_a_new_source(self):
        rid = self.new_session()
        with self.assertRaises(Error) as caught:
            self.service.set_publish_policy(self.b, rid, {"auto": True})
        self.assertEqual(caught.exception.code, "publish_target_missing")
        self.service.set_publish_policy(self.b, rid, {"auto": True, **self.target()})
        self.service.auto_publish_pending()
        self.assertTrue(self.service.run(self.b, rid)["publish_auto"]["ok"])


class SuggestionAttentionTests(NewRepositoryCase):
    def test_a_completed_session_with_a_suggested_repository_asks_for_confirmation(self):
        rid = self.new_session()
        with self.db.transaction() as db:
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
            snapshot["publish_suggestion"] = {"repository": {"owner": "owner", "name": "fresh", "private": True}, "quote": "建仓库"}
            db.execute("UPDATE runs SET snapshot=? WHERE id=?", (json.dumps(snapshot), rid))
        attention = self.service.run(self.b, rid)["attention"]
        self.assertEqual(attention["title"], "发布到新仓库待确认")
        self.assertIn("owner/fresh", attention["detail"])
        self.service.publish(self.b, rid, self.target())
        self.assertIsNone(self.service.run(self.b, rid)["attention"])


if __name__ == "__main__":
    unittest.main()
