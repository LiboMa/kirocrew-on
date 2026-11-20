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
printf '%s %s\\n' "${1##*/}" "${AWS_SECRET_ACCESS_KEY:+has-keys}" >> "$CALLS.keys"
printf '%s|%s|%s|%s\\n' "${1##*/}" "${KIRO_LICENSE:-}" "${KIRO_IDC_START_URL:-}" "${KIRO_IDC_REGION:-}" >> "$CALLS.kiro"
case "$1" in
  */single-stack.sh)
    printf '%s\\n' "$1 $2 EC2_INSTANCE_ID=$EC2_INSTANCE_ID" >> "$CALLS"
    [ "$2" != up ] || printf 'CF_DOMAIN=d1console.cloudfront.net\\nNATIVE_DOMAIN=d2native.cloudfront.net\\nPREVIEW_DOMAIN=d3preview.cloudfront.net\\nPUBLIC_IP=203.0.113.7\\n' > "$3"
    [ "$2" != check-role ] || exit "${ROLE_EXIT:-0}"
    exit 0 ;;
esac
printf '%s\\n' "$* KIROCREW_ON_RELEASE=$KIROCREW_ON_RELEASE AWS_REGION=$AWS_REGION CF_DOMAIN=$CF_DOMAIN" >> "$CALLS"
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
        self.assertEqual(self.steps_ran(), [
            f"{self.stage}/deploy/install/single-stack.sh up EC2_INSTANCE_ID=i-0abc123",   # CloudFront before the steps
            f"{self.stage}/deploy/install/al2023.sh personal KIROCREW_ON_RELEASE={self.version} AWS_REGION=us-east-1 "
            "CF_DOMAIN=d1console.cloudfront.net",
            f"{self.stage}/deploy/install/single-stack.sh check-role EC2_INSTANCE_ID=i-0abc123"])
        self.assertIn("控制台：https://d1console.cloudfront.net/", done.stdout)
        for name in ("octopus/__main__.py", "composition/gateway.py", "octopus/ui/dist/index.html", "deploy/install/al2023.sh",
                     "deploy/versions.env", "skills/kirocrew-on/SKILL.md", "package.json", "package-lock.json",
                     "requirements-workflow.txt",
                     # review 2026-10-28: boundary_check runs the native contract tests of the release being installed
                     "tests/native/test_native_contract.py", "tests/kirocrew_contract.json"):
            self.assertTrue((self.stage / name).is_file(), name)   # laid out as a checkout: al2023.sh copies the tree
        self.assertTrue(os.access(self.stage / "deploy/install/boundary.sh", os.X_OK))   # modes come back from the wheel
        self.assertIn("s3cret-initial", done.stdout)   # printed this once
        self.assertIn("「管理 › 执行环境」", done.stdout)   # the single-node edition's owner is its administrator
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

    def test_over_ssh_it_says_the_connection_will_drop_and_where_the_host_is_afterwards(self):   # review 2026-10-28
        done = self.install("--channel", "preview", PREFLIGHT_SSH="1")
        self.assertEqual(done.returncode, 0, done.stderr)
        notice = done.stdout.index("SSH 会断开")
        self.assertLess(notice, done.stdout.index("控制台：https://"))
        self.assertIn("公网 IP：203.0.113.7", done.stdout)
        self.assertNotIn("SSH 会断开", self.install("--channel", "preview", PREFLIGHT_SSH="0").stdout)
        self.assertNotIn("SSH 会断开", self.install("--channel", "preview", "--no-cloudfront", PREFLIGHT_SSH="1").stdout)

    def test_without_cloudfront_no_stack_is_made_and_the_console_is_reached_through_ssm(self):
        done = self.install("--channel", "preview", "--no-cloudfront")
        self.assertEqual(done.returncode, 0, done.stderr)
        steps = self.steps_ran()
        self.assertFalse(any(" up " in step for step in steps))
        self.assertTrue(steps[0].endswith("CF_DOMAIN="))
        self.assertIn("AWS-StartPortForwardingSession", done.stdout)

    def test_a_role_that_still_writes_is_pointed_out_at_the_end(self):
        done = self.install("--channel", "preview", ROLE_EXIT="10")
        self.assertEqual(done.returncode, 0, done.stderr)   # the platform is up; the role is the administrator's to trim
        self.assertIn("主机角色的核对没有通过", done.stdout)

    def test_the_installers_keys_reach_the_stack_step_only(self):   # review 2026-10-28: not npm, pip or the Agents
        done = self.install("--channel", "preview", AWS_ACCESS_KEY_ID="AKIAINSTALLER0000001", AWS_SECRET_ACCESS_KEY="s" * 40,
                            AWS_SESSION_TOKEN="t" * 40)
        self.assertEqual(done.returncode, 0, done.stderr)
        keys = Path(str(self.calls) + ".keys").read_text().split("\n")
        self.assertIn("single-stack.sh has-keys", keys)
        self.assertIn("al2023.sh ", keys)

    def test_how_kiro_signs_in_is_set_at_install(self):   # review 2026-10-28 (§6.4): Pro / IAM Identity Center
        done = self.install("--channel", "preview", "--kiro-license", "pro", "--kiro-identity-provider",
                            "https://d-9067e1d68a.awsapps.com/start", "--kiro-region", "us-east-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("al2023.sh|pro|https://d-9067e1d68a.awsapps.com/start|us-east-1",
                      Path(str(self.calls) + ".kiro").read_text().splitlines())
        for args in (("--kiro-license", "gold"), ("--kiro-identity-provider", "http://x"), ("--kiro-region", "east")):
            with self.subTest(args):
                self.assertEqual(self.install("--channel", "preview", *args).returncode, 2)

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
        # -P: never the current directory first on sys.path (root may run it inside an Agent's workspace)
        self.assertIn("PYTHONPATH=/opt/kirocrew-on/current exec /opt/kirocrew-on/venv/bin/python -P -m octopus", script)
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
    """The pip channel (review 2026-10-28): `sudo kirocrew-on setup` runs the install.sh its wheel carries — the one with
    the release's addresses and public key written in — for its own version, so the release it installs is downloaded and
    verified like any other; pip's own check of the index is not trusted alone."""
    def test_setup_needs_root(self):
        from octopus import installer
        with mock.patch("os.geteuid", return_value=1000), self.assertRaises(SystemExit) as stop:
            installer.setup()
        self.assertIn("root", str(stop.exception))

    def test_a_published_package_runs_its_signed_install_sh_for_its_own_version(self):
        from octopus import __version__, installer
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "install.sh"
            script.write_text("#!/bin/sh\nPUBLIC_KEY='-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----'\n")
            with mock.patch("os.geteuid", return_value=0), mock.patch("os.execvp") as run, \
                    mock.patch("octopus.release.path", return_value=script):
                installer.setup()
            run.assert_called_once_with("sh", ["sh", str(script), "--version", __version__])

    def test_a_package_without_the_release_key_installs_nothing(self):
        from octopus import installer
        with mock.patch("os.geteuid", return_value=0), mock.patch("os.execvp") as run, self.assertRaises(SystemExit) as stop:
            installer.setup()   # this checkout's install.sh has only the placeholders
        run.assert_not_called()
        self.assertIn("公钥", str(stop.exception))


@unittest.skipUnless((ROOT / "octopus/ui/dist/index.html").is_file(), "先构建控制台：npm --prefix web run build")
class StampedWheelTests(unittest.TestCase):
    def test_the_wheel_of_an_indexed_release_carries_the_stamped_install_sh(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SHASUMS256.txt").write_text(SUMS)
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-out", str(root / "key.pem")], check=True,
                           capture_output=True)
            build_release.main(["--out", str(root / "out"), "--node-sums", str(root / "SHASUMS256.txt"), "--sign-key",
                                str(root / "key.pem"), "--index", str(root / "feed"), "--downloads",
                                "https://downloads.example/releases", "--feed", "https://updates.example/feed"])
            wheel = next((root / "out").glob("*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                carried = archive.read("octopus/_release/deploy/install/install.sh").decode()
            self.assertEqual(carried, (root / "out/install.sh").read_text())
            self.assertIn("-----BEGIN PUBLIC KEY-----", carried)


if __name__ == "__main__":
    unittest.main()
