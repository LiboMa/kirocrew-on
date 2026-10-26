"""MVP 0.2.0 P1 Task 7 (CE12): which GET requests change something. Each GET route the server answers is requested once
on a scratch database, with a fake Worker; the tables that changed (``events``, ``audit`` and ``tokens.last_used`` set
aside: those are records of the read, kept on purpose) must be exactly those the audit lists in its fixed list
(docs/research/2026-10-26-get-side-effects-audit.md). A new GET with a side effect, or one that loses it, fails here
until the audit says so."""
from contextlib import contextmanager
import http.client
import re
import threading
from pathlib import Path

from octopus.db import dumps
from octopus.schema import openapi
from octopus.server import server
from tests.test_public_share_http import HttpCase

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs/research/2026-10-26-get-side-effects-audit.md"
KEPT = {"events", "audit"}   # records of the read itself (journal and admin oversight), on purpose


def listed():
    """The audit's fixed list: ``GET <route> -> <table>[, <table>]`` lines in its ```side-effects block."""
    block = re.search(r"```side-effects\n(.*?)```", AUDIT.read_text(), re.S).group(1)
    return {m.group(1): set(m.group(2).replace(" ", "").split(","))
            for m in re.finditer(r"^GET (\S+) -> (.+)$", block, re.M)}


class GetSideEffectTests(HttpCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        with self.db.transaction() as db:   # a demo that was running; the Worker now reports it exited
            db.execute("INSERT INTO apps(run_id,worker_id,status,port,started_by,started,last_access) "
                       "VALUES(?,?,?,?,?,?,?)", (self.rid, "w-bob", "running", 47001, self.b["id"], 1, 1))
            self.attempt = db.execute("SELECT id FROM attempts WHERE run_id=? LIMIT 1", (self.rid,)).fetchone()

    def worker(self, config, path, body=None, timeout=35):
        self.calls.append(path)
        if path == "/files":
            return super().worker(config, path, body, timeout)
        return {"/app": {"status": "failed", "error": "exited", "log": []},
                "/approvals": {"approvals": [], "supported": True},
                "/observe": {"messages": [], "slot": None},
                "/health": {"isolation": "dedicated", "boundary": "b-bob", "host_principal": "hp-bob"},
                }.get(path, {})

    def snapshot(self):
        with self.db.read() as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            state = {}
            for table in tables:
                if table in KEPT:
                    continue
                rows = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                if table == "tokens":
                    for row in rows:
                        row.pop("last_used", None)
                state[table] = dumps(rows)
            return state

    @contextmanager
    def raw(self):
        """GET as an API client does, whatever the answer's type (the report is Markdown, the export a file)."""
        app = server(self.db, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()

        def call(path, token):
            conn = http.client.HTTPConnection("127.0.0.1", app.server_address[1], timeout=40)
            try:
                conn.request("GET", path, headers={"Authorization": "Bearer " + token})
                response = conn.getresponse()
                response.read()
                return response.status
            finally:
                conn.close()
        try:
            yield call
        finally:
            app.shutdown()
            app.server_close()

    def routes(self):
        """Every GET route of the API document with its parameters filled in, and the public share's two."""
        fill = {"{id}": self.rid, "{attempt}": self.attempt["id"] if self.attempt else "attempt-x", "{version}": "1"}
        paths = []
        for path, methods in openapi()["paths"].items():
            if "get" not in methods:
                continue
            concrete = path
            if path.startswith("/v1/admin/users/"):
                concrete = path.replace("{id}", self.b["id"])
            for key, value in fill.items():
                concrete = concrete.replace(key, value)
            paths.append((path, concrete))
        return paths + [("/health", "/health")]

    def test_listed_get_routes_are_the_only_ones_with_side_effects(self):
        expected, seen = listed(), {}
        with self.raw() as call:
            for route, path in self.routes():
                before = self.snapshot()
                call(path, self.tokens["alice"])
                after = self.snapshot()
                changed = {t for t in after if after[t] != before.get(t)}
                if changed:
                    seen[route] = changed
        share = self.share(mode="open")
        with self.preview() as call:
            for route, path in (("/_kirocrew/s/{token}", self.link(share)),
                                ("/_kirocrew/s/{token}/{file}", self.link(share) + "/index.html")):
                before = self.snapshot()
                call("GET", path)
                after = self.snapshot()
                changed = {t for t in after if after[t] != before.get(t)}
                if changed:
                    seen[route] = changed
        exercised = {route for route, _ in self.routes()} | {"/_kirocrew/s/{token}", "/_kirocrew/s/{token}/{file}"}
        self.assertEqual(seen, {route: tables for route, tables in expected.items() if route in exercised})
        self.assertIn("/app", self.calls)   # the fake Worker was reached, so the writes behind its answers ran
