"""D37 §11.1 (review fixes): the baseline a test task's tests run on is the Session's base with the task's own change
(from where the task started to its candidate) applied three-way; it is skipped — never judged — when that cannot be
done faithfully, and the switch never loses workspace state that is not in the candidate."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from octopus import sources


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()


class BaselineTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "p"
        self.project.mkdir()
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "config", "user.email", "t@t")
        git(self.project, "config", "user.name", "t")
        (self.project / ".gitignore").write_text("node_modules/\n*.local.json\n")
        self.base = self.commit({"README.md": "readme\n", "old.py": "old = 1\n"})

    def commit(self, files, remove=()):
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)
        for name in remove:
            git(self.project, "rm", "-q", name)
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "--allow-empty", "-m", "c")
        return git(self.project, "rev-parse", "HEAD")

    def test_no_earlier_change_is_skipped(self):
        tests = self.commit({"tests/test_old.py": "t\n"})
        with self.assertRaises(ValueError) as caught:
            sources.baseline_apply(self.project, self.base, self.base, tests)
        self.assertIn("此前没有源码改动", str(caught.exception))
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")

    def test_the_tasks_change_is_applied_three_way(self):
        start = self.commit({"calc.py": "def f():\n    return 1\n"})
        tests = self.commit({"tests/test_calc.py": "t\n"})
        sources.baseline_apply(self.project, self.base, start, tests)
        self.assertFalse((self.project / "calc.py").exists())            # the implementation is not on the baseline
        self.assertTrue((self.project / "tests/test_calc.py").exists())  # the task's own change is
        self.assertTrue((self.project / "old.py").exists())
        sources.baseline_restore(self.project, tests)
        self.assertTrue((self.project / "calc.py").exists())
        self.assertEqual(git(self.project, "status", "--porcelain", "--untracked-files=no"), "")

    def test_a_test_task_that_edits_the_implementation_cannot_be_laid_on_the_baseline(self):
        start = self.commit({"calc.py": "def f():\n    return 1\n"})
        tests = self.commit({"tests/test_calc.py": "t\n", "calc.py": "def f():\n    return 1\n\n__all__ = ['f']\n"})
        with self.assertRaises(ValueError) as caught:
            sources.baseline_apply(self.project, self.base, start, tests)
        self.assertIn("calc.py", str(caught.exception))
        self.assertTrue((self.project / "calc.py").exists())   # nothing was switched

    def test_uncommitted_tracked_edits_are_never_overwritten(self):
        start = self.commit({"calc.py": "x\n", ".env.development": "API=old\n"})
        tests = self.commit({"tests/test_calc.py": "t\n"})
        (self.project / ".env.development").write_text("API=new\n")   # left out of candidates, only in the workspace
        with self.assertRaises(ValueError) as caught:
            sources.baseline_apply(self.project, self.base, start, tests)
        self.assertIn(".env.development", str(caught.exception))
        self.assertEqual((self.project / ".env.development").read_text(), "API=new\n")

    def test_a_local_file_where_the_baseline_has_one_is_never_overwritten(self):
        start = self.commit({"calc.py": "x\n"}, remove=["old.py"])
        tests = self.commit({"tests/test_calc.py": "t\n"})
        (self.project / "old.py").write_text("my local copy\n")   # untracked now; the baseline still has old.py
        with self.assertRaises(ValueError) as caught:
            sources.baseline_apply(self.project, self.base, start, tests)
        self.assertIn("old.py", str(caught.exception))
        self.assertEqual((self.project / "old.py").read_text(), "my local copy\n")

    def test_retries_lay_the_whole_task_on_the_baseline(self):
        start = self.commit({"calc.py": "x\n"})
        self.commit({"tests/test_calc.py": "t\n", "tests/test_util.py": "broken\n"})   # attempt 1
        retry = self.commit({"tests/test_util.py": "fixed\n"})                          # attempt 2 fixes one file
        sources.baseline_apply(self.project, self.base, start, retry)
        self.assertTrue((self.project / "tests/test_calc.py").exists())   # from attempt 1: still the task's change
        sources.baseline_restore(self.project, retry)

    def test_names_are_measured_from_where_the_task_started(self):
        start = self.commit({"src/a.py": "a = 1\n"})
        self.commit({"src/a.py": "a = 2\n", "README.md": "new\n"})   # attempt 1 touched a locked file
        reverted = self.commit({"src/a.py": "a = 1\n"})              # attempt 2 put it back
        self.assertEqual(sources.changed_names(self.project, start, reverted), ["README.md"])

    def test_check_artifacts_are_listed(self):
        self.commit({"calc.py": "x\n"})
        (self.project / ".coverage").write_text("c")
        (self.project / "calc.py").write_text("changed by a check\n")
        self.assertEqual(sorted(sources.dirty_paths(self.project)), [".coverage", "calc.py"])
