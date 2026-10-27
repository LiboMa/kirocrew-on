"""Review 2026-10-28 (MVP 0.2.0 P2): the scripts that register Workers (boundary.sh — 新建边界 runs it — and install
reruns) act as the administrator with a short-lived token minted on the host by the platform's own user, not with the
initial password in /etc/kirocrew-on/admin-password: an administrator who changed it in the console would otherwise
break both. The bootstrap's logout revokes the token when it is done."""
import io
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from octopus import bootstrap
from octopus.__main__ import main
from octopus.db import Database
from octopus.security import authenticate, create_user


class LocalTokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "platform.toml"
        self.config.write_text(f'data_dir = "{self.tmp.name}/data"\nlog_dir = "{self.tmp.name}/logs"\n')
        self.db = Database(Path(self.tmp.name) / "data")

    def mint(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--config", str(self.config), "local-token", "--label", "boundary.sh bob", *extra])
        return out.getvalue().strip()

    def test_a_short_lived_administrator_token_for_the_scripts_on_this_host(self):
        with self.db.transaction() as db:
            create_user(db, "dev1", "Dev1-password-123")
            admin = create_user(db, "admin", "Admin-password-123", admin=True)
        token = self.mint("--ttl", "600")
        with self.db.read() as db:
            actor = authenticate(db, token)
            row = db.execute("SELECT expires,label,kind FROM tokens WHERE user_id=?", (admin["id"],)).fetchone()
        self.assertEqual(actor["id"], admin["id"])
        self.assertIn("admin", actor["scopes"])
        self.assertLessEqual(row["expires"], time.time() + 601)
        self.assertEqual((row["label"], row["kind"]), ("boundary.sh bob", "api"))   # never a console login

    def test_no_administrator_no_token(self):
        with self.db.transaction() as db:
            create_user(db, "dev1", "Dev1-password-123")
        with self.assertRaises(SystemExit):
            self.mint()


class BootstrapTokenTests(unittest.TestCase):
    def test_the_bootstrap_uses_the_minted_token_instead_of_logging_in(self):
        seen = []

        class Fake:
            def __init__(self, url, token):
                self.token = token

            def request(self, method, path, body=None):
                seen.append((method, path, self.token))
                return {"id": "u1"} if path == "/v1/me" else [{"id": "u1", "username": "bob"}] if path == "/v1/admin/users" \
                    else [] if path == "/v1/workers" else {"ok": True}

        with mock.patch.object(bootstrap, "Client", Fake), mock.patch.dict("os.environ", {"OCTOPUS_ADMIN_TOKEN": "oct_minted"}):
            bootstrap.register_boundary_worker("http://127.0.0.1:8918", "admin", "", None, None, "bob", owner="bob", check_only=True)
        self.assertNotIn("/v1/login", [path for _, path, _ in seen])
        self.assertEqual({token for _, _, token in seen}, {"oct_minted"})
        self.assertEqual(seen[-1][:2], ("POST", "/v1/logout"))   # which revokes it


class InstallerTests(unittest.TestCase):
    def test_an_install_rerun_registers_with_a_minted_token(self):
        text = (Path(__file__).resolve().parents[1] / "deploy/install/al2023.sh").read_text()
        block = text[text.index('log "注册执行边界 Worker'):text.index("# others:begin")]
        self.assertIn('local-token --label "install $BOUNDARY"', block)
        self.assertIn('OCTOPUS_ADMIN_TOKEN="$ADMIN_TOKEN"', block)
        self.assertNotIn("OCTOPUS_ADMIN_PASSWORD", block)


if __name__ == "__main__":
    unittest.main()
