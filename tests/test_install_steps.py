"""MVP 0.2.0 P2 Task 2 (design §4.2.2–4.2.3): what differs between Amazon Linux 2023 and Ubuntu 22.04 / 24.04 lives in
deploy/install/os-lib.sh — package names, Python 3.12, SELinux or AppArmor, nginx's group — and al2023.sh calls it. On
AL2023 the commands are the ones al2023.sh ran before (m1 and deploy.sh push are unchanged)."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "deploy/install/os-lib.sh"
# al2023.sh before P2 (3f968e8), word for word
BEFORE = [
    "dnf install -y -q git tar gzip xz jq unzip make gcc gcc-c++ nginx python3.12 python3.12-pip python3.12-devel",
    "dnf install -y -q nss nspr libdrm libX11 libXcomposite libXdamage libXext libXfixes libXrandr alsa-lib atk at-spi2-atk "
    "at-spi2-core cairo cups-libs mesa-libgbm pango libxcb libxkbcommon",
]
# what the Agents' Playwright Chromium loads, by system: (dnf, Ubuntu 22.04, Ubuntu 24.04 after the t64 renames)
BROWSER = [("nss", "libnss3", "libnss3"), ("nspr", "libnspr4", "libnspr4"), ("libdrm", "libdrm2", "libdrm2"),
           ("libX11", "libx11-6", "libx11-6"), ("libXcomposite", "libxcomposite1", "libxcomposite1"),
           ("libXdamage", "libxdamage1", "libxdamage1"), ("libXext", "libxext6", "libxext6"),
           ("libXfixes", "libxfixes3", "libxfixes3"), ("libXrandr", "libxrandr2", "libxrandr2"),
           ("alsa-lib", "libasound2", "libasound2t64"), ("atk", "libatk1.0-0", "libatk1.0-0t64"),
           ("at-spi2-atk", "libatk-bridge2.0-0", "libatk-bridge2.0-0t64"), ("at-spi2-core", "libatspi2.0-0", "libatspi2.0-0t64"),
           ("cairo", "libcairo2", "libcairo2"), ("cups-libs", "libcups2", "libcups2t64"), ("mesa-libgbm", "libgbm1", "libgbm1"),
           ("pango", "libpango-1.0-0", "libpango-1.0-0"), ("libxcb", "libxcb1", "libxcb1"),
           ("libxkbcommon", "libxkbcommon0", "libxkbcommon0")]
RECORDER = '#!/bin/sh\nprintf "%s\\n" "$(basename "$0") $*" >> "$CALLS"\n'


class StepsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.calls = self.dir / "calls"

    def fake(self, name, body=RECORDER):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run_lib(self, script, os_id="al2023", version="2023", arch="aarch64", **env):
        environment = {"PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": str(self.calls), "PYTHON_PACKAGE": "python3.12",
                       "OS_ID": os_id, "OS_VERSION": version, "ARCH": arch, **env}
        return subprocess.run(["bash", "-c", f'set -euo pipefail; . "{LIB}"; os_detect; {script}'], capture_output=True,
                              text=True, env=environment, timeout=60)

    def recorded(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []


class PackageTests(StepsCase):
    def test_al2023_runs_the_same_commands_as_before(self):
        self.fake("dnf")
        done = self.run_lib("install_packages")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.recorded(), BEFORE)

    def test_both_package_sets_cover_the_same_libraries(self):
        for os_id, version, column in (("al2023", "2023", 0), ("ubuntu", "22.04", 1), ("ubuntu", "24.04", 2)):
            with self.subTest(version):
                done = self.run_lib("os_packages browser", os_id, version)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.split(), [row[column] for row in BROWSER])

    def test_ubuntu_installs_without_questions_after_an_update(self):
        self.fake("apt-get", RECORDER + 'printf "%s\\n" "DEBIAN_FRONTEND=$DEBIAN_FRONTEND" >> "$CALLS"\n')
        for version, python in (("24.04", ["python3.12", "python3.12-venv", "python3.12-dev"]), ("22.04", [])):
            with self.subTest(version):
                self.calls.unlink(missing_ok=True)
                done = self.run_lib("install_packages", "ubuntu", version)
                self.assertEqual(done.returncode, 0, done.stderr)
                calls = self.recorded()
                self.assertEqual(calls[:2], ["apt-get update -q", "DEBIAN_FRONTEND=noninteractive"])
                install = calls[2].split()
                self.assertEqual(install[:5], ["apt-get", "install", "-y", "-q", "--no-install-recommends"])
                for name in ["git", "xz-utils", "jq", "unzip", "g++", "nginx", *python,
                             *[row[2 if version == "24.04" else 1] for row in BROWSER]]:
                    self.assertIn(name, install)
                self.assertEqual(python == [], "python3.12" not in install)

    def test_nginx_runs_as_nginx_on_al2023_and_www_data_on_ubuntu(self):
        for os_id, version, group in (("al2023", "2023", "nginx"), ("ubuntu", "24.04", "www-data")):
            with self.subTest(os_id):
                self.assertEqual(self.run_lib('echo "$NGINX_GROUP"', os_id, version).stdout.strip(), group)

    def test_without_install_sh_the_system_is_read_from_os_release(self):   # deploy.sh push runs al2023.sh directly
        release = self.dir / "os-release"
        release.write_text('ID="amzn"\nVERSION_ID="2023"\n')
        done = self.run_lib('echo "$OS_ID $OS_VERSION $NGINX_GROUP"', "", "", OS_RELEASE=str(release))
        self.assertEqual(done.stdout.strip(), "al2023 2023 nginx")
        release.write_text('ID="amzn"\nVERSION_ID="2"\n')
        done = self.run_lib("echo went-on", "", "", OS_RELEASE=str(release))
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("went-on", done.stdout)


class PythonTests(StepsCase):
    def uv_tarball(self, arch="aarch64"):
        uv = textwrap.dedent(f'''\
            #!/bin/sh
            printf "%s\\n" "uv $* UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR UV_PYTHON_PREFERENCE=$UV_PYTHON_PREFERENCE" >> "$CALLS"
            if [ "$1 $2" = "python find" ]; then echo "$UV_PYTHON_INSTALL_DIR/cpython-3.12.12-linux-{arch}-gnu/bin/python3.12"; fi
            ''').encode()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo(f"uv-{arch}-unknown-linux-gnu/uv")
            info.size, info.mode = len(uv), 0o755
            tar.addfile(info, io.BytesIO(uv))
        path = self.dir / "uv.tar.gz"
        path.write_bytes(buffer.getvalue())
        self.fake("curl", RECORDER + f'while [ $# -gt 1 ]; do [ "$1" = -o ] && cp "{path}" "$2"; shift; done\n')
        return hashlib.sha256(buffer.getvalue()).hexdigest()

    def test_ubuntu_22_04_takes_a_cpython_that_uv_manages(self):
        digest = self.uv_tarball()
        root = self.dir / "python"
        done = self.run_lib('install_python; echo "PYTHON=$PYTHON"', "ubuntu", "22.04", PYTHON_ROOT=str(root),
                            UV_VERSION="0.13.5", UV_SHA256_AARCH64=digest, UV_SHA256_X86_64="0" * 64)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"PYTHON={root}/cpython/cpython-3.12.12-linux-aarch64-gnu/bin/python3.12", done.stdout)
        calls = self.recorded()
        self.assertIn("https://github.com/astral-sh/uv/releases/download/0.13.5/uv-aarch64-unknown-linux-gnu.tar.gz",
                      calls[0])
        self.assertIn("--proto =https", calls[0])
        self.assertIn(f"uv python install 3.12 UV_PYTHON_INSTALL_DIR={root}/cpython UV_PYTHON_PREFERENCE=only-managed", calls)
        self.assertTrue((root / "bin/uv").is_file())

    def test_a_uv_download_that_does_not_match_its_checksum_is_not_used(self):
        self.uv_tarball()
        root = self.dir / "python"
        done = self.run_lib("install_python", "ubuntu", "22.04", PYTHON_ROOT=str(root), UV_VERSION="0.13.5",
                            UV_SHA256_AARCH64="0" * 64, UV_SHA256_X86_64="0" * 64)
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse((root / "bin/uv").exists())
        self.assertFalse(any("python install" in call for call in self.recorded()))

    def test_al2023_and_ubuntu_24_04_use_the_system_python(self):
        self.fake("python3.12")
        for os_id, version in (("al2023", "2023"), ("ubuntu", "24.04")):
            with self.subTest(os_id):
                done = self.run_lib('install_python; echo "$PYTHON"', os_id, version)
                self.assertEqual(done.stdout.strip(), str(self.bin / "python3.12"))
        self.assertFalse(any(call.startswith("curl") for call in self.recorded()))


class SecurityModuleTests(StepsCase):
    def test_selinux_labels_the_port_and_apparmor_needs_nothing(self):
        self.fake("setsebool")
        self.fake("semanage", RECORDER + '[ "$2" = -a ] && exit 1; exit 0\n')   # already defined: -a fails, -m changes it
        self.fake("getenforce", "#!/bin/sh\necho Enforcing\n")
        self.assertEqual(self.run_lib("label_port 8943 8944").returncode, 0)
        self.assertEqual(self.recorded(), ["setsebool -P httpd_can_network_connect 1",
                                           "semanage port -a -t http_port_t -p tcp 8943",
                                           "semanage port -m -t http_port_t -p tcp 8943",
                                           "semanage port -a -t http_port_t -p tcp 8944",
                                           "semanage port -m -t http_port_t -p tcp 8944"])
        self.calls.unlink()
        self.fake("getenforce", "#!/bin/sh\necho Permissive\n")
        self.assertEqual(self.run_lib("label_port 8943").returncode, 0)
        (self.bin / "getenforce").unlink()   # Ubuntu: AppArmor, and no getenforce at all
        self.assertEqual(self.run_lib("label_port 8943", "ubuntu", "24.04").returncode, 0)
        self.assertEqual(self.recorded(), [])


class InstallerTests(unittest.TestCase):
    def test_al2023_sh_takes_every_system_difference_from_os_lib(self):
        text = (ROOT / "deploy/install/al2023.sh").read_text()
        for step in ('. "$SOURCE/deploy/install/os-lib.sh"', "os_detect", "install_packages", "install_python", "label_port"):
            self.assertTrue(step in text, step)
        for system_specific in ("dnf ", "semanage", "setsebool", "root:nginx", "-g nginx", 'PYTHON="$(command -v'):
            self.assertFalse(system_specific in text, system_specific)
        self.assertIn("UV_VERSION=", (ROOT / "deploy/versions.env").read_text())


if __name__ == "__main__":
    unittest.main()
