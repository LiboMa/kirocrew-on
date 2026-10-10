"""Local stack helper: isolated configs and ports, never the user's ~/.kiro."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from octopus.config import load

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools/local_stack.sh"


class LocalStackTests(unittest.TestCase):
    def run_script(self, base, *args):
        env = {**os.environ, "LOCAL_E2E_DIR": str(base)}
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_init_writes_isolated_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "e2e"
            result = self.run_script(base, "init")
            self.assertEqual(result.returncode, 0, result.stderr)
            boundary = load(base / "boundary.toml", environ={"HOME": str(Path.home())}, cwd=ROOT)
            platform = load(base / "platform.toml", environ={"HOME": str(Path.home())}, cwd=ROOT)
            self.assertEqual((boundary.gateway_port, boundary.worker_port, platform.api_port), (6476, 8938, 8948))
            self.assertEqual(boundary.crew_home, base / "crew")
            self.assertEqual(boundary.kiro_home, base / "kiro")
            self.assertEqual(boundary.worker_data_dir, base / "worker")
            self.assertEqual(platform.data_dir, base / "platform")
            for path in (boundary.crew_home, boundary.kiro_home):
                self.assertFalse(str(path).startswith(str(Path.home() / ".kiro")))

    def test_init_keeps_existing_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "e2e"
            self.run_script(base, "init")
            (base / "platform.toml").write_text((base / "platform.toml").read_text() + "# kept\n")
            self.run_script(base, "init")
            self.assertTrue((base / "platform.toml").read_text().endswith("# kept\n"))

    def test_status_reports_stopped_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_script(Path(temporary) / "e2e", "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            for service in ("gateway", "worker", "api"):
                self.assertIn(f"{service}: stopped", result.stdout)


if __name__ == "__main__":
    unittest.main()
