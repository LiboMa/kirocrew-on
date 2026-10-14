"""D38 §3.1: the Worker reports whether each Coding tool can be started on its boundary."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from octopus.worker import Worker


def executable(path, text="#!/bin/sh\nexit 0\n"):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class WorkerToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Neither the machine's PATH nor ~/.local/bin may decide these tests.
        patcher = patch.dict(os.environ, {"PATH": "", "HOME": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def worker(self, **kwargs):
        return Worker(self.root / "worker", "x" * 40, crew_home=self.root / "crew", **kwargs)

    def test_each_tool_is_found_on_the_configured_path_or_the_search_path(self):
        executable(self.bin / "claude")
        adapters = self.root / "adapters"
        adapters.mkdir()
        executable(adapters / "codex")
        kiro = executable(self.root / "kiro-cli", "#!/bin/sh\necho 'Logged in as fixture'\n")
        tools = self.worker(kiro_cli=kiro, path_prepend=(self.bin,), adapters_dir=adapters,
                            tool_paths={"opencode": self.root / "missing-opencode"}).tools()
        self.assertEqual(tools["kiro"], {"available": True, "login": "ok"})
        self.assertEqual(tools["claude"], {"available": True})
        self.assertEqual(tools["codex"], {"available": True})
        self.assertFalse(tools["opencode"]["available"])
        self.assertIn("missing-opencode", tools["opencode"]["reason"])

    def test_a_missing_command_says_which(self):
        tools = self.worker().tools()
        self.assertEqual(tools["claude"], {"available": False, "reason": "claude 命令不存在"})
        self.assertEqual(tools["kiro"]["available"], False)

    def test_an_expired_kiro_login_is_reported_beside_availability(self):
        kiro = executable(self.root / "kiro-cli", "#!/bin/sh\necho 'Not logged in. Run kiro-cli login' >&2\nexit 1\n")
        tools = self.worker(kiro_cli=kiro).tools()
        self.assertEqual(tools["kiro"], {"available": True, "login": "expired"})

    def test_health_carries_the_tools_and_the_known_limitations(self):
        executable(self.bin / "claude")
        health = self.worker(path_prepend=(self.bin,)).health()
        self.assertTrue(health["tools"]["claude"]["available"])
        self.assertTrue(any("K01" in item for item in health["limitations"]))


if __name__ == "__main__":
    unittest.main()
