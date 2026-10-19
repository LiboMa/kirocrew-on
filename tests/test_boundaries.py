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
        for line in ("[Service]", "MemoryHigh=6144M", "MemoryMax=8G", "CPUQuota=300%", "TasksMax=4096",
                     "OOMPolicy=continue"):   # an agent's build over the limit ends that process, not every task
            self.assertIn(line, gateway.splitlines())
        worker = (self.root / "systemd/octopus-worker@alice.service.d/limits.conf").read_text().splitlines()
        self.assertEqual(worker, ["[Service]", "MemoryMax=4G", "OOMPolicy=continue"])   # previews run under the Worker
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

    def test_a_gateway_that_does_not_restart_fails_and_leaves_no_mark(self):
        self.config("alice", gateway_port=5481)
        (self.root / "data/alice/worker").mkdir(parents=True)
        (self.root / "active").write_text("")
        failing = self.bash('systemctl() { echo "systemctl $*" >> ' + str(self.calls) +
                            '; [ "$1" = restart ] && [ "$2" = octopus-gateway@alice ] && return 1; '
                            'if [ "$1" = is-active ]; then return 0; fi; }\n'
                            'if boundary_restart alice /k; then echo RESTARTED; else echo FAILED; fi', check=False)
        self.assertIn("FAILED", failing.stdout)
        self.assertFalse((self.root / "data/alice/worker/.maintenance").exists())
        self.assertFalse((self.root / "data/alice/gateway.fingerprint").exists())   # the next deploy tries again
        self.assertNotIn("restart octopus-worker@alice", self.calls.read_text())

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

    def test_other_boundaries_restart_after_the_platform_is_up_and_a_failure_does_not_stop_the_rest(self):
        # One person's boundary must not hold up the platform (review 2026-10-19): the API and personal come first.
        loop = self.text.index('boundary_restart "$OTHER" "$KC"')
        self.assertGreater(loop, self.text.index("systemctl restart octopus-api"))
        self.assertGreater(loop, self.text.index('bootstrap-worker --worker-url "http://127.0.0.1:$WORKER_PORT"'))
        block = self.text[self.text.index("# others:begin"):self.text.index("# others:end")]
        calls = []
        stubs = ('other_boundaries() { echo alice; echo bob; echo carol; }\nboundary_env() { :; }\nlog() { echo "LOG $*"; }\n'
                 'systemctl() { :; }\nboundary_restart() { echo "restart $1"; [ "$1" != bob ]; }\nBOUNDARY=personal KC=/k\n')
        result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + stubs + block + "\necho END"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)   # the deploy reports the failure
        self.assertIn("restart carol", result.stdout)   # after bob failed, carol still restarted
        self.assertIn("bob", result.stderr)
        self.assertNotIn("END", result.stdout)

    def test_a_drain_cut_short_leaves_no_maintenance_mark(self):
        self.assertIn('[ -z "${BOUNDARY_DRAINING:-}" ] || rm -f "$BOUNDARY_DRAINING"',
                      self.text[self.text.index("cleanup() {"):].split("\n")[0])
        library = LIB.read_text()
        self.assertIn('BOUNDARY_DRAINING="$maintenance"', library)

    def test_a_host_with_only_personal_upgrades_as_before(self):
        # The loops run their bodies only for other boundaries; with none, the primary's steps are all there is.
        for start in (m.start() for m in re.finditer(r'for OTHER in \$\(other_boundaries "\$BOUNDARY"\)', self.text)):
            body = self.text[start:self.text.index("\ndone", start)]
            self.assertNotIn("$BOUNDARY\"", body.replace('other_boundaries "$BOUNDARY"', ""))


class BoundaryScriptTests(HostCase):
    """deploy/install/boundary.sh on a fake host: the current release and KiroCrew, the platform's Python, the admin file."""

    def setUp(self):
        super().setUp()
        on = self.root / "release"
        (on / "deploy/config").mkdir(parents=True)
        (on / "deploy/install").mkdir(parents=True)
        (on / "deploy/config/boundary.toml.example").write_text((ROOT / "deploy/config/boundary.toml.example").read_text())
        (on / "deploy/versions.env").write_text((ROOT / "deploy/versions.env").read_text())
        python = self.root / "kc/releases/1/venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\necho 0.10.0\n")
        python.chmod(0o755)
        self.platform = self.root / "platform-python"
        self.platform.write_text(f'#!/bin/sh\necho "register $*" >> {self.calls}\n'
                                 'case "$*" in *"--check-only"*"--owner nobody"*) echo "平台上没有用户 nobody" >&2; exit 1 ;; esac\n')
        self.platform.chmod(0o755)
        (self.root / "admin-password").write_text("not-for-output-0123456789")

    def add(self, *args, token=True, check=True):
        if token:
            (self.root / "data/alice").mkdir(parents=True, exist_ok=True)
            (self.root / "data/alice/worker.token").write_text("t" * 40)
        stubs = ('id() { [ "$1" = -u ] && echo 0 || return 0; }\n'
                 'useradd() { echo "useradd $*" >> CALLS; }\n'
                 'install() { local a=(); while [ $# -gt 0 ]; do case "$1" in -d) shift;; -m|-o|-g) shift 2;; *) a+=("$1"); shift;; esac; done; '
                 'mkdir -p "${a[@]}"; }\n').replace("CALLS", str(self.calls))
        body = (stubs + f"export ON_CURRENT={self.root}/release PLATFORM_PYTHON={self.platform} ADMIN_FILE={self.root}/admin-password\n"
                f"set -- {' '.join(args)}\n. {ROOT}/deploy/install/boundary.sh\n")
        return self.bash(body, check=check)

    def config_of(self, name):
        import tomllib
        return tomllib.loads((self.root / f"etc/boundary-{name}.toml").read_text())

    def test_boundary_script_writes_its_own_ports_and_dedicated_config(self):
        self.config("personal", worker_port=8928, gateway_port=5476)
        result = self.add("alice", "--owner", "alice")
        config = self.config_of("alice")
        self.assertTrue(5481 <= config["gateway_port"] <= 5580 and 9001 <= config["worker_port"] <= 9100)
        self.assertEqual(config["worker_isolation"], "dedicated")
        self.assertEqual(config["crew_url"], f"http://localhost:{config['gateway_port'] + 10000}")
        self.assertEqual(config["crew_entry_ttl"], "1h")
        self.assertIn("/boundaries/alice/", config["crew_home"])
        self.assertNotIn("/boundaries/personal/", (self.root / "etc/boundary-alice.toml").read_text())
        calls = self.calls.read_text()
        self.assertIn("systemctl enable octopus-gateway@alice octopus-worker@alice", calls)
        self.assertIn("-m composition.gateway --config", calls)   # the native contract check ran
        self.assertIn(f"--worker-url http://127.0.0.1:{config['worker_port']}", calls)
        self.assertIn("--name alice", calls)
        self.assertIn("--owner alice", calls)
        self.assertTrue((self.root / "systemd/octopus-gateway@alice.service.d/limits.conf").is_file())
        self.assertNotIn("not-for-output", result.stdout + result.stderr)
        self.assertIn("kiro-login --boundary alice", result.stdout)

    def test_adding_again_only_fills_in(self):
        self.add("alice", "--owner", "alice")
        first = (self.root / "etc/boundary-alice.toml").read_text()
        self.listening = {self.config_of("alice")["gateway_port"]}   # its own Gateway now listens
        self.add("alice", "--owner", "alice")
        self.assertEqual((self.root / "etc/boundary-alice.toml").read_text(), first)

    def test_a_second_boundary_gets_other_ports(self):
        self.add("alice", "--owner", "alice")
        (self.root / "data/bob").mkdir(parents=True)
        (self.root / "data/bob/worker.token").write_text("t" * 40)
        self.add("bob", "--owner", "bob", token=False)
        self.assertNotEqual(self.config_of("alice")["gateway_port"], self.config_of("bob")["gateway_port"])
        self.assertNotEqual(self.config_of("alice")["worker_port"], self.config_of("bob")["worker_port"])

    def test_an_owner_the_platform_refuses_leaves_nothing_behind(self):
        result = self.add("alice", "--owner", "nobody", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nobody", result.stderr)
        self.assertEqual(sorted(f.name for f in (self.root / "etc").iterdir()), [])
        self.assertNotIn("systemctl", self.calls.read_text())
        self.assertIn("--check-only", self.calls.read_text())

    def test_names_owners_and_limits_are_checked(self):
        for args in (("Alice", "--owner", "alice"), ("a;b", "--owner", "alice"), ("personal", "--owner", "alice"),
                     ("alice",), ("alice", "--owner", "a b"), ("alice", "--owner", "alice", "--memory", "0")):
            with self.subTest(args=args):
                self.assertNotEqual(self.add(*[f"'{a}'" for a in args], check=False).returncode, 0)
        self.assertEqual(sorted(f.name for f in (self.root / "etc").iterdir()), [])   # refused before anything is written
        self.assertEqual(self.calls.read_text(), "")


class DeployCommandTests(unittest.TestCase):
    def deploy(self, *args):
        env = {**os.environ, "DEPLOY_DRY_RUN": "1"}
        return subprocess.run(["bash", str(ROOT / "deploy/aws/deploy.sh"), *args], cwd=ROOT, env=env, capture_output=True,
                              text=True)

    def test_boundary_add_runs_the_boundary_script_through_ssm(self):
        result = self.deploy("boundary", "add", "alice", "--owner", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bash /opt/kirocrew-on/current/deploy/install/boundary.sh alice --owner alice --memory 8 --cpu 300",
                      result.stdout)
        sized = self.deploy("boundary", "add", "bob", "--owner", "bob", "--memory", "12", "--cpu", "200")
        self.assertIn("boundary.sh bob --owner bob --memory 12 --cpu 200", sized.stdout)
        for args in (("boundary", "add", "Alice", "--owner", "alice"), ("boundary", "add", "a;b", "--owner", "x"),
                     ("boundary", "add", "alice"), ("boundary", "add", "alice", "--owner", "x;y"),
                     ("boundary", "add", "alice", "--owner", "alice", "--memory", "x"), ("boundary", "remove", "alice")):
            with self.subTest(args=args):
                self.assertEqual(self.deploy(*args).returncode, 2)

    def test_a_push_may_take_as_long_as_its_drains(self):
        text = (ROOT / "deploy/aws/deploy.sh").read_text()
        push = text[text.index("push() {"):text.index("wait_command() {")]
        self.assertIn('"executionTimeout": ["10800"]', push)

    def test_boundaries_lists_name_ports_and_state(self):
        result = self.deploy("boundaries")
        self.assertEqual(result.returncode, 0, result.stderr)
        for part in ("/etc/kirocrew-on/boundary-*.toml", "gateway_port", "worker_port", "systemctl is-active"):
            self.assertIn(part, result.stdout)

    def test_kiro_login_takes_a_boundary_on_the_platform_host(self):
        result = self.deploy("kiro-login", "--boundary", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sudo -iu crew-alice", result.stdout)
        self.assertIn("/var/lib/octopus/boundaries/alice/logs/kiro-login-", result.stdout)
        self.assertNotIn("kirocrew-on-m1-worker-", result.stdout + result.stderr)   # the platform host's stack
        self.assertEqual(self.deploy("kiro-login", "--boundary", "A;b").returncode, 2)

    def test_tunnel_takes_a_boundary(self):
        result = self.deploy("tunnel", "--boundary", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/etc/kirocrew-on/boundary-alice.toml", result.stdout)
        self.assertIn('"localPortNumber":["15481"]', result.stdout)   # the dry run's placeholder port 5481
        self.assertIn('"localPortNumber":["15476"]', self.deploy("tunnel").stdout)   # personal, as before
        self.assertEqual(self.deploy("tunnel", "--boundary", "a b").returncode, 2)

    def test_space_finds_the_boundary_that_holds_the_session(self):
        run = "run-" + "0123456789abcdef" * 2
        result = self.deploy("space", run)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"/var/lib/octopus/boundaries/*/worker/{run}", result.stdout)
        self.assertIn('CREW="crew-$B"', result.stdout)
        # the lookup itself, against a fake tree
        lookup = "\n".join(result.stdout.splitlines()[:2])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / f"var/lib/octopus/boundaries/alice/worker/{run}").mkdir(parents=True)
            script = lookup.replace("/var/lib/octopus/boundaries", f"{tmp}/var/lib/octopus/boundaries") + '\necho "B=$B"'
            found = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(found.stdout.strip(), "B=alice", found.stderr)
            missing = subprocess.run(["bash", "-c", script.replace(run, "run-" + "f" * 32)], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 3)


if __name__ == "__main__":
    unittest.main()
