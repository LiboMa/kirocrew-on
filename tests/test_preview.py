"""Web preview of a run's output: signed short-lived tickets; the Worker serves project files read-only."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from octopus.db import Database
from octopus.security import Error
from octopus.server import preview_key, preview_run, preview_ticket, server
from octopus.worker import Worker

RID = "run-" + "a" * 32
USER = "user-" + "c" * 32
KEY = b"k" * 32


class PreviewTests(unittest.TestCase):
    def assertRejected(self, ticket, code, key=KEY, now=1000):
        with self.assertRaises(Error) as caught:
            preview_run(ticket, key, now=now)
        self.assertEqual((caught.exception.status, caught.exception.code), (403, code))

    def test_tickets_cannot_be_forged_reused_for_another_run_or_used_late(self):
        ticket = preview_ticket(RID, USER, KEY, now=1000)
        self.assertEqual(preview_run(ticket, KEY, now=1000), (RID, USER))
        self.assertEqual(preview_run(ticket, KEY, now=1000 + 600), (RID, USER))
        self.assertRejected(ticket, "preview_expired", now=1000 + 601)   # 10 minutes (B6)
        self.assertRejected(ticket.replace(RID, "run-" + "b" * 32), "preview_invalid")
        self.assertRejected(ticket.replace(USER, "user-" + "d" * 32), "preview_invalid")
        self.assertRejected(f"{RID}.2000.{ticket.rsplit('.', 1)[1]}", "preview_invalid")   # the old format
        self.assertRejected(ticket[:-2] + "xx", "preview_invalid")
        self.assertRejected("garbage", "preview_invalid")
        self.assertRejected(ticket, "preview_invalid", key=b"o" * 32)

    def test_the_key_is_kept_in_the_data_directory_so_tickets_survive_an_api_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ticket = preview_ticket(RID, USER, preview_key(Path(directory)), now=1000)
            self.assertEqual(preview_run(ticket, preview_key(Path(directory)), now=1000), (RID, USER))
            self.assertEqual(os.stat(Path(directory, "preview.key")).st_mode & 0o777, 0o600)
            Path(directory, "preview.key").write_bytes(b"short")
            self.assertEqual(len(preview_key(Path(directory))), 32)
            self.assertRejected(ticket, "preview_invalid", key=preview_key(Path(directory)))

    def test_a_restarted_server_accepts_tickets_issued_before_the_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ticket = preview_ticket(RID, USER, preview_key(Path(directory)))
            httpd = server(Database(Path(directory)), 0, host="127.0.0.1")
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                def fetch(ticket):
                    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
                    connection.request("GET", f"/preview/{ticket}/index.html")
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    connection.close()
                    return response.status, body["code"]
                # The ticket passes; the run itself does not exist on this fresh platform.
                self.assertEqual(fetch(ticket), (404, "not_found"))
                self.assertEqual(fetch(preview_ticket(RID, USER, b"o" * 32)), (403, "preview_invalid"))
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_the_worker_serves_project_files_only(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            (project / "dist").mkdir(parents=True)
            (project / "dist/index.html").write_text("<h1>demo</h1>")
            (project / ".env").write_text("SECRET=1")
            (Path(directory) / "outside.txt").write_text("no")
            node = Worker(Path(directory) / "worker", "x" * 40)
            with patch("octopus.worker.workspace.project_dir", return_value=project), patch.object(node, "path", return_value=project):
                self.assertEqual(node.files({"run_id": RID, "path": ""}), {"entry": "dist/index.html", "pages": ["dist/index.html"]})
                self.assertEqual(node.files({"run_id": RID, "path": "dist/index.html"})["size"], 13)
                for bad in ("../outside.txt", ".env", "dist/../../outside.txt", "missing.js"):
                    with self.subTest(path=bad), self.assertRaises(Error):
                        node.files({"run_id": RID, "path": bad})


class OutputPagesTests(unittest.TestCase):
    """EC2 run-968d6e37 / run-78526202 (2026-10-22): the reports were written to artifacts/<name>/index.html and
    report/index.html, and the preview answered 「未找到可预览的 index.html」. Any HTML the Session produced is a page."""
    def pages(self, files, mtimes=None):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            for name, text in files.items():
                (project / name).parent.mkdir(parents=True, exist_ok=True)
                (project / name).write_text(text)
            for name, when in (mtimes or {}).items():
                os.utime(project / name, (when, when))
            node = Worker(Path(directory) / "worker", "x" * 40)
            with patch("octopus.worker.workspace.project_dir", return_value=project), patch.object(node, "path", return_value=project):
                return node.files({"run_id": RID, "path": ""})

    def test_a_report_in_a_subdirectory_is_previewed(self):
        self.assertEqual(self.pages({"report/index.html": "<h1>报告</h1>", "README.md": "x"}),
                         {"entry": "report/index.html", "pages": ["report/index.html"]})

    def test_the_newest_output_comes_first_and_dependencies_are_not_pages(self):
        found = self.pages({"artifacts/research/report.html": "a", "artifacts/report/index.html": "b",
                            "LightRAG/lightrag_webui/index.html": "c",
                            "LightRAG/.venv/lib/site-packages/x/index.html": "d", "web/node_modules/y/index.html": "e",
                            ".octopus/notes.html": "f", "venv/lib/z.html": "g", "pkg/site-packages/w.html": "h"},
                           {"LightRAG/lightrag_webui/index.html": 1000, "artifacts/research/report.html": 2000,
                            "artifacts/report/index.html": 3000})
        self.assertEqual(found["pages"], ["artifacts/report/index.html", "artifacts/research/report.html",
                                          "LightRAG/lightrag_webui/index.html"])
        self.assertEqual(found["entry"], "artifacts/report/index.html")

    def test_a_project_entry_still_comes_first(self):
        found = self.pages({"dist/index.html": "app", "report/summary.html": "r"},
                           {"dist/index.html": 1000, "report/summary.html": 2000})
        self.assertEqual(found, {"entry": "dist/index.html", "pages": ["dist/index.html", "report/summary.html"]})

    def test_nothing_to_preview_says_so(self):
        with self.assertRaises(Error) as caught:
            self.pages({"notes.md": "x"})
        self.assertEqual(caught.exception.code, "no_preview")


if __name__ == "__main__":
    unittest.main()
