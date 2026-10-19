"""D49: several KiroCrew boundaries on one host, one per person — the per-boundary steps (deploy/install/boundary-lib.sh), the
installer that checks every boundary before it switches the release, and the commands that add and operate a boundary."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "deploy/install/boundary-lib.sh"
INSTALLER = ROOT / "deploy/install/al2023.sh"


class HostCase(unittest.TestCase):
    """A fake host: ETC, DATA and SYSTEMD in a temporary directory; systemctl, runuser, ss and chown are recorded stubs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("etc", "data", "systemd", "bin", "on/composition", "on/octopus", "kc/releases/1"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ("on/composition/gateway.py", "on/octopus/config.py", "on/octopus/prompts.py", "on/octopus/__init__.py",
                     "on/pipeline_spec.py", "systemd/octopus-gateway@.service"):
            (self.root / name).write_text(name)
        (self.root / "kc/current").symlink_to(self.root / "kc/releases/1")
        self.listening = set()
        self.calls = self.root / "calls"
        self.calls.write_text("")

    def stubs(self):
        listening = " ".join(str(p) for p in sorted(self.listening))
        return (f'systemctl() {{ echo "systemctl $*" >> {self.calls}; if [ "$1" = is-active ]; then [ -f {self.root}/active ]; fi; }}\n'
                f'runuser() {{ echo "runuser $*" >> {self.calls}; echo 0; }}\n'
                f'chown() {{ echo "chown $*" >> {self.calls}; }}\n'
                f'ss() {{ for p in {listening}; do [ "$2" = "sport = :$p" ] && echo "LISTEN 0 0 127.0.0.1:$p"; done; true; }}\n'
                'log() { echo "LOG $*"; }\n')

    def bash(self, body, check=True):
        script = (f"set -euo pipefail\nETC={self.root}/etc DATA={self.root}/data SYSTEMD={self.root}/systemd "
                  f"ON_CURRENT={self.root}/on KC_CURRENT={self.root}/kc/current NODE_ROOT=/opt/kirocrew-node REGION=us-east-1\n"
                  + self.stubs() + f". {LIB}\n" + body)
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def config(self, name, **ports):
        (self.root / f"etc/boundary-{name}.toml").write_text("".join(f"{key} = {value}\n" for key, value in ports.items()))


class LibraryTests(HostCase):
    def test_free_ports_skip_what_boundaries_use_and_what_listens(self):
        self.config("personal", worker_port=8928, gateway_port=5476)
        self.config("alice", worker_port=9001, gateway_port=5481)
        self.listening = {5482}
        self.assertEqual(self.bash("free_port 5481 5580 gateway_port").stdout.strip(), "5483")
        self.assertEqual(self.bash("free_port 9001 9100 worker_port").stdout.strip(), "9002")
        exhausted = self.bash("free_port 5481 5482 gateway_port", check=False)
        self.assertNotEqual(exhausted.returncode, 0)
        self.assertIn("已用尽", exhausted.stderr)

    def test_other_boundaries_are_every_config_but_the_primary_and_checks(self):
        for name in ("personal", "alice", "bob"):
            self.config(name, gateway_port=1)
        (self.root / "etc/boundary-alice.check.toml").write_text("")
        self.assertEqual(self.bash("other_boundaries personal").stdout.split(), ["alice", "bob"])
        self.assertEqual(self.bash("other_boundaries alice").stdout.split(), ["bob", "personal"])

    def test_a_host_with_only_personal_has_no_other_boundary(self):
        self.config("personal", gateway_port=5476)
        self.assertEqual(self.bash("other_boundaries personal").stdout, "")

    def test_limits_are_drop_ins_the_gateway_fingerprint_reads(self):
        self.bash("boundary_limits alice 8 300")
        gateway = (self.root / "systemd/octopus-gateway@alice.service.d/limits.conf").read_text()
        for line in ("[Service]", "MemoryHigh=6144M", "MemoryMax=8G", "CPUQuota=300%", "TasksMax=4096"):
            self.assertIn(line, gateway.splitlines())
        self.assertEqual((self.root / "systemd/octopus-worker@alice.service.d/limits.conf").read_text(), "[Service]\nMemoryMax=2G\n")
        for memory, cpu in (("0", "300"), ("8", "abc"), ("8;reboot", "300")):
            with self.subTest(memory=memory, cpu=cpu):
                self.assertNotEqual(self.bash(f"boundary_limits alice '{memory}' '{cpu}'", check=False).returncode, 0)

    def test_the_tools_environment_is_written_per_boundary(self):
        self.bash("boundary_env alice")
        self.assertEqual((self.root / "etc/boundary-alice.env").read_text(),
                         "CLAUDE_CODE_USE_BEDROCK=1\nAWS_REGION=us-east-1\n"
                         "PATH=/opt/kirocrew-tools/bin:/opt/kirocrew-node/bin:/usr/local/bin:/usr/bin:/bin\n")

    def test_the_check_reads_a_copy_rewritten_to_the_release_being_installed(self):
        (self.root / "etc/boundary-alice.toml").write_text('crew_home = "/opt/kirocrew-on/current/x"\nkc = "/opt/kirocrew/current/y"\n')
        self.bash("boundary_check alice /opt/kirocrew-on/releases/new /opt/kirocrew/v2")
        calls = self.calls.read_text()
        self.assertIn("runuser -u crew-alice -- env HOME=", calls)
        self.assertIn(f"-m composition.gateway --config {self.root}/etc/boundary-alice.check.toml --check", calls)
        self.assertIn(f"OCTOPUS_CONFIG={self.root}/etc/boundary-alice.check.toml", calls)
        self.assertIn("unittest discover -s /opt/kirocrew-on/releases/new/tests/native", calls)
        self.assertFalse((self.root / "etc/boundary-alice.check.toml").exists())

    def test_a_failing_check_stops_the_caller(self):
        self.config("alice", gateway_port=5481)
        failing = self.bash("runuser() { return 1; }\nboundary_check alice /r /k\necho AFTER", check=False)
        self.assertNotEqual(failing.returncode, 0)
        self.assertNotIn("AFTER", failing.stdout)

    def test_restart_drains_then_restarts_that_boundary(self):
        self.config("alice", gateway_port=5481)
        (self.root / "data/alice/worker").mkdir(parents=True)
        restart = lambda: self.bash("boundary_restart alice /k").stdout
        self.calls.write_text("")
        restart()
        self.assertIn("systemctl restart octopus-gateway@alice", self.calls.read_text())   # no fingerprint yet
        self.assertIn("systemctl restart octopus-worker@alice", self.calls.read_text())
        self.assertTrue((self.root / "data/alice/gateway.fingerprint").is_file())
        (self.root / "active").write_text("")
        self.calls.write_text("")
        out = restart()
        self.assertNotIn("restart octopus-gateway@alice", self.calls.read_text())   # unchanged and running
        self.assertIn("Gateway 未变化", out)
        self.assertIn("systemctl restart octopus-worker@alice", self.calls.read_text())
        self.bash("boundary_limits alice 8 300")
        self.calls.write_text("")
        restart()
        calls = self.calls.read_text()
        self.assertIn("systemctl restart octopus-gateway@alice", calls)   # its drop-in changed: drained, then restarted
        self.assertLess(calls.index("runuser -u crew-alice"), calls.index("restart octopus-gateway@alice"))
        self.assertFalse((self.root / "data/alice/worker/.maintenance").exists())

    def test_the_gateway_restarts_when_anything_it_loads_changes(self):
        self.config("b1", gateway_port=5481)
        (self.root / "data/b1").mkdir(parents=True)
        restarted = lambda: (self.calls.write_text(""), self.bash("boundary_restart b1 /k"),
                             "restart octopus-gateway@b1" in self.calls.read_text())[2]
        self.assertTrue(restarted())
        (self.root / "active").write_text("")
        self.assertFalse(restarted())
        (self.root / "etc/boundary-b1.toml").write_text("changed")
        self.assertTrue(restarted())
        (self.root / "on/pipeline_spec.py").write_text("changed")
        self.assertTrue(restarted())
        (self.root / "etc/boundary-b1.env").write_text("PATH=/x\n")
        self.assertTrue(restarted())
        self.assertFalse(restarted())
        (self.root / "active").unlink()
        self.assertTrue(restarted())   # unchanged, but not running


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.text = INSTALLER.read_text()

    def test_the_installer_uses_the_library_for_every_boundary(self):
        self.assertIn('. "$SOURCE/deploy/install/boundary-lib.sh"', self.text)
        self.assertIn('boundary_env "$BOUNDARY"', self.text)
        self.assertIn('boundary_check "$BOUNDARY" "$TARGET" "$KC"', self.text)
        self.assertIn('boundary_restart "$BOUNDARY" "$KC"', self.text)
        self.assertNotIn("# gateway:begin", self.text)   # one copy of the restart logic: the library's

    def test_any_failing_boundary_keeps_the_old_release(self):
        switch = self.text.index('ln -sfn "$TARGET" /opt/kirocrew-on/current')
        checks = [m.start() for m in re.finditer(r'boundary_check "\$', self.text)]
        self.assertEqual(len(checks), 2)   # the primary, then every other boundary
        self.assertTrue(all(index < switch for index in checks))
        loop = self.text[self.text.index('for OTHER in $(other_boundaries "$BOUNDARY")'):switch]
        self.assertIn('boundary_check "$OTHER" "$TARGET" "$KC"', loop)
        self.assertTrue(self.text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in self.text)

    def test_every_other_boundary_is_restarted_after_the_primary(self):
        primary = self.text.index('boundary_restart "$BOUNDARY" "$KC"')
        loop = self.text.index('boundary_restart "$OTHER" "$KC"')
        self.assertLess(primary, loop)
        block = self.text[self.text.rindex("for OTHER in", 0, loop):loop]
        self.assertIn('boundary_env "$OTHER"', block)
        self.assertIn('systemctl enable "octopus-gateway@$OTHER" "octopus-worker@$OTHER"', block)
        self.assertLess(self.text.index("systemctl daemon-reload"), loop)

    def test_a_host_with_only_personal_upgrades_as_before(self):
        # The loops run their bodies only for other boundaries; with none, the primary's steps are all there is.
        for start in (m.start() for m in re.finditer(r'for OTHER in \$\(other_boundaries "\$BOUNDARY"\)', self.text)):
            body = self.text[start:self.text.index("\ndone", start)]
            self.assertNotIn("$BOUNDARY\"", body.replace('other_boundaries "$BOUNDARY"', ""))


if __name__ == "__main__":
    unittest.main()
