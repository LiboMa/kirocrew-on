"""D74: a Session taken over to another boundary arrives as a git bundle of its branch with the whole history; the new
workspace is the same repository — the same commits, branch and baseline — so its candidates and reviews still hold."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from octopus import sources

RID = "run-" + "4" * 32


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.old, self.new = root / "a" / RID, root / "b" / RID
        for run in (self.old, self.new):
            run.mkdir(parents=True)
            (run / "workflow-run.json").write_text(json.dumps({"id": RID}))

    def work(self, record):
        """Two tasks' commits on the Session's branch, as Agents leave them, then a full bundle of it."""
        project = self.old / "workspace/project"
        for name in ("a.txt", "b.txt"):
            (project / name).write_text(name)
            git(project, "add", name)
            git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
        bundle = Path(self.tmp.name) / "session.bundle"
        git(project, "bundle", "create", "-q", str(bundle), "refs/heads/" + record["branch"])
        return git(project, "rev-parse", "HEAD"), bundle

    def test_a_new_project_moves_with_its_commits_and_baseline(self):
        record = sources.prepare_project(self.old, new=True)
        head, bundle = self.work(record)
        adopted = sources.adopt_project(self.new, record, bundle)
        project = sources.project_dir(self.new)
        self.assertEqual(git(project, "rev-parse", "HEAD"), head)
        self.assertEqual(git(project, "branch", "--show-current"), record["branch"])
        self.assertEqual((adopted["commit"], adopted["kind"], adopted["branch"]), (record["commit"], "new", record["branch"]))
        git(project, "merge-base", "--is-ancestor", record["commit"], head)
        self.assertEqual(git(project, "remote"), "")   # a new project has no origin
        self.assertEqual(git(project, "status", "--porcelain"), "")

    def test_a_cloned_source_keeps_its_origin_and_cannot_push(self):
        seed = Path(self.tmp.name) / "seed"
        git(Path(self.tmp.name), "init", "-q", "-b", "main", str(seed))
        (seed / "README.md").write_text("hello")
        git(seed, "add", "README.md")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        record = sources.prepare_project(self.old, source=str(seed))
        head, bundle = self.work(record)
        remote = {**record, "kind": "git", "source": "https://git.example.com/o/r.git"}   # what an API Session records
        sources.adopt_project(self.new, remote, bundle)
        project = sources.project_dir(self.new)
        self.assertEqual(git(project, "rev-parse", "HEAD"), head)
        self.assertEqual(git(project, "remote", "get-url", "origin"), "https://git.example.com/o/r.git")
        self.assertEqual(git(project, "remote", "get-url", "--push", "origin"), "DISABLED_NO_PUSH")

    def test_a_bundle_that_is_not_this_sessions_branch_is_refused(self):
        record = sources.prepare_project(self.old, new=True)
        _, bundle = self.work(record)
        with self.assertRaises(ValueError):   # another baseline: not this Session's history
            sources.adopt_project(self.new, {**record, "commit": "f" * 40}, bundle)
        self.assertFalse((self.new / "workspace/project").exists())
        with self.assertRaises(ValueError):   # another branch than this Session's
            sources.adopt_project(self.new, {**record, "branch": "octopus/run-" + "5" * 32}, bundle)
        project = self.old / "workspace/project"
        git(project, "branch", "extra")
        two = Path(self.tmp.name) / "two.bundle"
        git(project, "bundle", "create", "-q", str(two), "refs/heads/" + record["branch"], "refs/heads/extra")
        with self.assertRaises(ValueError):   # one head only
            sources.adopt_project(self.new, record, two)
        other = Path(self.tmp.name) / "other.bundle"   # one head, under another name than this Session's branch
        git(project, "bundle", "create", "-q", str(other), "refs/heads/extra")
        with self.assertRaises(ValueError):
            sources.adopt_project(self.new, record, other)
        self.assertFalse((self.new / "workspace/project").exists())

    def test_a_baseline_that_is_not_an_ancestor_is_refused(self):
        record = sources.prepare_project(self.old, new=True)
        _, bundle = self.work(record)
        project = self.old / "workspace/project"
        git(project, "checkout", "-q", "--orphan", "elsewhere")   # a real commit, not on the Session's branch
        git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "其他")
        unrelated = git(project, "rev-parse", "HEAD")
        git(project, "checkout", "-q", record["branch"])
        single = Path(self.tmp.name) / "single.bundle"
        git(project, "bundle", "create", "-q", str(single), "refs/heads/" + record["branch"])
        with self.assertRaises(ValueError):   # the object is unknown to this bundle's history
            sources.adopt_project(self.new, {**record, "commit": unrelated}, single)


if __name__ == "__main__":
    unittest.main()
