"""D57 §3.2–3.3: what looks like a credential, and how it is shown — only its first four and last two characters, with
its kind, line and a hash; the text itself nowhere. Every sample is put together while the test runs (from fragments
and generated characters), so no complete credential is written in this file."""
import base64
import difflib
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import unittest
from urllib.parse import quote

from octopus import checks, sources


def chars(seed, n, alphabet="url"):
    """``n`` made-up characters, the same for the same seed: URL-safe base64, standard base64, letters and digits, or
    upper-case letters and digits."""
    out, block = "", seed.encode()
    while len(out) < n:
        block = hashlib.sha512(block).digest()
        text = base64.urlsafe_b64encode(block).decode() if alphabet == "url" else base64.b64encode(block).decode()
        if alphabet in ("alnum", "upper"):
            text = "".join(c for c in text if c.isalnum())
        if alphabet == "upper":
            text = "".join(c for c in text.upper() if c.isalnum())
        out += text.rstrip("=")
    return out[:n]


def body_lines(seed, size):
    """``size`` bytes of a made-up key body as 64-character base64 lines, every line different."""
    return "\n".join(chars(f"{seed}{n}", 64, "std") for n in range(size // 64))


def private_key(kind="RSA", size=1024):
    lines = body_lines(kind or "pkcs8", size)
    label = (kind + " " if kind else "") + "PRIVATE" + " KEY"
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----", lines


ARMOUR = "Version: GnuPG v2\nComment: Alice Example <alice@example.com> (work)\n"


def pgp_key(armour=ARMOUR, size=640, end=True):
    lines = body_lines("pgp", size)
    label = "PGP " + "PRIVATE" + " KEY BLOCK"
    tail = f"\n=Ab3d\n-----END {label}-----\n" if end else "\n"
    return f"-----BEGIN {label}-----\n{armour}\n{lines}{tail}", lines


PKCS8 = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ"   # how every 2048-bit PKCS#8 RSA key's body begins


def pkcs8_key(seed, indent=0):
    """A made-up PKCS#8 key, each line indented: its first line begins as a real one does, so two of them compared
    share their first 80 or so characters (and pytest skips them)."""
    lines = body_lines(seed, 1600).splitlines()
    lines[0] = PKCS8 + lines[0][len(PKCS8):]
    label = "PRIVATE" + " KEY"
    framed = [f"-----BEGIN {label}-----", *lines, f"-----END {label}-----"]
    return "".join(" " * indent + line + "\n" for line in framed), lines


def pytest_ci(left, right, node="tests/test_k.py::test_key"):
    """What pytest 9.1 prints of a failed ``assert left == right`` of two strings in CI mode (nothing truncated):
    the failure's lines, and its line in the short test summary, which repeats the diff after two spaces. Its
    _diff_text (verbosity 0) and saferepr's ellipsis, as pytest 9.1.1 prints them byte for byte."""
    def ellipsized(text, size=30):
        i = max(0, (size - 3) // 2)
        return text if len(text) <= size else text[:i] + "..." + text[len(text) - max(0, size - 3 - i):]
    header = f"AssertionError: assert {ellipsized(repr(left))} == {ellipsized(repr(right))}"
    explanation, i = [], 0
    for i in range(min(len(left), len(right))):
        if left[i] != right[i]:
            break
    if i > 42:
        i -= 10
        explanation.append(f"Skipping {i} identical leading characters in diff, use -v to show")
        left, right = left[i:], right[i:]
    if len(left) == len(right):
        for i in range(len(left)):
            if left[-i] != right[-i]:
                break
        if i > 42:
            i -= 10
            explanation.append(f"Skipping {i} identical trailing characters in diff, use -v to show")
            left, right = left[:-i], right[:-i]
    explanation += [line.strip("\n") for line in difflib.ndiff(right.splitlines(True), left.splitlines(True))]
    lines = [header, "  ", *("  " + line for line in explanation)]
    return "".join("E       " + line + "\n" for line in lines), f"FAILED {node} - " + "\n".join(lines) + "\n"


AWS_SECRET = chars("aws-secret", 40, "std").replace("=", "A")
LABEL = "aws_secret_" + "access_key"
SESSION = chars("aws-session", 356, "std")
AKIA = "AKIA" + chars("glue", 16, "upper")
ANT = "sk-" + "ant-" + "api03-" + chars("ctx", 93) + "AA"
OPENAI = "sk-" + chars("openai-ctx", 48, "alnum")
PROJECT = "sk-" + "proj-" + chars("project-ctx", 120)
BOT = "xox" + "b-" + "1234567890123-" + "1234567890123-" + chars("bot-ctx", 24, "alnum")
OCT = "oct" + "_" + chars("oct-ctx", 43)
# (kind, the credential, the text it is found in)
SAMPLES = [
    ("github", "github_pat_" + "11" + chars("fine", 20, "alnum") + "_" + chars("grained", 59, "alnum"), "token: {}"),
    ("github", "ghp" + "_" + chars("classic", 36, "alnum"), "GH_TOKEN={}"),
    ("github", "gho" + "_" + chars("oauth", 36, "alnum"), "'{}'"),
    ("github", "ghs" + "_" + chars("app", 36, "alnum"), "x-access-token:{}@github.com"),
    ("gitlab", "glpat" + "-" + chars("gitlab", 20), "PRIVATE_TOKEN = {}"),
    ("aws_access_key", "AKIA" + chars("akia", 16, "upper"), "aws_access_key_id = {}"),
    ("aws_access_key", "ASIA" + chars("asia", 16, "upper"), '"AccessKeyId": "{}"'),
    ("aws_secret_key", AWS_SECRET, LABEL + " = {}"),
    ("aws_secret_key", AWS_SECRET, "export " + LABEL.upper() + '="{}"'),
    ("aws_secret_key", AWS_SECRET, '{{"' + LABEL + '": "{}"}}'),
    ("aws_secret_key", AWS_SECRET, '"Secret' + 'AccessKey": "{}"'),   # sts assume-role, iam create-access-key
    ("aws_secret_key", AWS_SECRET, "aws_secret_" + "key = {}"),
    ("aws_secret_key", AWS_SECRET, "secret_" + "access_key: {}"),
    ("aws_secret_key", AWS_SECRET, "aws configure set " + LABEL + " {}"),
    ("aws_secret_key", AWS_SECRET, 'provider "aws" {{\n  secret_' + 'key = "{}"\n}}'),   # Terraform
    ("aws_session_token", SESSION, '"Session' + 'Token": "{}"'),
    ("aws_session_token", SESSION, "aws_session_" + "token = {}"),
    ("aws_session_token", SESSION, "export AWS_SESSION_" + "TOKEN={}"),
    ("aws_session_token", SESSION, "aws_security_" + "token = {}"),   # boto
    ("aws_session_token", quote(SESSION, safe=""),
     "https://b.s3.amazonaws.com/k?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Security-" + "Token={}&X-Amz-Signature=ab12"),
    ("anthropic", "sk-" + "ant-" + "api03-" + chars("anthropic", 93) + "AA", "ANTHROPIC_API_KEY={}"),
    ("anthropic", "sk-" + "ant-" + "a" * 90, "x-api-key: {}"),
    ("openai", "sk-" + chars("openai-legacy", 48, "alnum"), "Authorization: Bearer {}"),
    ("openai", "sk-" + "proj-" + chars("openai-project", 156), "OPENAI_API_KEY={}"),
    ("slack", "xox" + "b-" + "1234567890123-" + "1234567890123-" + chars("slack-bot", 24, "alnum"), "SLACK={}"),
    ("slack", "xox" + "p-" + "1234567890-" + "1234567890-" + "1234567890123-" + chars("slack-user", 32, "alnum"),
     "token {}"),
    ("slack", "xa" + "pp-" + "1-" + "A0123456789-" + "1234567890123-" + chars("slack-app", 64, "alnum"), "{}"),
    ("slack", "xox" + "e-" + "1-" + chars("slack-refresh", 140, "alnum"), "refresh={}"),
    ("slack", "xox" + "e.xoxp-" + "1-" + chars("slack-rotating", 140, "alnum"), "access={}"),
    ("octopus_token", "oct" + "_" + secrets.token_urlsafe(32), "OCTOPUS_TOKEN={}"),   # as create_token mints it
    ("octopus_token", "oct" + "_" + chars("platform", 43), "Bearer {}"),
    ("private_key", private_key("RSA")[0], "key:\n{}\n"),
    ("private_key", private_key("OPENSSH")[0], "{}"),
    ("private_key", private_key("")[0], "{}"),
]


def found(text, extra=()):
    return sources.find_secrets(text.encode(), extra)


class SecretRuleTests(unittest.TestCase):
    def test_each_kind_is_found_with_its_kind(self):
        kinds = {kind for kind, _ in sources.SECRET_RULES}
        self.assertEqual(kinds, {"github", "gitlab", "aws_access_key", "aws_secret_key", "aws_session_token",
                                 "anthropic", "openai", "slack", "octopus_token", "private_key"})
        self.assertEqual({kind for kind, _, _ in SAMPLES}, kinds)
        for kind, secret, context in SAMPLES:
            with self.subTest(kind=kind, context=context):
                data = context.format(secret).encode()
                hits = sources.find_secrets(data)
                self.assertEqual([h["kind"] for h in hits], [kind])   # one span is reported once, as its own kind
                self.assertEqual(data[hits[0]["start"]:hits[0]["end"]], secret.encode())

    def test_anthropic_is_told_from_openai_and_both_are_found(self):
        anthropic = "sk-" + "ant-" + "api03-" + chars("a1", 93) + "AA"
        openai = "sk-" + chars("o1", 48, "alnum")
        hits = found(f"{anthropic}\n{openai}\n")
        self.assertEqual([h["kind"] for h in hits], ["anthropic", "openai"])
        self.assertEqual([h["line"] for h in hits], [1, 2])

    def assert_masked(self, text, *secrets_, kinds=None, tail=False):
        """Every one of ``secrets_`` is found in ``text`` and masked so that no ten characters of it remain."""
        hits = sources.find_secrets(text.encode())
        if kinds is not None:
            self.assertEqual([h["kind"] for h in hits], kinds, text[:120])
        shown = checks.masked_tail(text.encode())[0] if tail else sources.mask_text(text)[0]
        for secret in secrets_:
            left = [secret[i:i + 10] for i in range(len(secret) - 9) if secret[i:i + 10] in shown]
            self.assertEqual(left, [], shown[:300])

    def test_keys_glued_to_or_encoded_in_their_context_are_found(self):
        cases = [
            ("%22" + AKIA + "%22", [AKIA], ["aws_access_key"]),
            ("X-Amz-Credential%3D" + AKIA + "%2F20261022%2Fus-east-1%2Fs3%2Faws4_request", [AKIA], ["aws_access_key"]),
            ("%2F" + AKIA, [AKIA], ["aws_access_key"]),
            ("1" + AKIA, [AKIA], ["aws_access_key"]),
            (AKIA + "9", [AKIA], ["aws_access_key"]),
            ("AKIA" + chars("long", 20, "upper"), ["AKIA" + chars("long", 20, "upper")[:16]], ["aws_access_key"]),
            # the IAM credentials CSV, JSON-escaped; and a key ID glued to its secret: the secret goes with it
            ('"Access key ID,Secret access key\\n' + AKIA + "," + AWS_SECRET + '\\n"', [AKIA, AWS_SECRET],
             ["aws_access_key"]),
            (AKIA + AWS_SECRET, [AKIA, AWS_SECRET], ["aws_access_key"]),
            ("\\n" + ANT, [ANT], ["anthropic"]), ("Bearer%20" + ANT, [ANT], ["anthropic"]),
            ("%22" + ANT + "%22", [ANT], ["anthropic"]), ("mask-" + ANT[3:], [ANT[3:]], ["anthropic"]),
            ("\\n" + OPENAI, [OPENAI], ["openai"]), ("\\t" + OPENAI, [OPENAI], ["openai"]),
            ("Bearer%20" + OPENAI, [OPENAI], ["openai"]), ("%22" + OPENAI + "%22", [OPENAI], ["openai"]),
            ("_" + OPENAI, [OPENAI], ["openai"]),
            ("\x1b[32m" + OPENAI + "\x1b[0m", [OPENAI], ["openai"]),   # coloured
            ("\x1b[01;31m\x1b[K" + OPENAI + "\x1b[m\x1b[K", [OPENAI], ["openai"]),   # grep --color
            ("\\x22" + OPENAI + "\\x22", [OPENAI], ["openai"]), ("\\u0022" + OPENAI + "\\u0022", [OPENAI], ["openai"]),
            ("disk-" + OPENAI[3:], [OPENAI[3:]], ["openai"]), ("risk-" + OPENAI[3:], [OPENAI[3:]], ["openai"]),
            ("a" + PROJECT, [PROJECT], ["openai"]), ("Bearer%20" + PROJECT, [PROJECT], ["openai"]),
            ("%22" + BOT + "%22", [BOT], ["slack"]), ("\\n" + BOT, [BOT], ["slack"]), ("x" + BOT, [BOT], ["slack"]),
            ("\\n" + OCT, [OCT], ["octopus_token"]), ("Bearer%20" + OCT, [OCT], ["octopus_token"]),
            ("%22" + OCT + "%22", [OCT], ["octopus_token"]), ("d" + OCT, [OCT], ["octopus_token"]),
        ]
        for text, secrets_, kinds in cases:
            with self.subTest(text=text[:60]):
                self.assert_masked(text, *secrets_, kinds=kinds)

    def test_aws_credentials_as_the_cli_prints_them(self):
        access = "ASIA" + chars("sts", 16, "upper")
        printed = json.dumps({"Credentials": {"AccessKeyId": access, "SecretAccessKey": AWS_SECRET,
                                              "SessionToken": SESSION, "Expiration": "2026-10-22T10:00:00Z"}}, indent=4)
        kinds = ["aws_access_key", "aws_secret_key", "aws_session_token"]
        self.assert_masked(printed, access, AWS_SECRET, SESSION, kinds=kinds)
        self.assert_masked(json.dumps(printed), access, AWS_SECRET, SESSION, kinds=kinds)   # escaped once more

    def test_overlapping_matches_are_masked_as_one(self):
        # an AWS Secret Key that happens to start like a key ID: one finding, none of it shown
        value = "AKIA" + chars("overlap", 16, "upper") + chars("overlap-rest", 20, "std")
        text = LABEL + "=" + value
        hits = found(text)
        self.assertEqual([(h["kind"], text[h["start"]:h["end"]]) for h in hits], [("aws_secret_key", value)])
        self.assertEqual(hits[0]["hash"], hashlib.sha256(value.encode()).hexdigest())
        self.assert_masked(text, value)
        # a PEM line cut short and glued to a token: the union is masked, so no part of the token after the key's end
        token = "ghp" + "_" + chars("glued", 36, "alnum")
        text = "-----BEGIN RSA PRIVATE" + " KEY-----\n" + chars("pem-line", 64, "std") + token + " end"
        hits = found(text)
        self.assertEqual([h["kind"] for h in hits], ["private_key"])
        self.assertTrue(text[hits[0]["start"]:hits[0]["end"]].endswith(token))
        self.assert_masked(text, token)
        self.assertTrue(sources.mask_text(text)[0].endswith("…" + token[-2:] + " end"))

    def test_escaped_and_armoured_private_keys_are_masked_whole(self):
        pem, body = private_key("")
        service_account = json.dumps({"type": "service_account", "private_key": pem + "\n", "client_email": "a@b.c"})
        self.assertIn("\\n", service_account)   # the key is on one line, its line breaks written as \n
        lines = body.splitlines()
        self.assert_masked(service_account, *lines, kinds=["private_key"])
        self.assertIn('"private_key": "----…--\\n"', sources.mask_text(service_account)[0])
        php = service_account.replace("/", "\\/")   # PHP's json_encode also escapes "/"
        self.assert_masked(php, *[line.replace("/", "\\/") for line in lines], kinds=["private_key"])
        cut_short = service_account[:service_account.index("-----END")]   # no END: the base64 after the header
        self.assert_masked(cut_short, *lines, kinds=["private_key"])
        # across the cut of a check's output tail
        raw = "y" * 5000 + service_account + "\n" + "z" * (checks.TAIL - 600)
        self.assertLess(len(raw) - checks.TAIL, raw.index("-----END"))
        self.assert_masked(raw, *lines, tail=True)
        armoured, body = pgp_key()
        self.assert_masked("key:\n" + armoured, *body.splitlines(), kinds=["private_key"])
        self.assertEqual(sources.mask_text(armoured)[0], "----…--\n")
        self.assert_masked(json.dumps(armoured), *body.splitlines(), kinds=["private_key"])

    def test_private_key_bodies_escaped_twice_or_with_escaped_armour_are_masked(self):
        pem, body = private_key("RSA")
        lines = body.splitlines()
        twice = json.dumps(json.dumps({"private_key": pem}))   # a JSON log line carrying a JSON string
        self.assertIn("\\\\n", twice)
        self.assert_masked(twice, *lines, kinds=["private_key"])
        self.assert_masked(twice[:twice.index("-----END")], *lines, kinds=["private_key"])   # and with no END
        armour = ('Version: GnuPG v2\nComment: Alice "work" key\nComment: https://example.com/?a=1&b=2#x;\n'
                  "Comment: C:\\Users\\alice\\key.asc\n")
        for end in (True, False):
            armoured, body = pgp_key(armour, end=end)
            lines = body.splitlines()
            escaped = json.dumps(armoured)
            for text in (armoured, escaped, escaped.replace('\\"', "\\u0022"), escaped.replace("/", "\\/"),
                         json.dumps(escaped)):
                with self.subTest(end=end, text=text[:70]):
                    self.assert_masked(text, *lines, kinds=["private_key"])
        armoured, body = pgp_key("Comment:\n", end=False)   # an empty header line, and no END
        self.assert_masked(armoured, *body.splitlines(), kinds=["private_key"])

    def test_windows_paths_in_armour_headers_keep_the_key_masked(self):
        # a header value runs to its own line break: a path's \n or \r (C:\Users\nick, \rob, D:\keys\new) is not one at
        # any escape depth, and a value that ends in a backslash still ends at the real line break
        paths = ["C:\\Users\\nick\\key.asc", "C:\\Users\\rob\\keys\\key.asc", "D:\\keys\\new\\key.asc",
                 "D:\\keys\\new\\nick.asc", "C:\\n\\rob\\x.asc", "\\\\nas\\new\\key.asc", "C:\\Users\\nick", "C:\\keys\\",
                 "C:\\keys\\n", "C:\\keys\\\\", "C:\\Users\\rob\\"]
        for path in paths:
            for end in (True, False):
                armoured, body = pgp_key(f"Version: GnuPG v2\nComment: {path}\nComment: second\n", end=end)
                lines = body.splitlines()
                once, crlf = json.dumps(armoured), armoured.replace("\n", "\r\n")
                forms = {"raw": armoured, "crlf": crlf, "json": once, "json crlf": json.dumps(crlf),
                         "json twice": json.dumps(once), "json three times": json.dumps(json.dumps(once))}
                for form, text in forms.items():
                    text += ' then "shown"'   # what follows the key, on its line too, is not part of it
                    with self.subTest(path=path, end=end, form=form):
                        self.assert_masked(text, *lines, kinds=["private_key"])
                        self.assertTrue(sources.mask_text(text)[0].endswith(' then "shown"'))
                        raw = "y" * 1000 + "\n" + text + "\n" + "z" * (checks.TAIL - len(text) // 2)   # across the cut
                        self.assertLess(raw.index(text), len(raw) - checks.TAIL)
                        self.assertLess(len(raw) - checks.TAIL, raw.index(text) + len(text))
                        self.assert_masked(raw, *lines, tail=True)

    def test_a_header_value_ending_in_a_backslash_ends_at_its_line_break(self):
        # a real line break ends a header value even right after a backslash: the next line's error is still shown
        header = "-----BEGIN PGP PRIVATE" + " KEY BLOCK-----\nComment: C:\\keys\\\n"
        message = "ModuleNotFoundError: No module named 'requests'"
        for text in (header + message + "\n", (header + message + "\n").replace("\n", "\r\n")):
            with self.subTest(crlf="\r" in text):
                tail, found = checks.masked_tail(text.encode())
                self.assertIn(message, tail)
                self.assertEqual([f["kind"] for f in found], ["private_key"])
                self.assertEqual(checks.unrunnable({"command": "c", "exit": 1, "seconds": 0.1, "tail": tail}),
                                 "缺少 Python 模块：requests")

    def test_a_key_cut_short_still_has_its_body_masked(self):
        # no END in view: the lines after the header are masked — lines of letters only, a short last line, CRLF, YAML
        # indentation, JSON once and twice, a line cut short at the end of the text
        key, body = private_key("RSA", 640)
        lines = body.splitlines()
        header, cut_short = key.splitlines()[0], key[:key.index("-----END")]
        letters = "".join(c for c in chars("letters-only", 400, "std") if c.isalpha())[:64]
        texts = {"plain": cut_short, "crlf": cut_short.replace("\n", "\r\n"),
                 "letters and a short last line": "\n".join([header, *lines[:3], letters, lines[3], "AB=="]) + "\n",
                 "yaml": "private_key: |\n" + "".join("  " + line + "\n" for line in cut_short.splitlines()),
                 "json": json.dumps({"private_key": cut_short}), "json twice": json.dumps(json.dumps(cut_short)),
                 "cut mid-line": cut_short + lines[0][:30]}
        for name, text in texts.items():
            with self.subTest(name):
                self.assert_masked(text, *lines, letters, kinds=["private_key"])
                self.assert_masked(text, *lines, letters, tail=True)
        # cut short right before "..." (an ellipsis is no sentence's "."): masked through
        fragment = next(line[:40] for line in lines[2:] if any(c.isdigit() for c in line[:40]))
        text = "\n".join([header, *lines[:2], fragment]) + "...\n"
        self.assert_masked(text, *lines[:2], fragment, kinds=["private_key"])
        self.assert_masked(text, *lines[:2], fragment, tail=True)

    def test_the_lines_before_an_end_whose_begin_is_not_there_are_masked(self):
        # nothing dropped and no BEGIN anywhere: a key's last lines and its END — pytest's "Skipping N identical leading
        # characters in diff", tail key.pem, grep -B, docker compose — are masked with the END line, each line written
        # with the END line's own prefix; what is not one of those lines stays shown
        key, body = private_key("RSA", 1280)
        lines, end = body.splitlines(), key.splitlines()[-1]
        last, cut = lines[-6:], lines[-7][25:]   # a line pytest cut where its skipped characters end
        pgp, pgp_body = pgp_key()
        pgp_end = pgp.splitlines()[-1]
        prefixed = lambda prefix, rows: "".join(prefix + row + "\n" for row in rows)
        cases = {   # name: (before, the key's tail, after, its lines)
            "10 lines and END": ("", "\n".join(lines[-10:]) + "\n" + end, "", lines[-10:]),
            "tail key.pem": ("$ tail -n 7 key.pem\n", prefixed("", last + [end]), "$ echo ok\nok\n", last),
            "pytest skipping": ("E         Skipping 1500 identical leading characters in diff, use -v to show\n",
                                prefixed("E           ", [cut, *last, end]), "\nFAILED tests/test_k.py::test_key\n",
                                [cut, *last]),
            "pytest diff": ("E         - x\n", prefixed("E         + ", last + [end]), "", last),
            "grep -B": ("", prefixed("key.pem-", last) + "key.pem:" + end + "\n", "other.pem:nothing\n", last),
            "grep -n -B": ("", "".join(f"key.pem-{20 + i}-{row}\n" for i, row in enumerate(last))
                           + "key.pem:26:" + end + "\n", "", last),
            "docker compose": ("web-1  | listening on :8080\n", prefixed("web-1  | ", last + [end]), "web-1  | done\n", last),
            "crlf": ("$ cat k\r\n", prefixed("", last + [end]).replace("\n", "\r\n"), "ok\r\n", last),
            "yaml": ("key: |\n", prefixed("  ", last + [end]), "next: 1\n", last),
            "pgp": ("", prefixed("", pgp_body.splitlines()[-4:] + ["=Ab3d", pgp_end]), "", pgp_body.splitlines()[-4:]),
            "json": ('{"log": "pytest says:\\n', json.dumps("\n".join(last + [end]) + "\n")[1:-1], '", "next": "shown"}',
                     last),
            "json twice": ('"{\\"log\\": \\"pytest says:\\\\n',
                           json.dumps(json.dumps("\n".join(last + [end]) + "\n")[1:-1])[1:-1], '\\"}"', last),
        }
        for name, (before, tail_of_key, after, rows) in cases.items():
            text = before + tail_of_key + after
            for how in ("mask_text", "masked_tail"):
                with self.subTest(name, how=how):
                    if how == "mask_text":
                        shown, found = sources.mask_text(text)
                    else:
                        shown, found = checks.masked_tail(text.encode())
                    self.assertEqual([f["kind"] for f in found], ["private_key"], shown[:300])
                    self.assertEqual([row[i:i + 10] for row in rows for i in range(len(row) - 9)
                                      if row[i:i + 10] in shown], [], shown[:300])
                    self.assertTrue(shown.startswith(before) and shown.endswith(after), shown[:300])
        # the END only named (a hint, a grep hit), or lines that are not the key's: nothing is masked
        for text in ["ModuleNotFoundError: No module named 'x'\n… hint: expected " + end + "\n",
                     "Binary file matches\nkey.pem:" + end + "\n",
                     "x = 1\n\n" + end + "\n"]:
            with self.subTest(text[-60:]):
                self.assertEqual(sources.mask_text(text), (text, []))
                self.assertEqual(checks.masked_tail(text.encode()), (text, []))

    def test_a_keys_last_lines_in_a_diff_or_in_a_strings_first_segment_are_masked(self):
        # the same lines when their prefix differs from the END line's only in a diff's marker column (pytest's ndiff
        # of two keys after its "E         ": "- ", "+ ", "? ", "  "; a git hunk with no BEGIN in it: " ", "-", "+", the
        # "+" glued to the END; the key line git heads the hunk with), and when the first of them opens a JSON or repr
        # string (or follows "...")
        key, body = private_key("RSA", 1280)
        lines, end = body.splitlines(), key.splitlines()[-1]
        other, ec_end = body_lines("another key", 640).splitlines()[:6], end.replace("RSA", "EC")
        changed = lines[-1][:20] + ("A" if lines[-1][20] != "A" else "B") + lines[-1][21:]   # ndiff then adds "?" lines

        def pytest(right, left):   # pytest's diff of left == right: ndiff of their lines, each after "E         "
            return "".join("E         " + line.rstrip("\n") + "\n" for line in difflib.ndiff(right, left))

        def hunk(old, new, context):   # a git diff of key.pem whose hunk starts after the key's BEGIN
            return "".join(line + "\n" for line in difflib.unified_diff(
                context + old, context + new, "a/key.pem", "b/key.pem", lineterm=""))
        skipping = "E         Skipping 1500 identical leading characters in diff, use -v to show\n"
        cases = {   # name: (before, the key's tail, after, its lines)
            "pytest, two keys": (skipping, pytest(lines[-6:] + [end], other + [end]), "\nFAILED tests/test_k.py\n",
                                 lines[-6:] + other),
            "pytest, one character changed": (skipping, pytest(lines[-3:] + [end], lines[-3:-1] + [changed, end]), "",
                                              lines[-3:] + [changed]),
            "pytest, the END lines differ": (skipping, pytest(lines[-6:] + [end], other + [ec_end]), "",
                                             lines[-6:] + other),
            "git diff": ("$ git diff\n", hunk(lines[-3:] + [end], other[:3] + [end], lines[-9:-3]), "$ echo ok\n",
                         lines[-9:] + other[:3]),
            "git diff, the END changed": ("", hunk(lines[-3:] + [end], other + [ec_end], lines[-6:-3]), "",
                                          lines[-6:] + other),
            "git diff, the END added": ("", hunk([], lines[-2:] + [end], lines[-6:-2]), "", lines[-5:]),
            # the last line runs into the END (no line break): what is glued to it is the key's, not the prefix
            "docker compose, no line break": ("web-1  | listening on :8080\n",
                                              "".join(f"web-1  | {row}\n" for row in lines[-4:-1])
                                              + f"web-1  | {lines[-1]}{end}\n", "web-1  | done\n", lines[-4:]),
        }
        for name in ("git diff", "git diff, the END changed", "git diff, the END added"):   # git's hunk header shown
            before, tail_of_key, after, rows = cases[name]
            at = tail_of_key.index("\n", tail_of_key.index("@@")) + 1
            cases[name] = (before + tail_of_key[:at], tail_of_key[at:], after, rows)
        before, tail_of_key, after, rows = cases["git diff"]   # git heads the hunk with the last line above it that
        cases["git diff, a key line in the hunk header"] = (   # starts with a letter: in a key, one of its lines
            before[:-1] + " ", lines[-10] + "\n" + tail_of_key, after, [lines[-10], *rows])
        pad = lambda rows, indent: [" " * indent + row for row in rows]
        for indent in (2, 4):   # a key indented in what is compared (a YAML Secret, a Helm value)
            for context in (3, 1):
                text = "".join(line + "\n" for line in difflib.unified_diff(
                    pad(lines[-9:] + [end], indent), pad(lines[-9:-3] + other[:3] + [end], indent), "a/secret.yaml",
                    "b/secret.yaml", n=context, lineterm=""))
                at = text.index("\n", text.index("@@")) + 1
                cases[f"git diff -U{context}, a key indented {indent}"] = (text[:at], text[at:], "",
                                                                         lines[-9:] + other[:3])
        last = lines[-3:]
        cases.update({   # the escaped lines before an END, the first right after a quote or an ellipsis
            "json": ('{"stdout": "', json.dumps("\n".join(last + [end]) + "\n")[1:-1], '"}', last),
            "repr": ("key = '", repr("\n".join(last + [end]) + "\n")[1:-1], "'", last),
            "json twice": ('"{\\"stdout\\": \\"', json.dumps(json.dumps("\n".join(last + [end]))[1:-1])[1:-1], '\\"}"',
                           last),
            "repr cut at an ellipsis": ("E       AssertionError: assert '...", repr("\n".join(
                [lines[-4][30:], *last, end]))[1:-1], "' == ''", [lines[-4][30:], *last]),
        })
        escaped = json.dumps("\n".join(last + [end]))[1:-1]
        cases.update({   # the first of them a value: after a label's "=" or ": " (or, escaped, after a space)
            ".env": ("$ cat .env\nPRIVATE_KEY=", "\n".join(last + [end]) + "\n", "PORT=8080\n", last),
            "a label and a space": ("tls:\n  key: ", "\n".join(last + [end]) + "\n", "", last),
            "docker compose, a label": ("web-1  | PRIVATE_KEY=",
                                        "".join(f"{row}\nweb-1  | " for row in last) + end + "\n", "", last),
            ".env, escaped": ('{"env": "PRIVATE_KEY=', escaped, '", "port": 8080}', last),
            "label and a space, escaped": ('{"log": "key: ', escaped, '"}', last),
            "after a space, escaped": ('{"log": "using the key ', escaped, '"}', last),
            # on lines, after a bare space when what follows is long enough to be no word, or after a label's quote
            "after a space": ("$ deploy\nusing key ", "\n".join(last + [end]) + "\n", "done\n", last),
            ".env, quoted": ("PRIVATE_KEY=\"", "\n".join(last + [end]), '"\n', last),
            "export, quoted": ("export PRIVATE_KEY='", "\n".join(last + [end]), "'\n", last),
            "a label and a quote": ('tls:\n  key: "', "\n".join(last + [end]), '"\n', last),
        })
        for name, (before, tail_of_key, after, rows) in cases.items():
            text = before + tail_of_key + after
            for how in ("mask_text", "masked_tail"):
                with self.subTest(name, how=how):
                    if how == "mask_text":
                        shown, found = sources.mask_text(text)
                    else:
                        shown, found = checks.masked_tail(text.encode())
                    # the lines and their END are one span of the rules above (the marker rule below would take
                    # each line apart)
                    self.assertEqual([f["kind"] for f in found], ["private_key"] * (2 if "END changed" in name else 1),
                                     shown[:300])
                    self.assertEqual([row[i:i + 10] for row in rows for i in range(len(row) - 9)
                                      if row[i:i + 10] in shown], [], shown[:600])
                    self.assertTrue(shown.startswith(before) and shown.endswith(after), shown[:600])
        # what is no key line stays shown, in a diff as anywhere; and a value right before an END with no key line
        # between, or one that is not right above them, is no key's either
        for text in ["E         - see the README\nE           " + end + "\n",
                     "@@ -1,2 +1,2 @@\n-see the README\n+" + end + "\n",
                     "diff --git a/k b/k\n" + end + "\n", "x = '\\n" + end + "'\n",
                     "DEBUG_MODE=1\n" + end + "\n", "PORT: 8080\nsee the README\n" + end + "\n",
                     '{"env": "DEBUG_MODE=1\\n' + end + '"}', '{"log": "key: none\\n\\n' + end + '"}']:
            with self.subTest(text[:30]):
                self.assertEqual(sources.mask_text(text), (text, []))
                self.assertEqual(checks.masked_tail(text.encode()), (text, []))

    def test_next_to_a_private_key_marker_every_long_run_of_base64_is_masked(self):
        # (owner's ruling, 6b round 2) where a text holds a private key's BEGIN or END, every run of 40 or more base64
        # characters in it is masked as private_key, whatever shape its lines have: pytest's diff of two indented keys
        # (its first lines cut where the skipped characters end) and its CI summary that repeats it after two spaces, a
        # git diff of an indented key; display only (verdicts are judged as written), so a SHA next to a key goes too
        longest = lambda text: max([len(run) for run in re.findall(r"[A-Za-z0-9+/=]+", text)] or [0])
        outputs = {}
        for indent in (0, 2, 4):
            (left, lines), (right, others) = pkcs8_key("pytest-a", indent), pkcs8_key("pytest-b", indent)
            failure, summary = pytest_ci(left, right)
            self.assertIn("Skipping", failure)
            text = "=== FAILURES ===\n" + failure + "=== short test summary info ===\n" + summary
            outputs[f"pytest CI, indent {indent}"] = (text, lines[1:] + others[1:])
        with tempfile.TemporaryDirectory() as folder:
            for indent in (2, 4):
                old, lines = pkcs8_key("git-a", indent)
                whole, others = pkcs8_key("git-b", indent)
                tail = "".join(old.splitlines(True)[:-4] + whole.splitlines(True)[-4:])
                for name, new in (("its last lines changed", tail), ("another key", whole)):
                    for context in (3, 1):
                        for side, text in (("a", old), ("b", new)):
                            os.makedirs(os.path.join(folder, side), exist_ok=True)
                            with open(os.path.join(folder, side, "secret.yaml"), "w") as stream:
                                stream.write("stringData:\n  tls.key: |\n" + text + "  tls.crt: abc\n")
                        diff = subprocess.run(
                            ["git", "diff", "--no-index", "--no-color", "--no-ext-diff", f"-U{context}",
                             "a/secret.yaml", "b/secret.yaml"], cwd=folder, capture_output=True, text=True,
                            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}).stdout
                        self.assertIn("-----END", diff)
                        outputs[f"git diff -U{context}, indent {indent}, {name}"] = (diff, lines[1:] + others[1:])
        for name, (text, rows) in outputs.items():
            for how in ("mask_text", "masked_tail"):
                with self.subTest(name, how=how):
                    shown, found = sources.mask_text(text) if how == "mask_text" else checks.masked_tail(text.encode())
                    self.assertEqual({f["kind"] for f in found}, {"private_key"})
                    # what is left of a key line: only the ends pytest cut to fewer than 40 characters
                    self.assertLess(longest(shown), 40, shown[:600])
                    self.assertEqual([row for row in rows if any(row[i:i + 10] in shown for i in range(len(row) - 9))],
                                     [], shown[:600])
        # no marker, nothing masked: ordinary output with git SHAs and digests
        sha = hashlib.sha1(b"a commit").hexdigest()
        plain = (f"commit {sha}\nMerge: {sha[:7]} {sha[-7:]}\n    Bump the parser\n\n$ git rev-parse HEAD\n{sha}\n"
                 f"pip install --require-hashes: --hash=sha256:{hashlib.sha256(b'a wheel').hexdigest()}\n"
                 f"docker pull app@sha256:{hashlib.sha256(b'an image').hexdigest()}\n"
                 f"{base64.b64encode(bytes(48)).decode()}\n")
        self.assertEqual(sources.mask_text(plain), (plain, []))
        self.assertEqual(checks.masked_tail(plain.encode()), (plain, []))
        # a marker anywhere, one only named or before the shown tail: the SHA goes too
        end = "-----END RSA PRIVATE" + " KEY-----"
        named = "hint: expected " + end + "\n" + plain
        shown, found = sources.mask_text(named)
        self.assertNotIn(sha, shown)
        self.assertEqual({f["kind"] for f in found}, {"private_key"})
        steps = lambda first: "".join(f"step {n} ok\n" for n in range(first, first + 500))
        tail = (steps(0) + "key.pem:" + end + "\n" + steps(500) + f"HEAD is {sha}\n").encode()
        self.assertLess(tail.index(b"-----END"), len(tail) - checks.TAIL)   # the marker is in what is cut off
        shown, found = checks.masked_tail(tail, dropped=False)
        self.assertNotIn(sha, shown)
        self.assertEqual(checks.masked_tail(tail.replace(b"-----END", b"-----end"), dropped=False)[0],
                         tail.decode()[-checks.TAIL:])

    def test_a_key_whose_begin_was_cut_off_is_masked_up_to_its_end(self):
        # the check's output keeps only its last 8 KB: a key longer than that tail lost its BEGIN with the head
        output = "".join(f"test {n} ok\n" for n in range(200))   # about 2 KB after the key
        for name, (key, body) in [("pgp 7 KB", pgp_key(size=7040)), ("rsa 8192", private_key("RSA", 6400))]:
            raw = (key + output).encode()[-2 * checks.TAIL:]
            self.assertNotIn(b"-----BEGIN", raw)
            tail, found = checks.masked_tail(raw)
            with self.subTest(name):
                self.assertEqual([f["kind"] for f in found], ["private_key"])
                self.assertEqual(found[0]["line"], 1)
                self.assert_masked(tail, *body.splitlines())
                self.assertTrue(tail.endswith(output[-500:]))   # what came after the key is still shown
        key, body = private_key("RSA", 6400)
        lines = body.splitlines()
        for offset in (-100, -1, 0, 1, 20, 31, 64, 1000, checks.TAIL - 40, checks.TAIL):
            # the key's END ends ``offset`` bytes after the cut (what follows is no run of base64: next to a key's
            # marker, one of 40 characters or more is masked too)
            after = ("x " * checks.TAIL)[:checks.TAIL - offset]
            raw = (key + after).encode()[-2 * checks.TAIL:]
            with self.subTest(offset=offset):
                self.assertEqual(len(raw) - checks.TAIL + offset, raw.index(b"KEY-----", raw.index(b"-----END")) + 8)
                tail, found = checks.masked_tail(raw)
                self.assertEqual([f["kind"] for f in found], ["private_key"] if offset > 0 else [])
                self.assertTrue(all(line[i:i + 10] not in tail for line in lines for i in range(55)), tail[:120])
                self.assertTrue(tail.endswith(after[-100:]) if after else True)

    def test_errors_after_a_key_header_are_not_hidden_from_unrunnable(self):
        # masking must not swallow what tells an environment failure from the check's own (D57 §5): base64 has no ":"
        # or ".", so a first word followed by one is no key line
        header = "-----BEGIN RSA PRIVATE" + " KEY-----\n"
        for message, code, reason in [
                ("ModuleNotFoundError: No module named 'requests'", 1, "缺少 Python 模块：requests"),
                ("env: 'node': No such file or directory", 1, "命令不存在：node"),
                ("Error: Cannot find module 'express'", 1, "缺少 Node 模块：express"),
                ("python3: No module named pytest", 1, "缺少 Python 模块：pytest"),
                ("python3.11: No module named pytest", 1, "缺少 Python 模块：pytest"),
                ("/usr/bin/python3: No module named pytest", 1, "缺少 Python 模块：pytest"),
                ("/usr/bin/env: 'node': No such file or directory", 127, "命令不存在：node"),
                ("/usr/bin/env: ‘node’: No such file or directory", 127, "命令不存在：node"),
                ("/bin/sh: 1: pytest: not found", 127, "命令不存在：pytest"),
                ("/bin/bash: line 1: pytest: command not found", 127, "命令不存在：pytest")]:
            with self.subTest(message):
                tail, found = checks.masked_tail((header + message + "\n").encode())
                self.assertIn(message, tail)
                self.assertEqual([f["kind"] for f in found], ["private_key"])
                self.assertEqual(checks.unrunnable({"command": "c", "exit": code, "seconds": 0.1, "tail": tail}), reason)

    def test_an_end_line_is_taken_for_a_cut_off_key_only_when_its_begin_can_be_gone(self):
        # an END with no BEGIN before it is a key's whose BEGIN was dropped only when the output's head was dropped, or
        # when a BEGIN before the cut is still open; else it is just a line (a hint, grep output) and nothing is masked
        end = "-----END RSA PRIVATE" + " KEY-----"
        texts = ["hint: expected " + end + "\nModuleNotFoundError: No module named 'x'\n", "key.pem:" + end + "\n"]
        for size in (0, 1000, 4000, 5000, 7000):   # nothing cut (up to 4 KB), or all of the output kept (4-8 KB)
            for text in texts:
                raw = (("x " * size)[:size] + "\n" + text).encode()   # no run of base64 (masked next to a marker)
                with self.subTest(size=size, text=text[:12]):
                    self.assertLess(len(raw), 2 * checks.TAIL)
                    tail, found = checks.masked_tail(raw)
                    self.assertEqual((found, tail.encode()), ([], raw[-checks.TAIL:]))
        for text in texts:   # the head dropped (the last 8 KB kept): the END's BEGIN may have gone with it
            raw = (("x " * 9000)[:9000] + "\n" + text).encode()[-2 * checks.TAIL:]
            with self.subTest(dropped=text[:12]):
                self.assertEqual([f["kind"] for f in checks.masked_tail(raw)[1]], ["private_key"])
                self.assertEqual(checks.masked_tail(raw, dropped=False)[1], [])   # the reader knows best
                self.assertEqual([f["kind"] for f in checks.masked_tail(raw[-5000:], dropped=True)[1]], ["private_key"])
        # all of it kept (4-8 KB), its BEGIN before the cut, each line prefixed as docker compose prints it (the block
        # rule then takes only the header line): masked from the cut through the END
        key, body = private_key("RSA", 4800)
        raw = ("".join("web-1  | " + line + "\n" for line in key.splitlines()) + "web-1  | done\n").encode()
        cut = len(raw) - checks.TAIL
        self.assertTrue(len(raw) < 2 * checks.TAIL and raw.index(b"-----BEGIN") < cut < raw.index(b"-----END"))
        tail, found = checks.masked_tail(raw)
        self.assertEqual([f["kind"] for f in found], ["private_key"])
        self.assertTrue(all(line[i:i + 10] not in tail for line in body.splitlines() for i in range(55)), tail[:200])
        self.assertTrue(tail.endswith("web-1  | done\n"))

    def test_what_blocks_is_unchanged(self):
        # The owner's rule: no gate blocks more than before without the owner's approval. SUSPICIOUS (what the candidate,
        # publish and Knowledge scans block on) is the rule from before D57's wider kinds, which only mask and warn.
        before = re.compile(rb"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}"
                            rb"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
        self.assertEqual((sources.SUSPICIOUS.pattern, sources.SUSPICIOUS.flags), (before.pattern, before.flags))
        blocked = [secret for kind, secret, _ in SAMPLES if kind in ("github", "gitlab", "private_key")]
        blocked += ["AKIA" + chars("akia", 16, "upper"), "x" + AKIA + "y"]
        for secret in blocked:
            with self.subTest(blocked=secret[:20]):
                self.assertIsNotNone(sources.SUSPICIOUS.search(secret.encode()))
        masked_only = [ANT, OPENAI, PROJECT, BOT, OCT, "ASIA" + chars("asia", 16, "upper"), LABEL + " = " + AWS_SECRET,
                       '"Session' + 'Token": "' + SESSION + '"', "xa" + "pp-" + "1-" + "A0123456789-" + "1" * 13]
        for text in masked_only:
            with self.subTest(masked_only=text[:20]):
                self.assertIsNone(sources.SUSPICIOUS.search(text.encode()))
                self.assertTrue(sources.find_secrets(text.encode()))   # still masked and listed

    def test_lookalikes_are_not_secrets(self):
        lookalikes = [
            "oct_fixture", "oct" + "_" + chars("short", 42), "oct" + "_" + chars("long", 44),
            "sk-test", "sk-" + "ant-test", "task-" + "implement-the-login-page-and-its-tests-today",
            "ghp_test_secret_value", "github_pat_example",
            chars("bare-url", 43), chars("bare-std", 40, "std"), base64.b64encode(b"just some text here!").decode(),
            "xoxb", "xoxb-", "xox" + "b-your-token-here", "xox" + "b-123-abc",
            "AKIA" + "abcdefghijklmnop", "ASIA" + "_PACIFIC_REGION_SALES", "ASIAPACIFIC",
            LABEL + " = ${AWS_SECRET}", LABEL + " = <your-secret-access-key>",
            "-----BEGIN " + "PUBLIC KEY-----", "-----BEGIN " + "CERTIFICATE-----",
        ]
        for text in lookalikes:
            with self.subTest(text=text):
                self.assertEqual(found(f"value: {text}\n"), [])
                self.assertIsNone(sources.SUSPICIOUS.search(f"value: {text}\n".encode()))

    def test_masked_keeps_four_and_two(self):
        for kind, secret, context in SAMPLES:
            with self.subTest(kind=kind, context=context):
                text = context.format(secret)
                masked, hits = sources.mask_text(text)
                self.assertEqual(hits[0]["masked"], secret[:4] + "…" + secret[-2:])
                self.assertEqual(masked, text.replace(secret, secret[:4] + "…" + secret[-2:]))
                self.assertNotIn(secret[4:-2], masked)
                self.assertNotIn(secret[4:-2], json.dumps(hits, ensure_ascii=False))
        key, body = private_key("EC")
        masked, hits = sources.mask_text("before\n" + key + "\nafter")
        self.assertEqual((masked, hits[0]["line"]), ("before\n----…--\nafter", 2))   # the whole block, not the header
        self.assertTrue(all(line not in masked for line in body.splitlines()))

    def test_mask_text_keeps_the_rest_and_counts_in_characters(self):
        anthropic = "sk-" + "ant-" + "a" * 90
        text = f"中文说明\n第二行 {anthropic} 结束"
        masked, hits = sources.mask_text(text)
        self.assertEqual(masked, "中文说明\n第二行 sk-a…aa 结束")
        self.assertEqual((hits[0]["line"], text[hits[0]["start"]:hits[0]["end"]]), (2, anthropic))
        self.assertEqual(sources.mask_text("nothing here"), ("nothing here", []))
        self.assertEqual(sources.mask_text(masked)[1], [])   # masking twice changes nothing

    def test_hash_is_stable(self):
        secret = "ghp" + "_" + chars("hash", 36, "alnum")
        first, second = found(f"a {secret}")[0], found(f"line\nother text {secret} more")[0]
        self.assertEqual(first["hash"], hashlib.sha256(secret.encode()).hexdigest())
        self.assertEqual(first["hash"], second["hash"])   # the same content wherever it is
        self.assertNotEqual(first["hash"], found(f"a {secret[:-1]}x")[0]["hash"])
        aws = found(LABEL + " = " + AWS_SECRET)[0]
        self.assertEqual(aws["hash"], hashlib.sha256(AWS_SECRET.encode()).hexdigest())   # the key, not its label

    def test_an_extra_literal_is_found_as_worker_token(self):
        worker = secrets.token_urlsafe(40)   # how a Worker Token is made: no prefix any rule could know
        text = f"Authorization: Bearer {worker}\nagain {worker}"
        self.assertEqual(found(text), [])
        hits = found(text, [worker.encode()])
        self.assertEqual([(h["kind"], h["line"]) for h in hits], [("worker_token", 1), ("worker_token", 2)])
        self.assertEqual(hits[0]["hash"], hashlib.sha256(worker.encode()).hexdigest())
        masked, hits = sources.mask_text(text, [worker])
        self.assertNotIn(worker, masked)
        self.assertIn(worker[:4] + "…" + worker[-2:], masked)
        self.assertEqual(found("anything", [b"", None]), [])   # an empty or unset value is never "found" everywhere
        self.assertEqual(sources.mask_text("anything", ["", None]), ("anything", []))
        tail, hits = checks.masked_tail(text.encode(), extra=[worker])   # a check's output: the Worker's own token too
        self.assertNotIn(worker, tail)
        self.assertEqual([(h["kind"], h["line"], h["masked"]) for h in hits],
                         [("worker_token", line, worker[:4] + "…" + worker[-2:]) for line in (1, 2)])

    def test_mask_value_masks_every_string_and_keeps_the_rest(self):
        # what the platform stores as JSON (an Attempt's evidence, an event): its strings at any depth, keys too (an
        # Agent's outcome names its own); numbers, booleans and None as they are
        value = {"outcome": {"summary": f"见 {ANT}", "findings": [f"a {AKIA}", {"issue": OCT}], "score": 3, "ok": True},
                 "list": [None, 1.5, "fine"], f"{AKIA}": "a key"}
        masked, hits = sources.mask_value(value)
        self.assertEqual(masked, {"outcome": {"summary": f"见 {ANT[:4]}…{ANT[-2:]}",
                                              "findings": [f"a {AKIA[:4]}…{AKIA[-2:]}", {"issue": f"{OCT[:4]}…{OCT[-2:]}"}],
                                              "score": 3, "ok": True},
                                  "list": [None, 1.5, "fine"], f"{AKIA[:4]}…{AKIA[-2:]}": "a key"})
        self.assertEqual([h["kind"] for h in hits], ["anthropic", "aws_access_key", "octopus_token", "aws_access_key"])
        self.assertEqual(value["outcome"]["summary"], f"见 {ANT}")   # a copy: the value read for verdicts is untouched
        self.assertEqual(sources.mask_value("plain"), ("plain", []))

    def test_mask_happens_before_truncation(self):
        # a credential across the 4096-byte cut is masked whole before the tail is cut, so no part of it remains
        secret = "sk-" + "ant-" + "api03-" + chars("edge", 93) + "AA"
        raw = b"x" * 5000 + b"\nkey=" + secret.encode() + b"\n" + b"y" * (checks.TAIL - 60)
        self.assertLess(len(raw) - checks.TAIL, raw.index(secret.encode()) + len(secret))   # it is across the cut
        self.assertGreater(len(raw) - checks.TAIL, raw.index(secret.encode()))
        tail, hits = checks.masked_tail(raw)
        self.assertLessEqual(len(tail.encode()), checks.TAIL)
        self.assertTrue(all(secret[i:i + 8] not in tail for i in range(len(secret) - 8)), tail[:200])
        self.assertEqual([(h["kind"], h["masked"]) for h in hits], [("anthropic", "sk-a…AA")])
        self.assertEqual(set(hits[0]), {"kind", "line", "masked", "hash"})

    def test_masks_never_pull_the_buffers_head_into_the_tail(self):
        # the kept output starts wherever the last 8 KB began — possibly inside a private key whose header was dropped;
        # masks are shorter than what they replace, and must not make room for that head in the tail
        _, body = private_key("DSA")
        head = body[-1500:].encode() + b"\n-----END DSA PRIVATE" + b" KEY-----\n"
        keys = ["sk-" + "ant-" + "api03-" + chars(f"many{n}", 93) + "AA" for n in range(60)]
        raw = (head + "".join(k + "\n" for k in keys).encode())[-2 * checks.TAIL:]
        tail, hits = checks.masked_tail(raw)
        self.assertNotIn(body[-200:-100], tail)
        self.assertTrue(all(k not in tail for k in keys))
        self.assertTrue(hits and all(h["kind"] == "anthropic" for h in hits))
        self.assertEqual(hits[0]["line"], 1)


if __name__ == "__main__":
    unittest.main()
