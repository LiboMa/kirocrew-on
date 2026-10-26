"""MVP 0.2.0 P0 T8 (design §7.5, §8.5): how an Auto Scaling group treats hosts that carry users — scale-in protection, a
termination lifecycle hook as the host agent's drain (heartbeats keep it waiting; the hook's completion lets it go), and
replacing an unhealthy host (also through the hook, protection or not). Timings are measured; t4g.nano hosts, tagged
kirocrew:p0=1, deleted at the end (tools/p0/p0.sh teardown also removes the group and its template).

    python3 tools/p0/t8_asg.py --out /tmp/p0
"""
import argparse
import json
import time
from pathlib import Path

from ebs_bench import aws

NAME = "kc-p0-t8"
SUBNET = "subnet-0b12e072d10082aee"
HOOK = "kc-p0-drain"


def group():
    info, _ = aws("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", NAME)
    return info["AutoScalingGroups"][0] if info["AutoScalingGroups"] else None


def states():
    return {i["InstanceId"]: i["LifecycleState"] for i in (group() or {}).get("Instances", [])}


def wait(predicate, timeout=900, poll=3):
    start = time.time()
    while time.time() - start < timeout:
        now = states()
        if predicate(now):
            return round(time.time() - start, 1), now
        time.sleep(poll)
    raise TimeoutError(f"{NAME}: {states()}")


def in_service(count):
    return lambda s: sum(1 for v in s.values() if v == "InService") == count


def setup(sg, profile):
    image, _ = aws("ssm", "get-parameter", "--name", "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64")
    tags = [{"Key": "kirocrew:p0", "Value": "1"}, {"Key": "Name", "Value": NAME}]
    data = {"ImageId": image["Parameter"]["Value"], "InstanceType": "t4g.nano", "SecurityGroupIds": [sg],
            "IamInstanceProfile": {"Name": profile}, "MetadataOptions": {"HttpTokens": "required"},
            "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}, {"ResourceType": "volume", "Tags": tags}]}
    aws("ec2", "create-launch-template", "--launch-template-name", NAME, "--launch-template-data", json.dumps(data),
        "--tag-specifications", json.dumps([{"ResourceType": "launch-template", "Tags": tags}]))
    aws("autoscaling", "create-auto-scaling-group", "--auto-scaling-group-name", NAME,
        "--launch-template", f"LaunchTemplateName={NAME},Version=$Latest", "--min-size", "0", "--max-size", "3",
        "--desired-capacity", "2", "--vpc-zone-identifier", SUBNET, "--health-check-type", "EC2",
        "--health-check-grace-period", "60", "--tags", "Key=kirocrew:p0,Value=1,PropagateAtLaunch=true")


def save(out, result):
    Path(out).mkdir(parents=True, exist_ok=True)
    Path(out, "t8.json").write_text(json.dumps(result, indent=2))


def finish(instance):
    aws("autoscaling", "complete-lifecycle-action", "--auto-scaling-group-name", NAME, "--lifecycle-hook-name", HOOK,
        "--instance-id", instance, "--lifecycle-action-result", "CONTINUE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sg", required=True)
    parser.add_argument("--profile", default="kc-p0-ssm")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = {}
    setup(args.sg, args.profile)
    result["launch_two_s"], now = wait(in_service(2))
    keep, other = sorted(now)

    # 1. Scale-in protection: the protected host (a host with users) stays; the other goes.
    aws("autoscaling", "set-instance-protection", "--auto-scaling-group-name", NAME, "--instance-ids", keep,
        "--protected-from-scale-in")
    aws("autoscaling", "set-desired-capacity", "--auto-scaling-group-name", NAME, "--desired-capacity", "1")
    seconds, now = wait(lambda s: other not in s or s.get(other) == "Terminated")
    result["scale_in"] = {"s": seconds, "protected_kept": now.get(keep) == "InService", "unprotected_gone": other not in now
                          or now[other] == "Terminated"}
    save(args.out, result)

    # 2. The drain: a termination hook holds the host in Terminating:Wait; heartbeats extend it; completing lets it go.
    aws("autoscaling", "put-lifecycle-hook", "--auto-scaling-group-name", NAME, "--lifecycle-hook-name", HOOK,
        "--lifecycle-transition", "autoscaling:EC2_INSTANCE_TERMINATING", "--heartbeat-timeout", "120",
        "--default-result", "CONTINUE")
    aws("autoscaling", "set-instance-protection", "--auto-scaling-group-name", NAME, "--instance-ids", keep,
        "--no-protected-from-scale-in")
    aws("autoscaling", "set-desired-capacity", "--auto-scaling-group-name", NAME, "--desired-capacity", "0")
    to_wait, _ = wait(lambda s: s.get(keep) == "Terminating:Wait")
    held = time.time()
    for _ in range(3):   # 150 s in all: past the 120 s timeout only because of the heartbeats
        time.sleep(50)
        aws("autoscaling", "record-lifecycle-action-heartbeat", "--auto-scaling-group-name", NAME, "--lifecycle-hook-name",
            HOOK, "--instance-id", keep)
    still = states().get(keep)
    finish(keep)
    gone, _ = wait(lambda s: keep not in s)
    result["drain"] = {"to_wait_s": to_wait, "held_s": round(time.time() - held - gone, 1), "still_waiting_after_heartbeats":
                       still == "Terminating:Wait", "gone_after_complete_s": gone}
    save(args.out, result)

    # 3. An unhealthy host is replaced — through the hook, and even when protected from scale-in. By default the group
    #    terminates first: the replacement only starts once the old host has left (the hook completed or timed out).
    # 4. With an instance maintenance policy of 100–200 % it launches the replacement first, while the old one drains.
    aws("autoscaling", "set-desired-capacity", "--auto-scaling-group-name", NAME, "--desired-capacity", "1")
    for label, policy in (("unhealthy_default", None), ("unhealthy_launch_first", "MinHealthyPercentage=100,MaxHealthyPercentage=200")):
        if policy:
            aws("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", NAME, "--instance-maintenance-policy",
                policy)
        _, now = wait(in_service(1))
        sick = next(i for i, v in now.items() if v == "InService")
        aws("autoscaling", "set-instance-protection", "--auto-scaling-group-name", NAME, "--instance-ids", sick,
            "--protected-from-scale-in")
        marked = time.time()
        aws("autoscaling", "set-instance-health", "--instance-id", sick, "--health-status", "Unhealthy",
            "--no-should-respect-grace-period")
        to_wait, _ = wait(lambda s: s.get(sick) == "Terminating:Wait")
        # Whichever comes first: a replacement in service while the old host still drains, or the old host gone.
        _, now = wait(lambda s: sick not in s or any(i != sick and v == "InService" for i, v in s.items()), timeout=900)
        row = {"protected": True, "to_terminating_wait_s": to_wait, "first_event_s": round(time.time() - marked, 1),
               "replacement_while_draining": sick in now}
        if sick in now:
            finish(sick)
        wait(lambda s: sick not in s)
        row["old_gone_s"] = round(time.time() - marked, 1)
        wait(in_service(1))
        row["replacement_in_service_s"] = round(time.time() - marked, 1)
        result[label] = row
        save(args.out, result)

    activities, _ = aws("autoscaling", "describe-scaling-activities", "--auto-scaling-group-name", NAME, "--max-items", "20")
    result["activities"] = [{"status": a["StatusCode"], "cause": a["Cause"][:200]} for a in activities["Activities"]]

    aws("autoscaling", "delete-auto-scaling-group", "--auto-scaling-group-name", NAME, "--force-delete")
    aws("ec2", "delete-launch-template", "--launch-template-name", NAME)
    save(args.out, result)
    print(json.dumps({k: v for k, v in result.items() if k != "activities"}, indent=2))


if __name__ == "__main__":
    main()
