"""MVP 0.2.0 P2 Task 6 (design §4.2.5, S30): 「新建边界」 in the console adds a boundary on this host. The API runs with
NoNewPrivileges, so it never becomes root: it checks the owner and the name, then leaves a request — a name and a user
name — in its own directory. A root service started by a systemd path unit takes each request, checks both again with
boundary.sh's own patterns and runs boundary.sh with them, nothing else; its progress goes to a root-owned directory the
API reads."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_access import AccessCase

ROOT = Path(__file__).resolve().parents[1]
PROCESSOR = ROOT / "deploy/install/boundary-provision.py"
FAKE_BOUNDARY = '''#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/argv"
echo "[00:00:01] 边界 $1：OS 用户与目录"
echo "worker token tok_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
exit "$(cat "$(dirname "$0")/exit" 2>/dev/null || echo 0)"   # boundary.sh runs with a clean environment
'''


class ProvisionApiTests(AccessCase):
    def setUp(self):
        super().setUp()
        from octopus.provision import Provisions
        self.spool = tempfile.TemporaryDirectory()   # AccessCase keeps its database in self.tmp
        self.addCleanup(self.spool.cleanup)
        self.requests, self.results = Path(self.spool.name, "requests"), Path(self.spool.name, "results")
        self.results.mkdir()
        self.provisions = Provisions(self.requests, self.results)

    def http_with(self, provisions=True):
        import tests.test_access as access
        made = access.server
        patcher = mock.patch.object(access, "server", lambda db, port: made(
            db, port, provisioning=self.provisions if provisions else None))
        patcher.start()
        self.addCleanup(patcher.stop)
        context = self.http()
        call = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return call

    def pending(self):
        return sorted(p.name for p in self.requests.glob("*.json")) if self.requests.exists() else []

    def test_an_administrator_at_the_console_leaves_a_request_with_a_checked_name_and_owner(self):
        call = self.http_with()
        status, answer = call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"], {"owner_id": self.b["id"]})
        self.assertEqual(status, 202, answer)
        self.assertEqual((answer["name"], answer["owner"], answer["state"]), ("bob", "bob", "queued"))
        [request] = self.requests.glob("*.json")
        self.assertEqual(json.loads(request.read_text()), {"id": answer["id"], "name": "bob", "owner": "bob"})
        self.assertEqual(stat.S_IMODE(request.stat().st_mode), 0o600)
        status, listing = call("GET", "/v1/admin/boundaries/provision", self.tokens["alice-console"])
        self.assertEqual((status, listing["available"]), (200, True))
        self.assertEqual([(i["id"], i["state"]) for i in listing["items"]], [(answer["id"], "queued")])
        with self.db.read() as db:
            detail = db.execute("SELECT detail FROM audit WHERE action='boundary.provision'").fetchone()[0]
        self.assertEqual(json.loads(detail), {"name": "bob", "owner": "bob"})

    def test_names_and_owners_that_are_not_plain_are_refused_before_any_request(self):
        call = self.http_with()
        for body in ({"owner_id": self.b["id"], "name": "x; rm -rf /"}, {"owner_id": self.b["id"], "name": "$(id)"},
                     {"owner_id": self.b["id"], "name": "personal"}, {"owner_id": self.b["id"], "name": "Bob"},
                     {"owner_id": self.b["id"], "name": "a" * 21}, {"owner_id": self.b["id"], "extra": 1}):
            with self.subTest(body):
                self.assertEqual(call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"], body)[0], 400)
        self.assertEqual(call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"],
                              {"owner_id": "user-missing"})[0], 404)
        self.assertEqual(self.pending(), [])

    def test_a_user_name_that_is_not_a_boundary_name_needs_one_given(self):
        with self.db.transaction() as db:
            db.execute("UPDATE users SET username='Bob.Smith_2' WHERE id=?", (self.b["id"],))
        call = self.http_with()
        status, answer = call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"], {"owner_id": self.b["id"]})
        self.assertEqual((status, answer["name"], answer["owner"]), (202, "bob-smith-2", "bob.smith_2"))

    def test_only_an_administrator_at_the_console(self):
        call = self.http_with()
        self.assertEqual(call("POST", "/v1/admin/boundaries/provision", self.tokens["bob"], {"owner_id": self.b["id"]})[0], 403)
        status, answer = call("POST", "/v1/admin/boundaries/provision", self.tokens["alice"], {"owner_id": self.b["id"]})
        self.assertEqual((status, answer.get("code")), (403, "console_login_required"))   # an Agent's or API token: no
        self.assertEqual(call("GET", "/v1/admin/boundaries/provision", self.tokens["bob"])[0], 403)
        self.assertEqual(self.pending(), [])

    def test_a_host_without_the_root_service_says_so(self):
        call = self.http_with(provisions=False)
        status, answer = call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"], {"owner_id": self.b["id"]})
        self.assertEqual((status, answer["code"]), (409, "provisioning_unavailable"))
        self.assertEqual(call("GET", "/v1/admin/boundaries/provision", self.tokens["alice-console"])[1],
                         {"available": False, "items": []})

    def test_one_own_environment_per_person_and_one_request_per_name(self):
        call = self.http_with()
        self.assertEqual(call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"],
                              {"owner_id": self.b["id"]})[0], 202)
        status, answer = call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"], {"owner_id": self.b["id"]})
        self.assertEqual((status, answer["code"]), (409, "provision_in_progress"))
        with self.db.transaction() as db:
            db.execute("INSERT INTO workers(id,name,owner_id,config,scope) VALUES('w-own','bobs','%s','{}','dedicated')" % self.b["id"])
        for path in self.requests.glob("*.json"):
            path.unlink()
        status, answer = call("POST", "/v1/admin/boundaries/provision", self.tokens["alice-console"], {"owner_id": self.b["id"]})
        self.assertEqual((status, answer["code"]), (409, "has_own_environment"))

    def test_progress_comes_from_the_root_services_results(self):
        (self.results / "prv-00000000000000aa.json").write_text(json.dumps(
            {"id": "prv-00000000000000aa", "name": "bob", "owner": "bob", "state": "done", "exit": 0, "started": 1.0,
             "finished": 2.0}))
        (self.results / "prv-00000000000000aa.log").write_text("".join(f"[00:00:{i:02}] 第 {i} 步\n" for i in range(150)))
        call = self.http_with()
        [item] = call("GET", "/v1/admin/boundaries/provision", self.tokens["alice-console"])[1]["items"]
        self.assertEqual((item["state"], item["name"]), ("done", "bob"))
        self.assertEqual(len(item["log"]), 100)
        self.assertEqual(item["log"][-1], "[00:00:149] 第 149 步")
        self.assertIn("kirocrew-on single native bob", item["next"])   # a public native entry is the admin's to open


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.requests, self.results = self.dir / "requests", self.dir / "results"
        self.requests.mkdir()
        self.results.mkdir()
        self.script = self.dir / "boundary.sh"
        self.script.write_text(FAKE_BOUNDARY)
        self.script.chmod(0o755)

    def request(self, rid, body, name=None):
        path = self.requests / (name or f"{rid}.json")
        path.write_text(json.dumps(body))
        return path

    def process(self, uid=None, **env):
        return subprocess.run([sys.executable, "-I", str(PROCESSOR), "--requests", str(self.requests), "--results",
                               str(self.results), "--script", str(self.script), "--owner-uid", str(uid or os.getuid())],
                              capture_output=True, text=True, env={**os.environ, **env}, timeout=60)

    def result(self, rid):
        return json.loads((self.results / f"{rid}.json").read_text())

    def argv(self):
        path = self.dir / "argv"
        return path.read_text().splitlines() if path.exists() else []

    def test_a_request_runs_boundary_sh_with_its_name_and_owner_only(self):
        self.request("prv-0000000000000001", {"id": "prv-0000000000000001", "name": "bob", "owner": "bob"})
        done = self.process()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.argv(), ["bob --owner bob"])
        result = self.result("prv-0000000000000001")
        self.assertEqual((result["state"], result["exit"], result["name"], result["owner"]), ("done", 0, "bob", "bob"))
        log = (self.results / "prv-0000000000000001.log").read_text()
        self.assertIn("OS 用户与目录", log)
        self.assertNotIn("tok_abcdefghijklmnopqrstuvwxyz0123456789ABCD", log)   # nothing token-like reaches the console
        self.assertEqual(list(self.requests.iterdir()), [])

    def test_a_failure_is_recorded_with_its_exit_code(self):
        self.request("prv-0000000000000002", {"id": "prv-0000000000000002", "name": "carol", "owner": "carol"})
        (self.dir / "exit").write_text("3")
        self.process()
        self.assertEqual((self.result("prv-0000000000000002")["state"], self.result("prv-0000000000000002")["exit"]),
                         ("failed", 3))

    def test_requests_are_checked_again_on_the_root_side(self):
        for rid, body in (("prv-0000000000000003", {"name": "x; reboot", "owner": "bob"}),
                          ("prv-0000000000000004", {"name": "bob", "owner": "bob --memory 99"}),
                          ("prv-0000000000000005", {"name": "personal", "owner": "bob"}),
                          ("prv-0000000000000006", {"name": ["bob"], "owner": "bob"})):
            self.request(rid, body)
        self.process()
        self.assertEqual(self.argv(), [])
        for rid in ("prv-0000000000000003", "prv-0000000000000004", "prv-0000000000000005", "prv-0000000000000006"):
            self.assertEqual(self.result(rid)["state"], "failed")

    def test_links_foreign_files_and_odd_names_are_never_followed(self):
        secret = self.dir / "secret.json"
        secret.write_text(json.dumps({"name": "eve", "owner": "eve"}))
        (self.requests / "prv-0000000000000007.json").symlink_to(secret)
        self.request(None, {"name": "dave", "owner": "dave"}, name="../../escape.json".replace("/", "_"))
        self.request("prv-0000000000000008", {"name": "frank", "owner": "frank"})
        self.process(uid=os.getuid() + 1)   # the requests do not belong to the API's user
        self.assertEqual(self.argv(), [])
        self.process()
        self.assertEqual(self.argv(), [])   # the link was dropped unread, the foreign one too
        self.assertTrue(secret.exists())
        self.assertFalse((self.results / "prv-0000000000000007.json").exists())


class InstallTests(unittest.TestCase):
    def test_the_root_side_is_a_path_unit_and_a_oneshot_service_installed_on_the_single_node_edition_only(self):
        path = (ROOT / "deploy/systemd/octopus-boundary-provision.path").read_text()
        self.assertIn("DirectoryNotEmpty=/var/lib/octopus/platform/boundary-requests", path)
        service = (ROOT / "deploy/systemd/octopus-boundary-provision.service").read_text()
        self.assertIn("Type=oneshot", service)
        self.assertIn("ExecStart=/opt/kirocrew-on/venv/bin/python -I /opt/kirocrew-on/current/deploy/install/boundary-provision.py",
                      service)
        api = (ROOT / "deploy/systemd/octopus-api.service").read_text()
        self.assertIn("NoNewPrivileges=yes", api)   # the API itself never becomes root
        installer = (ROOT / "deploy/install/al2023.sh").read_text()
        block = installer[installer.index('if [ -n "${SINGLE_NODE:-}" ]'):]
        for line in ("install -d -m 0700 -o octopus -g octopus /var/lib/octopus/platform/boundary-requests",
                     "install -d -m 2750 -o root -g octopus /var/lib/octopus/boundary-provision",
                     "systemctl enable --now octopus-boundary-provision.path",
                     "toml_set /etc/kirocrew-on/platform.toml boundary_provisioning true"):
            self.assertIn(line, block)
        self.assertIn("SINGLE_NODE=1", (ROOT / "deploy/install/install.sh").read_text())
        self.assertNotIn("sudoers", installer)

    def test_the_boundary_script_points_owners_at_the_console_kiro_login(self):
        self.assertIn("「我的设置 › 执行环境」", (ROOT / "deploy/install/boundary.sh").read_text().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
