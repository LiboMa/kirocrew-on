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
        self.assertEqual(p["ImageId"]["Type"], "AWS::EC2::Image::Id")  # pinned; see tests/test_deploy_safety.py

    def test_host_is_only_reachable_from_cloudfront_on_the_api_and_native_ports(self):
        group = of_type(self.r, "AWS::EC2::SecurityGroup")[0]["Properties"]
        ingress = group["SecurityGroupIngress"]
        self.assertEqual({json.dumps(rule["FromPort"]) for rule in ingress},
                         {json.dumps({"Ref": "ApiPort"}), json.dumps({"Ref": "NativePort"})})
        for rule in ingress:
            self.assertEqual(rule["SourcePrefixListId"], {"Ref": "CloudFrontPrefixListId"})
            self.assertEqual(rule["FromPort"], rule["ToPort"])
            self.assertNotIn("CidrIp", rule)
            self.assertNotIn("CidrIpv6", rule)

    def test_instance_hardening(self):
        host = of_type(self.r, "AWS::EC2::Instance")[0]["Properties"]
        self.assertTrue(host["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(host["MetadataOptions"]["HttpTokens"], "required")
        self.assertNotIn("KeyName", host)
        self.assertTrue(host["PropagateTagsToVolumeOnCreation"])
        self.assertTrue(of_type(self.r, "AWS::EC2::EIP"), "stable origin name across stop/start")

    def distribution(self, port):
        return next(d["Properties"]["DistributionConfig"] for d in of_type(self.r, "AWS::CloudFront::Distribution")
                    if d["Properties"]["DistributionConfig"]["Origins"][0]["CustomOriginConfig"]["HTTPPort"] == {"Ref": port})

    def test_native_chat_has_its_own_distribution_behind_the_origin_secret(self):
        # The native KiroCrew dashboard owns /, /api and /api/ws, so it cannot share the console's domain.
        self.assertEqual(len(of_type(self.r, "AWS::CloudFront::Distribution")), 2)
        native, api = self.distribution("NativePort"), self.distribution("ApiPort")
        self.assertEqual(native["Origins"][0]["DomainName"], api["Origins"][0]["DomainName"])
        self.assertEqual(native["Origins"][0]["OriginCustomHeaders"],
                         [{"HeaderName": "X-Origin-Verify", "HeaderValue": {"Ref": "OriginSecret"}}])
        behavior = native["DefaultCacheBehavior"]
        self.assertEqual(behavior["CachePolicyId"], CACHING_DISABLED)
        self.assertEqual(behavior["OriginRequestPolicyId"], ALL_VIEWER_AND_CLOUDFRONT_HEADERS)
        self.assertEqual(behavior["ViewerProtocolPolicy"], "redirect-to-https")
        self.assertFalse(behavior["Compress"])
        self.assertIn("NativeUrl", self.t["Outputs"])
        self.assertIn("NativeDomain", self.t["Outputs"])

    def test_cloudfront_forwards_to_the_origin_with_secret_and_no_cache(self):
        config = self.distribution("ApiPort")
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

    def test_native_proxy_checks_the_origin_secret_and_keeps_websockets(self):
        conf = (ROOT / "deploy/nginx/kirocrew-native.conf").read_text()
        self.assertIn("listen __NATIVE_PORT__", conf)
        self.assertIn('"__ORIGIN_SECRET__" 1;', conf)
        self.assertIn("return 403;", conf)
        self.assertIn("proxy_pass http://127.0.0.1:__GATEWAY_PORT__;", conf)
        for line in ("proxy_set_header Upgrade $http_upgrade;", "proxy_set_header Connection $connection_upgrade;",
                     "proxy_set_header X-Forwarded-Proto https;", 'proxy_set_header X-Origin-Verify "";',
                     "proxy_set_header Host $host;", "proxy_buffering off;"):
            self.assertIn(line, conf)
        # The entry link carries the native login token in its query string; access logs record the path only.
        self.assertIn("kirocrew-native.access.log kirocrew_native;", conf)
        self.assertNotIn("$request ", conf.replace("$request_method", "").replace("$request_time", ""))
        self.assertNotIn("$args", conf)
        self.assertNotIn("$request_uri", conf)

    def test_installer_publishes_the_native_entry_through_nginx(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn("deploy/nginx/kirocrew-native.conf", text)
        self.assertIn("/etc/nginx/conf.d/kirocrew-native.conf", text)
        self.assertIn("nginx -t", text)
        self.assertIn(r'crew_url "\"https://$NATIVE_DOMAIN\""', text)
        self.assertNotIn("$(cat /etc/kirocrew-on/origin-secret)", text, "the secret must not reach a command line")
        deploy = (ROOT / "deploy/aws/deploy.sh").read_text()
        self.assertIn("NATIVE_DOMAIN=", deploy)
        self.assertIn("NativeDomain", deploy)

    def test_react_console_is_built_on_the_host_before_the_release_is_marked_installed(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        build = text.index('(cd "$TARGET/web" && npm ci --no-audit --no-fund && npm run build)')
        self.assertLess(build, text.index('touch "$TARGET/.installed"'))
        regress = (ROOT / "tools/regress.sh").read_text()
        self.assertIn("npm --prefix web test", regress)
        self.assertIn("npm --prefix web run build", regress)
        self.assertTrue((ROOT / "web/package-lock.json").is_file(), "the console build is pinned by its lockfile")

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
        self.assertIn("cloudformation create-change-set", result.stdout)
        self.assertNotIn("x" * 48, result.stdout + result.stderr)

    def test_kiro_login_passes_identity_center_settings_under_a_pseudo_terminal(self):
        # Observed on EC2: kiro-cli still prompts with every flag set and fails without a terminal, so the device flow
        # runs under script(1) through SSM Run Command instead of an interactive Session Manager session.
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        result = subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), "kiro-login"], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = result.stdout + result.stderr
        for part in ("--license pro", "--identity-provider https://d-9067e1d68a.awsapps.com/start",
                     "--region us-east-1", "--use-device-flow", "sudo -iu crew-personal bash -c", "script -q -f -c", "< <(",
                     "kiro-login-",
                     "ssm send-command", "AWS-RunShellScript"):
            self.assertIn(part, printed)
        self.assertNotIn("AWS-StartInteractiveCommand", printed)

    def test_space_runs_a_worker_project_on_the_host_and_forwards_it_to_localhost(self):
        run = "run-" + "0123456789abcdef" * 2
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        space = lambda *args: subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env,
                                             capture_output=True, text=True)
        result = space("space", run)
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = result.stdout + result.stderr
        for part in (f"DIR=/var/lib/octopus/boundaries/personal/worker/{run}/workspace/project",
                     f"UNIT=kirocrew-space-{run}", "CREW=crew-personal", "NODE_BIN=/opt/kirocrew-node/bin",
                     '"$NODE_BIN/npm" run dev', "systemd-run",
                     "--strictPort", "ssm start-session", "AWS-StartPortForwardingSession",
                     '"portNumber":["5173"]', '"localPortNumber":["15173"]'):
            self.assertIn(part, printed)
        stop = space("space", run, "stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn(f"systemctl stop kirocrew-space-{run}", stop.stdout)
        self.assertNotIn("start-session", stop.stdout)
        # the run ID becomes part of a root shell command on the host, so anything but run-<32 hex> is refused
        self.assertEqual(space("space", "run-1; reboot").returncode, 2)
        self.assertEqual(space("space").returncode, 2)

    def test_config_examples_load(self):
        from octopus.config import load
        platform = load(ROOT / "deploy/config/platform.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(platform.data_dir, Path("/var/lib/octopus/platform"))
        self.assertEqual(platform.api_host, "0.0.0.0")
        self.assertEqual(platform.origin_secret_file, Path("/etc/kirocrew-on/origin-secret"))
        boundary = load(ROOT / "deploy/config/boundary.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(boundary.worker_data_dir, Path("/var/lib/octopus/boundaries/personal/worker"))
        self.assertEqual(boundary.approval_mode, "auto")
        self.assertEqual(boundary.crew_entry_ttl, "1h")
        self.assertEqual(set(boundary.tools), {"kiro_cli", "claude", "codex", "opencode"})


if __name__ == "__main__":
    unittest.main()
