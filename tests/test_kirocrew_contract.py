"""The config the platform seeds for KiroCrew uses only keys KiroCrew reads (EC2 2026-10-16: `memory.enabled` was never read,
and `dashboard.host` / `dashboard.port` / `workspaces.default.path` neither). The list is checked against the installed
KiroCrew on every deploy (tests/native); a KiroCrew upgrade fails here until the list is checked again."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composition import templates

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "tests/kirocrew_contract.json").read_text())


def seeded():
    with tempfile.TemporaryDirectory() as temporary, \
            patch.dict("sys.modules", {"kiro_crew.agent": SimpleNamespace(managed_mcp_spec_entry=lambda name: {"command": "x"})}):
        root = Path(temporary)
        templates.seed(crew_home=root / "crew", kiro_home=root / "kiro", allowed_roots=[root / "w"],
                       legacy_pipeline=False)
        return json.loads((root / "crew/config.json").read_text())


class ConfigContractTests(unittest.TestCase):
    def test_the_seeded_config_uses_only_keys_kirocrew_reads(self):
        config, known = seeded(), CONTRACT["config"]
        self.assertEqual(sorted(set(config) - set(known[""])), [])
        for section, value in config.items():
            if isinstance(value, dict):
                self.assertEqual(sorted(set(value) - set(known.get(section, []))), [], section)

    def test_the_contract_is_for_the_kirocrew_the_platform_deploys(self):
        pinned = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
        self.assertEqual(CONTRACT["kirocrew_version"], pinned["KIROCREW_ACCEPTED_VERSION"])


if __name__ == "__main__":
    unittest.main()
