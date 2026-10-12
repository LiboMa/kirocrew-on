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
        # A single prefix-list reference covers ApiPort..NativePort: each reference counts as the list's maximum entries
        # against the rule quota, and a second group on the host would make CloudFormation consider replacing it.
        group = of_type(self.r, "AWS::EC2::SecurityGroup")
        self.assertEqual(len(group), 1)
        ingress = group[0]["Properties"]["SecurityGroupIngress"]
        self.assertEqual(len(ingress), 1)
        self.assertEqual((ingress[0]["FromPort"], ingress[0]["ToPort"]), ({"Ref": "ApiPort"}, {"Ref": "NativePort"}))
        self.assertEqual(ingress[0]["SourcePrefixListId"], {"Ref": "CloudFrontPrefixListId"})
        self.assertNotIn("CidrIp", ingress[0])
        self.assertEqual(self.t["Parameters"]["NativePort"]["Default"], self.t["Parameters"]["ApiPort"]["Default"] + 1)

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
        # Observed on EC2: without the stage step the package has no native dashboard ("Dashboard HTML not found").
        self.assertLess(text.index("npm run build)"), text.index("-m kiro_crew.frontend stage ."))
        self.assertLess(text.index("-m kiro_crew.frontend stage ."), text.index('-m pip install -q "$SRC"'))
        self.assertIn("DASHBOARD_CHECK=", text)

    def test_contract_check_runs_after_templates_are_seeded(self):
        # Observed on EC2: a fresh KIRO_HOME has no poc-* templates until the composition seeds them, so the
        # coordinator control-plane mount was empty. --check seeds and verifies routing before the native tests.
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn("-m composition.gateway --config", text)
        self.assertLess(text.index("-m composition.gateway --config"), text.index("-m unittest discover"))

    def test_native_proxy_checks_the_origin_secret_and_keeps_websockets(self):
        conf = (ROOT / "deploy/nginx/kirocrew-native.conf").read_text()
        self.assertIn("listen __NATIVE_PORT__", conf)
        self.assertIn('if ($http_x_origin_verify != "__ORIGIN_SECRET__") {', conf)
        self.assertNotIn("map $http_x_origin_verify", conf)
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

    def test_worker_host_mode_skips_the_platform_and_fronts_the_worker_with_tls(self):
        # WORKER_HOST=1 installs KiroCrew, the tools and one dedicated boundary; the platform is installed elsewhere.
        lines = (ROOT / "deploy/install/al2023.sh").read_text().splitlines()
        inside, regions = False, 0
        for number, line in enumerate(lines, 1):
            if line.strip() == "# platform:begin":
                self.assertEqual(lines[number - 2].strip(), 'if [ -z "$WORKER_HOST" ]; then', f"line {number}")
                inside, regions = True, regions + 1
            elif line.strip() == "# platform:end":
                inside = False
            elif not inside and not line.lstrip().startswith("#"):
                code = line.split(" # ")[0]   # trailing comments may name platform files
                for platform_only in ("octopus-api", "bootstrap-worker", "ADMIN_FILE", "ORIGIN_SECRET_PARAM",
                                      "platform.toml", "worker-ca.key"):
                    self.assertNotIn(platform_only, code, f"al2023.sh:{number} runs outside a platform-only region")
        self.assertGreaterEqual(regions, 3)
        text = "\n".join(lines)
        self.assertIn('WORKER_HOST="${WORKER_HOST:-}"', text)
        self.assertIn('toml_set "$BOUNDARY_TOML" worker_isolation \'"dedicated"\'', text)
        self.assertIn("deploy/nginx/kirocrew-worker.conf", text)
        self.assertIn("openssl req -x509", text)
        self.assertIn("chmod 0600 /etc/kirocrew-on/worker-ca.key", text)
        self.assertIn('toml_set /etc/kirocrew-on/platform.toml worker_ca_file \'"/etc/kirocrew-on/worker-ca.pem"\'', text)
        # Written only after the release switch: the old release rejects the unknown key if the contract check fails.
        self.assertGreater(text.index("toml_set /etc/kirocrew-on/platform.toml worker_ca_file"),
                           text.index('ln -sfn "$TARGET" /opt/kirocrew-on/current'))
        nginx = (ROOT / "deploy/nginx/kirocrew-worker.conf").read_text()
        for part in ("listen __WORKER_TLS_PORT__ ssl;", "ssl_certificate /etc/kirocrew-on/worker-tls.crt;",
                     "ssl_certificate_key /etc/kirocrew-on/worker-tls.key;", "proxy_pass http://127.0.0.1:__WORKER_PORT__;"):
            self.assertIn(part, nginx)

    def test_worker_host_template_admits_only_the_platform(self):
        worker = yaml.load((ROOT / "deploy/aws/kirocrew-on-worker.yaml").read_text(), Loader=CfnLoader)
        resources = worker["Resources"]
        ingress = resources["WorkerSecurityGroup"]["Properties"]["SecurityGroupIngress"]
        self.assertEqual(len(ingress), 1)
        self.assertEqual(ingress[0]["SourceSecurityGroupId"], {"Ref": "PlatformSecurityGroupId"})
        self.assertEqual((ingress[0]["FromPort"], ingress[0]["ToPort"]), ({"Ref": "WorkerPort"}, {"Ref": "WorkerPort"}))
        host = resources["WorkerHost"]["Properties"]
        self.assertEqual(host["MetadataOptions"]["HttpTokens"], "required")
        ebs = host["BlockDeviceMappings"][0]["Ebs"]
        self.assertTrue(ebs["Encrypted"])
        self.assertFalse(ebs["DeleteOnTermination"])
        self.assertIn({"Key": "Backup", "Value": {"Ref": "BackupTag"}}, host["Tags"])
        role = resources["WorkerRole"]["Properties"]
        self.assertIn("arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", role["ManagedPolicyArns"])
        statements = role["Policies"][0]["PolicyDocument"]["Statement"]
        actions = {action for statement in statements for action in statement["Action"]}
        self.assertNotIn("ssm:GetParameter", actions)       # a Worker host reads no platform secret
        self.assertIn("s3:GetObject", actions)
        self.assertEqual(set(worker["Outputs"]), {"InstanceId", "PrivateIp"})

    def test_platform_template_exports_its_network_without_touching_the_host_security_group(self):
        outputs = template()["Outputs"]
        for key in ("VpcId", "SubnetId", "HostSecurityGroupId", "ImageId"):
            self.assertIn(key, outputs)
        head = subprocess.run(["git", "show", "HEAD:deploy/aws/kirocrew-on-ec2.yaml"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout
        self.assertEqual(template()["Resources"]["HostSecurityGroup"],
                         yaml.load(head, Loader=CfnLoader)["Resources"]["HostSecurityGroup"])

    def test_worker_host_commands(self):
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        deploy = lambda *args: subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env,
                                              capture_output=True, text=True)
        up = deploy("worker-host", "up", "w1")
        self.assertEqual(up.returncode, 0, up.stderr)
        for part in ("kirocrew-on-worker.yaml", "kirocrew-on-m1-worker-w1", "create-change-set", "PlatformSecurityGroupId",
                     "BackupTag"):
            self.assertIn(part, up.stdout + up.stderr)
        push = deploy("worker-host", "push", "w1")
        self.assertEqual(push.returncode, 0, push.stderr)
        self.assertIn("WORKER_HOST=1", push.stdout)
        self.assertIn("al2023.sh w1", push.stdout)
        tls = deploy("worker-host", "tls", "w1")
        self.assertEqual(tls.returncode, 0, tls.stderr)
        for part in ("openssl req -new", "subjectAltName=IP:", "worker-ca.key", "kirocrew-worker.conf"):
            self.assertIn(part, tls.stdout)
        register = deploy("worker-host", "register", "w1")
        self.assertEqual(register.returncode, 0, register.stderr)
        self.assertIn("bootstrap-worker --spare", register.stdout)
        self.assertIn("--worker-url https://", register.stdout)
        login = deploy("kiro-login", "--host", "w1")
        self.assertEqual(login.returncode, 0, login.stderr)
        self.assertIn("crew-w1", login.stdout + login.stderr)
        self.assertIn("kirocrew-on-m1-worker-w1", login.stdout + login.stderr)
        self.assertEqual(deploy("worker-host", "bogus", "w1").returncode, 2)
        self.assertEqual(deploy("worker-host", "up", "W1;reboot").returncode, 2)

    def test_contract_check_runs_against_the_release_being_installed(self):
        # Found on the first fresh Worker host (D28): before the switch /opt/kirocrew-on/current and /opt/kirocrew/current
        # do not exist yet, and on an upgrade they still point at the old release.
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        check = text[text.index('log "原生契约检查'):text.index('log "切换到已通过检查的发布')]
        self.assertIn('-e "s#/opt/kirocrew-on/current/#$TARGET/#g" -e "s#/opt/kirocrew/current/#$KC/#g"', check)
        for line in (l for l in check.splitlines() if "runuser" in l or "composition.gateway" in l or "OCTOPUS_CONFIG" in l):
            self.assertNotIn("$BOUNDARY_TOML", line)
        self.assertIn('--config "$CHECK_TOML" --check', check)
        self.assertIn('OCTOPUS_CONFIG="$CHECK_TOML"', check)
        self.assertIn('rm -f "$CHECK_TOML"', check)

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
        self.assertEqual(platform.worker_ca_file, Path("/etc/kirocrew-on/worker-ca.pem"))
        boundary = load(ROOT / "deploy/config/boundary.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(boundary.worker_data_dir, Path("/var/lib/octopus/boundaries/personal/worker"))
        self.assertEqual(boundary.approval_mode, "auto")
        self.assertEqual(boundary.crew_entry_ttl, "1h")
        self.assertEqual(set(boundary.tools), {"kiro_cli", "claude", "codex", "opencode"})


if __name__ == "__main__":
    unittest.main()
