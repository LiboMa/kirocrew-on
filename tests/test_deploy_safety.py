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
from unittest.mock import patch

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
        # D49: every boundary on the host is checked (boundary_check) before either link moves.
        checks = self.text.index('boundary_check "$OTHER" "$TARGET" "$KC"')
        self.assertLess(self.text.index('boundary_check "$BOUNDARY" "$TARGET" "$KC"'), checks)
        for link in ('ln -sfn "$TARGET" /opt/kirocrew-on/current', 'ln -sfn "$KC" /opt/kirocrew/current'):
            self.assertEqual(self.text.count(link), 1, link)
            self.assertGreater(self.text.index(link), checks, link)
        self.assertNotIn("/opt/kirocrew/current/", self.text[:checks])
        self.assertNotIn("/opt/kirocrew-on/current/", self.text[:checks])
        # The checks read a copy of the boundary config whose current paths are rewritten to the release being installed.
        library = (ROOT / "deploy/install/boundary-lib.sh").read_text()
        rewrite = 'sed -e "s#/opt/kirocrew-on/current/#$2/#g" -e "s#/opt/kirocrew/current/#$3/#g" "$toml" > "$check"'
        self.assertEqual(library.count(rewrite), 1)
        self.assertIn('-m composition.gateway --config "$check" --check', library)
        self.assertIn('OCTOPUS_CONFIG="$check"', library)
        self.assertIn('"$3/venv/bin/python" -s -m unittest discover -s "$2/tests/native"', library)

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
        with tempfile.TemporaryDirectory() as directory:   # the console domain, or its cloudfront.net name once more
            config = Path(directory) / "platform.toml"
            config.write_text('api_host = "0.0.0.0"\n')
            subprocess.run(["bash", "-c", script + f'\nPYTHON=python3 merge_hosts "{config}" d1.cloudfront.net '
                            'd1.cloudfront.net on.example on.example localhost:18918'], check=True)
            self.assertIn('allowed_hosts = ["d1.cloudfront.net", "on.example", "localhost:18918"]', config.read_text())

    def test_an_unset_preview_session_domain_leaves_no_key_an_older_release_would_reject(self):
        start = self.text.index("toml_unset() {")
        script = self.text[start:self.text.index("\n}\n", start) + 3]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "platform.toml"
            config.write_text('api_host = "0.0.0.0"\npreview_domain = "preview.example"\n[tools]\nx = "y"\n')
            subprocess.run(["bash", "-c", script + f'\nPYTHON=python3 toml_unset "{config}" preview_domain'], check=True)
            self.assertEqual(config.read_text(), 'api_host = "0.0.0.0"\n[tools]\nx = "y"\n')
            subprocess.run(["bash", "-c", script + f'\nPYTHON=python3 toml_unset "{config}" preview_domain'], check=True)
            self.assertEqual(config.read_text(), 'api_host = "0.0.0.0"\n[tools]\nx = "y"\n')   # absent: unchanged


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
elif "--change-batch" in args and args[args.index("--change-batch") + 1].startswith("file://"):
    record({"batch": json.load(open(args[args.index("--change-batch") + 1][7:]))})
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
elif "list-hosted-zones" in args:
    print("/hostedzone/ZOTHER000\texample.org.\n/hostedzone/ZPUBLIC00\ttinyboat.example.\n"
          "/hostedzone/ZDEEP0000\tsub.tinyboat.example.\n/hostedzone/ZBLOG0000\ttinyboat.blog.")   # stack.env's own
elif "list-certificates" in args and "*.preview." in joined:   # the per-session preview certificate (D36 phase 3)
    print(os.environ.get("FAKE_PREVIEW_CERTIFICATE", '[["arn:aws:acm:us-east-1:111111111111:certificate/preview", "ISSUED"]]'))
elif "list-certificates" in args:
    print(os.environ.get("FAKE_CERTIFICATE", "arn:aws:acm:us-east-1:111111111111:certificate/abc"))
elif "request-certificate" in args:
    print("arn:aws:acm:us-east-1:111111111111:certificate/requested")
elif "describe-certificate" in args and "Certificate.Status" in query():
    print(os.environ.get("FAKE_CERT_STATUS", "ISSUED"))
elif "describe-certificate" in args:
    print("_abc.preview.tinyboat.example.\t_def.acm-validations.aws.")
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

    def parameters(self, calls):
        return {p["ParameterKey"]: p["ParameterValue"] for p in self.made(calls, "create-change-set")[0]["parameters"]}

    def test_the_preview_port_goes_to_the_stack(self):
        result, calls = self.up()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.parameters(calls)["PreviewPort"], env_file(ROOT / "deploy/aws/stack.env")["PREVIEW_PORT"])

    def test_without_a_console_domain_the_console_keeps_its_cloudfront_name(self):
        result, calls = self.up(stack_env={"CONSOLE_DOMAIN": "", "PREVIEW_SESSION_DOMAIN": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        parameters = self.parameters(calls)
        self.assertEqual([parameters[k] for k in ("ConsoleDomain", "ConsoleHostedZoneId", "ConsoleCertificateArn")], ["", "", ""])
        self.assertFalse(self.made(calls, "list-hosted-zones") or self.made(calls, "list-certificates"))

    def test_a_console_domain_finds_its_zone_and_certificate(self):
        result, calls = self.up(stack_env={"CONSOLE_DOMAIN": "on.sub.tinyboat.example"})
        self.assertEqual(result.returncode, 0, result.stderr)
        parameters = self.parameters(calls)
        self.assertEqual([parameters[k] for k in ("ConsoleDomain", "ConsoleHostedZoneId", "ConsoleCertificateArn")],
                         ["on.sub.tinyboat.example", "ZDEEP0000", "arn:aws:acm:us-east-1:111111111111:certificate/abc"])
        (lookup,) = [c for c in self.made(calls, "list-certificates") if "*.preview." not in " ".join(c["args"])]
        self.assertEqual(lookup["args"][:2], ["--region", "us-east-1"])   # CloudFront only uses us-east-1 certificates
        self.assertIn("ISSUED", lookup["args"])
        self.assertIn("*.sub.tinyboat.example", " ".join(lookup["args"]))

    def test_a_console_domain_without_a_zone_or_certificate_stops_before_any_change(self):
        for stack_env, fake in (({"CONSOLE_DOMAIN": "on.elsewhere.test"}, {}),
                                ({"CONSOLE_DOMAIN": "on.tinyboat.example"}, {"certificate": "None"})):
            result, calls = self.up(stack_env=stack_env, **fake)
            self.assertNotEqual(result.returncode, 0, stack_env)
            self.assertFalse(self.made(calls, "create-change-set"))
            self.assertIn("on.", result.stderr)

    def test_without_a_preview_session_domain_previews_keep_one_origin(self):
        result, calls = self.up(stack_env={"PREVIEW_SESSION_DOMAIN": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        parameters = self.parameters(calls)
        self.assertEqual([parameters[k] for k in ("PreviewSessionDomain", "PreviewHostedZoneId", "PreviewCertificateArn")],
                         ["", "", ""])
        self.assertFalse([c for c in calls if "*.preview." in " ".join(c["args"])] or self.made(calls, "request-certificate"))

    def test_a_preview_session_domain_finds_its_zone_and_wildcard_certificate(self):
        result, calls = self.up(stack_env={"PREVIEW_SESSION_DOMAIN": "preview.tinyboat.example"})
        self.assertEqual(result.returncode, 0, result.stderr)
        parameters = self.parameters(calls)
        self.assertEqual([parameters[k] for k in ("PreviewSessionDomain", "PreviewHostedZoneId", "PreviewCertificateArn")],
                         ["preview.tinyboat.example", "ZPUBLIC00", "arn:aws:acm:us-east-1:111111111111:certificate/preview"])
        (lookup,) = [c for c in self.made(calls, "list-certificates") if "*.preview." in " ".join(c["args"])]
        self.assertEqual(lookup["args"][:2], ["--region", "us-east-1"])
        self.assertIn("DomainName=='*.preview.tinyboat.example'", " ".join(lookup["args"]))
        self.assertFalse(self.made(calls, "request-certificate"))

    def test_a_missing_preview_certificate_is_requested_and_validated_in_its_zone(self):
        result, calls = self.up(stack_env={"PREVIEW_SESSION_DOMAIN": "preview.tinyboat.example"}, preview_certificate="[]")
        self.assertEqual(result.returncode, 0, result.stderr)
        (request,) = self.made(calls, "request-certificate")
        joined = " ".join(request["args"])
        for part in ("--region us-east-1", "--domain-name *.preview.tinyboat.example", "--validation-method DNS"):
            self.assertIn(part, joined)
        token = request["args"][request["args"].index("--idempotency-token") + 1]
        self.assertRegex(token, r"^[0-9a-f]{32}$")   # one per domain: a new domain within the hour is a new certificate
        self.assertNotEqual(token, self.token_for("preview.other.example"))
        (change,) = self.made(calls, "change-resource-record-sets")
        self.assertIn("ZPUBLIC00", change["args"])
        record = change["batch"]["Changes"][0]
        self.assertEqual((record["Action"], record["ResourceRecordSet"]["Type"], record["ResourceRecordSet"]["Name"],
                          record["ResourceRecordSet"]["ResourceRecords"]),
                         ("UPSERT", "CNAME", "_abc.preview.tinyboat.example.", [{"Value": "_def.acm-validations.aws."}]))
        polls = [c for c in self.made(calls, "describe-certificate") if "Certificate.Status" in " ".join(c["args"])]
        self.assertTrue(polls)
        self.assertLess(calls.index(polls[-1]), calls.index(self.made(calls, "create-change-set")[0]))   # issued first
        self.assertEqual(self.parameters(calls)["PreviewCertificateArn"], "arn:aws:acm:us-east-1:111111111111:certificate/requested")

    @staticmethod
    def token_for(domain):
        import hashlib
        return hashlib.sha256(domain.encode()).hexdigest()[:32]

    def test_a_pending_preview_certificate_is_validated_not_requested_again(self):
        pending = "arn:aws:acm:us-east-1:111111111111:certificate/pending"
        result, calls = self.up(stack_env={"PREVIEW_SESSION_DOMAIN": "preview.tinyboat.example"},
                                preview_certificate=json.dumps([[pending, "PENDING_VALIDATION"]]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.made(calls, "request-certificate"))
        self.assertEqual(len(self.made(calls, "change-resource-record-sets")), 1)
        self.assertEqual(self.parameters(calls)["PreviewCertificateArn"], pending)

    def test_an_issued_certificate_wins_over_an_abandoned_pending_one(self):
        rows = [["arn:aws:acm:us-east-1:111111111111:certificate/pending", "PENDING_VALIDATION"],
                ["arn:aws:acm:us-east-1:111111111111:certificate/issued", "ISSUED"]]
        result, calls = self.up(stack_env={"PREVIEW_SESSION_DOMAIN": "preview.tinyboat.example"}, preview_certificate=json.dumps(rows))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.parameters(calls)["PreviewCertificateArn"], "arn:aws:acm:us-east-1:111111111111:certificate/issued")
        self.assertFalse(self.made(calls, "change-resource-record-sets"))
        lookup = [c for c in self.made(calls, "list-certificates") if "*.preview." in " ".join(c["args"])][0]
        self.assertEqual(lookup["args"][lookup["args"].index("--output") + 1], "json")   # one result across pages

    def test_a_certificate_still_validating_stops_before_any_change_and_says_to_rerun(self):
        with patch.dict(os.environ, {"DEPLOY_CERT_WAIT_SECONDS": "0"}):
            result, calls = self.up(stack_env={"PREVIEW_SESSION_DOMAIN": "preview.tinyboat.example"}, preview_certificate="[]",
                                    cert_status="PENDING_VALIDATION")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.made(calls, "create-change-set"))
        self.assertIn("重新运行", result.stderr)

    def test_a_missing_origin_secret_is_created_once(self):
        result, calls = self.up(ssm_error="ParameterNotFound")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.made(calls, "put-parameter")), 1)


if __name__ == "__main__":
    unittest.main()
