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
                db.execute("UPDATE workers SET config=? WHERE id=?", (dumps({"url": url, "token": url}), wid))
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
