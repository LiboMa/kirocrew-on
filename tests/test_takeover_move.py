"""D74 §4: the move a takeover carries out in the background. The Session's branch with its whole history and its attempt
files go from the old boundary's Worker to the new one's, then — in one transaction — it runs there as the person who
took it over; a failure leaves it where it was, with the reason. Two real Workers, the platform's calls wired to them."""
import base64
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from octopus import credentials, takeover
from octopus.db import dumps
from octopus.security import Error
from octopus.worker import Worker
from tests.test_session_takeover import ExecutorCase
from tests.test_worker_adopt import stub


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


TOKENS = {"alice": "alice-worker-token-" + "Q" * 24, "bob": "bob-worker-token-" + "R" * 24}


class Response(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers


class Wire:
    """The platform's Worker calls (octopus.transport), answered by in-process Workers keyed by their config's url."""
    def __init__(self, workers):
        self.workers, self.adopt_error = workers, None

    def request(self, config, path, body=None, timeout=35):
        worker = self.workers[config["url"]]
        methods = {"/pull/attempts": worker.pull_attempts, "/candidate": worker.candidate}
        if path not in methods:
            raise Error(404, "not_found", "接口不存在")
        return methods[path](body)

    def stream(self, config, path, body, timeout=60):
        worker = self.workers[config["url"]]
        plan = worker.pull_plan(body)
        bundle = worker.pull_bundle(plan)
        data = bundle.read_bytes()
        bundle.unlink()
        return Response(data, {"X-Pull-Full": "1" if plan["full"] else "0"})

    def upload(self, config, path, data, headers, timeout=120):
        if self.adopt_error:
            raise self.adopt_error
        return self.workers[config["url"]].adopt(json.loads(base64.b64decode(headers["X-Octopus-Adopt"])), data)


class MoveCase(ExecutorCase):
    def setUp(self):
        super().setUp()
        root = Path(self.temporary.name)
        self.alice_w = Worker(root / "alice-w", "a" * 40, crew_home=root / "crew-a")
        self.bob_w = Worker(root / "bob-w", "b" * 40, crew_home=root / "crew-b")
        for worker in (self.alice_w, self.bob_w):
            stub(worker)
        with self.db.transaction() as db:
            for wid, url in (("alice-b", "alice"), ("bob-b", "bob")):
                db.execute("UPDATE workers SET config=? WHERE id=?", (dumps({"url": url, "token": TOKENS[url]}), wid))
        self.wire = Wire({"alice": self.alice_w, "bob": self.bob_w})
        for name, fake in (("octopus.transport.worker_request", self.wire.request),
                           ("octopus.chat_changes.worker_request", self.wire.request),
                           ("octopus.transport.worker_stream", self.wire.stream),
                           ("octopus.transport.worker_upload", self.wire.upload)):
            patcher = patch(name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        # What two tasks left on alice's boundary: a commit on the Session's branch and an attempt with its handoff
        prepared = self.alice_w.prepare({"run_id": self.rid, "source": {"type": "new"}})
        self.project = Path(prepared["workspace"])
        (self.project / "app.py").write_text("print('hi')\n")
        git(self.project, "add", "app.py")
        git(self.project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "实现")
        self.head = git(self.project, "rev-parse", "HEAD")
        self.aid = "attempt-" + "8" * 32
        folder = self.alice_w.path(self.rid) / "attempts" / self.aid
        folder.mkdir(parents=True)
        (folder / "handoff.md").write_text("# 交接\n实现了 app.py\n")
        evidence = {"handoff_path": str(folder / "handoff.md"),
                    "candidate": {"sha": self.head, "parent": prepared["project"]["commit"], "moved": True, "names": ["app.py"]}}
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET status='completed', native_parent=?, workspace=? WHERE id=?",
                       (prepared["native_parent"], str(self.project), self.rid))
            db.execute("INSERT INTO attempts(id,run_id,task_id,number,spec,status,evidence,created,updated) "
                       "VALUES(?,?,'code',1,'{}','accepted',?,1,1)", (self.aid, self.rid, dumps(evidence)))
            db.execute("UPDATE tasks SET status='accepted', current_attempt=? WHERE run_id=?", (self.aid, self.rid))
        self.keys = 0

    def take_over(self, person="bob"):
        self.keys += 1
        self.service.takeover(self.people[person], self.rid, {"request_id": f"move-fixture-{self.keys}",
                                                              "expected_version": self.row(self.rid)["version"]})
        self.service.carry_takeovers(self.rid)
        return self.row(self.rid)

    def kinds(self):
        with self.db.read() as db:
            return [r["kind"] for r in db.execute("SELECT kind FROM events WHERE run_id=? ORDER BY id", (self.rid,))]


class MoveTests(MoveCase):
    def test_the_session_moves_and_runs_as_the_person_who_took_it_over(self):
        before = self.row(self.rid)["version"]
        row = self.take_over()
        self.assertEqual((row["worker_id"], row["executor_id"], row["owner_id"]), ("bob-b", self.ids["bob"], self.ids["alice"]))
        self.assertTrue(row["workspace"].startswith(str(self.bob_w.root)))
        self.assertEqual(git(Path(row["workspace"]), "rev-parse", "HEAD"), self.head)   # the same commits
        self.assertEqual(row["status"], "completed")   # as it was
        self.assertEqual(row["version"], before + 2)   # the request, then the move
        snapshot = json.loads(row["snapshot"])
        self.assertNotIn("takeover", snapshot)
        self.assertEqual((snapshot["placement"]["kind"], snapshot["placement"]["from"]), ("taken_over", "alice-b"))
        with self.db.read() as db:
            evidence = json.loads(db.execute("SELECT evidence FROM attempts WHERE id=?", (self.aid,)).fetchone()["evidence"])
            kinds = [json.loads(r["content"])["kind"] for r in db.execute("SELECT content FROM revisions WHERE run_id=?", (self.rid,))]
        self.assertTrue(evidence["handoff_path"].startswith(str(self.bob_w.root)))   # where the next task reads it now
        self.assertEqual(Path(evidence["handoff_path"]).read_text(), "# 交接\n实现了 app.py\n")
        self.assertIn("taken_over", kinds)
        self.assertIn("run.taken_over", self.kinds())

    def test_the_creator_takes_it_back(self):
        self.take_over()
        row = self.take_over("alice")
        self.assertEqual((row["worker_id"], row["executor_id"]), ("alice-b", self.ids["alice"]))
        self.assertTrue(row["workspace"].startswith(str(self.alice_w.root)))
        self.assertEqual(git(Path(row["workspace"]), "rev-parse", "HEAD"), self.head)

    def test_what_a_chat_left_is_committed_before_it_moves(self):
        (self.project / "chat.py").write_text("x = 1\n")   # alice changed it in her native chat
        row = self.take_over()
        moved = Path(row["workspace"])
        self.assertEqual(git(moved, "show", "HEAD:chat.py"), "x = 1")
        self.assertEqual(git(moved, "rev-parse", "HEAD~1"), self.head)
        with self.db.read() as db:
            chat = [json.loads(r["evidence"]) for r in db.execute("SELECT evidence FROM attempts WHERE run_id=? AND id!=?",
                                                                  (self.rid, self.aid))]
        self.assertEqual(len(chat), 1)
        self.assertTrue(chat[0]["candidate"]["chat"])
        self.assertIn("接手前提交", next(g for g in chat[0]["gates"] if g["name"] == "checks_pass")["detail"])

    def test_a_failed_move_leaves_it_where_it_was(self):
        self.wire.adopt_error = Error(409, "adopt_invalid", "接手的内容含有不认识的文件")
        row = self.take_over()
        self.assertEqual((row["worker_id"], row["executor_id"], row["workspace"]), ("alice-b", None, str(self.project)))
        failed = json.loads(row["snapshot"])["takeover"]
        self.assertEqual((failed["state"], failed["code"]), ("failed", "adopt_invalid"))
        self.assertIn("不认识的文件", failed["reason"])
        self.assertIn("run.takeover_failed", self.kinds())
        self.wire.adopt_error = None   # it may be asked for again
        self.assertEqual(self.take_over()["worker_id"], "bob-b")

    def test_a_credential_in_the_chat_stops_it(self):
        (self.project / "settings.py").write_text('TOKEN = "ghp_' + "A1b2C3d4" * 5 + '"\n')
        row = self.take_over()
        failed = json.loads(row["snapshot"])["takeover"]
        self.assertEqual((failed["state"], failed["code"]), ("failed", "chat_secret"))
        self.assertIn("settings.py", failed["reason"])
        self.assertEqual(row["worker_id"], "alice-b")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.head)   # the branch did not move

    def test_an_env_file_stays_behind(self):
        (self.project / ".env").write_text("TOKEN=1\n")   # never committed: it stays on the creator's boundary
        row = self.take_over()
        self.assertEqual(row["worker_id"], "bob-b")
        self.assertFalse((Path(row["workspace"]) / ".env").exists())

    def test_a_session_never_prepared_is_prepared_afresh_there(self):
        with self.db.transaction() as db:
            db.execute("UPDATE runs SET native_parent=NULL, workspace=NULL, status='blocked' WHERE id=?", (self.rid,))
        row = self.take_over()
        self.assertEqual((row["worker_id"], row["executor_id"], row["workspace"], row["native_parent"]),
                         ("bob-b", self.ids["bob"], None, None))

    def test_an_older_worker_says_so(self):
        with patch.object(self.wire, "request", side_effect=Error(404, "not_found", "接口不存在")):
            with patch("octopus.transport.worker_request", self.wire.request):
                row = self.take_over()
        self.assertEqual(json.loads(row["snapshot"])["takeover"]["code"], "worker_outdated")


class RaceTests(MoveCase):
    """What changes while a move runs (final review of D74): the Session must not run again on the old boundary, and the
    person taking it over must still be allowed to when it lands."""
    def test_the_engine_leaves_a_moving_session_alone(self):
        from octopus import waiting
        from octopus.engine import Engine
        with self.db.transaction() as db:   # its Worker was disabled under it, and is enabled again during the move
            db.execute("UPDATE runs SET status='blocked', error=? WHERE id=?", (waiting.DISABLED, self.rid))
        self.service.takeover(self.people["bob"], self.rid, {"request_id": "race-engine", "expected_version": self.row(self.rid)["version"]})
        Engine(self.db).tick()
        self.assertEqual(self.row(self.rid)["status"], "blocked")   # not resumed on alice's boundary while it moves

    def test_a_session_that_started_again_is_not_moved(self):
        adopt = self.wire.upload
        def started(config, path, data, headers, timeout=120):
            with self.db.transaction() as db:   # e.g. a resume that slipped in: a task is dispatched on alice's boundary
                db.execute("UPDATE runs SET status='ready' WHERE id=?", (self.rid,))
            return adopt(config, path, data, headers, timeout)
        with patch("octopus.transport.worker_upload", started):
            row = self.take_over()
        self.assertEqual((row["worker_id"], row["executor_id"]), ("alice-b", None))
        self.assertEqual(json.loads(row["snapshot"])["takeover"]["code"], "takeover_conflict")

    def test_a_taker_who_lost_access_does_not_get_it(self):
        self.service.takeover(self.people["bob"], self.rid, {"request_id": "race-unshared", "expected_version": self.row(self.rid)["version"]})
        with self.db.transaction() as db:   # alice stopped sharing before the move ran (e.g. the platform was down)
            db.execute("DELETE FROM shares WHERE id='share-1'")
        self.service.carry_takeovers(self.rid)
        row = self.row(self.rid)
        self.assertEqual((row["worker_id"], row["executor_id"]), ("alice-b", None))
        self.assertEqual(json.loads(row["snapshot"])["takeover"]["code"], "takeover_forbidden")
        self.assertFalse(self.bob_w.path(self.rid).exists())   # nothing was sent to bob's boundary

    def test_access_lost_during_the_transfer_stops_it_landing(self):
        adopt = self.wire.upload
        def unshared(config, path, data, headers, timeout=120):
            answer = adopt(config, path, data, headers, timeout)
            with self.db.transaction() as db:
                db.execute("DELETE FROM shares WHERE id='share-1'")
            return answer
        with patch("octopus.transport.worker_upload", unshared):
            row = self.take_over()
        self.assertEqual((row["worker_id"], row["executor_id"]), ("alice-b", None))

    def test_a_continue_during_the_move_commits_nothing_on_the_old_workspace(self):
        self.service.takeover(self.people["bob"], self.rid, {"request_id": "race-continue", "expected_version": self.row(self.rid)["version"]})
        (self.project / "late.py").write_text("y = 2\n")
        with self.assertRaises(Error) as caught:
            self.service.operate(self.people["alice"], self.rid, "continue", {
                "request_id": "race-continue-2", "expected_version": self.row(self.rid)["version"], "task_id": "code"})
        self.assertEqual(caught.exception.code, "takeover_in_progress")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.head)   # D73's commit did not run first


class MaskTests(MoveCase):
    def test_a_credential_or_the_old_workers_token_in_a_handoff_never_reaches_the_new_boundary(self):
        github = "ghp_" + "A1b2C3d4" * 5
        handoff = self.alice_w.path(self.rid) / "attempts" / self.aid / "handoff.md"
        handoff.write_text(f"# 交接\ntoken={github}\nworker={TOKENS['alice']}\n")
        row = self.take_over()
        moved = json.loads(dict(next(iter([self.attempt()])))["evidence"])["handoff_path"]
        text = Path(moved).read_text()
        self.assertTrue(moved.startswith(str(self.bob_w.root)))
        self.assertNotIn(github, text)
        self.assertNotIn(TOKENS["alice"], text)
        self.assertEqual(row["worker_id"], "bob-b")

    def attempt(self):
        with self.db.read() as db:
            return db.execute("SELECT evidence FROM attempts WHERE id=?", (self.aid,)).fetchone()


class AssignTests(MoveCase):
    def test_assigning_ones_own_boundary_to_a_taken_over_session_runs_it_as_oneself(self):
        from octopus.domain import unserved
        self.take_over()
        with self.db.transaction() as db:   # bob's boundary went away: the Session waits for an environment
            db.execute("UPDATE runs SET status='waiting_worker' WHERE id=?", (self.rid,))
        self.service.operate(self.people["alice"], self.rid, "assign-worker", {
            "request_id": "assign-own", "expected_version": self.row(self.rid)["version"], "worker_id": "alice-b"})
        row = self.row(self.rid)
        self.assertEqual((row["worker_id"], row["executor_id"]), ("alice-b", self.ids["alice"]))
        with self.db.read() as db:
            self.assertIsNone(unserved(db, row))


class CredentialTests(MoveCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            for cid, owner in (("cred-a", "alice"), ("cred-b", "bob")):
                db.execute("INSERT INTO credentials(id,name,kind,host,scope,owner_id,access,hint,secret,repos,created_by,created,updated)"
                           " VALUES(?,?,'github_pat','github.com','user',?,'write','…1a2b','x','[\"o/r\"]',?,1,1)",
                           (cid, owner + " 的 GitHub", self.ids[owner], self.ids[owner]))
            self.pinned = {"source": credentials.binding(db, db.execute("SELECT * FROM credentials WHERE id='cred-a'").fetchone(),
                                                         self.people["alice"])}
        self.run_row = {**self.row(self.rid), "source": dumps({"type": "git", "url": "https://github.com/o/r.git"}),
                        "executor_id": self.ids["bob"], "worker_id": "bob-b"}

    def test_the_creators_personal_credential_gives_way_to_the_takers_own(self):
        with self.db.read() as db:
            rebound = takeover._rebound(db, self.pinned, self.run_row, self.people["bob"], 1)
        self.assertEqual(rebound["source"]["id"], "cred-b")

    def test_without_one_of_theirs_it_is_removed(self):
        with self.db.transaction() as db:
            db.execute("DELETE FROM credentials WHERE id='cred-b'")
        with self.db.read() as db:
            rebound = takeover._rebound(db, self.pinned, self.run_row, self.people["bob"], 1)
        self.assertIsNone(rebound["source"])
        self.assertEqual((rebound["removed"]["id"], rebound["removed"]["reason"]), ("cred-a", "takeover"))


if __name__ == "__main__":
    import unittest
    unittest.main()
