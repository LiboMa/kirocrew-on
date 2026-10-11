"""Web preview of a run's output: signed short-lived tickets; the Worker serves project files read-only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from octopus.security import Error
from octopus.server import preview_run, preview_ticket
from octopus.worker import Worker

RID = "run-" + "a" * 32


class PreviewTests(unittest.TestCase):
    def test_tickets_cannot_be_forged_reused_for_another_run_or_used_late(self):
        ticket = preview_ticket(RID, now=1000)
        self.assertEqual(preview_run(ticket, now=1000), RID)
        self.assertIsNone(preview_run(ticket, now=1000 + 3601))
        other = ticket.replace(RID, "run-" + "b" * 32)
        self.assertIsNone(preview_run(other, now=1000))
        self.assertIsNone(preview_run(ticket[:-2] + "xx", now=1000))
        self.assertIsNone(preview_run("garbage", now=1000))

    def test_the_worker_serves_project_files_only(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            (project / "dist").mkdir(parents=True)
            (project / "dist/index.html").write_text("<h1>demo</h1>")
            (project / ".env").write_text("SECRET=1")
            (Path(directory) / "outside.txt").write_text("no")
            node = Worker(Path(directory) / "worker", "x" * 40)
            with patch("octopus.worker.workspace.project_dir", return_value=project), patch.object(node, "path", return_value=project):
                self.assertEqual(node.files({"run_id": RID, "path": ""}), {"entry": "dist/index.html"})
                self.assertEqual(node.files({"run_id": RID, "path": "dist/index.html"})["size"], 13)
                for bad in ("../outside.txt", ".env", "dist/../../outside.txt", "missing.js"):
                    with self.subTest(path=bad), self.assertRaises(Error):
                        node.files({"run_id": RID, "path": bad})


if __name__ == "__main__":
    unittest.main()
