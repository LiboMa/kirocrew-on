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
KEY = b"k" * 32


class PreviewTests(unittest.TestCase):
    def assertRejected(self, ticket, code, key=KEY, now=1000):
        with self.assertRaises(Error) as caught:
            preview_run(ticket, key, now=now)
        self.assertEqual((caught.exception.status, caught.exception.code), (403, code))

    def test_tickets_cannot_be_forged_reused_for_another_run_or_used_late(self):
        ticket = preview_ticket(RID, KEY, now=1000)
        self.assertEqual(preview_run(ticket, KEY, now=1000), RID)
        self.assertRejected(ticket, "preview_expired", now=1000 + 3601)
        self.assertRejected(ticket.replace(RID, "run-" + "b" * 32), "preview_invalid")
        self.assertRejected(ticket[:-2] + "xx", "preview_invalid")
        self.assertRejected("garbage", "preview_invalid")
        self.assertRejected(ticket, "preview_invalid", key=b"o" * 32)

    def test_the_key_is_kept_in_the_data_directory_so_tickets_survive_an_api_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ticket = preview_ticket(RID, preview_key(Path(directory)), now=1000)
            self.assertEqual(preview_run(ticket, preview_key(Path(directory)), now=1000), RID)
            self.assertEqual(os.stat(Path(directory, "preview.key")).st_mode & 0o777, 0o600)
            Path(directory, "preview.key").write_bytes(b"short")
            self.assertEqual(len(preview_key(Path(directory))), 32)
            self.assertRejected(ticket, "preview_invalid", key=preview_key(Path(directory)))

    def test_a_restarted_server_accepts_tickets_issued_before_the_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ticket = preview_ticket(RID, preview_key(Path(directory)))
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
                self.assertEqual(fetch(preview_ticket(RID, b"o" * 32)), (403, "preview_invalid"))
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
                self.assertEqual(node.files({"run_id": RID, "path": ""}), {"entry": "dist/index.html"})
                self.assertEqual(node.files({"run_id": RID, "path": "dist/index.html"})["size"], 13)
                for bad in ("../outside.txt", ".env", "dist/../../outside.txt", "missing.js"):
                    with self.subTest(path=bad), self.assertRaises(Error):
                        node.files({"run_id": RID, "path": bad})


if __name__ == "__main__":
    unittest.main()
