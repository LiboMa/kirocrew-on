"""Deployment assets: CloudFront → SG (CloudFront prefix list) → EC2, pinned sources, no macOS assumptions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ALL_VIEWER_AND_CLOUDFRONT_HEADERS = "33f36d7e-f396-46d9-90e0-52428a34d9dc"


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: {suffix: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))})


def template():
    return yaml.load((ROOT / "deploy/aws/kirocrew-on-ec2.yaml").read_text(), Loader=CfnLoader)


def of_type(resources, kind):
    return [r for r in resources.values() if r["Type"] == kind]


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.t = template()
        self.r = self.t["Resources"]

    def test_capacity_and_placement_parameters_have_no_unverified_defaults(self):
        p = self.t["Parameters"]
        for name in ("InstanceType", "VolumeSizeGiB", "AvailabilityZone", "CloudFrontPrefixListId", "OriginSecret"):
            self.assertNotIn("Default", p[name], name)
        self.assertTrue(p["OriginSecret"]["NoEcho"])
        self.assertEqual(p["ImageId"]["Default"], "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")

    def test_host_is_only_reachable_from_cloudfront_on_the_api_port(self):
        group = of_type(self.r, "AWS::EC2::SecurityGroup")[0]["Properties"]
        ingress = group["SecurityGroupIngress"]
        self.assertEqual(len(ingress), 1)
        self.assertEqual(ingress[0]["SourcePrefixListId"], {"Ref": "CloudFrontPrefixListId"})
        self.assertEqual(ingress[0]["FromPort"], {"Ref": "ApiPort"})
        self.assertNotIn("CidrIp", ingress[0])

    def test_instance_hardening(self):
        host = of_type(self.r, "AWS::EC2::Instance")[0]["Properties"]
        self.assertTrue(host["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(host["MetadataOptions"]["HttpTokens"], "required")
        self.assertNotIn("KeyName", host)
        self.assertTrue(host["PropagateTagsToVolumeOnCreation"])
        self.assertTrue(of_type(self.r, "AWS::EC2::EIP"), "stable origin name across stop/start")

    def test_cloudfront_forwards_to_the_origin_with_secret_and_no_cache(self):
        config = of_type(self.r, "AWS::CloudFront::Distribution")[0]["Properties"]["DistributionConfig"]
        origin = config["Origins"][0]
        self.assertEqual(origin["CustomOriginConfig"]["OriginProtocolPolicy"], "http-only")
        self.assertEqual(origin["OriginCustomHeaders"], [{"HeaderName": "X-Origin-Verify", "HeaderValue": {"Ref": "OriginSecret"}}])
        behavior = config["DefaultCacheBehavior"]
        self.assertEqual(behavior["ViewerProtocolPolicy"], "redirect-to-https")
        self.assertEqual(behavior["CachePolicyId"], CACHING_DISABLED)
        self.assertEqual(behavior["OriginRequestPolicyId"], ALL_VIEWER_AND_CLOUDFRONT_HEADERS)
        self.assertEqual(set(behavior["AllowedMethods"]), {"GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"})
        self.assertFalse(behavior["Compress"], "SSE must not be buffered for compression")
        self.assertTrue(config["ViewerCertificate"]["CloudFrontDefaultCertificate"])

    def test_role_grants_ssm_bedrock_and_scoped_storage(self):
        role = of_type(self.r, "AWS::IAM::Role")
        host_role = next(r for r in role if "ec2.amazonaws.com" in json.dumps(r["Properties"]["AssumeRolePolicyDocument"]))
        props = host_role["Properties"]
        self.assertIn("arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", props["ManagedPolicyArns"])
        actions = json.dumps(props["Policies"])
        # Codex's amazon-bedrock provider uses bedrock-mantle:CreateInference (observed on EC2), not bedrock:InvokeModel.
        for action in ("bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock-mantle:CreateInference",
                       "s3:GetObject", "ssm:GetParameter"):
            self.assertIn(action, actions)
        self.assertNotIn('"s3:*"', actions)

    def test_artifact_bucket_is_private_encrypted_and_retained(self):
        bucket = of_type(self.r, "AWS::S3::Bucket")[0]
        block = bucket["Properties"]["PublicAccessBlockConfiguration"]
        self.assertTrue(all(block[k] for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")))
        self.assertIn("BucketEncryption", bucket["Properties"])
        self.assertEqual(bucket["DeletionPolicy"], "Retain")

    def test_backups_and_recovery(self):
        policy = of_type(self.r, "AWS::DLM::LifecyclePolicy")[0]["Properties"]["PolicyDetails"]
        self.assertEqual(policy["Schedules"][0]["RetainRule"]["Count"], 7)
        alarm = of_type(self.r, "AWS::CloudWatch::Alarm")[0]["Properties"]
        self.assertEqual(alarm["MetricName"], "StatusCheckFailed_System")
        self.assertIn("ec2:recover", json.dumps(alarm["AlarmActions"]))

    def test_outputs(self):
        self.assertTrue({"InstanceId", "CloudFrontDomain", "ConsoleUrl", "ArtifactBucket"} <= set(self.t["Outputs"]))


class ScriptTests(unittest.TestCase):
    def test_scripts_are_valid_bash_without_mac_assumptions(self):
        for name in ("deploy/install/al2023.sh", "deploy/aws/deploy.sh"):
            script = ROOT / name
            subprocess.run(["bash", "-n", str(script)], check=True)
            text = script.read_text()
            for forbidden in ("/Applications", "/opt/homebrew", "zsh", "lsof"):
                self.assertNotIn(forbidden, text, name)
            if shutil.which("shellcheck"):
                subprocess.run(["shellcheck", "-S", "warning", str(script)], check=True)

    def test_kirocrew_and_kiro_cli_come_from_pinned_verified_sources(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn('git -C "$SRC" checkout -q "$KIROCREW_GIT_REF"', text)
        self.assertIn("desktop-release.q.us-east-1.amazonaws.com", text)
        self.assertIn("sha256sum -c -", text)
        self.assertIn("-c 'import kiro_crew'", text)
        self.assertIn('touch "$KC/.installed"', text)
        self.assertNotIn('mv "$STAGE"', text)

    def test_contract_check_runs_after_templates_are_seeded(self):
        # Observed on EC2: a fresh KIRO_HOME has no poc-* templates until the composition seeds them, so the
        # coordinator control-plane mount was empty. --check seeds and verifies routing before the native tests.
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn("-m composition.gateway --config", text)
        self.assertLess(text.index("-m composition.gateway --config"), text.index("-m unittest discover"))

    def test_versions_file_declares_every_pin(self):
        values = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                      if line and not line.startswith("#"))
        for key in ("KIROCREW_GIT_URL", "KIROCREW_GIT_REF", "KIRO_CLI_VERSION", "NODE_DIST", "CLAUDE_CODE_VERSION",
                    "CODEX_VERSION", "OPENCODE_VERSION", "PYTHON_PACKAGE"):
            self.assertIn(key, values)
        self.assertEqual(values["KIROCREW_GIT_URL"], "https://github.com/kirodotdev/KiroCrew.git")
        self.assertRegex(values["KIROCREW_GIT_REF"], r"^[0-9a-f]{40}$")

    def test_deploy_dry_run_targets_us_east_1_and_never_prints_the_secret(self):
        env = {**os.environ, "DEPLOY_DRY_RUN": "1", "ORIGIN_SECRET": "x" * 48}
        result = subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), "up"], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--region us-east-1", result.stdout)
        self.assertIn("cloudformation deploy", result.stdout)
        self.assertNotIn("x" * 48, result.stdout + result.stderr)

    def test_kiro_login_passes_identity_center_settings(self):
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        result = subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), "kiro-login"], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for part in ("--license pro", "--identity-provider https://d-9067e1d68a.awsapps.com/start",
                     "--region us-east-1", "--use-device-flow", "sudo -iu crew-personal"):
            self.assertIn(part, result.stdout)

    def test_config_examples_load(self):
        from octopus.config import load
        platform = load(ROOT / "deploy/config/platform.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(platform.data_dir, Path("/var/lib/octopus/platform"))
        self.assertEqual(platform.api_host, "0.0.0.0")
        self.assertEqual(platform.origin_secret_file, Path("/etc/kirocrew-on/origin-secret"))
        boundary = load(ROOT / "deploy/config/boundary.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(boundary.worker_data_dir, Path("/var/lib/octopus/boundaries/personal/worker"))
        self.assertEqual(boundary.approval_mode, "auto")
        self.assertEqual(set(boundary.tools), {"kiro_cli", "claude", "codex", "opencode"})


if __name__ == "__main__":
    unittest.main()
