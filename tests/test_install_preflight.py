"""MVP 0.2.0 P2 Task 1 (design §4.2.2): `install.sh --check` tells, before anything changes, whether this host can take the
single-node edition. It runs as `curl … | sudo sh` — dash on Ubuntu — so it is POSIX sh and runs under dash here; every
check reads its data from a place the PREFLIGHT_* variables can point at."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "deploy/install/install.sh"
SHELL = shutil.which("dash") or "/bin/sh"
RELEASES = {
    "al2023": 'NAME="Amazon Linux"\nVERSION="2023"\nID="amzn"\nVERSION_ID="2023"\nPRETTY_NAME="Amazon Linux 2023.9.20251020"\n',
    "al2": 'NAME="Amazon Linux"\nVERSION="2"\nID="amzn"\nID_LIKE="centos rhel fedora"\nVERSION_ID="2"\n',
    "ubuntu-22.04": 'PRETTY_NAME="Ubuntu 22.04.5 LTS"\nNAME="Ubuntu"\nVERSION_ID="22.04"\nID=ubuntu\nID_LIKE=debian\n',
    "ubuntu-24.04": 'PRETTY_NAME="Ubuntu 24.04.3 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nID_LIKE=debian\n',
    "ubuntu-20.04": 'PRETTY_NAME="Ubuntu 20.04.6 LTS"\nNAME="Ubuntu"\nVERSION_ID="20.04"\nID=ubuntu\nID_LIKE=debian\n',
    "debian-12": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\nVERSION_ID="12"\nID=debian\n',
}
GIB = 1024 * 1024   # kB
RECOMMEND = "推荐 ARM64（Graviton）"


class Metadata(BaseHTTPRequestHandler):
    """IMDSv2: a token from PUT, then reads that carry it."""
    def do_PUT(self):
        if self.path == "/latest/api/token" and self.headers.get("X-aws-ec2-metadata-token-ttl-seconds"):
            self.answer(200, "tok-123")
        else:
            self.answer(403, "")

    def do_GET(self):
        values = {"/latest/meta-data/placement/region": "us-east-1", "/latest/meta-data/instance-id": "i-0abc123"}
        if self.headers.get("X-aws-ec2-metadata-token") != "tok-123":
            self.answer(401, "")
        elif self.path in values:
            self.answer(200, values[self.path])
        else:
            self.answer(404, "")

    def answer(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_):
        pass


class PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imds = ThreadingHTTPServer(("127.0.0.1", 0), Metadata)
        threading.Thread(target=cls.imds.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.imds.shutdown()
        cls.imds.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "systemd").mkdir()

    def check(self, system="al2023", machine="aarch64", uid="0", free_kb=47 * GIB, mem_kb=15 * GIB, imds=True,
              systemd=True, installed=False, args=("--check",)):
        (self.dir / "os-release").write_text(RELEASES[system])
        (self.dir / "meminfo").write_text(f"MemTotal:       {mem_kb} kB\nMemFree:         1000 kB\n")
        if installed:
            (self.dir / "current").mkdir(exist_ok=True)
        env = {"PATH": os.environ["PATH"], "PREFLIGHT_OS_RELEASE": str(self.dir / "os-release"), "PREFLIGHT_MACHINE": machine,
               "PREFLIGHT_UID": uid, "PREFLIGHT_FREE_KB": str(free_kb), "PREFLIGHT_MEMINFO": str(self.dir / "meminfo"),
               "PREFLIGHT_SYSTEMD": str(self.dir / ("systemd" if systemd else "no-systemd")),
               "PREFLIGHT_INSTALLED": str(self.dir / "current"),
               "PREFLIGHT_IMDS": f"http://127.0.0.1:{self.imds.server_port}" if imds else "http://127.0.0.1:9"}
        return subprocess.run([SHELL, str(INSTALL), *args], capture_output=True, text=True, env=env, timeout=60)

    def test_the_four_supported_combinations_pass(self):
        for system, machine, label in (("al2023", "aarch64", "Amazon Linux 2023 aarch64（dnf）"),
                                       ("al2023", "x86_64", "Amazon Linux 2023 x86_64（dnf）"),
                                       ("ubuntu-22.04", "aarch64", "Ubuntu 22.04 aarch64（apt）"),
                                       ("ubuntu-24.04", "x86_64", "Ubuntu 24.04 x86_64（apt）")):
            with self.subTest(system=system, machine=machine):
                done = self.check(system, machine)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn(label, done.stdout)
                self.assertIn("预检通过", done.stdout)
                self.assertIn("us-east-1", done.stdout)
                self.assertIn("i-0abc123", done.stdout)

    def test_x86_64_is_installed_with_the_arm64_hint_at_the_start_and_the_end(self):
        output = self.check(machine="x86_64").stdout
        self.assertEqual(output.count(RECOMMEND), 2)
        self.assertTrue(output.strip().splitlines()[0].count(RECOMMEND) + output.strip().splitlines()[-1].count(RECOMMEND) == 2,
                        output)
        self.assertNotIn(RECOMMEND, self.check(machine="aarch64").stdout)

    def test_other_systems_stop_with_code_2_and_the_supported_list(self):
        for system in ("al2", "ubuntu-20.04", "debian-12"):
            with self.subTest(system=system):
                done = self.check(system)
                self.assertEqual(done.returncode, 2)
                for supported in ("Amazon Linux 2023", "Ubuntu 22.04", "Ubuntu 24.04"):
                    self.assertIn(supported, done.stderr)
                self.assertNotIn("预检通过", done.stdout)

    def test_another_cpu_stops_with_code_2(self):
        done = self.check(machine="riscv64")
        self.assertEqual(done.returncode, 2)
        self.assertIn("riscv64", done.stderr)

    def test_off_ec2_it_says_why_and_stops(self):
        done = self.check(imds=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("IMDSv2", done.stderr)
        self.assertIn("EC2", done.stderr)

    def test_too_little_disk_or_memory_stops(self):
        for kwargs, words in (({"free_kb": 30 * GIB}, ("磁盘", "30 GiB", "40 GiB")), ({"mem_kb": 4 * GIB}, ("内存", "8 GiB"))):
            with self.subTest(kwargs):
                done = self.check(**kwargs)
                self.assertEqual(done.returncode, 2)
                for word in words:
                    self.assertIn(word, done.stderr)

    def test_an_8_gib_instance_passes_though_the_kernel_keeps_some_of_it(self):
        self.assertEqual(self.check(mem_kb=8_070_000).returncode, 0)   # 8 GiB reports about 7.7 GiB (a 16 GiB host: 15.4)

    def test_root_and_systemd_are_required(self):
        for kwargs, word in (({"uid": "1000"}, "root"), ({"systemd": False}, "systemd")):
            with self.subTest(kwargs):
                done = self.check(**kwargs)
                self.assertEqual(done.returncode, 2)
                self.assertIn(word, done.stderr)

    def test_every_unmet_requirement_is_named_at_once(self):
        done = self.check(uid="1000", free_kb=10 * GIB, mem_kb=2 * GIB)
        self.assertEqual(done.returncode, 2)
        for word in ("root", "磁盘", "内存"):
            self.assertIn(word, done.stderr)

    def test_a_rerun_on_an_installed_host_needs_room_for_one_release_only(self):
        # Review Focus 1: the first install used the disk; running it again only fills in what is missing
        self.assertEqual(self.check(free_kb=20 * GIB).returncode, 2)
        self.assertEqual(self.check(free_kb=20 * GIB, installed=True).returncode, 0)
        self.assertEqual(self.check(free_kb=2 * GIB, installed=True).returncode, 2)

    def test_an_unknown_option_says_how_to_use_it(self):
        done = self.check(args=("--frobnicate",))
        self.assertEqual(done.returncode, 2)
        self.assertIn("--check", done.stderr)

    def test_it_is_posix_sh(self):
        text = INSTALL.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        for bashism in ("[[", "local ", "function ", "$'", "echo -e", "<<<", "pipefail"):
            self.assertNotIn(bashism, text, bashism)
        subprocess.run([SHELL, "-n", str(INSTALL)], check=True)


if __name__ == "__main__":
    unittest.main()
