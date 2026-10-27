"""MVP 0.2.0 P2 Task 5 (design §6.4): Kiro is signed in from the console with a device code. The Worker — the boundary's
own OS user — runs `kiro-cli login --use-device-flow` in a terminal of its own and reads the address and code it shows;
only the boundary's owner sees them, nothing writes them to an audit or a log, and one login runs at a time."""
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock

from octopus.db import dumps
from octopus.worker import Worker
from tests.test_access import AccessCase

FAKE = r'''#!/usr/bin/env python3
import os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:] == ["whoami"]:
    if os.path.exists(os.path.join(here, "logged-in")):
        print("Logged in with IAM Identity Center"); sys.exit(0)
    print("Not logged in"); sys.exit(1)
with open(os.path.join(here, "argv"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
print("\x1b[1mConfirm the following code in the browser\x1b[0m", flush=True)
print("Code: ABCD-EFGH", flush=True)
print("Open this URL: https://device.example/activate?user_code=ABCD-EFGH", flush=True)
for _ in range(400):
    if os.path.exists(os.path.join(here, "approve")):
        open(os.path.join(here, "logged-in"), "w").close()
        print("Logged in successfully", flush=True); sys.exit(0)
    if os.path.exists(os.path.join(here, "deny")):
        print("Device authorization was denied", flush=True); sys.exit(1)
    time.sleep(0.05)
sys.exit(1)
'''


class WorkerLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        cli = self.bin / "kiro-cli"
        cli.write_text(FAKE)
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
        self.node = Worker(Path(self.tmp.name) / "worker", "t" * 40, crew_home=Path(self.tmp.name) / "crew", kiro_cli=str(cli),
                           kiro_license="pro", kiro_identity_provider="https://d-9067e1d68a.awsapps.com/start",
                           kiro_idc_region="us-east-1")
        self.addCleanup(self.node.kiro_login_stop)

    def wait(self, state, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            answer = self.node.kiro_login_status({})
            if answer["state"] == state:
                return answer
            time.sleep(0.05)
        self.fail(f"no {state}: {self.node.kiro_login_status({})}")

    def test_the_device_code_is_read_and_the_login_finishes(self):
        answer = self.node.kiro_login_start({})
        self.assertEqual((answer["state"], answer["code"]), ("waiting", "ABCD-EFGH"))
        self.assertEqual(answer["url"], "https://device.example/activate?user_code=ABCD-EFGH")
        self.assertGreater(answer["expires"], time.time())
        argv = (self.bin / "argv").read_text()
        self.assertIn("login --use-device-flow --license pro --identity-provider https://d-9067e1d68a.awsapps.com/start "
                      "--region us-east-1", argv)
        (self.bin / "approve").touch()
        self.assertEqual(self.wait("done")["state"], "done")
        self.assertEqual(self.node.tool_auth(), "ok")   # the cached login state is read again

    def test_asking_again_while_it_waits_gives_the_same_code_and_one_process(self):
        first = self.node.kiro_login_start({})
        second = self.node.kiro_login_start({})
        self.assertEqual((first["code"], second["code"]), ("ABCD-EFGH", "ABCD-EFGH"))
        self.assertEqual(len((self.bin / "argv").read_text().splitlines()), 1)

    def test_an_existing_login_starts_nothing(self):
        (self.bin / "logged-in").touch()
        self.assertEqual(self.node.kiro_login_start({})["state"], "done")
        self.assertFalse((self.bin / "argv").exists())

    def test_a_refused_login_says_so(self):
        self.node.kiro_login_start({})
        (self.bin / "deny").touch()
        answer = self.wait("failed")
        self.assertNotIn("code", answer)   # nothing to show any more

    def test_the_code_is_not_kept_on_disk(self):
        self.node.kiro_login_start({})
        for path in Path(self.tmp.name, "worker").rglob("*"):
            if path.is_file():
                self.assertNotIn("ABCD-EFGH", path.read_text(errors="replace"))


class PlatformLoginTests(AccessCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('w-bob','bob',?,?,'dedicated')",
                       (self.b["id"], dumps({"url": "https://10.0.0.7:8943", "token": "t" * 40, "capabilities": {}})))
        self.sent = []

    def worker(self, config, path, body=None, timeout=35, **_):
        self.sent.append(path)
        if path == "/kiro/login":
            return {"state": "waiting", "url": "https://device.example/activate", "code": "ABCD-EFGH", "expires": time.time() + 600}
        return {"state": "done"}

    def test_only_the_boundarys_owner_signs_its_kiro_in(self):
        with self.http() as call, mock.patch("octopus.transport.worker_request", self.worker):
            status, answer = call("POST", "/v1/workers/w-bob/kiro-login", self.tokens["bob"], {})
            self.assertEqual((status, answer["code"]), (200, "ABCD-EFGH"))
            self.assertEqual(call("GET", "/v1/workers/w-bob/kiro-login", self.tokens["bob"])[1]["state"], "done")
            for other in ("alice", "eve"):   # an administrator included: the code is the owner's alone (§6.4)
                with self.subTest(other=other):
                    self.assertEqual(call("POST", "/v1/workers/w-bob/kiro-login", self.tokens[other], {})[0], 404)
        with self.db.read() as db:
            details = [r[0] for r in db.execute("SELECT detail FROM audit WHERE action='worker.kiro_login'")]
        self.assertEqual(len(details), 1)
        self.assertNotIn("ABCD-EFGH", details[0])
        self.assertNotIn("device.example", details[0])


if __name__ == "__main__":
    unittest.main()


class SettingsTests(unittest.TestCase):
    def test_the_login_settings_are_checked(self):
        from octopus.config import ConfigError, load
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.toml"
            path.write_text('kiro_license = "pro"\nkiro_identity_provider = "https://d-9067e1d68a.awsapps.com/start"\n'
                            'kiro_idc_region = "us-east-1"\n')
            settings = load(path)
            self.assertEqual((settings.kiro_license, settings.kiro_idc_region), ("pro", "us-east-1"))
            for line in ('kiro_license = "gold"', 'kiro_identity_provider = "http://x"',
                         'kiro_identity_provider = "https://x; rm -rf /"', 'kiro_idc_region = "east"'):
                with self.subTest(line=line):
                    path.write_text(line + "\n")
                    with self.assertRaises(ConfigError):
                        load(path)
