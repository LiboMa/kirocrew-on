"""MVP 0.2.0 P1 Task 6 (N §3–§6, S6, CE6): a Worker registered with a one-time enrollment code an administrator issued —
the code fixes where it registers and under which name; the Worker brings its own URL and token, which the platform
verifies at /health as it always does; the token never passes through a person or a command history. A wrong code is a
cheap 404 before anything else, and every rule of POST /v1/workers applies."""
import hashlib
import threading
from unittest import mock

from octopus.security import Error
from tests.test_access import AccessCase

HEALTH = {"isolation": "dedicated", "boundary": "b-enroll", "host_principal": "hp-enroll"}
WORKER = {"url": "https://10.0.0.7:8943", "token": "w" * 40}


class EnrollmentCase(AccessCase):
    def issue(self, token=None, **body):
        with self.http() as call:
            return call("POST", "/v1/workers/enrollments", token or self.tokens["alice"],
                        {"name": "bob-host", "owner_id": self.b["id"], **body})

    def enroll(self, call, code, health=HEALTH, **extra):
        with mock.patch("octopus.server.validate_worker", return_value=health) as checked:
            answer = call("POST", "/v1/workers/enroll", None, {"code": code, **WORKER, **extra})
        return answer, checked


class IssueTests(EnrollmentCase):
    def test_an_admin_issues_a_code_shown_once(self):
        status, issued = self.issue()
        self.assertEqual(status, 201)
        self.assertTrue(issued["code"].startswith("kce_"))
        self.assertIn("worker enroll", issued["command"])
        self.assertNotIn(issued["code"], issued["command"])   # the command reads it from the environment
        with self.db.read() as db:
            row = db.execute("SELECT * FROM worker_enrollments WHERE id=?", (issued["id"],)).fetchone()
        self.assertEqual(row["code_hash"], hashlib.sha256(issued["code"].encode()).hexdigest())
        self.assertAlmostEqual(row["expires"] - row["created"], 1800, delta=2)   # thirty minutes unless chosen
        with self.http() as call:
            status, listed = call("GET", "/v1/workers/enrollments", self.tokens["alice"])
        self.assertEqual(status, 200)
        self.assertNotIn(issued["code"], str(listed))

    def test_only_an_admin_issues_or_lists_codes(self):
        self.assertEqual(self.issue(self.tokens["bob"])[0], 403)
        with self.http() as call:
            self.assertEqual(call("GET", "/v1/workers/enrollments", self.tokens["bob"])[0], 403)

    def test_a_code_lasts_at_most_a_day(self):
        self.assertEqual(self.issue(expires_in=86401)[0], 400)
        self.assertEqual(self.issue(expires_in=86400)[0], 201)


class EnrollTests(EnrollmentCase):
    def test_a_code_registers_one_worker_where_it_was_bound(self):
        _, issued = self.issue()
        with self.http() as call:
            (status, made), checked = self.enroll(call, issued["code"])
        self.assertEqual((status, made["name"], made["scope"]), (201, "bob-host", "dedicated"))
        checked.assert_called_once()
        with self.db.read() as db:
            worker = db.execute("SELECT * FROM workers WHERE id=?", (made["id"],)).fetchone()
            audit = db.execute("SELECT detail FROM audit WHERE action='worker.enroll'").fetchone()
        self.assertEqual(worker["owner_id"], self.b["id"])
        self.assertNotIn("w" * 40, worker["config"])   # sealed
        self.assertNotIn("w" * 40, audit["detail"])
        self.assertNotIn("10.0.0.7", audit["detail"])
        with self.http() as call:
            (status, _), _ = self.enroll(call, issued["code"])
        self.assertEqual(status, 404)   # used

    def test_a_code_is_used_once_even_at_the_same_time(self):
        _, issued = self.issue()
        results = []
        with self.http() as call, mock.patch("octopus.server.validate_worker", return_value=HEALTH):
            def go():
                results.append(call("POST", "/v1/workers/enroll", None, {"code": issued["code"], **WORKER})[0])
            threads = [threading.Thread(target=go) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(results), [201, 404])
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workers WHERE name='bob-host'").fetchone()[0], 1)

    def test_a_wrong_or_late_code_answers_404_without_calling_the_worker(self):
        with self.http() as call:
            (status, answer), checked = self.enroll(call, "kce_" + "x" * 43)
        self.assertEqual((status, answer["code"]), (404, "enrollment_invalid"))
        checked.assert_not_called()
        _, issued = self.issue(expires_in=60)
        with self.db.transaction() as db:
            db.execute("UPDATE worker_enrollments SET expires=1 WHERE id=?", (issued["id"],))
        with self.http() as call:
            (status, _), checked = self.enroll(call, issued["code"])
        self.assertEqual(status, 404)
        checked.assert_not_called()

    def test_enroll_follows_the_registration_rules_and_failures_retire_the_code(self):
        with self.db.transaction() as db:   # an earlier Worker of the same boundary
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope,enabled) VALUES('w-old','old',?,?,'dedicated',0)",
                       (self.e["id"], '{"capabilities": {"boundary": "b-enroll"}}'))
        _, issued = self.issue()
        with self.http() as call:
            for attempt in range(5):
                (status, answer), _ = self.enroll(call, issued["code"])
                self.assertEqual((status, answer["code"]), (409, "worker_conflict"))
            (status, _), checked = self.enroll(call, issued["code"], health={**HEALTH, "boundary": "b-fresh"})
        self.assertEqual(status, 404)   # five failures retired it
        checked.assert_not_called()

    def test_a_revoked_code_is_gone(self):
        _, issued = self.issue()
        with self.http() as call:
            self.assertEqual(call("DELETE", f"/v1/workers/enrollments/{issued['id']}", self.tokens["alice"])[0], 200)
            (status, _), _ = self.enroll(call, issued["code"])
        self.assertEqual(status, 404)

    def test_enroll_is_rate_limited_per_source(self):
        with self.http() as call:
            statuses = [self.enroll(call, "kce_" + str(n) * 43)[0][0] for n in range(11)]
        self.assertEqual(statuses[:10], [404] * 10)
        self.assertEqual(statuses[10], 429)

    def test_check_only_registers_nothing(self):
        _, issued = self.issue()
        with self.http() as call:
            (status, answer), _ = self.enroll(call, issued["code"], check=True)
            self.assertEqual((status, answer["ok"], answer["name"]), (200, True, "bob-host"))
            (status, _), _ = self.enroll(call, issued["code"])
        self.assertEqual(status, 201)   # still unused after the check


class CliTests(EnrollmentCase):
    def test_the_cli_reads_the_code_from_the_environment_not_argv(self):
        import io
        import os
        import tempfile
        from pathlib import Path
        from octopus import __main__ as entry
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "worker.token"
            token.write_text("w" * 40)
            sent = {}

            class Fake:
                def __init__(self, url, token):
                    sent["platform"] = url

                def request(self, method, path, body=None):
                    sent.update(method=method, path=path, body=body)
                    return {"id": "worker-1", "name": "bob-host", "scope": "dedicated"}
            with mock.patch("octopus.client.Client", Fake), mock.patch.dict("os.environ", {"OCTOPUS_ENROLL_CODE": "kce_abc"}), \
                    mock.patch("sys.stdout", io.StringIO()):
                entry.main(["worker", "enroll", "--token-file", str(token), "--platform", "https://k.example",
                            "--url", "https://10.0.0.7:8943"])
            self.assertEqual((sent["method"], sent["path"]), ("POST", "/v1/workers/enroll"))
            self.assertEqual(sent["body"], {"code": "kce_abc", "url": "https://10.0.0.7:8943", "token": "w" * 40})
            with self.assertRaises(SystemExit):   # no --code: it would show in ps and shell history
                entry.main(["worker", "enroll", "--code", "kce_abc", "--platform", "https://k.example", "--url", "x"])
            with mock.patch("octopus.client.Client", Fake), mock.patch.dict("os.environ", {}, clear=False), \
                    mock.patch("sys.stdin", io.StringIO("kce_pasted\n")), mock.patch("sys.stdout", io.StringIO()):
                os.environ.pop("OCTOPUS_ENROLL_CODE", None)
                entry.main(["worker", "enroll", "--code-stdin", "--token-file", str(token), "--platform", "https://k.example",
                            "--url", "https://10.0.0.7:8943"])
            self.assertEqual(sent["body"]["code"], "kce_pasted")

    def test_the_command_shown_reads_the_code_from_stdin(self):
        _, issued = self.issue()
        self.assertIn("--code-stdin", issued["command"])
        # As installed on a host (no `octopus` on PATH; the token file is root's to read; review 2026-10-26)
        self.assertTrue(issued["command"].startswith(
            "PYTHONPATH=/opt/kirocrew-on/current /opt/kirocrew-on/venv/bin/python -m octopus "
            "--config /etc/kirocrew-on/boundary-<边界名>.toml worker enroll"), issued["command"])


class ApiDocumentTests(EnrollmentCase):
    def test_the_api_document_lists_the_enrollment_routes(self):
        from octopus.schema import openapi
        paths = openapi()["paths"]
        self.assertEqual(paths["/v1/workers/enroll"]["post"]["security"], [])   # no login: the code is the authority
        self.assertEqual(set(paths["/v1/workers/enrollments"]), {"get", "post"})
        self.assertIn("delete", paths["/v1/workers/enrollments/{id}"])


class LimiterTests(EnrollmentCase):
    def test_a_flood_from_one_source_costs_little_and_others_still_pass(self):   # review 2026-10-26
        import time as clock
        from octopus import enrollments
        sources, now = enrollments.Sources(), 1000.0
        start = clock.perf_counter()
        allowed = sum(sources.allow("6.6.6.6", now + n * 0.0001) for n in range(200000))
        self.assertLess(clock.perf_counter() - start, 2.0)   # constant work a request, not the length of its history
        self.assertEqual(allowed, enrollments.PER_MINUTE)
        self.assertLessEqual(len(sources.seen["6.6.6.6"]), enrollments.PER_MINUTE)
        self.assertTrue(sources.allow("7.7.7.7", now + 30))
        self.assertTrue(sources.allow("6.6.6.6", now + 200))   # a minute after its last try the source may try again

    def test_many_sources_do_not_grow_the_table_without_bound(self):
        from octopus import enrollments
        sources = enrollments.Sources()
        for n in range(30000):
            sources.allow(f"10.0.{n // 250}.{n % 250}", 1000.0 + n * 0.01)
        sources.allow("1.1.1.1", 2000.0)   # well past a minute after the earliest ones
        self.assertLess(len(sources.seen), 30000)


class HardeningTests(EnrollmentCase):   # review 2026-10-26 (BACKLOG「MVP 0.2.0 P1 打底的终审延后项」)
    def enroll_raising(self, call, code, error):
        with mock.patch("octopus.server.validate_worker", side_effect=error):
            return call("POST", "/v1/workers/enroll", None, {"code": code, **WORKER})

    def test_a_worker_answering_like_the_platform_still_counts_as_a_failure(self):
        _, issued = self.issue()
        with self.http() as call:
            for _ in range(5):   # a "Worker" whose /health answers 404 enrollment_invalid
                status, answer = self.enroll_raising(call, issued["code"], Error(404, "enrollment_invalid", "x"))
                self.assertEqual((status, answer["code"]), (503, "worker_unavailable"))
            (status, _), checked = self.enroll(call, issued["code"])
        self.assertEqual(status, 404)   # five failures retired it
        checked.assert_not_called()

    def test_health_that_is_not_an_object_is_a_counted_failure(self):
        _, issued = self.issue()
        with self.http() as call:
            (status, answer), _ = self.enroll(call, issued["code"], health=["not", "an", "object"])
        self.assertEqual((status, answer["code"]), (503, "worker_unavailable"))
        with self.db.read() as db:
            self.assertEqual(db.execute("SELECT failures FROM worker_enrollments WHERE id=?", (issued["id"],)).fetchone()[0], 1)

    def test_check_must_be_a_boolean(self):
        _, issued = self.issue()
        with self.http() as call:
            (status, _), checked = self.enroll(call, issued["code"], check=1)
        self.assertEqual(status, 400)
        checked.assert_not_called()

    def test_a_code_dies_with_its_issuers_admin_rights(self):
        _, issued = self.issue()
        with self.db.transaction() as db:
            db.execute("UPDATE users SET active=0 WHERE id=?", (self.a["id"],))
        with self.http() as call:
            (status, answer), checked = self.enroll(call, issued["code"])
        self.assertEqual((status, answer["code"]), (404, "enrollment_invalid"))
        checked.assert_not_called()

    def test_a_loopback_worker_url_is_only_for_a_request_from_this_machine(self):   # no probing of the platform host
        import http.client
        import json as jsonlib
        import threading
        from octopus.server import server
        _, issued = self.issue()
        app = server(self.db, 0, allowed_hosts=("kirocrew-on.example",))   # as behind the console's own domain
        threading.Thread(target=app.serve_forever, daemon=True).start()
        self.addCleanup(app.server_close)
        self.addCleanup(app.shutdown)

        def post(host):
            conn = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=10)
            body = jsonlib.dumps({"code": issued["code"], "url": "http://127.0.0.1:9002", "token": "w" * 40, "check": True})
            conn.request("POST", "/v1/workers/enroll", body=body, headers={"Host": host, "Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, jsonlib.loads(response.read() or b"{}")
        with mock.patch("octopus.server.validate_worker", return_value=HEALTH) as checked:
            status, answer = post("kirocrew-on.example")
            self.assertEqual((status, answer["code"]), (400, "loopback_worker"))
            checked.assert_not_called()
            self.assertEqual(post(f"127.0.0.1:{app.server_address[1]}")[0], 200)   # boundary add on the platform host

    def test_a_spoofed_viewer_address_does_not_split_the_limit(self):
        with self.http() as call:
            statuses = [call("POST", "/v1/workers/enroll", None, {"code": "kce_" + str(n % 10) * 43, **WORKER},
                             headers={"CloudFront-Viewer-Address": f"198.51.100.{n}:443"})[0] for n in range(11)]
        self.assertEqual(statuses[10], 429)   # an unverified request's header is not believed
