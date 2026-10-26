"""MVP 0.2.0 P1 Task 11 (design §12.1): the platform's own counts, every minute, as one CloudWatch EMF line in its log
(namespace KiroCrewOn) — the host's CloudWatch agent takes them; the platform calls no AWS API, so no host role needs
PutMetricData — and for an administrator at GET /v1/admin/metrics."""
import json
import tempfile
import time
from pathlib import Path

from octopus import metrics
from octopus.db import dumps
from tests.test_access import AccessCase

NOW = 1_800_000_000.0


class MetricsCase(AccessCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            for wid, enabled, retired, checked in (("w-ok", 1, 0, NOW - 60), ("w-stale", 1, 0, NOW - 3600),
                                                   ("w-never", 1, 0, None), ("w-off", 0, 0, None), ("w-gone", 1, 1, None)):
                capabilities = {"checked_at": checked} if checked else {}
                db.execute("INSERT INTO workers(id,name,owner_id,config,scope,enabled,retired) VALUES(?,?,?,?,?,?,?)",
                           (wid, wid, self.b["id"], dumps({"capabilities": capabilities}), "dedicated", enabled, retired))
        runs = [self.start(self.b, request=f"metrics-run-{n:04d}", worker_id="w-ok")["id"] for n in range(6)]
        states = [("running", {}), ("running", {"takeover": {"state": "moving"}}), ("waiting_worker", {}),
                  ("waiting_worker", {"quota_wait": {"since": NOW}}), ("completed", {}), ("waiting_approval", {})]
        with self.db.transaction() as db:
            for rid, (status, extra) in zip(runs, states):
                snapshot = json.loads(db.execute("SELECT snapshot FROM runs WHERE id=?", (rid,)).fetchone()[0])
                db.execute("UPDATE runs SET status=?, snapshot=? WHERE id=?", (status, dumps({**snapshot, **extra}), rid))
            db.execute("UPDATE attempts SET status='running' WHERE run_id=?", (runs[0],))
            db.execute("INSERT INTO apps(run_id,worker_id,status) VALUES(?,?,?)", (runs[0], "w-ok", "running"))
            db.execute("INSERT INTO apps(run_id,worker_id,status) VALUES(?,?,?)", (runs[4], "w-ok", "stopped"))
            for sid, expires in (("ps-1", None), ("ps-2", NOW + 60), ("ps-3", NOW - 60)):
                db.execute("INSERT INTO public_shares(id,run_id,token,kind,mode,created_by,created,expires) "
                           "VALUES(?,?,?,?,?,?,?,?)", (sid, runs[4], sid + "-" + "t" * 20, "pages", "open", self.b["id"], NOW,
                                                     expires))
        self.runs = runs


class SnapshotTests(MetricsCase):
    def test_the_snapshot_counts_what_runs_and_waits(self):
        with self.db.read() as db:
            counts = metrics.snapshot(db, NOW)
        with self.db.read() as db:   # runs[0]'s attempts were all set running
            attempts = db.execute("SELECT count(*) FROM attempts WHERE run_id=?", (self.runs[0],)).fetchone()[0]
        self.assertEqual(counts, {"sessions_running": 3, "sessions_waiting_worker": 2, "sessions_quota_wait": 1,
                                  "attempts_running": attempts, "workers_enabled": 3, "workers_unhealthy": 2,
                                  "apps_running": 1, "takeovers_moving": 1, "public_shares_active": 2})

    def test_the_emf_line_names_the_namespace_and_units(self):
        with self.db.read() as db:
            counts = metrics.snapshot(db, NOW)
        line = json.loads(metrics.emf(counts, NOW))
        directive = line["_aws"]["CloudWatchMetrics"][0]
        self.assertEqual(directive["Namespace"], "KiroCrewOn")
        self.assertEqual(line["_aws"]["Timestamp"], int(NOW * 1000))
        self.assertEqual({m["Name"] for m in directive["Metrics"]}, set(counts))
        self.assertTrue(all(m["Unit"] == "Count" for m in directive["Metrics"]))
        for dimensions in directive["Dimensions"]:
            self.assertTrue(all(name in line for name in dimensions))
        self.assertTrue(all(line[name] == value for name, value in counts.items()))

    def test_the_line_goes_to_its_log_and_the_log_stays_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.emf.log"
            metrics.publish(self.db, path, NOW)
            metrics.publish(self.db, path, NOW + 60)
            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["_aws"]["Timestamp"], int((NOW + 60) * 1000))
            path.write_text("x" * (metrics.LOG_LIMIT + 1))
            metrics.publish(self.db, path, NOW + 120)
            self.assertEqual(len(path.read_text().splitlines()), 1)   # the full log was set aside as .1
            self.assertTrue(path.with_name(path.name + ".1").exists())


class HttpTests(MetricsCase):
    def test_only_an_admin_reads_metrics(self):
        with self.http() as call:
            status, counts = call("GET", "/v1/admin/metrics", self.tokens["alice"])
            self.assertEqual(status, 200)
            self.assertEqual(counts["sessions_waiting_worker"], 2)
            self.assertEqual(call("GET", "/v1/admin/metrics", self.tokens["bob"])[0], 403)
        self.assertLess(abs(counts.get("at", time.time()) - time.time()), 5)
