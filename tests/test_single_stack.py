"""MVP 0.2.0 P2 Task 4 (design §4.2.3 step 13, §4.2.4, S29, S31): the single-node edition's CloudFront stack
kirocrew-on-single — an Elastic IP, the CloudFront-only ingress rule on the host's own security group, the console,
native and preview distributions with the origin secret injected, and daily DLM snapshots — made with the installing
credentials. Afterwards the host role should keep Bedrock (and SSM) only: the check names any policy still granting writes.
A fake aws records every call."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.test_deploy_safety import CfnLoader
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/aws/kirocrew-on-single.yaml"
SCRIPT = ROOT / "deploy/install/single-stack.sh"
KEY_ID, SECRET_KEY = "AKIAFAKEINSTALLKEY01", "fake/Secret+AccessKeyForTheInstaller0001"
FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
state = json.load(open(os.environ["FAKE_STATE"]))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
    if "--parameters" in args:
        log.write("PARAMS " + open(args[args.index("--parameters") + 1][len("file://"):]).read().strip() + "\n")
def out(text):
    print(text); sys.exit(0)
def deny(action):
    sys.stderr.write(f"An error occurred (AccessDenied) when calling the {action} operation: not authorized\n"); sys.exit(254)
joined = " ".join(args)
if "describe-managed-prefix-lists" in args: out("pl-3b927c52")
if "describe-instances" in args: out("sg-0123abcd")
if "create-tags" in args: out("")
if "describe-stacks" in args:
    if "ParameterKey" in joined:
        out("\t".join(state.get("parameters", [])) or "None")
    if "Outputs" in joined:
        out("\t".join(f"{k}\t{v}" for k, v in state["outputs"].items()))
    if state.get("statuses"):   # one status per read, the last one kept
        status = state["statuses"].pop(0) if len(state["statuses"]) > 1 else state["statuses"][0]
        json.dump(state, open(os.environ["FAKE_STATE"], "w"))
        out(status)
    if state.get("exists"): out("CREATE_COMPLETE")
    sys.stderr.write("Stack with id kirocrew-on-single does not exist\n"); sys.exit(255)
if "create-change-set" in args:
    if state.get("deny"): deny("CreateChangeSet")
    out("{}")
if "wait" in args:
    if "change-set-create-complete" in args and state.get("no_changes"): sys.exit(255)
    out("")
if "describe-change-set" in args:
    if "StatusReason" in joined: out("The submitted information didn't contain changes.")
    out(state.get("replaced", "None"))
if "execute-change-set" in args or "delete-change-set" in args or "delete-stack" in args: out("")
if "get-role" in args: out("arn:aws:iam::123456789012:role/kirocrew-on-host")
if "simulate-principal-policy" in args:
    if state.get("simulate_denied"): deny("SimulatePrincipalPolicy")
    out("\t".join(state.get("allowed", [])) or "None")
if "list-attached-role-policies" in args: out("\t".join(state.get("attached", [])) or "None")
if "list-role-policies" in args: out("\t".join(state.get("inline", [])) or "None")
sys.stderr.write("unexpected call: " + joined + "\n"); sys.exit(99)
'''
OUTPUTS = {"ConsoleDomain": "d1console.cloudfront.net", "NativeDomain": "d2native.cloudfront.net",
           "PreviewDomain": "d3preview.cloudfront.net", "PublicIp": "203.0.113.7"}


def load():
    return yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)


class TemplateTests(unittest.TestCase):
    def test_nothing_grants_the_host_role_anything(self):
        resources = load()["Resources"]
        for name, resource in resources.items():
            with self.subTest(name):
                self.assertNotIn(resource["Type"], ("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy", "AWS::IAM::InstanceProfile",
                                                     "AWS::IAM::User", "AWS::IAM::AccessKey"))
                if resource["Type"] == "AWS::IAM::Role":   # only DLM's own service role
                    principal = resource["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]
                    self.assertEqual(principal, {"Service": "dlm.amazonaws.com"})
                    self.assertEqual(resource["Properties"]["ManagedPolicyArns"],
                                     ["arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"])
                    self.assertNotIn("Policies", resource["Properties"])

    def test_dlm_runs_as_its_service_role_on_the_tagged_host(self):
        resources = load()["Resources"]
        policy = next(r for r in resources.values() if r["Type"] == "AWS::DLM::LifecyclePolicy")["Properties"]
        role = policy["ExecutionRoleArn"]["GetAtt"]
        self.assertEqual(resources[role.split(".")[0]]["Type"], "AWS::IAM::Role")
        self.assertEqual(policy["PolicyDetails"]["ResourceTypes"], ["INSTANCE"])
        self.assertEqual(policy["PolicyDetails"]["TargetTags"], [{"Key": "Backup", "Value": {"Sub": "${AWS::StackName}"}}])
        self.assertEqual(policy["PolicyDetails"]["Schedules"][0]["CreateRule"]["Interval"], 24)

    def test_only_cloudfront_reaches_the_host_and_every_distribution_carries_the_origin_secret(self):
        resources = load()["Resources"]
        ingress = [r["Properties"] for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"]
        self.assertEqual(len(ingress), 1)
        self.assertEqual((ingress[0]["SourcePrefixListId"], ingress[0]["FromPort"], ingress[0]["ToPort"]),
                         ({"Ref": "CloudFrontPrefixListId"}, {"Ref": "ApiPort"}, {"Ref": "NativeRelayEnd"}))
        self.assertNotIn("CidrIp", ingress[0])
        distributions = [r["Properties"]["DistributionConfig"] for r in resources.values()
                         if r["Type"] == "AWS::CloudFront::Distribution"]
        self.assertEqual(len(distributions), 3 + 9)   # and one per extra boundary's native chat (8920–8928)
        ports = set()
        for config in distributions:
            origin = config["Origins"][0]
            self.assertEqual(origin["OriginCustomHeaders"], [{"HeaderName": "X-Origin-Verify", "HeaderValue": {"Ref": "OriginSecret"}}])
            self.assertEqual(config["DefaultCacheBehavior"]["ViewerProtocolPolicy"], "redirect-to-https")
            port = origin["CustomOriginConfig"]["HTTPPort"]
            ports.add(port["Ref"] if isinstance(port, dict) else port)
        self.assertEqual(ports, {"ApiPort", "NativePort", "PreviewPort", *range(8920, 8929)})
        self.assertTrue(load()["Parameters"]["OriginSecret"]["NoEcho"])
        self.assertEqual({k for k in load()["Outputs"] if not k.startswith("NativeRelayDomain")},
                         {"ConsoleDomain", "NativeDomain", "PreviewDomain", "PublicIp"})


class StackScriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        (self.bin / "aws").write_text(FAKE_AWS)
        (self.bin / "aws").chmod(0o755)
        self.fs = self.dir / "fs"
        self.state = {"outputs": OUTPUTS}
        self.log = self.dir / "aws.log"

    def run_script(self, *args, **env):
        (self.dir / "state.json").write_text(json.dumps(self.state))
        environment = {"PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_STATE": str(self.dir / "state.json"),
                       "FAKE_LOG": str(self.log), "INSTALL_FS_ROOT": str(self.fs), "EC2_REGION": "us-east-1",
                       "EC2_INSTANCE_ID": "i-0abc123", "EC2_ROLE": "kirocrew-on-host", "TMPDIR": str(self.dir / "tmp"),
                       "AWS_ACCESS_KEY_ID": KEY_ID, "AWS_SECRET_ACCESS_KEY": SECRET_KEY, **env}
        (self.dir / "tmp").mkdir(exist_ok=True)
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=environment, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class StackTests(StackScriptCase):
    def test_the_stack_is_made_with_a_change_set_and_its_domains_come_back(self):
        out = self.dir / "domains"
        done = self.run_script("up", str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(out.read_text().splitlines(), ["CF_DOMAIN=d1console.cloudfront.net", "NATIVE_DOMAIN=d2native.cloudfront.net",
                                                        "PREVIEW_DOMAIN=d3preview.cloudfront.net", "PUBLIC_IP=203.0.113.7"])
        calls = self.calls()
        self.assertTrue(any("create-tags --resources i-0abc123 --tags Key=Backup,Value=kirocrew-on-single" in c for c in calls))
        create = next(c for c in calls if "create-change-set" in c)
        self.assertIn("--change-set-type CREATE", create)
        self.assertIn("--stack-name kirocrew-on-single", create)
        self.assertIn("--capabilities CAPABILITY_IAM", create)
        self.assertTrue(any("execute-change-set" in c for c in calls))
        params = {p["ParameterKey"]: p["ParameterValue"]
                  for p in json.loads(next(c for c in calls if c.startswith("PARAMS "))[len("PARAMS "):])}
        secret = (self.fs / "etc/kirocrew-on/origin-secret").read_text().strip()
        self.assertRegex(secret, r"^[A-Za-z0-9_-]{48}$")
        self.assertEqual(params, {"InstanceId": "i-0abc123", "SecurityGroupId": "sg-0123abcd",
                                  "CloudFrontPrefixListId": "pl-3b927c52", "OriginSecret": secret})
        self.assertEqual((self.fs / "etc/kirocrew-on/origin-secret").stat().st_mode & 0o777, 0o600)
        self.assertFalse(any(secret in c for c in calls if not c.startswith("PARAMS ")))   # never on a command line

    def test_a_rerun_keeps_the_secret_and_a_stack_without_changes_is_fine(self):
        self.run_script("up", str(self.dir / "first"))
        secret = (self.fs / "etc/kirocrew-on/origin-secret").read_text()
        self.state.update(exists=True, no_changes=True, parameters=["InstanceId", "SecurityGroupId", "CloudFrontPrefixListId",
                                                                     "OriginSecret", "NativeRelay1", "NativeRelay2"])
        self.log.unlink()
        done = self.run_script("up", str(self.dir / "second"))
        self.assertEqual(done.returncode, 0, done.stderr)
        params = json.loads(next(c for c in self.calls() if c.startswith("PARAMS "))[len("PARAMS "):])
        # the boundaries' native entries (kirocrew-on single native) survive a rerun of install.sh
        self.assertIn({"ParameterKey": "NativeRelay1", "UsePreviousValue": True}, params)
        self.assertIn({"ParameterKey": "NativeRelay2", "UsePreviousValue": True}, params)
        self.assertEqual((self.fs / "etc/kirocrew-on/origin-secret").read_text(), secret)
        self.assertIn("--change-set-type UPDATE", next(c for c in self.calls() if "create-change-set" in c))
        self.assertFalse(any("execute-change-set" in c for c in self.calls()))
        self.assertIn("CF_DOMAIN=d1console.cloudfront.net", (self.dir / "second").read_text())

    def test_a_change_that_replaces_a_resource_is_refused(self):
        self.state.update(exists=True, replaced="HostAddress")
        done = self.run_script("up", str(self.dir / "domains"))
        self.assertEqual(done.returncode, 3)
        self.assertIn("HostAddress", done.stderr)
        self.assertFalse(any("execute-change-set" in c for c in self.calls()))

    def test_a_first_change_set_that_never_ran_is_created_again(self):   # review 2026-10-28: REVIEW_IN_PROGRESS
        self.state.update(statuses=["REVIEW_IN_PROGRESS"])
        self.assertEqual(self.run_script("up", str(self.dir / "domains")).returncode, 0)
        self.assertIn("--change-set-type CREATE", next(c for c in self.calls() if "create-change-set" in c))

    def test_a_stack_still_changing_is_waited_for(self):
        self.state.update(statuses=["CREATE_IN_PROGRESS", "CREATE_IN_PROGRESS", "CREATE_COMPLETE"])
        done = self.run_script("up", str(self.dir / "domains"), STACK_POLL="0")
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls()
        create = next(i for i, c in enumerate(calls) if "create-change-set" in c)
        self.assertEqual(sum("StackStatus" in c for c in calls[:create]), 3)   # read until it settled
        self.assertIn("--change-set-type UPDATE", calls[create])

    def test_a_stack_that_failed_to_roll_back_says_what_to_do(self):
        for status in ("ROLLBACK_FAILED", "DELETE_FAILED", "UPDATE_ROLLBACK_FAILED"):
            with self.subTest(status):
                self.state.update(statuses=[status])
                done = self.run_script("up", str(self.dir / "domains"))
                self.assertEqual(done.returncode, 5)
                self.assertIn(status, done.stderr)
                self.assertIn("CloudFormation", done.stderr)

    def test_missing_permissions_print_what_the_install_needs(self):
        self.state.update(deny=True)
        done = self.run_script("up", str(self.dir / "domains"))
        self.assertEqual(done.returncode, 4)
        for action in ("cloudformation:CreateChangeSet", "cloudfront:CreateDistribution", "ec2:AuthorizeSecurityGroupIngress",
                       "ec2:AllocateAddress", "dlm:CreateLifecyclePolicy", "iam:PassRole"):
            self.assertIn(action, done.stderr)
        self.assertIn("deploy/aws/kirocrew-on-single-install-policy.json", done.stderr)

    def test_the_installers_keys_are_never_written_down(self):
        self.run_script("up", str(self.dir / "domains"))
        for path in self.dir.rglob("*"):
            if path.is_file() and path.name != "state.json":
                text = path.read_text(errors="replace")
                self.assertNotIn(KEY_ID, text, path)
                self.assertNotIn(SECRET_KEY, text, path)
        self.assertEqual(list((self.dir / "tmp").iterdir()), [])   # the parameters file is gone too


class RoleTests(StackScriptCase):
    def test_a_role_with_only_bedrock_passes(self):
        done = self.run_script("check-role")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("只剩", done.stdout)
        simulate = next(c for c in self.calls() if "simulate-principal-policy" in c)
        for action in ("cloudformation:CreateStack", "ec2:AuthorizeSecurityGroupIngress", "iam:PassRole", "s3:PutObject"):
            self.assertIn(action, simulate)
        self.assertNotIn("bedrock:", simulate)
        self.assertEqual(self.recorded_role()["state"], "ok")

    def recorded_role(self):   # what the console's 管理 › 部署 shows until it is fixed (review 2026-10-28)
        path = self.fs / "etc/kirocrew-on/host-role.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        return json.loads(path.read_text())

    def test_write_permissions_left_on_the_role_name_the_policies_to_remove(self):
        self.state.update(allowed=["cloudformation:CreateStack", "ec2:AllocateAddress"],
                          attached=["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                                    "arn:aws:iam::123456789012:policy/kirocrew-on-single-install"],
                          inline=["bedrock-invoke"])
        done = self.run_script("check-role")
        self.assertEqual(done.returncode, 10)
        self.assertIn("cloudformation:CreateStack", done.stderr)
        self.assertIn("aws iam detach-role-policy --role-name kirocrew-on-host --policy-arn "
                      "arn:aws:iam::123456789012:policy/kirocrew-on-single-install", done.stderr)
        self.assertNotIn("--policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", done.stderr)   # SSM stays
        self.assertIn("bedrock-invoke", done.stderr)
        recorded = self.recorded_role()
        self.assertEqual((recorded["state"], recorded["role"], recorded["allowed"]),
                         ("writes", "kirocrew-on-host", ["cloudformation:CreateStack", "ec2:AllocateAddress"]))
        self.assertIn("aws iam detach-role-policy --role-name kirocrew-on-host --policy-arn "
                      "arn:aws:iam::123456789012:policy/kirocrew-on-single-install", recorded["commands"])

    def test_a_check_it_cannot_make_says_so(self):
        self.state.update(simulate_denied=True)
        done = self.run_script("check-role")
        self.assertEqual(done.returncode, 11)
        self.assertIn("iam:SimulatePrincipalPolicy", done.stderr)
        self.assertEqual(self.recorded_role()["state"], "unchecked")

    def test_no_role_at_all_is_reported(self):
        done = self.run_script("check-role", EC2_ROLE="")
        self.assertEqual(done.returncode, 12)
        self.assertIn("bedrock:InvokeModel", done.stderr)
        self.assertEqual(self.recorded_role()["state"], "none")


class AccessTests(unittest.TestCase):
    """Review 2026-10-28: without CloudFront there is no origin secret, so the API must not ask for one — and then it
    listens on loopback only (the SSM port forward reaches it there); with CloudFront it listens for it and checks it."""
    def apply(self, cf):
        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "platform.toml"
            shutil.copy(ROOT / "deploy/config/platform.toml.example", toml)
            done = subprocess.run(["bash", "-c", f'. "{ROOT}/deploy/install/single-lib.sh"; PYTHON=python3; '
                                   f'single_node_access "{toml}" "{cf}"; single_node_access "{toml}" "{cf}"'],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            import tomllib
            return tomllib.loads(toml.read_text())

    def test_without_cloudfront_loopback_only_and_no_secret_file(self):
        settings = self.apply("")
        self.assertEqual(settings["api_host"], "127.0.0.1")
        self.assertNotIn("origin_secret_file", settings)

    def test_with_cloudfront_the_secret_is_checked(self):
        settings = self.apply("d1console.cloudfront.net")
        self.assertEqual((settings["api_host"], settings["origin_secret_file"]), ("0.0.0.0", "/etc/kirocrew-on/origin-secret"))

    def test_al2023_sh_applies_it_before_the_api_starts(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        call = text.index('single_node_access /etc/kirocrew-on/platform.toml "${CF_DOMAIN:-}"')
        self.assertLess(text.index("install -m 0640 -o root -g octopus \"$TARGET/deploy/config/platform.toml.example\""), call)
        self.assertLess(call, text.index("systemctl restart octopus-api"))


class InstallerTests(unittest.TestCase):
    def test_install_sh_makes_the_stack_unless_told_not_to_and_checks_the_role_after(self):
        text = (ROOT / "deploy/install/install.sh").read_text()
        self.assertIn("--no-cloudfront", text)
        self.assertIn('bash "$STAGE/deploy/install/single-stack.sh" up', text)
        self.assertIn('bash "$STAGE/deploy/install/single-stack.sh" check-role', text)
        self.assertLess(text.index("single-stack.sh\" up"), text.index("bash \"$STAGE/deploy/install/al2023.sh\""))

    def test_the_install_policy_lets_a_failed_stack_roll_back_and_cannot_make_an_administrator(self):
        policy = json.loads((ROOT / "deploy/aws/kirocrew-on-single-install-policy.json").read_text())
        actions = {a for statement in policy["Statement"] for a in statement["Action"]}
        for action in ("cloudfront:DeleteDistribution", "ec2:ReleaseAddress", "ec2:DisassociateAddress",
                       "ec2:RevokeSecurityGroupIngress", "iam:DeleteRole", "iam:DetachRolePolicy", "dlm:DeleteLifecyclePolicy"):
            self.assertIn(action, actions)   # a rollback deletes what the failed create made
        for statement in policy["Statement"]:
            if {"iam:AttachRolePolicy", "iam:DetachRolePolicy"} & set(statement["Action"]):
                self.assertEqual(statement["Condition"], {"ArnEquals": {"iam:PolicyARN": [
                    "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"]}})
            if "iam:PassRole" in statement["Action"]:
                self.assertEqual(statement["Condition"], {"StringEquals": {"iam:PassedToService": "dlm.amazonaws.com"}})
                self.assertEqual(statement["Action"], ["iam:PassRole"])
            self.assertNotIn("iam:PutRolePolicy", statement["Action"])

    def test_the_install_policy_lists_what_single_stack_needs(self):
        policy = json.loads((ROOT / "deploy/aws/kirocrew-on-single-install-policy.json").read_text())
        actions = {a for statement in policy["Statement"] for a in statement["Action"]}
        for action in ("cloudformation:CreateChangeSet", "cloudfront:CreateDistribution", "ec2:AuthorizeSecurityGroupIngress",
                       "ec2:AllocateAddress", "ec2:AssociateAddress", "ec2:CreateTags", "dlm:CreateLifecyclePolicy",
                       "iam:CreateRole", "iam:PassRole", "iam:SimulatePrincipalPolicy"):
            self.assertIn(action, actions)

    def test_the_origin_secret_made_on_the_host_is_handed_to_the_platform_user(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn("elif [ -s /etc/kirocrew-on/origin-secret ]; then", text)


if __name__ == "__main__":
    unittest.main()
