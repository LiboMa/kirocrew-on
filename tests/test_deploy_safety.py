"""Deployment safety (Part B review): no silent host replacement, secrets the execution user cannot read, installs that
switch to a release only after it passed its checks, and AWS errors that stop instead of desynchronising secrets."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: {suffix: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))})


def template():
    return yaml.load((ROOT / "deploy/aws/kirocrew-on-ec2.yaml").read_text(), Loader=CfnLoader)


def env_file(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#") and "=" in line)


class TemplateSafetyTests(unittest.TestCase):
    def test_the_image_is_a_pinned_value_not_a_moving_ssm_alias(self):
        # An SSM-alias ImageId re-resolves on every update; a new AL2023 AMI would replace the host and strand its data.
        parameter = template()["Parameters"]["ImageId"]
        self.assertEqual(parameter["Type"], "AWS::EC2::Image::Id")
        self.assertNotIn("Default", parameter)
        pins = env_file(ROOT / "deploy/aws/stack.env")
        self.assertRegex(pins.get("IMAGE_ID", ""), r"^ami-[0-9a-f]{8,}$")
        self.assertRegex(pins.get("AVAILABILITY_ZONE", ""), r"^us-east-1[a-z]$")

    def test_the_host_role_reads_only_the_origin_secret(self):
        role = next(r for r in template()["Resources"].values() if r["Type"] == "AWS::IAM::Role"
                    and "ec2.amazonaws.com" in json.dumps(r["Properties"]["AssumeRolePolicyDocument"]))
        statements = [s for p in role["Properties"]["Policies"] for s in p["PolicyDocument"]["Statement"]]
        ssm = [s for s in statements if "ssm:GetParameter" in s["Action"]]
        self.assertEqual(len(ssm), 1)
        self.assertTrue(json.dumps(ssm[0]["Resource"]).endswith('/origin-secret"}'), ssm[0]["Resource"])

    def test_the_security_group_description_is_unchanged(self):
        # GroupDescription is immutable in CloudFormation: editing it replaces the group and, through it, the host.
        group = template()["Resources"]["HostSecurityGroup"]["Properties"]
        self.assertEqual(group["GroupDescription"], "KiroCrew ON API - inbound only from CloudFront origin-facing addresses; admin via SSM")

    def test_alarm_notifications_can_reach_the_topic(self):
        # CloudWatch alarms cannot publish to a topic encrypted with the AWS-managed aws/sns key.
        topic = template()["Resources"]["AlertTopic"]["Properties"]
        self.assertNotEqual(topic.get("KmsMasterKeyId"), "alias/aws/sns")


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "deploy/install/al2023.sh").read_text()

    def test_current_switches_only_after_the_new_release_passed_its_checks(self):
        checks = self.text.index("-m unittest discover")
        for link in ('ln -sfn "$TARGET" /opt/kirocrew-on/current', 'ln -sfn "$KC" /opt/kirocrew/current'):
            self.assertEqual(self.text.count(link), 1, link)
            self.assertGreater(self.text.index(link), checks, link)
        # The checks read a copy of the boundary config whose current paths are rewritten to the release being installed.
        rewrite = 'sed -e "s#/opt/kirocrew-on/current/#$TARGET/#g" -e "s#/opt/kirocrew/current/#$KC/#g" "$BOUNDARY_TOML" > "$CHECK_TOML"'
        self.assertEqual(self.text.count(rewrite), 1)
        self.assertIn('-m composition.gateway --config "$CHECK_TOML" --check', self.text)
        self.assertIn('OCTOPUS_CONFIG="$CHECK_TOML"', self.text)
        before = self.text[:checks].replace(rewrite, "")
        self.assertNotIn("/opt/kirocrew/current/", before)
        self.assertNotIn("/opt/kirocrew-on/current/", before)
        self.assertIn('"$KC/venv/bin/python" -s -m unittest discover -s "$TARGET/tests/native"', self.text)

    def test_the_admin_password_lives_in_a_root_only_file_on_the_host(self):
        self.assertNotIn("ADMIN_PASSWORD_PARAM", self.text)
        self.assertIn("ADMIN_FILE=/etc/kirocrew-on/admin-password", self.text)
        self.assertIn('chmod 0600 "$ADMIN_FILE"', self.text)

    def test_a_failed_secret_fetch_never_leaves_an_empty_secret_file(self):
        self.assertIn("> /etc/kirocrew-on/origin-secret.new", self.text)
        self.assertIn("mv -f /etc/kirocrew-on/origin-secret.new /etc/kirocrew-on/origin-secret", self.text)
        self.assertNotIn("--output text > /etc/kirocrew-on/origin-secret\n", self.text)

    def test_allowed_hosts_keep_operator_entries_and_the_ssm_tunnel(self):
        script = self.text[self.text.index("merge_hosts() {"):self.text.index("\n}\n", self.text.index("merge_hosts() {")) + 3]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "platform.toml"
            config.write_text('api_host = "0.0.0.0"\nallowed_hosts = ["ops.example"]\n\n[tools]\nx = "y"\n')
            subprocess.run(["bash", "-c", script + f'\nPYTHON=python3 merge_hosts "{config}" d1.cloudfront.net localhost:18918'], check=True)
            merged = config.read_text()
        self.assertIn('allowed_hosts = ["ops.example", "d1.cloudfront.net", "localhost:18918"]', merged)
        self.assertIn('[tools]\nx = "y"', merged)


FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_LOG"]
def record(extra=None):
    with open(log, "a") as handle:
        handle.write(json.dumps({"args": args, **(extra or {})}) + "\n")
joined = " ".join(args)
if "--parameters" in args and args[args.index("--parameters") + 1].startswith("file://"):
    record({"parameters": json.load(open(args[args.index("--parameters") + 1][7:]))})
else:
    record()
def query():
    return args[args.index("--query") + 1] if "--query" in args else ""
if "describe-stacks" in args:
    q = query()
    if "StackStatus" in q:
        if os.environ.get("FAKE_STACK") == "none":
            sys.stderr.write("Stack with id x does not exist\n"); sys.exit(255)
        print("UPDATE_COMPLETE")
    elif "InstanceId" in q:
        print("i-fake")
    else:
        print("[]")
elif "describe-managed-prefix-lists" in args:
    print("pl-fake")
elif "describe-instance-type-offerings" in args:
    print("us-east-1c")
elif "describe-instances" in args:
    print("ami-0running0000000\tus-east-1b")
elif "get-parameter" in args:
    name = args[args.index("--name") + 1]
    if name.startswith("/aws/service/"):
        print("ami-0latest00000000"); sys.exit(0)
    error = os.environ.get("FAKE_SSM_ERROR", "")
    if error:
        sys.stderr.write(f"An error occurred ({error}) when calling the GetParameter operation\n"); sys.exit(254)
    print("o" * 48)
elif "describe-change-set" in args:
    if "StatusReason" in query():
        print(os.environ.get("FAKE_REASON", ""))
    else:
        print(os.environ.get("FAKE_REPLACED", ""))
elif "wait" in args and "change-set-create-complete" in args:
    sys.exit(255 if os.environ.get("FAKE_REASON") else 0)
'''


class DeployUpTests(unittest.TestCase):
    """deploy.sh up against a fake `aws` that records every call."""

    def up(self, stack_env=None, **fake):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy/aws").mkdir(parents=True)
            for name in ("deploy.sh", "kirocrew-on-ec2.yaml", "stack.env"):
                shutil.copy(ROOT / "deploy/aws" / name, root / "deploy/aws" / name)
            if stack_env is not None:
                env = (root / "deploy/aws/stack.env").read_text()
                for key, value in stack_env.items():
                    env = "\n".join(line for line in env.splitlines() if not line.startswith(key + "=")) + f"\n{key}={value}\n"
                (root / "deploy/aws/stack.env").write_text(env)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "aws").write_text(FAKE_AWS)
            (bin_dir / "aws").chmod((bin_dir / "aws").stat().st_mode | stat.S_IEXEC)
            log = root / "aws.log"
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log),
                   **{"FAKE_" + k.upper(): v for k, v in fake.items()}}
            env.pop("DEPLOY_DRY_RUN", None)
            result = subprocess.run(["bash", str(root / "deploy/aws/deploy.sh"), "up"], cwd=root, env=env,
                                    capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    @staticmethod
    def made(calls, word):
        return [c for c in calls if word in c["args"]]

    def test_a_change_set_that_replaces_a_resource_is_refused(self):
        result, calls = self.up(replaced="Host")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("Host", result.stderr)
        self.assertFalse(self.made(calls, "execute-change-set"))
        self.assertTrue(self.made(calls, "delete-change-set"))

    def test_an_update_keeps_the_pinned_image_and_zone(self):
        result, calls = self.up()
        self.assertEqual(result.returncode, 0, result.stderr)
        created = self.made(calls, "create-change-set")[0]
        parameters = {p["ParameterKey"]: p["ParameterValue"] for p in created["parameters"]}
        pins = env_file(ROOT / "deploy/aws/stack.env")
        self.assertEqual(parameters["ImageId"], pins["IMAGE_ID"])
        self.assertEqual(parameters["AvailabilityZone"], pins["AVAILABILITY_ZONE"])
        self.assertIn("UPDATE", created["args"])
        self.assertTrue(self.made(calls, "execute-change-set"))
        self.assertNotIn("o" * 48, result.stdout + result.stderr + json.dumps([c["args"] for c in calls]))

    def test_an_existing_stack_without_pins_keeps_the_running_image_and_zone(self):
        result, calls = self.up(stack_env={"IMAGE_ID": "", "AVAILABILITY_ZONE": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        parameters = {p["ParameterKey"]: p["ParameterValue"] for p in self.made(calls, "create-change-set")[0]["parameters"]}
        self.assertEqual((parameters["ImageId"], parameters["AvailabilityZone"]), ("ami-0running0000000", "us-east-1b"))

    def test_a_change_set_without_changes_is_not_an_error(self):
        result, calls = self.up(reason="The submitted information didn't contain changes.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.made(calls, "execute-change-set"))
        self.assertTrue(self.made(calls, "delete-change-set"))

    def test_an_unreadable_origin_secret_stops_instead_of_creating_a_new_one(self):
        result, calls = self.up(ssm_error="ThrottlingException")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.made(calls, "put-parameter"))
        self.assertFalse(self.made(calls, "create-change-set"))

    def test_a_missing_origin_secret_is_created_once(self):
        result, calls = self.up(ssm_error="ParameterNotFound")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.made(calls, "put-parameter")), 1)


if __name__ == "__main__":
    unittest.main()
