"""D35 §3.3: repository credentials are kept sealed — AES-256-CBC by the host's openssl (the master key read from its
file, never a command line) and an HMAC over the ciphertext, checked before anything is decrypted."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from octopus import vault
from octopus.config import ConfigError, _layer


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = vault.key_file(None, Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sealed_secrets_open_again_and_differ_each_time(self):
        first, second = vault.seal(self.key, "github_pat_11ABCDEF"), vault.seal(self.key, "github_pat_11ABCDEF")
        self.assertNotEqual(first, second)   # a fresh salt each time
        self.assertTrue(first.startswith("v1."))
        self.assertNotIn("github_pat_11ABCDEF", first)
        self.assertEqual(vault.open_(self.key, first), "github_pat_11ABCDEF")
        self.assertEqual(vault.open_(self.key, vault.seal(self.key, "glpat-中文 ünïcode")), "glpat-中文 ünïcode")

    def test_a_tampered_secret_or_another_key_never_opens(self):
        sealed = vault.seal(self.key, "glpat-secret-value")
        version, body, mac = sealed.split(".")
        flip = lambda text: text[:5] + ("A" if text[5] != "A" else "B") + text[6:]
        for broken in (f"{version}.{flip(body)}.{mac}", f"{version}.{body}.{flip(mac)}", "v0." + body + "." + mac, "junk"):
            with self.assertRaises(vault.VaultError):
                vault.open_(self.key, broken)
        (Path(self.tmp.name) / "other").mkdir()
        other = vault.key_file(None, Path(self.tmp.name) / "other")
        with self.assertRaises(vault.VaultError) as caught:
            vault.open_(other, sealed)
        self.assertNotIn("glpat-secret-value", str(caught.exception))

    def test_the_key_never_reaches_a_command_line(self):
        key_text = self.key.read_text().strip()
        real, seen = subprocess.run, []

        def watch(args, *rest, **options):
            seen.append((list(args), options.get("input")))
            return real(args, *rest, **options)
        with patch("octopus.vault.subprocess.run", side_effect=watch):
            vault.open_(self.key, vault.seal(self.key, "plain-secret-1234"))
        self.assertEqual(len(seen), 2)
        for argv, _ in seen:
            self.assertFalse(any(key_text in arg or "plain-secret-1234" in arg for arg in argv), argv)
            self.assertIn("-pass", argv)
            self.assertIn("file:" + str(self.key), argv)
        self.assertEqual(seen[0][1], b"plain-secret-1234")   # the secret only through stdin

    def test_an_unconfigured_key_is_created_once_and_private(self):
        mode = stat.S_IMODE(os.stat(self.key).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertGreaterEqual(len(self.key.read_text().strip()), 40)
        self.assertEqual(vault.key_file(None, Path(self.tmp.name)).read_text(), self.key.read_text())
        configured = Path(self.tmp.name) / "configured.key"
        configured.write_text("k" * 44)
        self.assertEqual(vault.key_file(configured, Path(self.tmp.name)), configured)

    def test_a_configured_key_that_is_missing_or_short_fails_closed(self):
        with self.assertRaises(vault.VaultError):
            vault.key_file(Path(self.tmp.name) / "absent.key", Path(self.tmp.name))
        short = Path(self.tmp.name) / "short.key"
        short.write_text("tiny")
        with self.assertRaises(vault.VaultError):
            vault.key_file(short, Path(self.tmp.name))

    def test_hint_and_the_setting(self):
        self.assertEqual(vault.hint("github_pat_abcd1a2b"), "…1a2b")
        self.assertEqual(_layer({"credentials_key_file": "/etc/kirocrew-on/credentials.key"}, Path("/"))["credentials_key_file"],
                         Path("/etc/kirocrew-on/credentials.key"))
        with self.assertRaises(ConfigError):
            _layer({"credentials_key": "x"}, Path("/"))
        self.assertEqual(_layer({"credentials_private_hosts": ["gitlab.corp.example", "10.0.0.5:8443"]}, Path("/")),
                         {"credentials_private_hosts": ("gitlab.corp.example", "10.0.0.5:8443")})
        with self.assertRaises(ConfigError):
            _layer({"credentials_private_hosts": ["https://x"]}, Path("/"))


if __name__ == "__main__":
    unittest.main()
