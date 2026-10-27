"""MVP 0.2.0 P1 Task 10, cloud check (P §5.2, S31): on a throwaway host without Agents (standing in for a cluster control
plane), the platform database replicated by Litestream with the release's template; a writer commits a row every 20 ms;
the host then crashes (sysrq reboot, no sync). On a second host the replica is restored and checked with
`backup --source` + `restore-verify`; RPO = the crash moment (written to the kernel console just before it) minus the
newest row the replica has. A temporary bucket (SSE-S3) and an inline policy on the P0 role, both removed at the end.

    python3 tools/p0/litestream_rpo.py --a i-... --b i-... --wheel dist/release/kirocrew_on-...whl --out /tmp/p0
"""
import argparse
import json
from pathlib import Path
import re
import secrets
import time

from ebs_bench import REGION, aws, ssm

ROOT = Path(__file__).resolve().parents[2]
ROLE = "kc-p0-ssm"
PINS = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
            if line.strip() and not line.startswith("#") and "=" in line)
VERSION, SHA = PINS["LITESTREAM_VERSION"], PINS["LITESTREAM_SHA256_AARCH64"]
DB = "/var/lib/octopus/platform/octopus.sqlite3"


def prepare(bucket, wheel):
    """Python 3.12, the release wheel and the pinned Litestream (arm64, checksum-checked)."""
    tar = f"litestream-{VERSION}-linux-arm64.tar.gz"
    return ("set -e; dnf install -y -q python3.12 >/dev/null; "
            f"aws s3 cp --only-show-errors s3://{bucket}/{wheel} /tmp/{wheel}; "
            "python3.12 -m venv /opt/kc; /opt/kc/bin/pip install -q --no-index /tmp/" + wheel + "; "
            f"curl --proto '=https' -fsSL https://github.com/benbjohnson/litestream/releases/download/v{VERSION}/{tar} -o /tmp/{tar}; "
            f"echo '{SHA}  /tmp/{tar}' | sha256sum -c - >/dev/null; tar -xzf /tmp/{tar} -C /tmp litestream; "
            "install -m 0755 /tmp/litestream /usr/local/bin/litestream; /usr/local/bin/litestream version")


WRITER = r'''
import sqlite3, sys, time, os
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute("PRAGMA busy_timeout=5000")
db.execute("CREATE TABLE IF NOT EXISTS rpo_probe(t REAL)")
end = time.time() + float(sys.argv[2])
while time.time() < end:
    db.execute("INSERT INTO rpo_probe VALUES(?)", (time.time(),))
    time.sleep(0.02)
with open("/dev/kmsg", "w") as k:
    k.write("<2>kc-crash %.3f\n" % time.time())   # critical: shown on the console whatever its log level
os.system("echo 1 > /proc/sys/kernel/sysrq; echo b > /proc/sysrq-trigger")
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    account = aws("sts", "get-caller-identity")[0]["Account"]
    bucket = f"kc-p0-litestream-{account}-{secrets.token_hex(3)}"
    wheel = Path(args.wheel).name
    result = {"bucket_kind": "temporary, SSE-S3", "litestream": VERSION}
    aws("s3api", "create-bucket", "--bucket", bucket)
    try:
        aws("s3api", "put-bucket-tagging", "--bucket", bucket, "--tagging", "TagSet=[{Key=kirocrew:p0,Value=1}]")
        aws("s3api", "put-public-access-block", "--bucket", bucket, "--public-access-block-configuration",
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true")
        aws("iam", "put-role-policy", "--role-name", ROLE, "--policy-name", "kc-p0-litestream", "--policy-document",
            json.dumps({"Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": f"arn:aws:s3:::{bucket}"},
                {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                 "Resource": f"arn:aws:s3:::{bucket}/*"}]}))
        aws("s3", "cp", "--only-show-errors", args.wheel, f"s3://{bucket}/{wheel}")
        time.sleep(15)   # the role's new policy reaching the hosts' credentials
        for host in (args.a, args.b):
            out, _ = ssm(host, prepare(bucket, wheel), timeout=900)
            result.setdefault("installed", []).append(out.strip().splitlines()[-1])
        config = (ROOT / "deploy/litestream/litestream.yml.template").read_text().replace("__STATE_BUCKET__", bucket) \
            .replace("__REGION__", REGION)
        ssm(args.a, "set -e; install -d -m 0700 /var/lib/octopus/platform /etc/kirocrew-on; "
                    f"cat > /etc/kirocrew-on/litestream.yml <<'YML'\n{config}YML\n"
                    "OCTOPUS_ADMIN_PASSWORD=$(head -c 24 /dev/urandom | base64) /opt/kc/bin/kirocrew-on init "
                    "--data /var/lib/octopus/platform --password-env OCTOPUS_ADMIN_PASSWORD >/dev/null; "
                    "systemd-run --unit kc-litestream /usr/local/bin/litestream replicate -config /etc/kirocrew-on/litestream.yml; "
                    "sleep 5; systemctl is-active kc-litestream")
        ssm(args.a, f"cat > /tmp/writer.py <<'PY'\n{WRITER}\nPY\n"
                    f"systemd-run --unit kc-writer /opt/kc/bin/python /tmp/writer.py {DB} {args.seconds}")
        time.sleep(args.seconds + 20)
        aws("ec2", "wait", "instance-running", "--instance-ids", args.a)
        crash = None
        for _ in range(40):   # the console log of the crashed boot (the latest buffer, or the one kept at reboot)
            for latest in (["--latest"], []):
                console, _ = aws("ec2", "get-console-output", "--instance-id", args.a, *latest, check=False)
                found = re.findall(r"kc-crash ([0-9.]+)", (console or {}).get("Output", "") or "")
                if found:
                    crash = float(found[-1])
                    break
            if crash:
                break
            time.sleep(15)
        start = time.time()
        out, _ = ssm(args.b, "set -e; rm -rf /tmp/r; install -d /tmp/r; "
                             f"/usr/local/bin/litestream restore -o /tmp/r/octopus.sqlite3 s3://{bucket}/db/octopus.sqlite3; "
                             "/opt/kc/bin/kirocrew-on backup --source /tmp/r/octopus.sqlite3 --dest /tmp/r/check > /tmp/r/m.json; "
                             "/opt/kc/bin/kirocrew-on restore-verify /tmp/r/check/$(/opt/kc/bin/python -c \"import json;"
                             "print(json.load(open('/tmp/r/m.json'))['file'].removesuffix('.sqlite3'))\").json --into /tmp/r/verified; "
                             "/opt/kc/bin/python -c \"import sqlite3;d=sqlite3.connect('/tmp/r/octopus.sqlite3');"
                             "import json;print(json.dumps(d.execute('SELECT count(*), max(t) FROM rpo_probe').fetchone()))\"",
                     timeout=900)
        lines = out.strip().splitlines()
        result["restore_s"] = round(time.time() - start, 1)
        result["restore_verify"] = json.loads(lines[-2]).get("ok")
        rows, newest = json.loads(lines[-1])
        result.update(rows=rows, newest_row=newest, crash=crash, rpo_s=round(crash - newest, 3) if crash else None)
    finally:
        aws("iam", "delete-role-policy", "--role-name", ROLE, "--policy-name", "kc-p0-litestream", check=False)
        aws("s3", "rm", "--recursive", "--only-show-errors", f"s3://{bucket}", check=False)
        aws("s3api", "delete-bucket", "--bucket", bucket, check=False)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    Path(args.out, "litestream.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
