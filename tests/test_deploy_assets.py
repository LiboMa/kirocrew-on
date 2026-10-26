"""Deployment assets: CloudFront → SG (CloudFront prefix list) → EC2, pinned sources, no macOS assumptions."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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
        # NativeRelayEnd: the relay ports for the Worker hosts' native entries follow NativePort (D28, design §4.0.1).
        self.assertEqual((ingress[0]["FromPort"], ingress[0]["ToPort"]), ({"Ref": "ApiPort"}, {"Ref": "NativeRelayEnd"}))
        self.assertEqual(self.t["Parameters"]["NativeRelayEnd"]["Default"], self.t["Parameters"]["NativePort"]["Default"] + 10)
        self.assertEqual(ingress[0]["SourcePrefixListId"], {"Ref": "CloudFrontPrefixListId"})
        self.assertNotIn("CidrIp", ingress[0])
        self.assertEqual(self.t["Parameters"]["NativePort"]["Default"], self.t["Parameters"]["ApiPort"]["Default"] + 1)

    def distribution(self, port):
        return next(d["Properties"]["DistributionConfig"] for d in of_type(self.r, "AWS::CloudFront::Distribution")
                    if d["Properties"]["DistributionConfig"]["Origins"][0]["CustomOriginConfig"]["HTTPPort"] == {"Ref": port})

    def test_native_chat_has_its_own_distribution_behind_the_origin_secret(self):
        # The native KiroCrew dashboard owns /, /api and /api/ws, so it cannot share the console's domain.
        self.assertEqual(len(of_type(self.r, "AWS::CloudFront::Distribution")), 3)   # console, native chat, preview
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

    def test_the_console_can_have_its_own_domain(self):
        # Optional: with ConsoleDomain the console distribution answers on it too (its cloudfront.net name stays), with
        # an issued us-east-1 certificate and alias records in the zone deploy.sh found (stack.env CONSOLE_DOMAIN).
        p = self.t["Parameters"]
        for name in ("ConsoleDomain", "ConsoleCertificateArn", "ConsoleHostedZoneId"):
            self.assertEqual(p[name]["Default"], "", name)
        config = self.distribution("ApiPort")
        self.assertEqual(config["Aliases"], {"If": ["HasConsoleDomain", [{"Ref": "ConsoleDomain"}], {"Ref": "AWS::NoValue"}]})
        condition, custom, default = config["ViewerCertificate"]["If"]
        self.assertEqual(condition, "HasConsoleDomain")
        self.assertEqual(custom, {"AcmCertificateArn": {"Ref": "ConsoleCertificateArn"}, "SslSupportMethod": "sni-only",
                                  "MinimumProtocolVersion": "TLSv1.2_2021"})
        self.assertEqual(default, {"CloudFrontDefaultCertificate": True})
        records = [r for r in of_type(self.r, "AWS::Route53::RecordSet") if r["Condition"] == "HasConsoleRecord"]
        self.assertEqual(sorted(r["Properties"]["Type"] for r in records), ["A", "AAAA"])
        for record in records:
            self.assertEqual(record["Condition"], "HasConsoleRecord")
            self.assertEqual(record["Properties"]["HostedZoneId"], {"Ref": "ConsoleHostedZoneId"})
            self.assertEqual(record["Properties"]["Name"], {"Ref": "ConsoleDomain"})
            self.assertEqual(record["Properties"]["AliasTarget"], {"HostedZoneId": "Z2FDTNDATAQYW2",   # CloudFront's zone
                                                                    "DNSName": {"GetAtt": "Distribution.DomainName"}})
        native = self.distribution("NativePort")
        self.assertNotIn("Aliases", native)
        self.assertTrue(native["ViewerCertificate"]["CloudFrontDefaultCertificate"])
        self.assertIn("ConsoleDomain", self.t["Outputs"])
        self.assertEqual(self.t["Outputs"]["ConsoleUrl"]["Value"]["If"][0], "HasConsoleDomain")

    def test_each_session_can_have_its_own_preview_origin(self):
        # D36 phase 3: *.<PreviewSessionDomain> on the preview distribution (its cloudfront.net name stays), one wildcard
        # certificate, wildcard alias records; deploy.sh finds the zone and finds or requests the certificate.
        p = self.t["Parameters"]
        for name in ("PreviewSessionDomain", "PreviewCertificateArn", "PreviewHostedZoneId"):
            self.assertEqual(p[name]["Default"], "", name)
        wildcard = {"Sub": "*.${PreviewSessionDomain}"}
        preview = self.distribution("PreviewPort")
        self.assertEqual(preview["Aliases"], {"If": ["HasPreviewSessionDomain", [wildcard], {"Ref": "AWS::NoValue"}]})
        condition, custom, default = preview["ViewerCertificate"]["If"]
        self.assertEqual((condition, default), ("HasPreviewSessionDomain", {"CloudFrontDefaultCertificate": True}))
        self.assertEqual(custom, {"AcmCertificateArn": {"Ref": "PreviewCertificateArn"}, "SslSupportMethod": "sni-only",
                                  "MinimumProtocolVersion": "TLSv1.2_2021"})
        records = [r for r in of_type(self.r, "AWS::Route53::RecordSet") if r["Condition"] == "HasPreviewSessionRecord"]
        self.assertEqual(sorted(r["Properties"]["Type"] for r in records), ["A", "AAAA"])
        for record in records:
            self.assertEqual(record["Properties"]["HostedZoneId"], {"Ref": "PreviewHostedZoneId"})
            self.assertEqual(record["Properties"]["Name"], wildcard)
            self.assertEqual(record["Properties"]["AliasTarget"], {"HostedZoneId": "Z2FDTNDATAQYW2",
                                                                    "DNSName": {"GetAtt": "PreviewDistribution.DomainName"}})
        output = self.t["Outputs"]["PreviewSessionDomain"]
        self.assertEqual((output["Condition"], output["Value"]), ("HasPreviewSessionDomain", {"Ref": "PreviewSessionDomain"}))

    def test_the_remote_preview_has_its_own_distribution_inside_the_existing_rule(self):
        # D36 §5.2. Its port is the last of the one prefix-list rule, so the rule (quota-weighted) is not touched.
        p = self.t["Parameters"]
        self.assertEqual(p["PreviewPort"]["Default"], p["NativeRelayEnd"]["Default"])
        preview, native = self.distribution("PreviewPort"), self.distribution("NativePort")
        self.assertEqual(preview["Origins"][0]["DomainName"], native["Origins"][0]["DomainName"])
        self.assertEqual(preview["Origins"][0]["OriginCustomHeaders"],
                         [{"HeaderName": "X-Origin-Verify", "HeaderValue": {"Ref": "OriginSecret"}}])
        behavior = preview["DefaultCacheBehavior"]
        self.assertEqual((behavior["CachePolicyId"], behavior["OriginRequestPolicyId"], behavior["ViewerProtocolPolicy"]),
                         (CACHING_DISABLED, ALL_VIEWER_AND_CLOUDFRONT_HEADERS, "redirect-to-https"))
        self.assertFalse(behavior["Compress"])
        self.assertEqual(preview["ViewerCertificate"]["If"][2], {"CloudFrontDefaultCertificate": True})
        ingress = of_type(self.r, "AWS::EC2::SecurityGroup")[0]["Properties"]["SecurityGroupIngress"]
        self.assertEqual((len(ingress), ingress[0]["ToPort"]), (1, {"Ref": "NativeRelayEnd"}))
        self.assertTrue({"PreviewDomain", "PreviewUrl"} <= set(self.t["Outputs"]))

    def test_push_and_the_installer_admit_the_console_domain(self):
        deploy = (ROOT / "deploy/aws/deploy.sh").read_text()
        installer = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn('console="$(output ConsoleDomain)"', deploy)
        self.assertIn("CONSOLE_DOMAIN={console}", deploy)
        self.assertIn('merge_hosts /etc/kirocrew-on/platform.toml "$CF_DOMAIN" ${CONSOLE_DOMAIN:+"$CONSOLE_DOMAIN"} localhost:18918',
                      installer)

    def test_the_installer_gives_agents_a_headless_browser(self):
        # EC2 2026-10-19 (run-48f02d32): Playwright's Chromium lacked 16 system libraries (exit 127), so reviews and the
        # D50 frontend checks could not open the page; every host (platform and Worker hosts) installs them on each push
        installer = (ROOT / "deploy/install/al2023.sh").read_text()
        for package in ("libX11", "libXcomposite", "libXdamage", "libXext", "libXfixes", "libXrandr", "alsa-lib", "atk",
                        "at-spi2-atk", "at-spi2-core", "cairo", "cups-libs", "mesa-libgbm", "pango", "libxcb", "libxkbcommon"):
            self.assertRegex(installer, r"dnf install -y -q [^\n]*(?<![\w-])" + re.escape(package) + r"(?![\w-])", package)

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
    def test_gateway_restarts_only_when_what_it_loads_changed(self):
        # D49: the restart logic is boundary_restart in deploy/install/boundary-lib.sh, run for every boundary on the host;
        # tests/test_boundaries.py runs it against a fake host (fingerprint, drop-ins, env, not running).
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn('boundary_restart "$BOUNDARY" "$KC"', text)
        library = (ROOT / "deploy/install/boundary-lib.sh").read_text()
        block = library[library.index("# gateway:begin"):library.index("# gateway:end")]
        for part in ('readlink -f "$KC_CURRENT"', '"$toml" "$SYSTEMD/octopus-gateway@.service"',
                     '"$SYSTEMD/octopus-gateway@$name.service.d/"*.conf', '"$ETC/boundary-$name.env"'):
            self.assertIn(part, block)

    def test_the_admin_password_never_reaches_a_command_line(self):   # other crew-* users on the host can read /proc/*/cmdline
        for name in ("deploy/install/al2023.sh", "deploy/install/boundary.sh"):
            joined = (ROOT / name).read_text().replace("\\\n", " ")
            with self.subTest(name):
                self.assertIsNone(re.search(r"(\benv|--)\s[^;|&\n]*OCTOPUS_ADMIN_PASSWORD=", joined))

    def test_each_install_prunes_old_releases_first(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        prune = text.index('prune_releases /opt/kirocrew-on/releases "$(readlink -f /opt/kirocrew-on/current')
        self.assertLess(prune, text.index('cp -a "$SOURCE" "$TARGET"'))   # room for the new one first
        self.assertIn('find /opt/kirocrew-on/incoming -mindepth 1 -maxdepth 1 -mmin +1440 ! -name "*$RELEASE*"', text)

    def test_scripts_are_valid_bash_without_mac_assumptions(self):
        for name in ("deploy/install/al2023.sh", "deploy/install/boundary-lib.sh", "deploy/install/boundary.sh", "deploy/aws/deploy.sh"):
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
        text = (ROOT / "deploy/install/boundary-lib.sh").read_text()   # D49: boundary_check, for every boundary
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

    def test_the_native_entry_login_ends_with_its_access_cookie(self):
        # K03 (2026-10-26, EC2): the Gateway gives the native entry a 30-day refresh chain (mc_refresh_<port>, Path=/api/auth)
        # beside its crew_entry_ttl access cookie. Every public relay answers the refresh as a revoked chain does.
        guard = (ROOT / "deploy/nginx/kirocrew-native-guard.conf").read_text()
        self.assertIn("location = /api/auth/refresh {", guard)
        # 2026-10-26: as a revoked chain the native page navigated to / after every load (its scheduler refreshes one hour
        # before the access cookie ends — at once with a 1 h cookie): every 7–8 s a full reload. As a missing refresh
        # cookie it backs off instead (1, 4, 16, 60 min) and shows its session-expired banner when the cookie ends.
        self.assertIn("""return 401 '{"error":"no_refresh_cookie"}';""", guard)
        self.assertNotIn("refresh_chain_revoked", guard.split("location")[1])
        self.assertIn("default_type application/json;", guard)
        for name in ("kirocrew-native.conf", "kirocrew-relay.conf", "kirocrew-boundary.conf"):
            conf = (ROOT / "deploy/nginx" / name).read_text()
            include = conf.index("include /etc/nginx/kirocrew-native-guard.conf;")
            self.assertLess(conf.index("server {"), include, name)
            self.assertLess(include, conf.index("location / {"), name)
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        install = text.index('install -m 0644 "$TARGET/deploy/nginx/kirocrew-native-guard.conf" /etc/nginx/kirocrew-native-guard.conf')
        self.assertLess(install, text.index("/etc/nginx/conf.d/kirocrew-native.conf.new"))
        # relays rendered before the guard existed get it too, kept as they were when nginx refuses the change
        self.assertIn("for relay in /etc/nginx/conf.d/kirocrew-relay-*.conf", text)
        self.assertIn("kirocrew-native-guard.conf", text[text.index("for relay in /etc/nginx/conf.d/kirocrew-relay-*.conf"):])
        self.assertIn("nginx -t -q || { restore_relays; nginx -t -q; }", text)

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

    def test_the_preview_proxy_asks_the_api_before_every_request(self):
        conf = (ROOT / "deploy/nginx/kirocrew-preview.conf").read_text()
        for part in ("listen __PREVIEW_PORT__;", 'if ($http_x_origin_verify != "__ORIGIN_SECRET__")', "location ^~ /_kirocrew/",
                     "auth_request /_kirocrew_auth;", "internal;", "proxy_pass http://127.0.0.1:__API_PORT__/v1/preview-auth;",
                     "proxy_method GET;", "proxy_pass_request_body off;",
                     "auth_request_set $kc_upstream $upstream_http_x_preview_upstream;", "proxy_pass $kc_upstream;",
                     "error_page 401 403 = @kirocrew_denied;", "proxy_ssl_verify on;",
                     "proxy_ssl_trusted_certificate /etc/kirocrew-on/worker-ca.pem;", "proxy_ssl_name $kc_name;",
                     "proxy_set_header Host localhost:$kc_port;", "proxy_set_header X-Preview-Verify $kc_verify;",
                     "proxy_set_header Upgrade $http_upgrade;", 'proxy_set_header X-Origin-Verify "";'):
            self.assertIn(part, conf)
        log_format = conf[conf.index("log_format"):conf.index(";", conf.index("log_format"))]
        self.assertIn("$kirocrew_preview_logged", log_format)
        self.assertNotIn("$request_uri", log_format)   # opening links carry a ticket: the path only
        # ...and the path without an opening ticket or a public share's token (D75 review)
        self.assertIn("map $uri $kirocrew_preview_logged {", conf)
        self.assertIn('"~^(?<kc_head>/_kirocrew/(s|open|enter)/)[^/]+(?<kc_tail>.*)$" "${kc_head}-${kc_tail}";', conf)
        self.assertEqual(set(re.findall(r"__[A-Z_]+__", conf)),
                         {"__PREVIEW_PORT__", "__API_PORT__", "__ORIGIN_SECRET__", "__PREVIEW_GATE__"})
        # Every request to the API proves it comes through this nginx (other processes on the host cannot).
        self.assertEqual(conf.count('proxy_set_header X-Preview-Gate "__PREVIEW_GATE__";'), 3)
        app = conf[conf.index("    location / {"):]
        for part in ("if ($http_service_worker) {", "proxy_hide_header Cache-Control;", 'add_header Cache-Control "no-cache" always;',
                     "proxy_ignore_headers X-Accel-Redirect X-Accel-Expires X-Accel-Limit-Rate X-Accel-Buffering X-Accel-Charset;",
                     'proxy_set_header X-Preview-Gate "";'):
            self.assertIn(part, app)
        # The service-worker check comes before auth_request, so no script is ever fetched for a registration.
        self.assertLess(app.index("$http_service_worker"), app.index("auth_request /_kirocrew_auth;"))

    def test_a_sessions_own_preview_host_reaches_the_api_and_keeps_cookies_to_itself(self):
        # D36 phase 3: the API learns which host was asked (the host names the Session), and an app's cookies stay on
        # its own host even when it names a parent domain (shared with other Sessions or other sites).
        conf = (ROOT / "deploy/nginx/kirocrew-preview.conf").read_text()
        self.assertEqual(conf.count("proxy_set_header X-Preview-Host $host;"), 3)
        app = conf[conf.index("    location / {"):]
        self.assertIn("proxy_cookie_domain ~.+ $host;", app)
        self.assertIn('proxy_set_header X-Preview-Host "";', app)

    def test_a_worker_host_preview_port_reaches_only_preview_ports_with_the_check_header(self):
        conf = (ROOT / "deploy/nginx/kirocrew-worker-preview.conf").read_text()
        for part in ("listen __PREVIEW_TLS_PORT__ ssl;", "ssl_certificate /etc/kirocrew-on/worker-tls.crt;",
                     'if ($http_x_preview_verify != "__PREVIEW_VERIFY__")', '"~^47[0-9]{3}$"',
                     "proxy_pass http://127.0.0.1:$kirocrew_app_port;", "proxy_set_header Host localhost:$kirocrew_app_port;",
                     'proxy_set_header X-Preview-Verify "";', "proxy_set_header Upgrade $http_upgrade;"):
            self.assertIn(part, conf)
        self.assertEqual(set(re.findall(r"__[A-Z_]+__", conf)), {"__PREVIEW_TLS_PORT__", "__PREVIEW_VERIFY__"})
        self.assertIn("proxy_ignore_headers X-Accel-Redirect X-Accel-Expires X-Accel-Limit-Rate X-Accel-Buffering X-Accel-Charset;",
                      conf)

    def installer_function(self, name):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        start = text.index(name + "() {")
        return text[start:text.index("\n}\n", start) + 3]

    def test_the_preview_configs_are_rendered_from_files_with_the_values_the_api_uses(self):
        from octopus.remote_preview import verify_value
        with tempfile.TemporaryDirectory() as directory:
            token, secret = Path(directory, "worker.token"), Path(directory, "origin-secret")
            token.write_text("t" * 40 + "\n")
            secret.write_text("s" * 40 + "\n")
            run = lambda function, *args: subprocess.run(
                ["bash", "-c", self.installer_function(function) + f'\nPYTHON={sys.executable} TARGET={ROOT} {function} "$@"',
                 function, *args], capture_output=True, text=True)
            worker = run("render_worker_preview", str(ROOT / "deploy/nginx/kirocrew-worker-preview.conf"), str(token), "8945")
            self.assertEqual(worker.returncode, 0, worker.stderr)
            self.assertIn(f'if ($http_x_preview_verify != "{verify_value("t" * 40)}")', worker.stdout)
            self.assertIn("listen 8945 ssl;", worker.stdout)
            platform = run("render_preview", str(ROOT / "deploy/nginx/kirocrew-preview.conf"), str(secret), "8929", "8918")
            self.assertEqual(platform.returncode, 0, platform.stderr)
            self.assertIn("listen 8929;", platform.stdout)
            self.assertIn("proxy_pass http://127.0.0.1:8918/v1/preview-auth;", platform.stdout)
            from octopus.remote_preview import gate_value
            self.assertIn(f'proxy_set_header X-Preview-Gate "{gate_value("s" * 40)}";', platform.stdout)
            self.assertNotRegex(worker.stdout + platform.stdout, r"__[A-Z_]+__")
            secret.write_text("short")
            self.assertNotEqual(run("render_preview", str(ROOT / "deploy/nginx/kirocrew-preview.conf"), str(secret), "8929",
                                    "8918").returncode, 0)

    def test_the_installer_turns_on_per_session_preview_origins(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertGreater(text.index('toml_set /etc/kirocrew-on/platform.toml preview_domain "\\"$PREVIEW_SESSION_DOMAIN\\""'),
                           text.index('ln -sfn "$TARGET" /opt/kirocrew-on/current'))
        self.assertIn("toml_unset /etc/kirocrew-on/platform.toml preview_domain", text)   # unset: no key an older release rejects
        deploy = (ROOT / "deploy/aws/deploy.sh").read_text()
        self.assertIn('session="$(output PreviewSessionDomain)"', deploy)
        self.assertIn("PREVIEW_SESSION_DOMAIN={session}", deploy)

    def test_the_installer_publishes_the_remote_preview(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        switch = text.index('ln -sfn "$TARGET" /opt/kirocrew-on/current')
        # Platform host: the preview server, and preview_url once the new release (which knows the key) is current.
        self.assertIn('render_preview "$TARGET/deploy/nginx/kirocrew-preview.conf" /etc/kirocrew-on/origin-secret', text)
        self.assertGreater(text.index('toml_set /etc/kirocrew-on/platform.toml preview_url "\\"https://$PREVIEW_DOMAIN\\""'),
                           switch)
        self.assertIn("rm -f /etc/nginx/conf.d/kirocrew-preview.conf", text)   # a config nginx rejects never stays
        # Worker host: its preview port and certificate name after the switch; the check header once its token exists.
        self.assertGreater(text.index('toml_set "$BOUNDARY_TOML" preview_tls_port "$WORKER_PREVIEW_TLS_PORT"'), switch)
        self.assertIn('toml_set "$BOUNDARY_TOML" preview_tls_name "\\"$BOUNDARY.worker.kirocrew-on\\""', text)
        self.assertGreater(text.index('render_worker_preview "$TARGET/deploy/nginx/kirocrew-worker-preview.conf"'),
                           text.index('boundary_restart "$BOUNDARY" "$KC"'))
        deploy = (ROOT / "deploy/aws/deploy.sh").read_text()
        self.assertIn('preview="$(output PreviewDomain)"', deploy)
        self.assertIn("PREVIEW_DOMAIN={preview} PREVIEW_PORT={preview_port}", deploy)
        self.assertIn("WORKER_PREVIEW_TLS_PORT={preview_port}", deploy)
        self.assertIn("/etc/kirocrew-on/nginx-worker-preview.conf /etc/nginx/conf.d/kirocrew-worker-preview.conf", deploy)

    def test_the_platform_keeps_a_credentials_master_key_of_its_own(self):
        # D35 §3.3: generated once on the platform host, readable by the API only, written into platform.toml after the
        # switch (an older release rejects the key), never fetched from or stored in SSM.
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        start = text.index('if [ ! -s /etc/kirocrew-on/credentials.key ]; then')
        block = text[start:text.index("\nfi\n", start)]
        self.assertIn("openssl rand -base64 32 > /etc/kirocrew-on/credentials.key.new", block)
        self.assertIn("umask 077", block)
        self.assertIn("chown root:octopus /etc/kirocrew-on/credentials.key", text)
        self.assertIn("chmod 0640 /etc/kirocrew-on/credentials.key", text)
        region = text.rfind("# platform:begin", 0, start)
        self.assertGreater(region, text.rfind("# platform:end", 0, start))   # inside a platform-only region
        written = text.index('toml_set /etc/kirocrew-on/platform.toml credentials_key_file \'"/etc/kirocrew-on/credentials.key"\'')
        self.assertGreater(written, text.index('ln -sfn "$TARGET" /opt/kirocrew-on/current'))
        self.assertNotIn("credentials.key", (ROOT / "deploy/aws/deploy.sh").read_text())

    def test_worker_host_template_admits_only_the_platform(self):
        worker = yaml.load((ROOT / "deploy/aws/kirocrew-on-worker.yaml").read_text(), Loader=CfnLoader)
        resources = worker["Resources"]
        ingress = resources["WorkerSecurityGroup"]["Properties"]["SecurityGroupIngress"]
        # The Worker port, the Gateway TLS port (native entry relay) and the preview port (D36), each a single port, all only
        # from the platform.
        self.assertEqual([rule["FromPort"] for rule in ingress],
                         [{"Ref": "WorkerPort"}, {"Ref": "GatewayTlsPort"}, {"Ref": "PreviewTlsPort"}])
        self.assertEqual(worker["Parameters"]["PreviewTlsPort"]["Default"], 8945)
        for rule in ingress:
            self.assertEqual(rule["SourceSecurityGroupId"], {"Ref": "PlatformSecurityGroupId"})
            self.assertEqual(rule["FromPort"], rule["ToPort"])
            self.assertNotIn("CidrIp", rule)
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
        self.assertEqual(set(worker["Outputs"]), {"InstanceId", "PrivateIp", "NativeDomain"})

    def test_platform_template_exports_its_network_without_touching_the_host_security_group(self):
        outputs = template()["Outputs"]
        for key in ("VpcId", "SubnetId", "HostSecurityGroupId", "ImageId"):
            self.assertIn(key, outputs)
        # The Worker hosts' native distributions use this host as their origin, addressed like its own native one.
        native = template()["Resources"]["NativeDistribution"]["Properties"]["DistributionConfig"]["Origins"][0]
        self.assertEqual(outputs["HostOriginDomain"]["Value"], native["DomainName"])
        # Changing the group's description would replace the host; its single CloudFront rule may only widen.
        self.assertEqual(template()["Resources"]["HostSecurityGroup"]["Properties"]["GroupDescription"],
                         "KiroCrew ON API - inbound only from CloudFront origin-facing addresses; admin via SSM")

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
        calls = text[text.index('log "原生契约检查'):text.index('log "切换到已通过检查的发布')]
        self.assertIn('boundary_check "$BOUNDARY" "$TARGET" "$KC"', calls)   # the release being installed, not current
        library = (ROOT / "deploy/install/boundary-lib.sh").read_text()
        start = library.index("boundary_check() {")
        check = library[start:library.index("\n}\n", start)]
        self.assertIn('sed -e "s#/opt/kirocrew-on/current/#$2/#g" -e "s#/opt/kirocrew/current/#$3/#g" "$toml" > "$check"', check)
        for line in (l for l in check.splitlines() if "runuser" in l or "composition.gateway" in l or "OCTOPUS_CONFIG" in l):
            self.assertNotIn("$toml", line)
        self.assertIn('--config "$check" --check', check)
        self.assertIn('OCTOPUS_CONFIG="$check"', check)
        self.assertIn('rm -f "$check"', check)

    def test_security_group_descriptions_use_only_what_ec2_accepts(self):
        # Observed on EC2: an apostrophe in a rule description made the platform update roll back.
        allowed = re.compile(r"[a-zA-Z0-9. _\-:/()#,@\[\]+=&;{}!$*]{0,255}")
        worker = yaml.load((ROOT / "deploy/aws/kirocrew-on-worker.yaml").read_text(), Loader=CfnLoader)
        for name, document in (("platform", template()), ("worker", worker)):
            for logical, resource in document["Resources"].items():
                if resource["Type"] != "AWS::EC2::SecurityGroup":
                    continue
                properties = resource["Properties"]
                texts = [properties["GroupDescription"]] + [rule["Description"] for key in
                         ("SecurityGroupIngress", "SecurityGroupEgress") for rule in properties.get(key, [])]
                for text in texts:
                    self.assertTrue(allowed.fullmatch(text), (name, logical, text))

    def test_a_boundary_native_entry_is_its_own_distribution_relayed_to_its_gateway(self):   # D49 phase 3, 2026-10-21
        native = yaml.load((ROOT / "deploy/aws/kirocrew-on-native.yaml").read_text(), Loader=CfnLoader)
        origin = native["Resources"]["NativeDistribution"]["Properties"]["DistributionConfig"]["Origins"][0]
        self.assertEqual(origin["DomainName"], {"Ref": "PlatformOriginDomain"})
        self.assertEqual(origin["CustomOriginConfig"]["HTTPPort"], {"Ref": "RelayPort"})
        self.assertEqual(origin["OriginCustomHeaders"], [{"HeaderName": "X-Origin-Verify", "HeaderValue": {"Ref": "OriginSecret"}}])
        self.assertTrue(native["Parameters"]["OriginSecret"]["NoEcho"])
        self.assertEqual(set(native["Resources"]), {"NativeDistribution"})   # nothing else: no host, no security group
        self.assertIn("NativeDomain", native["Outputs"])
        relay = (ROOT / "deploy/nginx/kirocrew-boundary.conf").read_text()
        for part in ('if ($http_x_origin_verify != "__ORIGIN_SECRET__")', "listen __RELAY_PORT__;",
                     "proxy_pass http://127.0.0.1:__GATEWAY_PORT__;", "proxy_set_header Upgrade $http_upgrade;",
                     'proxy_set_header X-Origin-Verify "";',
                     "access_log /var/log/nginx/kirocrew-relay/boundary-__BOUNDARY__.access.log kirocrew_native;"):
            self.assertIn(part, relay)
        directives = "\n".join(line for line in relay.splitlines() if not line.lstrip().startswith("#"))
        for absent in ("default_server", "map ", "log_format"):   # those of kirocrew-native.conf are shared, never redefined
            self.assertNotIn(absent, directives)

    def test_worker_host_native_entry_is_relayed_through_the_platform(self):
        worker = yaml.load((ROOT / "deploy/aws/kirocrew-on-worker.yaml").read_text(), Loader=CfnLoader)
        resources, parameters = worker["Resources"], worker["Parameters"]
        ingress = resources["WorkerSecurityGroup"]["Properties"]["SecurityGroupIngress"]
        self.assertEqual({rule["FromPort"]["Ref"] for rule in ingress}, {"WorkerPort", "GatewayTlsPort", "PreviewTlsPort"})
        self.assertTrue(all(rule["SourceSecurityGroupId"] == {"Ref": "PlatformSecurityGroupId"} for rule in ingress))
        origin = resources["NativeDistribution"]["Properties"]["DistributionConfig"]["Origins"][0]
        self.assertEqual(origin["DomainName"], {"Ref": "PlatformOriginDomain"})
        self.assertEqual(origin["CustomOriginConfig"]["HTTPPort"], {"Ref": "RelayPort"})
        self.assertEqual(origin["OriginCustomHeaders"], [{"HeaderName": "X-Origin-Verify", "HeaderValue": {"Ref": "OriginSecret"}}])
        self.assertTrue(parameters["OriginSecret"]["NoEcho"])
        self.assertIn("NativeDomain", worker["Outputs"])
        relay = (ROOT / "deploy/nginx/kirocrew-relay.conf").read_text()
        for part in ('if ($http_x_origin_verify != "__ORIGIN_SECRET__")', "listen __RELAY_PORT__",
                     "proxy_pass https://__WORKER_IP__:__GATEWAY_TLS_PORT__;", "proxy_ssl_verify on;",
                     "proxy_ssl_trusted_certificate /etc/kirocrew-on/worker-ca.pem;",
                     "proxy_ssl_name __WORKER_NAME__.worker.kirocrew-on;", "proxy_set_header Upgrade $http_upgrade;",
                     'proxy_set_header X-Origin-Verify "";'):
            self.assertIn(part, relay)
        gateway = (ROOT / "deploy/nginx/kirocrew-worker.conf").read_text()
        for part in ("listen __GATEWAY_TLS_PORT__ ssl;", "proxy_pass http://127.0.0.1:__GATEWAY_PORT__;",
                     "proxy_set_header Upgrade $http_upgrade;"):
            self.assertIn(part, gateway)
        install = (ROOT / "deploy/install/al2023.sh").read_text()
        start = install.index('if [ -n "$WORKER_HOST" ]; then\n  log "Worker 主机')
        worker_mode = install[start:install.index("\nfi\n", start)]
        self.assertIn('toml_set "$BOUNDARY_TOML" crew_url "\\"https://$NATIVE_DOMAIN\\""', worker_mode)
        self.assertIn('-e "s#__GATEWAY_PORT__#$GATEWAY_PORT#g"', worker_mode)
        self.assertIn('if [ -z "$WORKER_HOST" ] && [ -n "${NATIVE_DOMAIN:-}" ]; then', install)

    def test_worker_host_native_commands_never_print_the_origin_secret(self):
        secret = "s" * 48
        env = {**os.environ, "DEPLOY_DRY_RUN": "1", "ORIGIN_SECRET": secret}
        deploy = lambda *args: subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env,
                                              capture_output=True, text=True)
        up = deploy("worker-host", "up", "w1")
        self.assertEqual(up.returncode, 0, up.stderr)
        for part in ("RelayPort", "8920", "PlatformOriginDomain", "<HostOriginDomain>", "OriginSecret", "GatewayTlsPort"):
            self.assertIn(part, up.stdout)
        push = deploy("worker-host", "push", "w1")
        self.assertIn("NATIVE_DOMAIN=", push.stdout)
        tls = deploy("worker-host", "tls", "w1")
        self.assertIn("DNS:w1.worker.kirocrew-on", tls.stdout)
        native = deploy("worker-host", "native", "w1")
        self.assertEqual(native.returncode, 0, native.stderr)
        for part in ("kirocrew-relay.conf", "/etc/kirocrew-on/origin-secret", "kirocrew-relay-w1.conf", "nginx -t"):
            self.assertIn(part, native.stdout)
        for result in (up, push, tls, native):
            self.assertNotIn(secret, result.stdout + result.stderr)

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

    def test_kiro_login_finishes_once_signed_in_instead_of_waiting_for_kiro_cli_to_exit(self):
        # Observed on w1: kiro-cli printed "Logged in successfully" and kept running, so exit= never reached the log.
        text = (ROOT / "deploy/aws/deploy.sh").read_text()
        login = text[text.index("kiro_login() {"):text.index("admin_password() {")]
        signed_in = login.index("grep -aqF 'Logged in successfully'")
        ended = login.index("pkill -u crew-$boundary -f 'kiro-cli login'", signed_in)
        verdict = login.index('[[ "$whoami" == "Logged in"* ]]', ended)
        self.assertLess(signed_in, verdict)
        # No long-lived feeder: once the attempt is ended, nothing of it outlives a two-second sleep.
        self.assertNotIn("sleep 900", login)
        self.assertNotIn('[ "$finished" = "exit=0" ]', login)

    def test_worker_host_add_runs_every_step_in_order_and_stops_at_the_first_failure(self):
        secret = "a" * 48
        env = {**os.environ, "DEPLOY_DRY_RUN": "1", "ORIGIN_SECRET": secret}
        deploy = lambda *args, **extra: subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT,
                                                       env={**env, **extra}, capture_output=True, text=True)
        added = deploy("worker-host", "add", "w2")
        self.assertEqual(added.returncode, 0, added.stderr)
        steps = ("cloudformation create-change-set", "WORKER_HOST=1", "subjectAltName=IP:", "kirocrew-relay-w2.conf",
                 "bootstrap-worker --spare", "kiro-cli login --use-device-flow")
        positions = [added.stdout.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("kirocrew-on-m1-worker-w2", added.stdout)
        self.assertNotIn(secret, added.stdout + added.stderr)
        self.assertEqual(deploy("worker-host", "add", "W2").returncode, 2)
        # A secret the relay would refuse stops up before any change, and nothing after it runs.
        stopped = deploy("worker-host", "add", "w2", ORIGIN_SECRET="short")
        self.assertEqual(stopped.returncode, 2)
        self.assertIn("源站密钥", stopped.stderr)
        self.assertNotIn("create-change-set", stopped.stdout)
        self.assertNotIn("WORKER_HOST=1", stopped.stdout)

    def fake_aws(self, state, *args):
        """Run deploy.sh for real against tests/fake_aws.py; returns (result, log lines)."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "bin").mkdir()
        (tmp / "bin/aws").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{ROOT / "tests/fake_aws.py"}" "$@"\n')
        (tmp / "bin/aws").chmod(0o755)
        (tmp / "state.json").write_text(json.dumps(state))
        env = {k: v for k, v in os.environ.items() if k not in {"DEPLOY_DRY_RUN", "ORIGIN_SECRET"}}
        env.update(PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", FAKE_AWS_STATE=str(tmp / "state.json"),
                   FAKE_AWS_LOG=str(tmp / "aws.log"))
        result = subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        log = (tmp / "aws.log").read_text().splitlines() if (tmp / "aws.log").exists() else []
        return result, log

    def platform(self, **params):
        outputs = {"VpcId": "vpc-1", "SubnetId": "subnet-1", "HostSecurityGroupId": "sg-1", "ArtifactBucket": "bucket",
                   "InstanceId": "i-platform", "HostOriginDomain": "ec2-1-2-3-4.compute-1.amazonaws.com"}
        return {"params": {"NativeRelayEnd": "8929", **params}, "outputs": outputs}

    @staticmethod
    def relay(log):
        changes = [json.loads(line[len("PARAMS "):]) for line in log if line.startswith("PARAMS ")]
        return [next(p["ParameterValue"] for p in change if p["ParameterKey"] == "RelayPort") for change in changes]

    def test_relay_ports_stay_unique_across_worker_hosts(self):
        worker = lambda **params: {"params": params, "outputs": {"InstanceId": "i-w", "PrivateIp": "10.42.1.9"}}
        # An existing host created before relay ports gets the lowest free one; a host keeps a port no one else has.
        result, log = self.fake_aws({"stacks": {"kirocrew-on-m1": self.platform(), "kirocrew-on-m1-worker-w1": worker()}},
                                    "worker-host", "up", "w1")
        self.assertEqual((result.returncode, self.relay(log)), (0, ["8920"]), result.stderr)
        stacks = {"kirocrew-on-m1": self.platform(), "kirocrew-on-m1-worker-w1": worker(RelayPort="8920"),
                  "kirocrew-on-m1-worker-w2": worker(RelayPort="8922")}
        result, log = self.fake_aws({"stacks": stacks}, "worker-host", "up", "w2")
        self.assertEqual(self.relay(log), ["8922"], result.stderr)
        # Two hosts that were created at the same time and share a port: the one being updated moves.
        stacks["kirocrew-on-m1-worker-w3"] = worker(RelayPort="8920")
        result, log = self.fake_aws({"stacks": stacks}, "worker-host", "up", "w3")
        self.assertEqual(self.relay(log), ["8921"], result.stderr)
        # The range is the platform's deployed NativeRelayEnd, not the local stack.env.
        stacks["kirocrew-on-m1"] = self.platform(NativeRelayEnd="8922")
        stacks["kirocrew-on-m1-worker-w2"] = worker(RelayPort="8921")
        result, log = self.fake_aws({"stacks": stacks}, "worker-host", "up", "w4")
        self.assertEqual((result.returncode, self.relay(log)), (0, ["8922"]), result.stderr)
        stacks["kirocrew-on-m1-worker-w4"] = worker(RelayPort="8922")
        result, log = self.fake_aws({"stacks": stacks}, "worker-host", "up", "w5")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("中转端口", result.stderr)
        self.assertEqual(self.relay(log), [])

    def test_the_preview_port_is_never_a_relay_port(self):
        worker = lambda port: {"params": {"RelayPort": str(port)}, "outputs": {"InstanceId": "i-w", "PrivateIp": "10.42.1.9"}}
        stacks = {"kirocrew-on-m1": self.platform(PreviewPort="8929"),
                  **{f"kirocrew-on-m1-worker-w{port - 8919}": worker(port) for port in range(8920, 8929)}}
        result, log = self.fake_aws({"stacks": stacks}, "worker-host", "up", "w10")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("中转端口", result.stderr)
        self.assertEqual(self.relay(log), [])
        result, log = self.fake_aws({"stacks": {"kirocrew-on-m1": self.platform(PreviewPort="8929"),
                                                "kirocrew-on-m1-worker-w1": worker(8920)}}, "worker-host", "up", "w1")
        self.assertEqual(result.returncode, 0, result.stderr)
        change = [json.loads(line[len("PARAMS "):]) for line in log if line.startswith("PARAMS ")][0]
        self.assertEqual(next(p["ParameterValue"] for p in change if p["ParameterKey"] == "PreviewTlsPort"), "8945")

    def test_relay_port_lookups_fail_closed(self):
        worker = {"params": {"RelayPort": "8920"}, "outputs": {"InstanceId": "i-w", "PrivateIp": "10.42.1.9"}}
        for throttle in ("*list*", "kirocrew-on-m1-worker-w1"):
            result, log = self.fake_aws({"stacks": {"kirocrew-on-m1": self.platform(), "kirocrew-on-m1-worker-w1": worker},
                                         "throttle": [throttle]}, "worker-host", "up", "w1")
            self.assertNotEqual(result.returncode, 0, throttle)
            self.assertEqual(self.relay(log), [], throttle)
        # A platform stack from before the relay range: update it first.
        platform = self.platform()
        del platform["params"]["NativeRelayEnd"]
        result, log = self.fake_aws({"stacks": {"kirocrew-on-m1": platform}}, "worker-host", "up", "w1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deploy.sh up", result.stderr)
        self.assertEqual(self.relay(log), [])

    def test_kiro_login_on_a_host_that_is_already_signed_in_does_not_start_a_device_flow(self):
        state = {"stacks": {"kirocrew-on-m1-worker-w1": {"outputs": {"InstanceId": "i-w1"}}}, "boundaries": ["w1"],
                 "whoami": "Logged in with IAM Identity Center (https://d-9067e1d68a.awsapps.com/start)"}
        result, log = self.fake_aws(state, "kiro-login", "--host", "w1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Logged in with IAM Identity Center", result.stdout)
        commands = [line for line in log if line.startswith("COMMAND ")]
        self.assertTrue(commands and all("kiro-cli login" not in line for line in commands), commands)

    def test_kiro_login_refuses_a_boundary_the_host_does_not_have(self):
        # 2026-10-20: "kiro-login --boundary kirocrew-on" waited for a device code that could never come
        state = {"stacks": {"kirocrew-on-m1": {"outputs": {"InstanceId": "i-platform"}}}, "boundaries": ["owner", "personal"]}
        result, log = self.fake_aws(state, "kiro-login", "--boundary", "kirocrew-on")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("主机上没有边界 kirocrew-on；现有边界：owner personal", result.stderr)
        self.assertTrue(all("kiro-cli login" not in line for line in log if line.startswith("COMMAND ")))

    def test_boundary_native_opens_one_boundarys_entry_through_the_platform(self):
        secret = "s" * 48
        env = {**os.environ, "DEPLOY_DRY_RUN": "1", "ORIGIN_SECRET": secret}
        deploy = lambda *args: subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env,
                                              capture_output=True, text=True)
        native = deploy("boundary", "native", "owner")
        self.assertEqual(native.returncode, 0, native.stderr)
        for part in ("kirocrew-on-native.yaml", "kirocrew-on-m1-native-owner", "create-change-set", "RelayPort",
                     "PlatformOriginDomain", "kirocrew-boundary.conf", "/etc/nginx/conf.d/kirocrew-relay-boundary-owner.conf",
                     "/etc/kirocrew-on/origin-secret", "nginx -t", "crew_url", "boundary_restart"):
            self.assertIn(part, native.stdout)
        self.assertNotIn(secret, native.stdout + native.stderr)
        for name in ("personal", "Bad", "x"):   # personal has the platform's own distribution
            with self.subTest(name):
                self.assertEqual(deploy("boundary", "native", name).returncode, 2)
        # nginx reads conf.d in name order: a relay uses log_format kirocrew_native, defined in kirocrew-native.conf (EC2
        # 2026-10-21: "kirocrew-boundary-owner.conf" came first and nginx -t refused "unknown log format")
        self.assertLess("kirocrew-native.conf", "kirocrew-relay-boundary-owner.conf")
        text = (ROOT / "deploy/aws/deploy.sh").read_text()   # one relay port per distribution, Worker hosts' and boundaries'
        self.assertIn("starts_with(StackName, '$STACK_NAME-worker-') || starts_with(StackName, '$STACK_NAME-native-')", text)

    def test_the_boundary_relay_refuses_a_taken_port_and_a_boundary_the_host_lacks(self):
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        printed = subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), "boundary", "native", "owner"], cwd=ROOT,
                                 env=env, capture_output=True, text=True).stdout
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        script = printed[printed.index("set -euo pipefail"):].replace("/etc/nginx/conf.d", str(tmp)).replace("/etc/kirocrew-on", str(tmp))
        head = f"NAME=owner PORT=8921 NATIVE=d1234abcd.cloudfront.net CONF={tmp}/kirocrew-relay-boundary-owner.conf\n"
        run = lambda: subprocess.run(["bash", "-c", head + script], capture_output=True, text=True)
        missing = run()
        self.assertEqual(missing.returncode, 3)
        self.assertIn("主机上没有边界 owner", missing.stderr)
        (tmp / "boundary-owner.toml").write_text('gateway_port = 5481\ncrew_url = "http://localhost:15481"\n')
        (tmp / "kirocrew-relay-w1.conf").write_text("server {\n    listen 8921;\n}\n")
        taken = run()
        self.assertEqual(taken.returncode, 3)
        self.assertIn("kirocrew-relay-w1.conf", taken.stderr)

    def test_the_relay_refuses_a_port_another_worker_host_already_listens_on(self):
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        printed = subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), "worker-host", "native", "w2"], cwd=ROOT,
                                 env=env, capture_output=True, text=True).stdout
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        script = printed.split("\n", 1)[1].replace("/etc/nginx/conf.d", str(tmp))
        head = f"NAME=w2 IP=10.42.1.9 PORT=8921 GATEWAY_TLS_PORT=8944 CONF={tmp}/kirocrew-relay-w2.conf\n"
        run = lambda: subprocess.run(["bash", "-c", head + script], capture_output=True, text=True)
        (tmp / "kirocrew-relay-w1.conf").write_text("server {\n    listen 8920;\n    listen [::]:8920;\n}\n")
        (tmp / "kirocrew-relay-w2.conf").write_text("server {\n    listen 8921;\n}\n")  # its own, re-rendered
        self.assertIn("尚未启用原生入口", run().stderr)  # past the port check, stopped by the next one
        (tmp / "kirocrew-relay-w1.conf").write_text("server {\n    listen 8921;\n    listen [::]:8921;\n}\n")
        refused = run()
        self.assertEqual(refused.returncode, 3)
        self.assertIn("kirocrew-relay-w1.conf", refused.stderr)
        relay = (ROOT / "deploy/nginx/kirocrew-relay.conf").read_text()
        # Upstream errors log the request line, one-time ?token= included: kept where only root reads.
        self.assertIn("error_log /var/log/nginx/kirocrew-relay/__WORKER_NAME__.error.log;", relay)
        self.assertIn("access_log /var/log/nginx/kirocrew-relay/__WORKER_NAME__.access.log kirocrew_native;", relay)
        self.assertIn("install -d -m 0700 /var/log/nginx/kirocrew-relay", printed)
        # Observed on EC2: right after systemctl reload the new port was not listening yet, so the check read HTTP 000.
        self.assertLess(printed.index('ss -Hltn "sport = :$PORT"'), printed.index("curl -s -o /dev/null"))

    def test_space_runs_a_worker_project_on_the_host_and_forwards_it_to_localhost(self):
        run = "run-" + "0123456789abcdef" * 2
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        space = lambda *args: subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env,
                                             capture_output=True, text=True)
        result = space("space", run)
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = result.stdout + result.stderr
        # D49: the boundary that holds the Session, found on the host (tests/test_boundaries)
        for part in (f'DIR="/var/lib/octopus/boundaries/$B/worker/{run}/workspace/project"',
                     f"UNIT=kirocrew-space-{run}", 'CREW="crew-$B"', "NODE_BIN=/opt/kirocrew-node/bin",
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
