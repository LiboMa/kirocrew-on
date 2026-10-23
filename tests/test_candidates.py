"""D37 §3, §6: a task's candidate commit on the Session's branch (secrets keep the branch where it was), and a review's
check that the workspace is still the candidate it reviewed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from octopus import sources
from octopus.security import Error
from octopus.worker import serve
from tests.test_archive import RID, TOKEN, make_worker

ATTEMPT = "attempt-" + "5" * 32


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.node, self.project = make_worker(self.root)
        self.base = sources.read_record(self.node.path(RID))["commit"]

    def write(self, files):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)

    def commit(self, **extra):
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-1", "mode": "commit",
                                    "message": "KiroCrew ON：实现登录（run/code 第 1 次）", **extra})

    def agent_commit(self, message):
        git(self.project, "add", "-A")
        git(self.project, "-c", "user.email=a@a", "-c", "user.name=agent", "commit", "-q", "-m", message)

    def test_a_change_becomes_a_candidate_on_the_branch(self):
        self.write({"src/a.js": "export const a = 1;\nexport const b = 2;\n", "node_modules/x/i.js": "x"})
        result = self.commit()
        self.assertTrue(result["changed"])
        self.assertEqual((result["parent"], result["files"], result["insertions"], result["deletions"]), (self.base, 1, 2, 0))
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), result["sha"])
        self.assertEqual(git(self.project, "log", "-1", "--format=%an|%s", result["sha"]),
                         "KiroCrew ON|KiroCrew ON：实现登录（run/code 第 1 次）")
        self.assertNotIn("node_modules/x/i.js", git(self.project, "ls-tree", "-r", "--name-only", result["sha"]))
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")

    def test_no_change_is_the_parent_itself(self):
        first = self.commit()
        self.assertEqual((first["sha"], first["changed"]), (self.base, False))
        self.write({"src/a.js": "a"})
        second = self.commit()
        third = self.commit(parent=second["sha"])
        self.assertEqual((third["sha"], third["changed"], third["files"]), (second["sha"], False, 0))

    def test_agent_commits_are_kept(self):
        self.write({"src/a.js": "a"})
        self.agent_commit("agent: a")
        agents = git(self.project, "rev-parse", "HEAD")
        self.write({"src/b.js": "b"})
        result = self.commit()
        self.assertEqual(git(self.project, "rev-parse", result["sha"] + "^"), agents)
        self.assertEqual(result["files"], 2)

    def test_a_secret_leaves_the_branch_where_it_was(self):
        token = "ghp_" + "A1b2C3d4" * 5
        self.write({"src/x.js": f"const t = '{token}';"})
        result = self.commit()
        self.assertEqual(result["secrets"], ["src/x.js"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertNotIn(token, json.dumps(result))

    def test_a_rewritten_history_is_marked_not_fatal(self):
        self.write({"src/a.js": "a"})
        first = self.commit()
        git(self.project, "reset", "-q", "--hard", self.base)   # the Agent rewrote what came before
        self.write({"src/b.js": "b"})
        result = self.commit(parent=first["sha"])
        self.assertTrue(result["rewritten"])
        self.assertEqual(result["parent"], first["sha"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), result["sha"])

    def test_check_reports_head_and_pending_changes(self):
        self.write({"src/a.js": "a"})
        sha = self.commit()["sha"]
        check = lambda: self.node.candidate({"run_id": RID, "attempt_id": "attempt-2", "mode": "check", "expected": sha})
        self.assertEqual(check(), {"head": sha, "clean": True, "changed_files": [], "created": [], "tracked_only": True})
        self.write({".env": "SECRET=1", "node_modules/y/i.js": "y"})
        self.assertTrue(check()["clean"])   # what a download and a publish leave out does not count
        self.write({".coverage": "data", "reports/junit.xml": "<x/>", "dist/app.js": "built"})
        self.assertTrue(check()["clean"])   # what checks and test runs leave untracked is not a change to the code
        self.write({"src/a.js": "changed by the review"})
        self.assertEqual(check()["changed_files"], ["src/a.js"])
        self.assertFalse(check()["clean"])

    def rewind(self, expected):
        return self.node.candidate({"run_id": RID, "attempt_id": "attempt-2", "mode": "rewind", "expected": expected})

    def test_rewind_resets_a_reviewers_commits_and_edits(self):   # D57 §4: what a review changed is put back
        self.write({"src/a.js": "a", "docs/guide.md": "指南\n"})
        c1 = self.commit()["sha"]
        # left out of every candidate, and what checks left: the reviewer's `git add -A` sweeps them into its commit
        self.write({".env": "SECRET=1", "node_modules/x/i.js": "x", "reports/junit.xml": "<x/>"})
        self.write({"docs/review.md": "审查结论\n", "docs/guide.md": "审查者改了指南\n"})
        self.agent_commit("review: 写下审查结论")
        self.write({"src/a.js": "审查者改了实现", "scratch.txt": "未跟踪"})
        result = self.rewind(c1)
        self.assertEqual((result["head"], result["clean"], result["changed_files"], result["rewound"]), (c1, True, [], 1))
        self.assertEqual(sorted(result["reverted"]), ["docs/guide.md", "docs/review.md", "reports/junit.xml", "src/a.js"])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), c1)
        self.assertEqual(git(self.project, "rev-parse", "--abbrev-ref", "HEAD"),
                         sources.read_record(self.node.path(RID))["branch"])
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(((self.project / "src/a.js").read_text(), (self.project / "docs/guide.md").read_text()), ("a", "指南\n"))
        self.assertFalse((self.project / "docs/review.md").exists())
        # untracked files stay, and so does what a candidate leaves out (never the reviewer's to drop)
        self.assertEqual((self.project / "scratch.txt").read_text(), "未跟踪")
        self.assertEqual((self.project / ".env").read_text(), "SECRET=1")
        self.assertTrue((self.project / "node_modules/x/i.js").is_file())

    def test_rewind_without_commits_puts_back_the_edits(self):
        self.write({"docs/guide.md": "指南\n"})
        c1 = self.commit()["sha"]
        self.write({"docs/guide.md": "审查结论写进了文档\n"})
        result = self.rewind(c1)
        self.assertEqual(result, {"head": c1, "clean": True, "changed_files": [], "reverted": ["docs/guide.md"], "rewound": 0,
                                  "removed": [], "created": [], "tracked_only": True})
        self.assertEqual((self.project / "docs/guide.md").read_text(), "指南\n")
        self.assertEqual(self.rewind(c1)["reverted"], [])   # nothing left to put back

    def test_rewind_keeps_the_tracked_files_candidates_leave_out(self):
        self.write({"node_modules/x/i.js": "v1"})
        self.agent_commit("a vendored dependency")   # the project tracks it; a candidate never commits its changes
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        self.write({"node_modules/x/i.js": "patched"})   # the implementer's, left in the workspace
        self.write({"src/a.js": "审查者改了实现"})
        self.agent_commit("review")   # sweeps the implementer's patch in with its own change
        result = self.rewind(c1)
        self.assertEqual((result["head"], result["clean"], result["reverted"]), (c1, True, ["src/a.js"]))
        self.assertEqual(((self.project / "node_modules/x/i.js").read_text(), (self.project / "src/a.js").read_text()),
                         ("patched", "a"))

    def test_rewind_refuses_a_head_that_does_not_descend_from_the_candidate(self):
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        git(self.project, "reset", "-q", "--hard", self.base)   # the reviewer rewrote the branch: the candidate is gone
        self.write({"src/b.js": "b"})
        self.agent_commit("review: elsewhere")
        head = git(self.project, "rev-parse", "HEAD")
        for expected in (c1, "c" * 40):
            with self.subTest(expected=expected), self.assertRaises(Error) as caught:
                self.rewind(expected)
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), head)
        self.assertEqual((self.project / "src/b.js").read_text(), "b")

    def test_rewind_refuses_a_workspace_off_the_sessions_branch(self):
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        git(self.project, "checkout", "-q", "-b", "review-notes")
        self.write({"docs/review.md": "审查结论\n"})
        self.agent_commit("review")
        head = git(self.project, "rev-parse", "HEAD")
        with self.assertRaises(Error) as caught:
            self.rewind(c1)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), head)

    def snapshot(self, attempt=ATTEMPT):   # what the Worker records when it dispatches a review (D57 §4)
        folder = self.node.path(RID) / "attempts" / attempt
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "untracked.json").write_text(json.dumps({"paths": sources.untracked(self.project)}))
        return folder

    def review(self, mode, expected, attempt=ATTEMPT):
        return self.node.candidate({"run_id": RID, "attempt_id": attempt, "mode": mode, "expected": expected})

    def test_rewind_moves_out_files_the_reviewer_created(self):   # D57 §4: a review's new files never reach a candidate
        self.write({"src/a.js": "a", ".gitignore": "build/\n"})
        c1 = self.commit()["sha"]
        self.write({"notes.txt": "实现者的笔记", "build/old.log": "old", "node_modules/x/i.js": "x"})
        folder = self.snapshot()
        self.assertEqual(json.loads((folder / "untracked.json").read_text()), {"paths": ["notes.txt"]})
        self.write({"review.md": "审查结论\n", "docs/new.md": "新文档\n", "build/out.log": "log", "node_modules/x/y.js": "y"})
        check = self.review("check", c1)
        self.assertEqual((check["head"], check["clean"], check["changed_files"]), (c1, True, []))
        self.assertEqual((check["created"], check["tracked_only"]), (["docs/new.md", "review.md"], False))
        result = self.review("rewind", c1)
        self.assertEqual((result["head"], result["clean"], result["reverted"], result["rewound"]), (c1, True, [], 0))
        self.assertEqual((result["removed"], result["created"]), (["docs/new.md", "review.md"], []))
        moved = folder / "review-removed"
        self.assertEqual(((moved / "review.md").read_text(), (moved / "docs/new.md").read_text()), ("审查结论\n", "新文档\n"))
        self.assertFalse((self.project / "review.md").exists())
        self.assertFalse((self.project / "docs").exists())   # left empty by the move
        self.assertTrue(self.project.is_dir())
        # the implementer's untracked file, ignored files and what a candidate leaves out stay where they are
        self.assertEqual((self.project / "notes.txt").read_text(), "实现者的笔记")
        for name in ("build/out.log", "build/old.log", "node_modules/x/y.js", "node_modules/x/i.js"):
            self.assertTrue((self.project / name).is_file(), name)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), c1)
        self.assertEqual(self.review("check", c1)["created"], [])
        self.assertEqual(self.review("rewind", c1)["removed"], [])   # nothing left to move

    def test_rewind_without_a_snapshot_moves_nothing(self):   # an Attempt dispatched before D57 §4 recorded none
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        self.write({"review.md": "审查结论\n"})
        self.assertEqual(self.review("check", c1), {"head": c1, "clean": True, "changed_files": [], "created": [],
                                                    "tracked_only": True})
        result = self.review("rewind", c1)
        self.assertEqual((result["removed"], result["created"], result["tracked_only"]), ([], [], True))
        self.assertEqual((self.project / "review.md").read_text(), "审查结论\n")
        self.assertFalse((self.node.path(RID) / "attempts" / ATTEMPT / "review-removed").exists())
        self.assertEqual(sources.rewind(self.project, c1)["removed"], [])

    def test_a_review_dispatch_records_the_untracked_snapshot(self):
        native = self.node.native
        self.node.native = lambda path, body=None, method=None, **kw: {"id": "abcdef12"} if path == "/api/spawn" \
            else native(path, body, method)
        run = self.node.path(RID)
        self.assertFalse((run / "attempts").resolve().is_relative_to(self.project.resolve()))   # never in a candidate
        self.write({"notes.txt": "实现者的笔记", ".env": "SECRET=1", "node_modules/x/i.js": "x"})

        def dispatch(attempt, kind, **extra):
            return self.node.dispatch({"run_id": RID, "attempt_id": attempt, "input": "需求", "upstream": [], "quality": True,
                                       "spec": {"id": kind, "name": kind, "tool": "claude", "model": "auto", "effort": "",
                                                "prompt": kind},
                                       "contract": {"kind": kind, "acceptance": ["完成"]}, **extra})
        held, listing = [], sources.untracked

        def probe():   # from another thread, as another request of this Worker would
            free = self.node.lock.acquire(blocking=False)
            held.append(not free)
            if free:
                self.node.lock.release()

        def listed(project):   # a large workspace is slow to list: never while this Worker's other requests wait
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            return listing(project)
        with patch.object(sources, "untracked", side_effect=listed):
            self.assertEqual(dispatch(ATTEMPT, "review")["status"], "running")
        self.assertEqual(held, [False])
        snapshot = run / "attempts" / ATTEMPT / "untracked.json"
        self.assertEqual(json.loads(snapshot.read_text()), {"paths": ["notes.txt"]})
        self.write({"review.md": "审查结论\n"})
        with patch.object(sources, "untracked", side_effect=listed) as again:
            dispatch(ATTEMPT, "review")   # the same Attempt again: the snapshot of its first dispatch stays, not relisted
        self.assertEqual((json.loads(snapshot.read_text()), again.call_count), ({"paths": ["notes.txt"]}, 0))
        other = "attempt-" + "6" * 32
        self.assertEqual(dispatch(other, "implement")["status"], "running")
        self.assertFalse((run / "attempts" / other / "untracked.json").exists())
        # a listing that fails never holds the review up: it runs without a snapshot
        failed = "attempt-" + "7" * 32
        with patch.object(sources, "untracked", side_effect=ValueError("Git 操作失败（exit 128）")):
            self.assertEqual(dispatch(failed, "review")["status"], "running")
        self.assertFalse((run / "attempts" / failed / "untracked.json").exists())
        self.assertTrue((run / "attempts" / failed / "dispatch.json").is_file())

    def test_a_folder_the_reviewer_made_a_repository_keeps_the_files_it_held(self):
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        self.write({"tools/kept.sh": "echo kept\n"})
        folder = self.snapshot()
        self.assertEqual(json.loads((folder / "untracked.json").read_text()), {"paths": ["tools/kept.sh"]})
        git(self.project, "init", "-q", "tools")   # git now lists the folder as one entry, tools/
        self.write({"tools/new.md": "新文件\n"})
        self.assertEqual(self.review("check", c1)["created"], ["tools/"])
        result = self.review("rewind", c1)
        self.assertEqual((result["removed"], result["created"]), ([], ["tools/"]))   # left in place, and said
        self.assertEqual((self.project / "tools/kept.sh").read_text(), "echo kept\n")
        self.assertEqual((self.project / "tools/new.md").read_text(), "新文件\n")
        self.assertFalse((folder / "review-removed").exists())

    def test_a_check_that_cannot_list_untracked_files_tells_tracked_files_only(self):   # never a failure of its own
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        self.snapshot()
        self.write({"review.md": "审查结论\n", "src/a.js": "审查者改了实现"})
        with patch.object(sources, "untracked", side_effect=ValueError("Git 操作超时（status，180 秒）")):
            self.assertEqual(self.review("check", c1), {"head": c1, "clean": False, "changed_files": ["src/a.js"],
                                                        "created": [], "tracked_only": True})

    def test_a_rewind_that_cannot_list_untracked_files_still_puts_back_tracked_files(self):   # nothing moved, and said
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        folder = self.snapshot()
        self.write({"review.md": "审查结论\n", "src/a.js": "审查者改了实现"})
        self.assertEqual(self.review("check", c1)["created"], ["review.md"])
        with patch.object(sources, "untracked", side_effect=ValueError("Git 操作超时（status，180 秒）")):
            result = self.review("rewind", c1)
        self.assertEqual((result["head"], result["clean"], result["reverted"], result["removed"], result["listing_failed"]),
                         (c1, True, ["src/a.js"], [], True))
        self.assertEqual(((self.project / "src/a.js").read_text(), (self.project / "review.md").read_text()),
                         ("a", "审查结论\n"))
        self.assertFalse((folder / "review-removed").exists())
        self.assertNotIn("listing_failed", self.review("rewind", c1))   # listed: the flag only when it failed

    def test_rewind_never_moves_a_path_outside_the_project(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("外部文件")
        self.write({"src/a.js": "a"})
        c1 = self.commit()["sha"]
        folder = self.snapshot()
        (self.project / "link.txt").symlink_to(outside / "secret.txt")   # the reviewer's links, out of the project
        (self.project / "linkdir").symlink_to(outside)
        result = self.review("rewind", c1)
        self.assertEqual(result["removed"], ["link.txt", "linkdir"])
        moved = folder / "review-removed"
        self.assertEqual((os.readlink(moved / "link.txt"), os.readlink(moved / "linkdir")),
                         (str(outside / "secret.txt"), str(outside)))   # moved as links, never followed
        self.assertFalse(os.path.lexists(self.project / "link.txt") or os.path.lexists(self.project / "linkdir"))
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["secret.txt"])
        self.assertEqual((outside / "secret.txt").read_text(), "外部文件")
        # a path that would resolve outside the project is left where it is, never moved
        (self.project / "via").symlink_to(outside)
        with patch.object(sources, "untracked", return_value=["../outside/secret.txt", "via/secret.txt", "/etc/hosts"]):
            self.assertEqual(sources.rewind(self.project, c1, keep=[], into=folder / "again")["removed"], [])
        self.assertEqual((outside / "secret.txt").read_text(), "外部文件")
        self.assertFalse((folder / "again").exists())

    def test_commit_ids_are_checked_before_git_sees_them(self):
        for bad in ("--output=/tmp/x", "HEAD", "abc"):
            with self.subTest(bad=bad), self.assertRaises(Error):
                self.commit(parent=bad)

    def test_the_route_is_served_and_unknown_routes_stay_404(self):
        server = serve(self.node, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def post(path, body):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method="POST",
                                             data=json.dumps(body).encode(),
                                             headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.loads(exc.read())
        status, body = post("/candidate", {"run_id": RID, "attempt_id": "attempt-1", "mode": "commit"})
        self.assertEqual((status, body["sha"]), (200, self.base))
        self.assertEqual(post("/no-such-route", {"run_id": RID})[0], 404)


if __name__ == "__main__":
    unittest.main()
