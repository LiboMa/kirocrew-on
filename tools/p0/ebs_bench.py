"""MVP 0.2.0 P0 (design §16; assessment E §5): EBS move mechanics on throwaway hosts — attach / detach timing (T1), the
attachment limit (T2), duplicate filesystem UUIDs (T4) and concurrent moves under API limits (T10). Driven from the
admin's machine with the AWS CLI; commands on the hosts go through SSM (its own latency is measured apart and left out of
what a user would wait, which the host agent replaces). Results are JSON lines under --out.

    python3 tools/p0/ebs_bench.py t1 --a i-... --b i-... --volume vol-... --runs 200 --out /tmp/p0
"""
import argparse
import concurrent.futures
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REGION = "us-east-1"
MOUNT = "/mnt/kc"


def aws(*args, check=True):
    done = subprocess.run(["aws", "--region", REGION, *args, "--output", "json"], capture_output=True, text=True)
    if check and done.returncode:
        raise RuntimeError(done.stderr.strip()[-400:])
    return json.loads(done.stdout) if done.stdout.strip() else {}, done.stderr


def ssm(instance, script, timeout=300):
    """Run ``script`` on ``instance``; (stdout, seconds of SSM round trip)."""
    start = time.time()
    sent, _ = aws("ssm", "send-command", "--instance-ids", instance, "--document-name", "AWS-RunShellScript",
                  "--parameters", json.dumps({"commands": [script], "executionTimeout": [str(timeout)]}))
    cid = sent["Command"]["CommandId"]
    while True:
        time.sleep(0.5)
        result, err = aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", instance, check=False)
        if not result:
            if "InvocationDoesNotExist" in err:
                continue
            raise RuntimeError(err[-300:])
        if result["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            if result["Status"] != "Success":
                raise RuntimeError(f"{instance}: {result['Status']}: {result.get('StandardErrorContent', '')[-300:]}")
            return result["StandardOutputContent"], time.time() - start


def device_of(volume):
    return "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_" + volume.replace("-", "")


def mount_script(volume, fs="ext4", make=False):
    """Wait for the volume's device, make a filesystem the first time, mount; print the host's own timings."""
    dev = device_of(volume)
    mk = {"ext4": f"mkfs.ext4 -q -F -i 8192 {dev}", "xfs": f"mkfs.xfs -q -f {dev}"}[fs] if make else "true"
    return (f"set -e; s=$(date +%s.%N); while [ ! -e {dev} ]; do sleep 0.1; done; d=$(date +%s.%N); {mk}; mkdir -p {MOUNT}; "
            f"mount -o nodev,nosuid {dev} {MOUNT}; m=$(date +%s.%N); "
            "python3 -c \"import sys;s,d,m=map(float,sys.argv[1:]);print('{\\\"device_s\\\":%.3f,\\\"mount_s\\\":%.3f,\\\"seen\\\":%.3f}'%(d-s,m-d,d))\" $s $d $m")


UMOUNT = (f"set -e; s=$(date +%s.%N); sync; umount {MOUNT}; e=$(date +%s.%N); "
          "python3 -c \"import sys;s,e=map(float,sys.argv[1:]);print('{\\\"umount_s\\\":%.3f}'%(e-s))\" $s $e")


def wait_volume(volume, state, poll=1.0, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        info, _ = aws("ec2", "describe-volumes", "--volume-ids", volume)
        vol = info["Volumes"][0]
        attached = [a for a in vol.get("Attachments", []) if a["State"] == "attached"]
        if (state == "available" and vol["State"] == "available") or (state == "attached" and attached):
            return time.time() - start
        time.sleep(poll)
    raise TimeoutError(f"{volume} not {state} after {timeout}s")


def move(volume, source, target, device="/dev/sdf"):
    """One move as a user would wait for it: umount → detach → attach → device → mount."""
    row = {"volume": volume, "from": source, "to": target, "at": time.time()}
    out, row["ssm_umount_s"] = ssm(source, UMOUNT)
    row.update(json.loads(out.strip().splitlines()[-1]))
    t = time.time()
    aws("ec2", "detach-volume", "--volume-id", volume)
    wait_volume(volume, "available")
    row["detach_s"] = round(time.time() - t, 3)   # the API call included
    t = time.time()
    aws("ec2", "attach-volume", "--volume-id", volume, "--instance-id", target, "--device", device)
    wait_volume(volume, "attached")
    row["attach_s"] = round(time.time() - t, 3)
    out, row["ssm_mount_s"] = ssm(target, mount_script(volume))
    row.update(json.loads(out.strip().splitlines()[-1]))
    row["device_after_attached_s"] = round(max(0.0, row.pop("seen") - (t + row["attach_s"])), 3)
    row["user_s"] = round(row["umount_s"] + row["detach_s"] + row["attach_s"] + row["device_s"] + row["mount_s"], 3)
    return row


def device_name(index):
    """The index-th device name EC2 accepts on Linux: /dev/sdb–/dev/sdz, then /dev/xvdba–/dev/xvdbz."""
    letters = "bcdefghijklmnopqrstuvwxyz"
    return f"/dev/sd{letters[index]}" if index < 25 else f"/dev/xvdb{'abcdefghijklmnopqrstuvwxyz'[index - 25]}"


def summary(rows, keys=("umount_s", "detach_s", "attach_s", "device_s", "mount_s", "user_s")):
    out = {}
    for key in keys:
        values = sorted(r[key] for r in rows if key in r)
        if values:
            pick = lambda q: values[min(len(values) - 1, int(q * len(values)))]
            out[key] = {"n": len(values), "p50": pick(0.5), "p95": pick(0.95), "p99": pick(0.99), "max": values[-1],
                        "mean": round(statistics.mean(values), 3)}
    return out


def t1(args):
    """T1: one volume moved back and forth between two hosts ``runs`` times."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "t1.jsonl").open("a")
    aws("ec2", "attach-volume", "--volume-id", args.volume, "--instance-id", args.a, "--device", "/dev/sdf")
    wait_volume(args.volume, "attached")
    ssm(args.a, mount_script(args.volume, make=True))
    hosts, rows, failures = [args.a, args.b], [], 0
    for n in range(args.runs):
        source, target = hosts[n % 2], hosts[(n + 1) % 2]
        try:
            row = move(args.volume, source, target)
            row["n"] = n
            rows.append(row)
            log.write(json.dumps(row) + "\n")
            log.flush()
        except Exception as exc:   # recorded, then the loop stops: the volume's state must be looked at by hand
            failures += 1
            log.write(json.dumps({"n": n, "error": str(exc)[-400:]}) + "\n")
            break
    result = {"runs": len(rows), "failures": failures, "summary": summary(rows)}
    (out / "t1-summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


def t2(args):
    """T2: attach 1 GiB volumes one by one until the instance refuses; where it refuses is the limit."""
    attached = []
    for volume in args.volumes:
        try:
            aws("ec2", "attach-volume", "--volume-id", volume, "--instance-id", args.instance, "--device", device_name(len(attached)))
            wait_volume(volume, "attached")
            attached.append(volume)
        except Exception as exc:
            result = {"instance": args.instance, "attached_data_volumes": len(attached), "refused_with": str(exc)[-300:]}
            print(json.dumps(result, indent=2))
            Path(args.out).mkdir(parents=True, exist_ok=True)
            Path(args.out, f"t2-{args.instance}.json").write_text(json.dumps(result, indent=2))
            break
    else:
        print(json.dumps({"instance": args.instance, "attached_data_volumes": len(attached), "refused_with": None}))
    for volume in attached:   # leave them detached for the next test
        aws("ec2", "detach-volume", "--volume-id", volume, check=False)
    for volume in attached:
        wait_volume(volume, "available")


def t10(args):
    """T10: ``len(volumes)`` users moved at once from A to B (no filesystem: the API under concurrency)."""
    def retried(*call):
        """An EC2 call retried with backoff while the API throttles; (retries)."""
        tries = 0
        while True:
            _, err = aws(*call, check=False)
            if "RequestLimitExceeded" not in err:
                if err.strip():
                    raise RuntimeError(err[-300:])
                return tries
            tries += 1
            time.sleep(min(2 ** tries, 20))

    def one(volume):
        index, start = args.volumes.index(volume), time.time()
        retries = retried("ec2", "detach-volume", "--volume-id", volume)
        wait_volume(volume, "available")
        retries += retried("ec2", "attach-volume", "--volume-id", volume, "--instance-id", args.b, "--device", device_name(index))
        wait_volume(volume, "attached")
        return {"volume": volume, "seconds": round(time.time() - start, 3), "retries": retries}
    for index, volume in enumerate(args.volumes):   # all on A first
        aws("ec2", "attach-volume", "--volume-id", volume, "--instance-id", args.a, "--device", device_name(index))
    for volume in args.volumes:
        wait_volume(volume, "attached")
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.volumes)) as pool:
        rows = list(pool.map(one, args.volumes))
    result = {"volumes": len(rows), "wall_s": round(time.time() - start, 3), "retries": sum(r["retries"] for r in rows),
              "summary": summary(rows, ("seconds",))}
    Path(args.out).mkdir(parents=True, exist_ok=True)
    Path(args.out, "t10.json").write_text(json.dumps({**result, "rows": rows}, indent=2))
    print(json.dumps(result, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="test", required=True)
    p = sub.add_parser("t1")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--volume", required=True)
    p.add_argument("--runs", type=int, default=200)
    p.add_argument("--out", required=True)
    p = sub.add_parser("t2")
    p.add_argument("--instance", required=True)
    p.add_argument("--volumes", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("t10")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--volumes", nargs="+", required=True)
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    {"t1": t1, "t2": t2, "t10": t10}[args.test](args)


if __name__ == "__main__":
    sys.exit(main())
