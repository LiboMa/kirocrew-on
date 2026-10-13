"""Preparing a session's project: a new project starts as a Git repository like a cloned one (owner, 2026-10-13)."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from octopus import sources

RID = "run-" + "3" * 32


class NewProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / RID
        self.run.mkdir()
        (self.run / "workflow-run.json").write_text(json.dumps({"id": RID}))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.run / "workspace/project", capture_output=True, text=True)

    def test_a_new_project_is_a_repository_with_a_first_commit(self):
        record = sources.prepare_project(self.run, new=True)
        self.assertEqual((record["kind"], record["status"], record["branch"]), ("new", "ready", "octopus/" + RID))
        self.assertRegex(record["commit"], r"^[a-f0-9]{40}$")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), record["commit"])
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "octopus/" + RID)
        self.assertEqual(self.git("log", "--format=%an|%s").stdout.strip(), "KiroCrew ON|KiroCrew ON：新建项目")
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        self.assertEqual(sources.project_dir(self.run), self.run / "workspace/project")

    def test_preparing_again_keeps_the_same_repository(self):
        first = sources.prepare_project(self.run, new=True)["commit"]
        self.assertEqual(sources.prepare_project(self.run, new=True)["commit"], first)

    def test_a_new_project_prepared_before_this_change_stays_usable(self):
        record = sources.prepare_project(self.run, new=True)
        subprocess.run(["rm", "-rf", str(self.run / "workspace/project/.git")], check=True)
        sources._write(self.run, {**record, "commit": None, "branch": None})
        self.assertEqual(sources.project_dir(self.run), self.run / "workspace/project")


if __name__ == "__main__":
    unittest.main()
