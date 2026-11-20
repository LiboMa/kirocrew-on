"""MVP 0.2.0 P0 T7 (design §7.6): a user's volume moved to another AZ by snapshot — how long the snapshots take (the
first full one, then an incremental one), how long a volume from it takes in the other AZ, and how fast its data can be
read there the first time (EBS loads a restored volume's blocks lazily from S3), by default and with a provisioned
initialization rate. The data is checked end to end. Run from the admin's machine:

    python3 tools/p0/t7_cross_az.py --a i-... --b i-... --az-b us-east-1b --gib 8 --out /tmp/p0
"""
import argparse
import json
import time
from pathlib import Path

from ebs_bench import MOUNT, UMOUNT, aws, device_of, mount_script, ssm, wait_volume

TAG = [{"Key": "kirocrew:p0", "Value": "1"}, {"Key": "Name", "Value": "kc-p0-t7"}]
TAGS = json.dumps([{"ResourceType": "volume", "Tags": TAG}])

# A workspace-like tree: many small files (sources, node_modules) and a few large ones (build output, caches).
FILL = (f"set -e; cd {MOUNT}; mkdir -p small big; "
        "for d in $(seq 1 40); do mkdir -p small/$d; for f in $(seq 1 500); do head -c $((4096 + RANDOM % 61440)) /dev/urandom > small/$d/$f; done; done; "
        "for f in $(seq 1 6); do head -c 805306368 /dev/urandom > big/$f; done; sync; du -sm . | cut -f1")
CHANGE = (f"set -e; cd {MOUNT}; mkdir -p small/new; for f in $(seq 1 100); do head -c 65536 /dev/urandom > small/1/$f; done; "
          "head -c 268435456 /dev/urandom > big/new; sync")
# One hash over every file's content, in a fixed order: the same data on both sides.
DIGEST = f"cd {MOUNT} && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-64"
READ = (f"s=$(date +%s.%N); b=$(cd {MOUNT} && find . -type f -print0 | sort -z | xargs -0 cat | wc -c); "
        "e=$(date +%s.%N); echo $s $e $b")


def timed_read(host):
    out, _ = ssm(host, READ, timeout=3600)
    start, end, size = out.split()
    seconds = float(end) - float(start)
    return {"read_s": round(seconds, 2), "mib": round(int(size) / 1048576), "mib_s": round(int(size) / 1048576 / seconds, 1)}


def snapshot(volume, label):
    start = time.time()
    snap, _ = aws("ec2", "create-snapshot", "--volume-id", volume, "--description", f"kc-p0 t7 {label}",
                  "--tag-specifications", json.dumps([{"ResourceType": "snapshot", "Tags": TAG}]))
    while True:
        info, _ = aws("ec2", "describe-snapshots", "--snapshot-ids", snap["SnapshotId"])
        if info["Snapshots"][0]["State"] == "completed":
            return snap["SnapshotId"], round(time.time() - start, 1)
        if info["Snapshots"][0]["State"] == "error":
            raise RuntimeError(f"snapshot {label} failed")
        time.sleep(5)


def restore(snap, az, host, rate=None):
    """A volume from ``snap`` in ``az``, attached and mounted on ``host``: its timings, its first and second full read."""
    row, start = {"rate": rate}, time.time()
    extra = ["--volume-initialization-rate", str(rate)] if rate else []
    made, _ = aws("ec2", "create-volume", "--snapshot-id", snap, "--availability-zone", az, "--volume-type", "gp3",
                  "--tag-specifications", TAGS, *extra)
    volume = made["VolumeId"]
    wait_volume(volume, "available", poll=2)
    row["create_s"] = round(time.time() - start, 1)
    aws("ec2", "attach-volume", "--volume-id", volume, "--instance-id", host, "--device", "/dev/sdg")
    wait_volume(volume, "attached", poll=2)
    ssm(host, mount_script(volume))
    row["attached_s"] = round(time.time() - start, 1)
    row["first_read"] = timed_read(host)
    ssm(host, "sync; echo 3 > /proc/sys/vm/drop_caches")
    row["second_read"] = timed_read(host)   # the blocks are on the volume now: the volume's own speed
    out, _ = ssm(host, DIGEST, timeout=3600)
    row["digest"] = out.strip()
    status, _ = aws("ec2", "describe-volume-status", "--volume-ids", volume)
    row["initialization"] = status["VolumeStatuses"][0].get("InitializationStatusDetails")
    ssm(host, UMOUNT)
    aws("ec2", "detach-volume", "--volume-id", volume)
    wait_volume(volume, "available", poll=2)
    aws("ec2", "delete-volume", "--volume-id", volume)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--az-a", default="us-east-1a")
    parser.add_argument("--az-b", default="us-east-1b")
    parser.add_argument("--gib", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = {"gib": args.gib}
    made, _ = aws("ec2", "create-volume", "--availability-zone", args.az_a, "--size", str(args.gib), "--volume-type", "gp3",
                  "--encrypted", "--tag-specifications", TAGS)
    source = made["VolumeId"]
    wait_volume(source, "available", poll=2)
    aws("ec2", "attach-volume", "--volume-id", source, "--instance-id", args.a, "--device", "/dev/sdg")
    wait_volume(source, "attached", poll=2)
    ssm(args.a, mount_script(source, make=True))
    out, _ = ssm(args.a, FILL, timeout=3600)
    result["data_mib"] = int(out.strip().splitlines()[-1])
    ssm(args.a, UMOUNT)
    first, result["snapshot_full_s"] = snapshot(source, "full")
    ssm(args.a, mount_script(source))
    ssm(args.a, CHANGE, timeout=1800)
    out, _ = ssm(args.a, DIGEST, timeout=3600)
    result["digest"] = out.strip()
    ssm(args.a, UMOUNT)
    second, result["snapshot_incremental_s"] = snapshot(source, "incremental")
    result["restores"] = [restore(second, args.az_b, args.b), restore(second, args.az_b, args.b, rate=300)]
    result["same_data"] = all(r["digest"] == result["digest"] for r in result["restores"])
    aws("ec2", "detach-volume", "--volume-id", source)
    wait_volume(source, "available", poll=2)
    aws("ec2", "delete-volume", "--volume-id", source)
    for snap in (first, second):
        aws("ec2", "delete-snapshot", "--snapshot-id", snap)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    Path(args.out, "t7.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
