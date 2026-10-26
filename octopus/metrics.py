"""The platform's own counts (MVP 0.2.0 P1, design §12.1): what runs and what waits, every minute, as one CloudWatch
Embedded Metric Format line in the platform's log (namespace KiroCrewOn). The host's CloudWatch agent takes the lines
from the file: the platform calls no AWS API, so no host role needs cloudwatch:PutMetricData. An administrator reads the
same counts at GET /v1/admin/metrics. Alarms (SNS) come with P3's data-plane stack (§12.2)."""
import json
import os

from .domain import RUN_STATES, SETTLED
from .remote_preview import LIVE

NAMESPACE = "KiroCrewOn"
SERVICE = "kirocrew-on"
STALE = 15 * 60          # a Worker whose health was last read longer ago than this is unhealthy (the Engine reads it every 10 min)
LOG_LIMIT = 10 << 20     # past this the log is set aside as <name>.1 (one generation is kept)
RUNNING = tuple(s for s in RUN_STATES if s not in SETTLED)   # in progress on its Worker, as the Engine counts it
ATTEMPTS = ("dispatching", "running", "dispatch_unknown", "waiting_approval")
ENDED = ("completed", "failed", "stopped")


def snapshot(db, now):
    """The counts, in one read of the database."""
    one = lambda sql, *args: db.execute(sql, args).fetchone()[0]
    marks = lambda items: ",".join("?" * len(items))
    return {
        "sessions_running": one(f"SELECT count(*) FROM runs WHERE status IN ({marks(RUNNING)})", *RUNNING),
        "sessions_waiting_worker": one("SELECT count(*) FROM runs WHERE status='waiting_worker'"),
        "sessions_quota_wait": one(f"SELECT count(*) FROM runs WHERE status NOT IN ({marks(ENDED)}) "
                                   "AND json_extract(snapshot,'$.quota_wait') IS NOT NULL", *ENDED),
        "attempts_running": one(f"SELECT count(*) FROM attempts WHERE status IN ({marks(ATTEMPTS)})", *ATTEMPTS),
        "workers_enabled": one("SELECT count(*) FROM workers WHERE enabled=1 AND retired=0"),
        "workers_unhealthy": one("SELECT count(*) FROM workers WHERE enabled=1 AND retired=0 AND "
                                 "COALESCE(json_extract(config,'$.capabilities.checked_at'), 0) < ?", now - STALE),
        "apps_running": one(f"SELECT count(*) FROM apps WHERE status IN ({marks(LIVE)})", *LIVE),
        "takeovers_moving": one("SELECT count(*) FROM runs WHERE json_extract(snapshot,'$.takeover.state')='moving'"),
        "public_shares_active": one("SELECT count(*) FROM public_shares WHERE expires IS NULL OR expires>?", now),
    }


def emf(counts, now):
    """One EMF line: every count a Count metric in KiroCrewOn, under the Service dimension."""
    return json.dumps({"_aws": {"Timestamp": int(now * 1000), "CloudWatchMetrics": [{
        "Namespace": NAMESPACE, "Dimensions": [["Service"]],
        "Metrics": [{"Name": name, "Unit": "Count"} for name in counts]}]}, "Service": SERVICE, **counts},
        separators=(",", ":"))


def publish(database, path, now):
    """Append this minute's line to ``path``; a log past LOG_LIMIT is set aside first."""
    with database.read() as db:
        line = emf(snapshot(db, now), now)
    if path.exists() and path.stat().st_size > LOG_LIMIT:
        os.replace(path, path.with_name(path.name + ".1"))
    with path.open("a") as log:
        log.write(line + "\n")
