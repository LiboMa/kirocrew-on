"""Baseline import copies only the manifest, never runtime data, and records provenance."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import import_baseline as importer  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ImportBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source, self.legacy, self.dest = root / "source", root / "legacy", root / "dest"
        for directory in (self.source / "octopus" / "__pycache__", self.source / "state", self.legacy, self.dest):
            directory.mkdir(parents=True)
        (self.source / "octopus" / "db.py").write_text("SCHEMA = 1\n")
        (self.source / "octopus" / "__pycache__" / "db.cpython-312.pyc").write_bytes(b"x")
        (self.source / "api.py").write_text("NEW = 1\n")
        (self.legacy / "api.py").write_text("OLD = 1\n")
        (self.source / "poc.py").write_text("same\n")
        (self.legacy / "poc.py").write_text("same\n")
        (self.source / "state" / "secret.token").write_text("never")
        git(self.source, "init", "-q")
        git(self.source, "add", "poc.py")
        git(self.source, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        (self.source / "poc.py").write_text("same\n")

    def tearDown(self):
        self.temporary.cleanup()

    def test_copies_manifest_files_and_skips_runtime_data(self):
        records = importer.run(self.source, self.legacy, self.dest)
        paths = {r["path"] for r in records}
        self.assertIn("octopus/db.py", paths)
        self.assertIn("api.py", paths)
        self.assertFalse((self.dest / "state").exists())
        self.assertFalse((self.dest / "octopus" / "__pycache__").exists())

    def test_records_hash_git_status_and_legacy_comparison(self):
        records = {r["path"]: r for r in importer.run(self.source, self.legacy, self.dest)}
        self.assertEqual(len(records["api.py"]["sha256"]), 64)
        self.assertEqual(records["api.py"]["legacy"], "differs")
        self.assertEqual(records["poc.py"]["legacy"], "identical")
        self.assertEqual(records["octopus/db.py"]["git_status"], "untracked")
        self.assertEqual(records["poc.py"]["git_status"], "committed")
        text = (self.dest / "docs" / "PROVENANCE.md").read_text()
        self.assertIn(records["api.py"]["sha256"], text)

    def test_refuses_to_overwrite_existing_files(self):
        (self.dest / "api.py").write_text("refactored\n")
        with self.assertRaises(FileExistsError):
            importer.run(self.source, self.legacy, self.dest)
        self.assertEqual((self.dest / "api.py").read_text(), "refactored\n")


if __name__ == "__main__":
    unittest.main()
