"""D47 (EC2 run-361cd491…): a Session the user asked to open a pull request for is published on its own once it completes;
otherwise a person approves the publish. A planned "push" task is not how it happens, and its failure says so."""
import json
import unittest

from tests.test_confirmation import ConfirmationCase
from tests.test_publish import FakeApi, PublishCase


class AutoPublishCase(PublishCase):
    def started(self, request, **body):
        rid = self.service.start(self.b, {"request_id": request, "input": "研发需求与真实验证", "team_id": self.tid, "worker_id": "w-p",
                                          "source": {"type": "git", "url": self.url},
                                          "steps": [{"id": "code", "name": "实现", "tool": "claude", "prompt": "实现"}],
                                          "recovery": {"enabled": False, "max_cycles": 2},
                                          "credentials": {"source": self.cred["id"]}, **body})["id"]
        auth = {"kind": "github_pat", "host": self.host, "username": "x-access-token", "secret": "x" * 40}
        from tests.test_publish import SECRET
        auth["secret"] = SECRET
        result = self.node.prepare({"run_id": rid, "source": {"type": "git", "url": self.url}, "git_auth": auth})
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET workspace=?,native_parent='octopus-x',status='completed' WHERE id=?", (result["workspace"], rid))
        from pathlib import Path
        (Path(result["workspace"]) / "feature.js").write_text("export const x = 1;")
        return rid

    def events(self, rid, kind):
        with self.db.read() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM events WHERE run_id=? AND kind=?", (rid, kind))]


class AutoPublishTests(AutoPublishCase):
    def test_a_session_that_asks_for_it_is_published_when_it_completes(self):
        rid = self.started("start-0000000601", publish={"auto": True})
        self.service.auto_publish_pending()
        self.assertEqual(self.remote_head("octopus/" + rid) is not None, True)
        self.assertEqual(len(FakeApi.created), 1)
        self.assertTrue(FakeApi.created[0]["draft"])
        view = self.service.run(self.b, rid)
        self.assertEqual(view["publish"]["url"], "https://forge.example/owner/app/pull/1")
        self.assertTrue(view["publish_auto"]["ok"])
        self.assertEqual(len(self.events(rid, "run.publish_auto")), 1)

    def test_by_default_a_person_approves_the_publish(self):
        rid = self.started("start-0000000602")
        self.service.auto_publish_pending()
        self.assertIsNone(self.remote_head("octopus/" + rid))
        self.assertEqual(FakeApi.created, [])

    def test_an_automatic_publish_that_cannot_happen_says_why_and_is_not_retried(self):
        self.service.credentials.update(self.b, self.cred["id"], {"access": "read"})
        rid = self.started("start-0000000603", publish={"auto": True})
        self.service.auto_publish_pending()
        self.service.auto_publish_pending()
        view = self.service.run(self.b, rid)
        self.assertEqual(view["status"], "completed")   # the work is done; only the publish is not
        self.assertFalse(view["publish_auto"]["ok"])
        self.assertEqual(view["publish_auto"]["code"], "credential_read_only")
        self.assertEqual(len(self.events(rid, "run.publish_auto")), 1)   # tried once for this version
        self.assertIn("自动发布未完成", view["attention"]["title"])

    def test_the_policy_can_be_switched_on_the_session_page(self):
        rid = self.started("start-0000000604")
        self.service.set_publish_policy(self.b, rid, {"auto": True})
        self.assertTrue(self.service.run(self.b, rid)["publish_policy"]["auto"])
        self.service.auto_publish_pending()
        self.assertEqual(len(FakeApi.created), 1)

    def test_a_policy_is_checked(self):
        from octopus.security import Error
        for bad in ({"auto": "yes"}, {"auto": True, "branch": "main"}, {"extra": 1}):
            with self.subTest(bad), self.assertRaises(Error):
                self.started("start-00000006%02d" % (10 + len(str(bad))), publish=bad)


PLAN = """```json
{"intent": "实现并提 PR", "summary": "需求", "name": "计划", "publish": {"quote": "完成后提交 PR"}, "steps": [
 {"id": "code", "name": "实现", "tool": "claude", "prompt": "实现", "kind": "deliver", "acceptance": ["完成"]},
 {"id": "ship", "name": "提交", "tool": "claude", "prompt": "git push 到 main 并创建 PR", "kind": "deliver", "acceptance": ["已推送"]}]}
```"""


class PlannedTests(ConfirmationCase):
    def test_a_requirement_asking_for_a_pull_request_turns_the_automatic_publish_on(self):
        import tests.test_confirmation as confirmation
        original, confirmation.PLAN = confirmation.PLAN, PLAN
        self.addCleanup(setattr, confirmation, "PLAN", original)
        rid = self.start(steps=None, plan="agent", source={"type": "git", "url": "https://github.com/o/r.git"})
        run = self.tick(rid)
        self.assertEqual(run["publish_policy"], {"auto": True, "source": "requested", "quote": "完成后提交 PR"})

    def test_a_session_without_a_repository_says_so_instead_of_publishing(self):
        # EC2 run-f7670bfb…: 「新建一个 Repo 并推 README」 started from a new source
        import tests.test_confirmation as confirmation
        original, confirmation.PLAN = confirmation.PLAN, PLAN
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertFalse((run.get("publish_policy") or {}).get("auto"))
        notes = run["planning"]["notes"]
        self.assertTrue(any("要求推送或创建 PR" in note and "没有远端仓库" in note for note in notes))
        self.assertTrue(any("未开启自动发布" in note for note in notes))

    def test_a_push_task_planned_for_a_repository_says_the_platform_publishes(self):
        import tests.test_confirmation as confirmation
        original, confirmation.PLAN = confirmation.PLAN, PLAN
        self.addCleanup(setattr, confirmation, "PLAN", original)
        rid = self.start(steps=None, plan="agent", source={"type": "git", "url": "https://github.com/o/r.git"})
        notes = self.tick(rid)["planning"]["notes"]
        self.assertTrue(any("发布由平台在会话完成后进行" in note for note in notes))

    def test_a_policy_set_at_the_start_is_kept(self):
        import tests.test_confirmation as confirmation
        original, confirmation.PLAN = confirmation.PLAN, PLAN
        self.addCleanup(setattr, confirmation, "PLAN", original)
        rid = self.start(steps=None, plan="agent", publish={"auto": False})
        self.assertFalse(self.tick(rid)["publish_policy"]["auto"])

    def test_a_requirement_to_create_a_repository_is_suggested_not_created(self):
        import tests.test_confirmation as confirmation
        plan = PLAN.replace('"publish": {"quote": "完成后提交 PR"}',
                            '"publish": {"quote": "新建 LiboMa/demo 仓库", "repository": {"owner": "LiboMa", "name": "demo"}}')
        original, confirmation.PLAN = confirmation.PLAN, plan
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertEqual(run["publish_suggestion"]["repository"], {"owner": "LiboMa", "name": "demo", "private": True})
        self.assertFalse((run.get("publish_policy") or {}).get("auto"))
        self.assertTrue(any("LiboMa/demo" in note and "不自动创建" in note for note in run["planning"]["notes"]))

    def test_an_invalid_suggested_repository_is_dropped_with_a_note(self):
        import tests.test_confirmation as confirmation
        plan = PLAN.replace('"publish": {"quote": "完成后提交 PR"}',
                            '"publish": {"quote": "建仓库", "repository": {"owner": "a/b", "name": "../x"}}')
        original, confirmation.PLAN = confirmation.PLAN, plan
        self.addCleanup(setattr, confirmation, "PLAN", original)
        run = self.tick(self.start(steps=None, plan="agent"))
        self.assertIsNone(run.get("publish_suggestion"))
        self.assertTrue(any("仓库名不合法" in note for note in run["planning"]["notes"]))

    def test_the_planner_is_told_how_to_mark_a_pull_request(self):
        from octopus import planning
        self.assertIn('"publish"', planning.plan_step()["prompt"])


class PushFailureTests(ConfirmationCase):
    def request(self, config, path, body, **kwargs):
        result = super().request(config, path, body, **kwargs)
        if path == "/inspect" and not self.bodies[body["attempt_id"]].get("coordination"):
            result["outcome"] = json.dumps({"decision": "blocked", "failure_type": "auth",
                                            "summary": "git push 失败：fatal: Authentication failed for 'https://github.com/o/r.git/'",
                                            "findings": []})
        return result

    def test_a_task_that_failed_trying_to_push_is_explained_not_blamed_on_permissions(self):
        rid = self.start(recovery={"enabled": False, "max_cycles": 2}, source={"type": "git", "url": "https://github.com/o/r.git"})
        run = self.tick(rid)
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["attention"]["title"], "Agent 不能推送或创建 PR")
        self.assertIn("发布到仓库", run["attention"]["action"])

    def test_a_session_without_a_repository_is_not_sent_to_publish(self):
        # EC2 run-f7670bfb…: 「新建一个 Repo 并推 README」 on a new source: the platform publishes it to a new repository (D48)
        rid = self.start(recovery={"enabled": False, "max_cycles": 2})
        run = self.tick(rid)
        self.assertEqual(run["attention"]["title"], "Agent 不能推送或创建 PR")
        self.assertNotIn("「发布到仓库」", run["attention"]["action"])
        self.assertIn("新建仓库并发布", run["attention"]["action"])


class McpTests(unittest.TestCase):
    def test_the_policy_is_set_through_its_own_command(self):
        from octopus import mcp
        seen = []
        class Client:
            def request(self, method, path, body):
                seen.append((method, path, body))
                return {}
        mcp.call(Client(), "octopus_publish_policy", {"run_id": "run-1", "auto": True})
        self.assertEqual(seen, [("POST", "/v1/runs/run-1/publish-policy", {"auto": True})])
        start = next(t for t in mcp.TOOLS if t["name"] == "octopus_start")["inputSchema"]["properties"]
        self.assertIn("auto", start["publish"]["properties"])


if __name__ == "__main__":
    unittest.main()
