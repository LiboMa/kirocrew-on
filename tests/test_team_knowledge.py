"""D52: a repository's knowledge, reviewed by people — on a shared environment too a Knowledge task gathers what a Session
learned, kept only for the repository (no lessons written or given); on publish the items go to a Draft PR of the
repository's .octopus/knowledge.md; tasks are pointed at that file."""
import base64
import json
import unittest
from unittest.mock import patch

from octopus import knowledge, knowledge_repo
from octopus.security import Error
from tests.test_knowledge import CAPS, SOURCE, KnowledgeCase
from tests.test_publish import PublishCase


class GatherTests(KnowledgeCase):
    def test_a_shared_environment_gathers_for_the_repository(self):
        self.configure(CAPS, "shared")
        _, run = self.run_()
        self.assertEqual(run["status"], "completed")
        self.assertIn("knowledge", self.tasks(run))
        self.assertTrue(knowledge.gathers({"execution_contract": 4}, SOURCE, "shared", {"capabilities": CAPS}))
        self.assertFalse(knowledge.applies({"execution_contract": 4}, SOURCE, "shared", {"capabilities": CAPS}))

    def test_a_shared_environment_keeps_items_for_the_repository_only(self):
        self.configure(CAPS, "shared")
        _, run = self.run_()
        self.assertEqual(self.writes, [])   # no lessons on an environment several spaces share
        self.assertEqual({b.get("lessons") for b in self.work()}, {None})
        self.assertEqual([(i["rule"], i["result"]) for i in run["knowledge"]["items"]],
                         [("依赖用 pnpm 安装", "kept"), ("接口测试在 tests/api/", "kept")])
        self.assertIn("发布时提到仓库", run["knowledge"]["note"])

    def test_dedicated_environments_still_write_lessons(self):
        _, run = self.run_()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual({i["result"] for i in run["knowledge"]["items"]}, {"written"})



ITEMS = [{"rule": "依赖用 pnpm 安装", "avoid": "不要用 npm install"}, {"rule": "接口测试在 tests/api/"}]


class FakeHost:
    """The hosting API as knowledge_repo calls it through publish._api: answers from a small state, records each call."""
    def __init__(self, kind="github_pat", branch=False, text=None, open_request=False):
        self.kind, self.branch, self.text, self.open_request, self.calls = kind, branch, text, open_request, []

    def __call__(self, method, url, secret, kind, body=None, *, missing=False, need=None):
        self.calls.append((method, url.split("/api", 1)[-1] if "/api/v4" in url else url.split("example", 1)[-1], body))
        path = url.split("?")[0]
        encoded = lambda: {"content": base64.b64encode(self.text.encode()).decode(), "sha": "f" * 40, "encoding": "base64"}
        if kind == "github_pat":
            if path.endswith("/repos/o/r"):
                return {"default_branch": "main"}
            if path.endswith("/git/ref/heads/octopus/knowledge"):
                return {"object": {"sha": "b" * 40}} if self.branch else None
            if path.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "c" * 40}}
            if path.endswith("/git/refs"):
                self.branch = True
                return {"ref": body["ref"]}
            if "/contents/.octopus/knowledge.md" in path:
                if method == "GET":
                    return encoded() if self.text is not None else None
                self.text = base64.b64decode(body["content"]).decode()
                return {"commit": {"sha": "d" * 40}}
            if path.endswith("/pulls"):
                if method == "GET":
                    return [{"html_url": "https://forge.example/o/r/pull/7", "number": 7}] if self.open_request else []
                return {"html_url": "https://forge.example/o/r/pull/8", "number": 8}
        else:
            if path.endswith("/projects/o%2Fr"):
                return {"default_branch": "main"}
            if "/repository/branches/" in path:
                return {"name": "octopus/knowledge"} if self.branch else None
            if "/repository/files/" in path:
                return encoded() if self.text is not None else None
            if path.endswith("/repository/commits"):
                self.text = body["actions"][0]["content"]
                return {"id": "d" * 40}
            if path.endswith("/merge_requests"):
                if method == "GET":
                    return []
                return {"web_url": "https://forge.example/o/r/-/merge_requests/3", "iid": 3}
        raise AssertionError(f"unexpected call {method} {url}")

    def methods(self):
        return [(m, u.split("?")[0]) for m, u, _ in self.calls]


class ProposalTests(unittest.TestCase):
    def propose(self, host, items=ITEMS):
        with patch("octopus.publish._api", host), patch("octopus.credentials.api_base", lambda kind, h: "https://api.example"
                                                                                          + ("/api/v4" if kind == "gitlab_token" else "")):
            return knowledge_repo.propose(host.kind, "forge.example", "o/r", items, "run-1", "secret", day="2026-10-20")

    def test_render_adds_new_items_only(self):
        first, added = knowledge_repo.render("", ITEMS, "run-1", "2026-10-20")
        self.assertEqual(len(added), 2)
        self.assertTrue(first.startswith("# 项目知识"))
        self.assertIn("- 规则：依赖用 pnpm 安装\n- 避免：不要用 npm install\n- 来源：会话 run-1 · 2026-10-20", first)
        again, added = knowledge_repo.render(first, [*ITEMS, {"rule": "提交前跑 make check"}], "run-2", "2026-10-21")
        self.assertEqual([i["rule"] for i in added], ["提交前跑 make check"])
        self.assertEqual(again.count("- 规则：依赖用 pnpm 安装\n"), 1)

    def test_a_missing_branch_is_made_from_the_default_one(self):
        host = FakeHost()
        result = self.propose(host)
        self.assertEqual((result["result"], result["number"], result["items"]), ("proposed", 8, 2))
        made = next(b for m, u, b in host.calls if m == "POST" and u.endswith("/git/refs"))
        self.assertEqual(made, {"ref": "refs/heads/octopus/knowledge", "sha": "c" * 40})
        put = next(b for m, u, b in host.calls if m == "PUT")
        self.assertEqual(put["branch"], "octopus/knowledge")
        self.assertNotIn("sha", put)   # a new file
        pull = next(b for m, u, b in host.calls if m == "POST" and u.endswith("/pulls"))
        self.assertEqual((pull["head"], pull["base"], pull["draft"], pull["title"]), ("octopus/knowledge", "main", True, knowledge_repo.TITLE))

    def test_an_open_proposal_gets_the_new_items_only(self):
        text, _ = knowledge_repo.render("", ITEMS[:1], "run-0", "2026-10-19")
        host = FakeHost(branch=True, text=text, open_request=True)
        result = self.propose(host)
        self.assertEqual((result["result"], result["number"], result["items"], result["reused"]), ("proposed", 7, 1, True))
        self.assertEqual(next(b for m, u, b in host.calls if m == "PUT")["sha"], "f" * 40)
        self.assertEqual(host.text.count("- 规则：依赖用 pnpm 安装\n"), 1)
        self.assertIn("接口测试在 tests/api/", host.text)
        self.assertFalse(any(m == "POST" for m, _ in host.methods()))   # no new branch, no new request

    def test_a_branch_without_an_open_request_gets_a_new_one(self):
        host = FakeHost(branch=True, text="")
        result = self.propose(host)
        self.assertEqual((result["number"], result["reused"]), (8, False))

    def test_nothing_new_writes_nothing(self):
        text, _ = knowledge_repo.render("", ITEMS, "run-0", "2026-10-19")
        host = FakeHost(branch=True, text=text, open_request=True)
        self.assertEqual(self.propose(host)["result"], "unchanged")
        self.assertFalse(any(m in {"PUT", "POST"} for m, _ in host.methods()))
        self.assertEqual(self.propose(FakeHost(), items=[])["result"], "unchanged")

    def test_gitlab_uses_one_commit(self):
        host = FakeHost(kind="gitlab_token")
        result = self.propose(host)
        self.assertEqual((result["result"], result["number"], result["kind"]), ("proposed", 3, "merge_request"))
        commit = next(b for m, u, b in host.calls if u.endswith("/repository/commits"))
        self.assertEqual((commit["branch"], commit["start_branch"]), ("octopus/knowledge", "main"))
        self.assertEqual([(a["action"], a["file_path"]) for a in commit["actions"]], [("create", ".octopus/knowledge.md")])



class PublishProposalTests(PublishCase):
    def knowledge(self, rid, rows):
        with self.db.transaction() as db:
            for n, (rule, result, removed) in enumerate(rows):
                db.execute("INSERT INTO knowledge(id,run_id,task_id,worker_id,repo,rule,avoid,result,created,removed) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)", (f"k{n}", rid, "knowledge", "w-p", "repo", rule,
                                                           "别这样" if n == 0 else None, result, n, removed))

    def test_publish_proposes_the_sessions_knowledge(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        self.knowledge(rid, [("依赖用 pnpm 安装", "written", None), ("接口测试在 tests/api/", "kept", None),
                             ("太长的", "skipped", None), ("已删除的", "written", 5)])
        seen = []
        answer = {"result": "proposed", "items": 2, "url": "https://forge.example/owner/app/pull/9", "number": 9,
                  "kind": "pull_request", "reused": False}
        with patch("octopus.knowledge_repo.propose", lambda *args, **kw: (seen.append(args), answer)[1]):
            record = self.service.publish(self.b, rid, {})
        kind, host, path, items, run_id, secret = seen[0]
        self.assertEqual((path, run_id), ("owner/app", rid))
        self.assertEqual(items, [{"rule": "依赖用 pnpm 安装", "avoid": "别这样"}, {"rule": "接口测试在 tests/api/"}])
        self.assertEqual(record["knowledge"], answer)
        with self.db.read() as db:
            event = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='run.knowledge_proposed'", (rid,)).fetchone()
            audited = db.execute("SELECT detail FROM audit WHERE action='knowledge.proposed' AND target=?", (rid,)).fetchone()
            snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()["snapshot"])
        self.assertEqual(json.loads(event["payload"])["number"], 9)
        self.assertEqual(json.loads(audited["detail"])["items"], 2)
        self.assertEqual(snapshot["knowledge_proposal"]["url"], answer["url"])

    def test_a_refusal_never_fails_the_publish(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        self.knowledge(rid, [("依赖用 pnpm 安装", "written", None)])

        def refuse(*args, **kw):
            raise Error(409, "credential_permission", "凭证没有此操作的权限（HTTP 403）")
        with patch("octopus.knowledge_repo.propose", refuse):
            record = self.service.publish(self.b, rid, {})
        self.assertEqual(record["kind"], "pull_request")   # the code's request stands
        self.assertEqual((record["knowledge"]["result"], record["knowledge"]["code"]), ("failed", "credential_permission"))

    def test_without_items_no_proposal_is_made(self):
        rid, project = self.session(cred=self.cred["id"])
        (project / "a.txt").write_text("1")
        with patch("octopus.knowledge_repo.propose", side_effect=AssertionError("not called")):
            record = self.service.publish(self.b, rid, {})
        self.assertNotIn("knowledge", record)


class InjectionTests(unittest.TestCase):
    """R5: a task is pointed at the repository's reviewed knowledge, on every environment."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from octopus import gateway_client
        from octopus.worker import Worker
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.node = Worker(Path(tmp.name) / "worker", "x" * 40, crew_home=Path(tmp.name) / "crew")

        def native(path, body=None, method=None, timeout=None, headers=None):
            if path.startswith("/api/chat/slots/octopus-") and body is None:
                raise gateway_client.GatewayError(404, '{"error":"not found"}')
            if path == "/api/chat/slots":
                return {"key": body["name"]}
            if path == "/api/spawn" and body is not None:
                return {"id": "aaaa1111"}
            return {"ok": True}
        self.node.native = native
        self.rid = "run-" + "2" * 32
        self.node.prepare({"run_id": self.rid, "source": {"type": "new"}})

    def dispatch(self):
        from octopus import sources
        attempt = "attempt-" + "c" * 32
        self.node.dispatch({"run_id": self.rid, "attempt_id": attempt, "input": "需求", "upstream": [],
                            "spec": {"id": "code", "name": "编码", "tool": "claude", "model": "auto", "effort": "", "prompt": "实现"}})
        return (self.node.path(self.rid) / "attempts" / attempt / "instructions.md").read_text(), sources.project_dir(self.node.path(self.rid))

    def test_a_task_is_pointed_at_the_reviewed_knowledge(self):
        from octopus import sources
        project = sources.project_dir(self.node.path(self.rid))
        (project / ".octopus").mkdir(exist_ok=True)
        (project / ".octopus/knowledge.md").write_text("# 项目知识\n")
        text, project = self.dispatch()
        self.assertIn("## 项目知识（经评审）", text)
        self.assertIn(str(project / ".octopus/knowledge.md"), text)

    def test_without_the_file_nothing_is_added(self):
        text, _ = self.dispatch()
        self.assertNotIn("项目知识（经评审）", text)


if __name__ == "__main__":
    unittest.main()
