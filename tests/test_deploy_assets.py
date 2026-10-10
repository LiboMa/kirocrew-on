"""Deployment assets: SSM-only access, encrypted storage, pinned versions, no macOS assumptions."""
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: {suffix: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))})


class DeployAssetTests(unittest.TestCase):
    def template(self):
        return yaml.load((ROOT / "deploy/aws/kirocrew-on-ec2.yaml").read_text(), Loader=CfnLoader)

    def test_template_is_ssm_only_and_encrypted(self):
        resources = self.template()["Resources"]
        group = next(r for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroup")
        self.assertNotIn("SecurityGroupIngress", group["Properties"])
        role = next(r for r in resources.values() if r["Type"] == "AWS::IAM::Role")
        self.assertIn("arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", role["Properties"]["ManagedPolicyArns"])
        instance = next(r for r in resources.values() if r["Type"] == "AWS::EC2::Instance")["Properties"]
        self.assertTrue(instance["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(instance["MetadataOptions"]["HttpTokens"], "required")
        self.assertNotIn("KeyName", instance)

    def test_capacity_parameters_have_no_unverified_defaults(self):
        parameters = self.template()["Parameters"]
        for name in ("InstanceType", "SubnetId", "VpcId", "VolumeSizeGiB"):
            self.assertNotIn("Default", parameters[name], name)
        self.assertEqual(parameters["ImageId"]["Default"],
                         "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")

    def test_install_script_is_valid_bash_without_mac_assumptions(self):
        script = ROOT / "deploy/install/al2023.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text()
        for forbidden in ("/Applications", "/opt/homebrew", "zsh", "lsof", "open "):
            self.assertNotIn(forbidden, text)
        if shutil.which("shellcheck"):
            subprocess.run(["shellcheck", "-S", "warning", str(script)], check=True)

    def test_versions_file_declares_every_pin(self):
        values = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                      if line and not line.startswith("#"))
        for key in ("KIROCREW_VERSION", "KIROCREW_WHEEL_URL", "KIROCREW_WHEEL_SHA256", "KIRO_CLI_URL", "KIRO_CLI_SHA256",
                    "CLAUDE_CODE_VERSION", "CODEX_VERSION", "OPENCODE_VERSION", "NODE_PACKAGE", "PYTHON_PACKAGE"):
            self.assertIn(key, values)

    def test_config_examples_load(self):
        from octopus.config import load
        platform = load(ROOT / "deploy/config/platform.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(platform.data_dir, Path("/var/lib/octopus/platform"))
        self.assertIn("localhost:18918", platform.allowed_hosts)
        boundary = load(ROOT / "deploy/config/boundary.toml.example", environ={"HOME": "/tmp"}, cwd="/tmp")
        self.assertEqual(boundary.worker_data_dir, Path("/var/lib/octopus/boundaries/personal/worker"))
        self.assertEqual(boundary.crew_home, Path("/var/lib/octopus/boundaries/personal/crew"))
        self.assertEqual(set(boundary.tools), {"kiro_cli", "claude", "codex", "opencode"})


if __name__ == "__main__":
    unittest.main()
