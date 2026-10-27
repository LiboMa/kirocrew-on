"""MVP 0.2.0 P1 Task 9 (S1, design §4.4): a release is a wheel, its checksums and a manifest of what it was tested with,
optionally signed. The wheel installs into a plain venv with no other dependency and carries the console build and the
deployment files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_release   # noqa: E402

SUMS = "a1  node-v22.23.3-linux-x64.tar.xz\nb2  node-v22.23.3-linux-arm64.tar.xz\nc3  node-v22.23.3-linux-x64.tar.gz\n"


@unittest.skipUnless((ROOT / "octopus/ui/dist/index.html").is_file(), "先构建控制台：npm --prefix web run build")
class ReleaseBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "SHASUMS256.txt").write_text(SUMS)
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out",
                        str(root / "key.pem")], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(root / "key.pem"), "-pubout", "-out", str(root / "pub.pem")],
                       check=True, capture_output=True)
        cls.probe = ROOT / "deploy/untracked-probe.txt"   # an untracked file in a packaged directory
        cls.probe.write_text("must not ship")
        cls.out = root / "out"
        build_release.main(["--out", str(cls.out), "--node-sums", str(root / "SHASUMS256.txt"),
                            "--sign-key", str(root / "key.pem")])
        cls.manifest = json.loads((cls.out / "manifest.json").read_text())
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.probe.unlink(missing_ok=True)
        cls.tmp.cleanup()

    def test_the_release_has_a_wheel_sums_and_a_manifest(self):
        from octopus import __version__
        wheel = self.out / f"kirocrew_on-{__version__}-py3-none-any.whl"
        self.assertTrue(wheel.is_file())
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        self.assertIn(f"{digest}  {wheel.name}", (self.out / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(self.manifest["wheel"], {"name": wheel.name, "sha256": digest})
        self.assertEqual((self.manifest["version"], self.manifest["channel"]), (__version__, "preview"))
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            record = archive.read(f"kirocrew_on-{__version__}.dist-info/RECORD").decode()
        for name in ("octopus/__main__.py", "composition/gateway.py", "octopus/ui/dist/index.html",
                     "octopus/_release/deploy/install/al2023.sh", "octopus/_release/skills/kirocrew-on/SKILL.md"):
            self.assertIn(name, names)
        self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))
        # Review 2026-10-26: only what git tracks (no stray file a checkout holds), and no deployment's own settings
        self.assertNotIn("octopus/_release/deploy/aws/stack.env", names)
        self.assertNotIn("octopus/_release/deploy/untracked-probe.txt", names)
        self.assertIn("octopus/__main__.py,sha256=", record)   # every file's hash, as pip checks it

    def test_the_wheel_installs_and_runs(self):
        venv = self.root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
        wheel = next(self.out.glob("*.whl"))
        subprocess.run([str(venv / "bin/python"), "-m", "pip", "install", "--no-index", "--quiet", str(wheel)], check=True,
                       capture_output=True, cwd=self.root)
        done = subprocess.run([str(venv / "bin/kirocrew-on"), "--help"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        probe = ("import importlib.resources as r; from octopus import release; f = r.files('octopus'); "
                 "print(f.joinpath('ui/dist/index.html').is_file(), f.joinpath('_release/deploy/install/al2023.sh').is_file(), "
                 "release.path('skills/kirocrew-on/SKILL.md').is_file())")
        done = subprocess.run([str(venv / "bin/python"), "-I", "-c", probe], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(done.stdout.split(), ["True", "True", "True"], done.stderr)

    def test_a_signed_manifest_verifies_and_a_changed_one_does_not(self):
        verify = lambda path: subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(self.root / "pub.pem"),
                                              "-signature", str(self.out / "manifest.sig"), str(path)], capture_output=True)
        self.assertEqual(verify(self.out / "manifest.json").returncode, 0)
        changed = self.root / "changed.json"
        data = bytearray((self.out / "manifest.json").read_bytes())
        data[10] ^= 1
        changed.write_bytes(bytes(data))
        self.assertNotEqual(verify(changed).returncode, 0)

    def test_pins_come_from_versions_env(self):
        pins = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                    if line and not line.startswith("#") and "=" in line)
        got = self.manifest["pins"]
        self.assertEqual(got["kirocrew"], {"source": "git", "git_ref": pins["KIROCREW_GIT_REF"],
                                           "version": pins["KIROCREW_ACCEPTED_VERSION"]})
        self.assertEqual(got["node"], {"version": pins["NODE_DIST"], "sha256": {"x64": "a1", "arm64": "b2"}})
        self.assertEqual((got["kiro_cli"], got["claude_code"], got["codex"]),
                         (pins["KIRO_CLI_VERSION"], pins["CLAUDE_CODE_VERSION"], pins["CODEX_VERSION"]))


if __name__ == "__main__":
    unittest.main()
