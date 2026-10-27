"""MVP 0.2.0 P2 Task 3 (design §4.4): install.sh takes a release from a channel, checks manifest.sig with the public key
written into it and the wheel against the manifest, and only then unpacks it and runs the install steps. Every failure
stops before any change. A local directory stands in for the channel (--from); a fake bash stands in for the steps."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_install_preflight import GIB, RELEASES, Metadata, ThreadingHTTPServer, threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_release   # noqa: E402

SHELL = shutil.which("dash") or "/bin/sh"
SUMS = "a1  node-v22.23.3-linux-x64.tar.xz\nb2  node-v22.23.3-linux-arm64.tar.xz\n"
STEPS = '''#!/bin/sh
printf '%s\\n' "$* KIROCREW_ON_RELEASE=$KIROCREW_ON_RELEASE AWS_REGION=$AWS_REGION" >> "$CALLS"
if [ -n "${MAKE_PASSWORD:-}" ]; then mkdir -p "$INSTALL_FS_ROOT/etc/kirocrew-on"; echo s3cret-initial > "$INSTALL_FS_ROOT/etc/kirocrew-on/admin-password"; fi
exit "${STEPS_EXIT:-0}"
'''


@unittest.skipUnless((ROOT / "octopus/ui/dist/index.html").is_file(), "先构建控制台：npm --prefix web run build")
class ReleaseInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from octopus import __version__
        cls.version = __version__
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "SHASUMS256.txt").write_text(SUMS)
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out",
                        str(root / "key.pem")], check=True, capture_output=True)
        cls.channel = root / "channel"
        build_release.main(["--out", str(cls.channel / "releases" / __version__), "--node-sums", str(root / "SHASUMS256.txt"),
                            "--sign-key", str(root / "key.pem"), "--index", str(cls.channel / "feed"),
                            "--downloads", "https://downloads.example/releases", "--feed", "https://updates.example/feed"])
        cls.installer = cls.channel / "releases" / __version__ / "install.sh"
        cls.imds = ThreadingHTTPServer(("127.0.0.1", 0), Metadata)
        threading.Thread(target=cls.imds.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.imds.shutdown()
        cls.imds.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.dir = Path(self.work.name)
        shutil.copytree(self.channel, self.dir / "channel")
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        (self.bin / "bash").write_text(STEPS)
        (self.bin / "bash").chmod(0o755)
        (self.dir / "os-release").write_text(RELEASES["al2023"])
        (self.dir / "meminfo").write_text(f"MemTotal: {15 * GIB} kB\n")
        (self.dir / "systemd").mkdir()
        self.fs = self.dir / "fs"
        self.calls = self.dir / "calls"

    def install(self, *args, installer=None, **env):
        environment = {"PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": str(self.calls), "INSTALL_FS_ROOT": str(self.fs),
                       "PREFLIGHT_OS_RELEASE": str(self.dir / "os-release"), "PREFLIGHT_MACHINE": "aarch64",
                       "PREFLIGHT_UID": "0", "PREFLIGHT_FREE_KB": str(47 * GIB), "PREFLIGHT_MEMINFO": str(self.dir / "meminfo"),
                       "PREFLIGHT_SYSTEMD": str(self.dir / "systemd"),
                       "PREFLIGHT_IMDS": f"http://127.0.0.1:{self.imds.server_port}", **env}
        return subprocess.run([SHELL, str(installer or self.installer), "--from", str(self.dir / "channel"), *args],
                              capture_output=True, text=True, env=environment, timeout=120)

    @property
    def stage(self):
        return self.fs / "opt/kirocrew-on/incoming" / self.version

    def steps_ran(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def assert_nothing_changed(self, done):
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertFalse(self.stage.exists())
        self.assertEqual(self.steps_ran(), [])

    def test_a_verified_release_is_unpacked_and_handed_to_the_install_steps(self):
        done = self.install("--channel", "preview", MAKE_PASSWORD="1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.steps_ran(), [f"{self.stage}/deploy/install/al2023.sh personal "
                                            f"KIROCREW_ON_RELEASE={self.version} AWS_REGION=us-east-1"])
        for name in ("octopus/__main__.py", "composition/gateway.py", "octopus/ui/dist/index.html", "deploy/install/al2023.sh",
                     "deploy/versions.env", "skills/kirocrew-on/SKILL.md", "package.json", "package-lock.json",
                     "requirements-workflow.txt"):
            self.assertTrue((self.stage / name).is_file(), name)   # laid out as a checkout: al2023.sh copies the tree
        self.assertTrue(os.access(self.stage / "deploy/install/boundary.sh", os.X_OK))   # modes come back from the wheel
        self.assertIn("s3cret-initial", done.stdout)   # printed this once
        self.assertIn("「我的设置 › 执行环境」", done.stdout)
        again = self.install("--channel", "preview")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("s3cret-initial", again.stdout)   # a rerun only fills in, and keeps the password to itself
        self.assertIn("/etc/kirocrew-on/admin-password", again.stdout)

    def test_a_changed_manifest_fails_its_signature_before_any_change(self):
        manifest = self.dir / "channel/releases" / self.version / "manifest.json"
        data = bytearray(manifest.read_bytes())
        data[20] ^= 1
        manifest.write_bytes(bytes(data))
        done = self.install("--channel", "preview")
        self.assert_nothing_changed(done)
        self.assertIn("签名", done.stderr)

    def test_an_unsigned_release_is_refused(self):   # no fallback to the checksums alone
        (self.dir / "channel/releases" / self.version / "manifest.sig").unlink()
        done = self.install("--channel", "preview")
        self.assert_nothing_changed(done)
        self.assertIn("manifest.sig", done.stderr)

    def test_a_wheel_that_does_not_match_the_manifest_stops(self):
        wheel = next((self.dir / "channel/releases" / self.version).glob("*.whl"))
        wheel.write_bytes(wheel.read_bytes() + b"\0")
        done = self.install("--channel", "preview")
        self.assert_nothing_changed(done)
        self.assertIn("sha256", done.stderr)

    def test_a_version_the_channel_does_not_have_says_so(self):
        done = self.install("--version", "9.9.9")
        self.assert_nothing_changed(done)
        self.assertIn("9.9.9", done.stderr)
        done = self.install("--channel", "stable")   # nothing published there yet
        self.assert_nothing_changed(done)
        self.assertIn("stable", done.stderr)

    def test_a_signed_manifest_of_another_version_is_not_taken_for_this_one(self):
        other = self.dir / "channel/releases/9.9.9"
        shutil.copytree(self.dir / "channel/releases" / self.version, other)
        done = self.install("--version", "9.9.9")
        self.assert_nothing_changed(done)
        self.assertIn("9.9.9", done.stderr)

    def test_odd_versions_and_channels_are_refused_before_any_download(self):
        for args in (("--version", "../../etc"), ("--channel", "nightly")):
            with self.subTest(args):
                done = self.install(*args)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(self.steps_ran(), [])

    def test_failed_steps_say_a_rerun_only_fills_in(self):
        done = self.install("--channel", "preview", STEPS_EXIT="5")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("再运行一次", done.stderr)

    def test_an_installer_without_a_public_key_installs_nothing(self):
        done = self.install("--channel", "preview", installer=ROOT / "deploy/install/install.sh")
        self.assertEqual(done.returncode, 2)
        self.assertIn("公钥", done.stderr)
        self.assertEqual(self.steps_ran(), [])

    def test_the_stamped_installer_knows_where_releases_live(self):
        text = self.installer.read_text()
        self.assertIn('DOWNLOADS="https://downloads.example/releases"', text)
        self.assertIn('FEED="https://updates.example/feed"', text)
        self.assertIn("-----BEGIN PUBLIC KEY-----", text)
        self.assertNotIn("PRIVATE KEY", text)


@unittest.skipUnless((ROOT / "octopus/ui/dist/index.html").is_file(), "先构建控制台：npm --prefix web run build")
class IndexTests(unittest.TestCase):
    def test_the_channel_index_points_at_the_wheel_with_its_hash(self):
        from octopus import __version__
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SHASUMS256.txt").write_text(SUMS)
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-out", str(root / "key.pem")], check=True,
                           capture_output=True)
            build = lambda channel: build_release.main([
                "--out", str(root / "releases" / __version__), "--node-sums", str(root / "SHASUMS256.txt"),
                "--sign-key", str(root / "key.pem"), "--channel", channel, "--index", str(root / "feed"),
                "--downloads", "https://downloads.example/releases", "--feed", "https://updates.example/feed"])
            build("preview")
            wheel = next((root / "releases" / __version__).glob("*.whl"))
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            page = (root / "feed/preview/simple/kirocrew-on/index.html").read_text()
            self.assertIn(f'href="https://downloads.example/releases/{__version__}/{wheel.name}#sha256={digest}"', page)
            self.assertIn('data-requires-python="&gt;=3.11"', page)
            self.assertIn('href="kirocrew-on/"', (root / "feed/preview/simple/index.html").read_text())
            self.assertEqual(json.loads((root / "feed/preview/latest.json").read_text())["version"], __version__)
            build("preview")   # building the same version again lists it once
            self.assertEqual((root / "feed/preview/simple/kirocrew-on/index.html").read_text().count("<a "), 1)
            self.assertFalse((root / "feed/stable").exists())

    def test_an_index_needs_a_signature(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            build_release.main(["--out", tmp, "--index", tmp + "/feed", "--downloads", "https://d.example", "--feed",
                                "https://u.example", "--node-sums", os.devnull])


class CommandTests(unittest.TestCase):
    def test_kirocrew_on_runs_the_current_release_as_the_services_do(self):
        script = (ROOT / "deploy/install/kirocrew-on").read_text()
        self.assertIn("PYTHONPATH=/opt/kirocrew-on/current exec /opt/kirocrew-on/venv/bin/python -m octopus", script)
        self.assertIn('install -m 0755 "$TARGET/deploy/install/kirocrew-on" /usr/local/bin/kirocrew-on',
                      (ROOT / "deploy/install/al2023.sh").read_text().split("ln -sfn \"$TARGET\" /opt/kirocrew-on/current", 1)[1])
        local = script.replace("/opt/kirocrew-on/current", str(ROOT)).replace("/opt/kirocrew-on/venv/bin/python", sys.executable)
        done = subprocess.run(["sh", "-c", local, "kirocrew-on", "--help"], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("setup", done.stdout)


class PrebuiltConsoleTests(unittest.TestCase):
    def test_a_release_from_the_wheel_is_not_rebuilt(self):   # the wheel has octopus/ui/dist and no web/ sources
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        self.assertIn('[ -n "$WORKER_HOST" ] || [ ! -d "$TARGET/web" ] || (cd "$TARGET/web" && npm ci', text)


class SetupTests(unittest.TestCase):
    """The pip channel: `sudo kirocrew-on setup` lays the installed package out as install.sh unpacks a wheel, then runs
    install.sh on it (preflight and the same install steps)."""
    def test_setup_needs_root(self):
        from octopus import installer
        with mock.patch("os.geteuid", return_value=1000), self.assertRaises(SystemExit) as stop:
            installer.setup()
        self.assertIn("root", str(stop.exception))

    def test_the_installed_package_is_laid_out_like_an_unpacked_release_and_install_sh_runs_on_it(self):
        from octopus import __version__, installer
        with tempfile.TemporaryDirectory() as tmp, mock.patch("os.geteuid", return_value=0), \
                mock.patch("os.execvp") as run:
            installer.setup(incoming=Path(tmp))
            stage = Path(tmp) / f"{__version__}-pip"
            for name in ("octopus/__main__.py", "composition/gateway.py", "deploy/install/al2023.sh", "skills/kirocrew-on/SKILL.md",
                         "package.json", "requirements-workflow.txt"):
                self.assertTrue((stage / name).is_file(), name)
            self.assertFalse(any("__pycache__" in str(p) for p in stage.rglob("*")))
            run.assert_called_once_with("sh", ["sh", str(stage / "deploy/install/install.sh"), "--source", str(stage),
                                               "--version", __version__])


if __name__ == "__main__":
    unittest.main()
