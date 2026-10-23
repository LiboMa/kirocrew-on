"""D73 (owner 2026-10-23): a person works in the Session's native chat, then continues on the platform from the state the
chat left. A continue first commits what the chat changed in the workspace as a new candidate — 「对话中的改动」, an Attempt
of the last task that writes code — because a review's put-back would otherwise restore those files and a code task would
take them as its own; the tasks after it run again. A credential among them refuses the continue, and a diagnosis request
is refused while they are uncommitted. Design: docs/superpowers/specs/2026-10-23-chat-and-structured-iteration-design.md."""
import json
import unittest
from unittest import mock

from octopus.engine import Engine
from octopus.security import Error
from tests.test_engine_gates import C1, FakeWorker, IMPLEMENT, REVIEW
from tests.test_teams_workers import TeamsWorkersCase

C2 = "d" * 40


class ChatChangesCase(TeamsWorkersCase):
    def setUp(self):
        super().setUp()
        self.worker, self.dirty, self.secrets, self.continues = FakeWorker(), False, [], 0
        clean, made = self.worker.check, self.worker.candidate
        self.worker.check = lambda body: ({"head": body["expected"], "clean": False, "changed_files": ["src/chat.js"]}
                                          if self.dirty else clean(body))

        def candidate(body):
            if not self.dirty:
                return made(body)
            if not self.secrets:
                self.dirty = False   # committed: the branch now holds the chat's change
            return {"sha": C2, "parent": body["parent"], "changed": True, "files": 1, "insertions": 3, "deletions": 0,
                    "rewritten": False, "secrets": list(self.secrets), "names": ["src/chat.js"]}
        self.worker.candidate = candidate
        patcher = mock.patch("octopus.chat_changes.worker_request",
                             side_effect=lambda config, path, body=None, **kwargs: self.worker(config, path, body))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tick(self, rid, ticks=24):
        engine = Engine(self.db, request=self.worker)
        for _ in range(ticks):
            engine.tick()
        return self.service.run(self.c, rid)

    def finished(self, steps=(IMPLEMENT, REVIEW), **body):
        rid = self.service.start(self.c, {"request_id": "start-chat-0001", "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "steps": [dict(s) for s in steps], **body})["id"]
        self.assertEqual(self.tick(rid)["status"], "completed")
        return rid

    def proceed(self, rid, task, version=None, **extra):
        self.continues += 1
        run = self.service.run(self.c, rid)
        return self.service.operate(self.c, rid, "continue", {
            "request_id": f"continue-chat-{self.continues:04d}", "expected_version": run["version"] if version is None else version,
            "task_id": task, "input": run["input"], **extra})

    def attempts(self, rid, task):
        with self.db.read() as db:
            return [{**dict(r), "evidence": json.loads(r["evidence"] or "{}")} for r in
                    db.execute("SELECT * FROM attempts WHERE run_id=? AND task_id=? ORDER BY number", (rid, task))]

    def gates(self, attempt):
        return {g["name"]: g["status"] for g in attempt["evidence"].get("gates", [])}

    def candidate_calls(self, mode):
        return [b for p, b in self.worker.calls if p == "/candidate" and b["mode"] == mode]


class ContinueTests(ChatChangesCase):
    def test_a_continue_after_the_writer_commits_the_chats_changes_first(self):
        rid = self.finished()
        self.dirty = True
        self.proceed(rid, "review")
        code = self.attempts(rid, "code")
        self.assertEqual(len(code), 2)
        chat = code[1]
        self.assertEqual(chat["evidence"]["chat"]["files"], ["src/chat.js"])
        self.assertEqual((chat["evidence"]["candidate"]["sha"], chat["evidence"]["candidate"]["parent"]), (C2, C1))
        self.assertEqual(self.gates(chat), {"native_completed": "skip", "diff_present": "pass", "no_secrets": "pass"})
        self.assertEqual(chat["status"], "running")   # its checks run first, as any candidate's
        run = self.tick(rid)
        self.assertEqual(run["status"], "completed", run.get("error"))
        chat = self.attempts(rid, "code")[1]
        self.assertEqual((chat["status"], self.gates(chat)["checks_pass"]), ("accepted", "pass"))
        reviews = self.attempts(rid, "review")
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[1]["evidence"]["reviewed"], C2)   # the review is bound to the chat's candidate
        events = [e for e in self.service.events(self.c, rid) if e["kind"] == "run.chat_changes"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["files"], 1)
        latest = self.service.revisions(self.c, rid)[-1]
        self.assertTrue(latest["summary"].startswith("对话中的改动：1 个文件"), latest["summary"])

    def test_a_continue_of_the_writer_itself_reruns_it_on_top_of_the_chats_changes(self):
        rid = self.finished()
        self.dirty = True
        self.proceed(rid, "code")
        chat = self.attempts(rid, "code")[1]
        self.assertEqual((chat["status"], self.gates(chat).get("checks_pass")), ("accepted", "skip"))
        self.assertEqual(self.tick(rid)["status"], "completed")
        code = self.attempts(rid, "code")
        self.assertEqual(len(code), 3)
        commits = [b for b in self.candidate_calls("commit") if b["attempt_id"] == code[2]["id"]]
        self.assertEqual(commits[0]["parent"], C2)

    def test_a_clean_workspace_adds_nothing(self):
        rid = self.finished()
        self.proceed(rid, "review")
        self.assertEqual(len(self.attempts(rid, "code")), 1)
        self.assertNotIn("run.chat_changes", [e["kind"] for e in self.service.events(self.c, rid)])

    def test_a_credential_in_them_refuses_the_continue(self):
        rid = self.finished()
        before = self.service.run(self.c, rid)["version"]
        self.dirty, self.secrets = True, ["config/.env"]
        with self.assertRaises(Error) as caught:
            self.proceed(rid, "review")
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "chat_secret"))
        self.assertIn("config/.env", str(caught.exception))
        self.assertEqual(self.service.run(self.c, rid)["version"], before)
        self.assertEqual(len(self.attempts(rid, "code")), 1)

    def test_an_older_worker_is_left_as_it_was(self):
        rid = self.finished()
        self.worker.check = lambda body: (_ for _ in ()).throw(Error(404, "not_found", "接口不存在"))
        self.proceed(rid, "review")
        self.assertEqual(len(self.attempts(rid, "code")), 1)

    def test_a_stale_version_never_reaches_the_worker(self):
        rid = self.finished()
        self.dirty = True
        checks = len(self.candidate_calls("check"))
        with self.assertRaises(Error) as caught:
            self.proceed(rid, "review", version=0)
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertEqual(len(self.candidate_calls("check")), checks)
        self.assertEqual(len(self.candidate_calls("commit")), 1)   # the first run's own

    def test_someone_the_continue_refuses_never_has_the_chats_changes_committed(self):
        rid = self.finished()
        self.dirty = True
        commits = len(self.candidate_calls("commit"))
        with mock.patch("octopus.domain.directs", return_value="只有执行环境的主人可以续作"), \
                mock.patch("octopus.chat_changes.directs", create=True, return_value="只有执行环境的主人可以续作"):
            with self.assertRaises(Error) as caught:
                self.proceed(rid, "review")
        self.assertEqual(caught.exception.code, "boundary_owner_only")
        self.assertEqual(len(self.candidate_calls("commit")), commits)
        self.assertEqual(len(self.attempts(rid, "code")), 1)


class RecoverTests(ChatChangesCase):
    def test_a_diagnosis_request_is_refused_while_the_chat_has_uncommitted_changes(self):
        self.worker.outcomes["review"] = {"decision": "changes_requested", "summary": "有缺陷", "failure_type": "implementation",
                                          "findings": ["缺少校验"]}
        rid = self.service.start(self.c, {"request_id": "start-chat-0002", "input": "研发需求与真实验证", "source": {"type": "new"},
                                          "team_id": "team-p", "steps": [dict(IMPLEMENT), dict(REVIEW)],
                                          "recovery": {"enabled": False}})["id"]
        run = self.tick(rid)
        self.assertEqual(run["status"], "blocked", run.get("error"))
        self.dirty = True
        with self.assertRaises(Error) as caught:
            self.service.operate(self.c, rid, "recover", {"request_id": "recover-chat-0001", "expected_version": run["version"]})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "chat_changes"))
        self.assertIn("续作", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
