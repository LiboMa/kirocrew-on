"""T9: how long a dedicated host takes to come back — stop/start and hibernate/resume, each N times, timed to SSM Online
(the earliest a Gateway could answer; the real Gateway start adds its own seconds, measured in P0b)."""
import json
import sys
import time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import ebs_bench as e

host, rounds, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def online(since):
    while True:
        info, _ = e.aws("ssm", "describe-instance-information", "--filters", f"Key=InstanceIds,Values={host}")
        rows = info.get("InstanceInformationList", [])
        if rows and rows[0]["PingStatus"] == "Online" and e.ssm(host, "echo up")[0].strip() == "up":
            return round(time.time() - since, 1)
        time.sleep(2)


rows = []
for mode in ("stop", "hibernate"):
    for n in range(rounds):
        t = time.time()
        e.aws("ec2", "stop-instances", "--instance-ids", host, *(["--hibernate"] if mode == "hibernate" else []))
        e.aws("ec2", "wait", "instance-stopped", "--instance-ids", host, check=False)
        stopped = round(time.time() - t, 1)
        time.sleep(5)
        t = time.time()
        e.aws("ec2", "start-instances", "--instance-ids", host)
        e.aws("ec2", "wait", "instance-running", "--instance-ids", host, check=False)
        running = round(time.time() - t, 1)
        ready = online(t)
        row = {"mode": mode, "n": n, "stop_s": stopped, "running_s": running, "ssm_online_s": ready}
        rows.append(row)
        print(json.dumps(row), flush=True)
summary = {m: e.summary([r for r in rows if r["mode"] == m], ("stop_s", "running_s", "ssm_online_s")) for m in ("stop", "hibernate")}
open(out, "w").write(json.dumps({"rows": rows, "summary": summary}, indent=2))
print(json.dumps(summary, indent=2))
