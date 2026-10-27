"""`kirocrew-on single`: the single-node edition's deployment kit (MVP 0.2.0 P2 Task 7, design §4.2.4, S29, S31). After the
install the host role keeps Bedrock only, so every later AWS change runs here — on the administrator's computer or in
CloudShell, with the administrator's credentials (the aws CLI's). What has to happen on the host goes through SSM, and
no administrator credential is ever sent there:

- ``native <boundary>``: that boundary's own public native chat — a distribution in the stack kirocrew-on-single (slot
  1–9, relay port 8919 + slot; the origin secret stays the stack's own), then the host relays it and makes it the
  boundary's crew_url (deploy/install/boundary-native.sh);
- ``backup --bucket <bucket>``: the host takes a consistent backup of the platform database and uploads it to one object
  through a URL presigned here for that object only, for 15 minutes at most (S31);
- ``up``: the stack takes this release's template, keeping every value it has.

With ``--platform``, ``--token-file`` and ``--request`` the kit reports what it did to the platform's waiting list
(aws_requests), which the console then stops showing.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from . import release

STACK = "kirocrew-on-single"
SLOTS = range(1, 10)
NAME = re.compile(r"[a-z][a-z0-9-]{1,19}")
BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
EXPIRES = 900
POLL = 2   # seconds between SSM status reads


def aws(region, *args):
    done = subprocess.run(["aws", "--region", region, *args, "--output", "json"], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"aws {' '.join(args[:2])} 失败：{done.stderr.strip()}")
    return json.loads(done.stdout) if done.stdout.strip() else {}


def stack(region, name):
    found = aws(region, "cloudformation", "describe-stacks", "--stack-name", name)["Stacks"][0]
    return ({p["ParameterKey"]: p.get("ParameterValue", "") for p in found.get("Parameters", [])},
            {o["OutputKey"]: o["OutputValue"] for o in found.get("Outputs", [])})


def update(region, name, changed):
    """The stack with this release's template: the values in ``changed``, every other parameter as it is. A change set
    that would replace a resource is refused (an Elastic IP replaced moves every distribution's origin)."""
    params, _ = stack(region, name)
    values = [{"ParameterKey": key, "ParameterValue": changed[key]} if key in changed else
              {"ParameterKey": key, "UsePreviousValue": True} for key in params]
    values += [{"ParameterKey": key, "ParameterValue": value} for key, value in changed.items() if key not in params]
    change = time.strftime("kirocrew-on-%Y%m%d%H%M%S", time.gmtime())
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "parameters.json"
        path.write_text(json.dumps(values))
        aws(region, "cloudformation", "create-change-set", "--stack-name", name, "--change-set-name", change,
            "--change-set-type", "UPDATE", "--template-body", "file://" + str(release.path("deploy") / "aws" / "kirocrew-on-single.yaml"),
            "--capabilities", "CAPABILITY_IAM", "--parameters", "file://" + str(path))
    ready = subprocess.run(["aws", "--region", region, "cloudformation", "wait", "change-set-create-complete",
                            "--stack-name", name, "--change-set-name", change], capture_output=True, text=True)
    described = aws(region, "cloudformation", "describe-change-set", "--stack-name", name, "--change-set-name", change)
    if ready.returncode != 0:
        aws(region, "cloudformation", "delete-change-set", "--stack-name", name, "--change-set-name", change)
        reason = described.get("StatusReason", "")
        if "didn't contain changes" in reason or "No updates are to be performed" in reason:
            return
        raise SystemExit(f"变更集创建失败：{reason}")
    replaced = [c["ResourceChange"]["LogicalResourceId"] for c in described.get("Changes", [])
                if c.get("ResourceChange", {}).get("Replacement") in ("True", "Conditional")]
    if replaced:
        aws(region, "cloudformation", "delete-change-set", "--stack-name", name, "--change-set-name", change)
        raise SystemExit(f"变更会替换资源：{'、'.join(replaced)}，未执行")
    aws(region, "cloudformation", "execute-change-set", "--stack-name", name, "--change-set-name", change)
    subprocess.run(["aws", "--region", region, "cloudformation", "wait", "stack-update-complete", "--stack-name", name],
                   check=True)


def on_host(region, instance, script):
    """``script`` as root on the host through SSM; its output, or a stop with what it said."""
    command = aws(region, "ssm", "send-command", "--instance-ids", instance, "--document-name", "AWS-RunShellScript",
                  "--parameters", json.dumps({"commands": [script]}))["Command"]["CommandId"]
    while True:
        time.sleep(POLL)
        try:
            result = aws(region, "ssm", "get-command-invocation", "--command-id", command, "--instance-id", instance)
        except SystemExit:   # not registered yet
            continue
        if result["Status"] not in ("Pending", "InProgress", "Delayed"):
            break
    if result["Status"] != "Success":
        raise SystemExit(f"主机上没有完成（{result['Status']}）：{result.get('StandardErrorContent', '').strip()}")
    return result.get("StandardOutputContent", "")


def presign(method, bucket, key, region, credentials, expires, now=None, host=None):
    """A SigV4 query-string URL for one S3 object and one method (signed headers: host only)."""
    stamp = now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    host = host or f"{bucket}.s3.{region}.amazonaws.com"
    scope = f"{stamp[:8]}/{region}/s3/aws4_request"
    query = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{credentials['AccessKeyId']}/{scope}",
             "X-Amz-Date": stamp, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    if credentials.get("SessionToken"):
        query["X-Amz-Security-Token"] = credentials["SessionToken"]
    quote = lambda value: urllib.parse.quote(value, safe="-_.~")
    canonical_query = "&".join(f"{quote(k)}={quote(v)}" for k, v in sorted(query.items()))
    path = "/" + urllib.parse.quote(key, safe="/-_.~")
    canonical = "\n".join([method, path, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signing = ("AWS4" + credentials["SecretAccessKey"]).encode()
    for part in (stamp[:8], region, "s3", "aws4_request"):
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{canonical_query}&X-Amz-Signature={signature}"


def native(args):
    if not NAME.fullmatch(args.boundary) or args.boundary == "personal":
        raise SystemExit("边界名称为小写字母开头的 2–20 位小写字母、数字或连字符（personal 用平台自己的原生入口）")
    params, _ = stack(args.region, args.stack)
    slot = next((i for i in SLOTS if params.get(f"NativeRelay{i}") == args.boundary), None) or \
        next((i for i in SLOTS if not params.get(f"NativeRelay{i}")), None)
    if slot is None:
        raise SystemExit("9 个原生中转端口（8920–8928）都已使用：先在栈里清空不再需要的边界")
    update(args.region, args.stack, {f"NativeRelay{slot}": args.boundary})
    _, outputs = stack(args.region, args.stack)
    domain = outputs.get(f"NativeRelayDomain{slot}", "")
    if not re.fullmatch(r"[a-z0-9]+\.cloudfront\.net", domain):
        raise SystemExit(f"栈没有给出边界 {args.boundary} 的原生分发域名（{domain}）")
    print(on_host(args.region, params["InstanceId"],
                  f"bash /opt/kirocrew-on/current/deploy/install/boundary-native.sh {args.boundary} {8919 + slot} {domain}"), end="")
    return {"boundary": args.boundary, "slot": slot, "native_domain": domain}


def backup(args):
    if not BUCKET.fullmatch(args.bucket):
        raise SystemExit(f"S3 桶名不对：{args.bucket}")
    params, _ = stack(args.region, args.stack)
    credentials = aws(args.region, "configure", "export-credentials", "--format", "process")
    key = f"kirocrew-on/backups/{params['InstanceId']}/{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.tar.gz"
    url = presign("PUT", args.bucket, key, args.region, credentials, EXPIRES)
    script = "\n".join([
        "set -euo pipefail",
        'D="$(mktemp -d)"; trap \'rm -rf "$D"\' EXIT',
        'kirocrew-on --config /etc/kirocrew-on/platform.toml backup --dest "$D/backup" >/dev/null',
        'tar -czf "$D/backup.tar.gz" -C "$D" backup',
        f"curl -fsS --upload-file \"$D/backup.tar.gz\" '{url}'",
        'stat -c %s "$D/backup.tar.gz"'])
    size = on_host(args.region, params["InstanceId"], script).strip().splitlines()[-1:]
    print(f"已上传 s3://{args.bucket}/{key}（{size[0] if size else '?'} 字节）；用 kirocrew-on restore-verify 可在别处核对")
    return {"bucket": args.bucket, "key": key}


def up(args):
    update(args.region, args.stack, {})
    print(f"栈 {args.stack} 已按这个版本的模板更新")
    return {"stack": args.stack}


def report(args, state, result):
    request = urllib.request.Request(
        args.platform.rstrip("/") + f"/v1/admin/aws-requests/{args.request}/done", method="POST",
        data=json.dumps({"state": state, "result": result}).encode(),
        headers={"Authorization": "Bearer " + Path(args.token_file).read_text().strip(), "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30):
        pass


def main(argv=None):
    def options(target, suppress):   # before or after the action alike; the action's copies never reset the main ones
        default = (lambda value: argparse.SUPPRESS) if suppress else (lambda value: value)
        target.add_argument("--region", default=default(os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")))
        target.add_argument("--stack", default=default(STACK))
        target.add_argument("--platform", default=default(None), help="控制台地址：执行后向它回报（与 --token-file、--request 一起）")
        target.add_argument("--token-file", default=default(None), help="管理员 API Token 所在的文件")
        target.add_argument("--request", default=default(None), help="控制台「管理 › 部署」里待执行项的编号")
    parser = argparse.ArgumentParser(prog="kirocrew-on single", description="单机版的部署套件：在你的电脑或 CloudShell 上用管理员凭证运行")
    options(parser, False)
    sub = parser.add_subparsers(dest="action", required=True)
    for action, text in (("native", "给一个边界开公网原生入口"), ("backup", "平台数据的异地备份"), ("up", "按这个版本的模板更新单机版的栈")):
        command = sub.add_parser(action, help=text)
        options(command, True)
        if action == "native":
            command.add_argument("boundary")
        if action == "backup":
            command.add_argument("--bucket", required=True)
    args = parser.parse_args(argv)
    if not args.region:
        raise SystemExit("需要 --region（或 AWS_REGION）")
    if args.request and not (args.platform and args.token_file):
        raise SystemExit("--request 需要同时给出 --platform 与 --token-file")
    try:
        result = {"native": native, "backup": backup, "up": up}[args.action](args)
    except SystemExit as stop:
        if args.request:
            report(args, "failed", {"error": str(stop)[:500]})
        raise
    if args.request:
        report(args, "done", result)
    return 0
