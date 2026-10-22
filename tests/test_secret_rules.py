"""D57 §3.2–3.3: what looks like a credential, and how it is shown — only its first four and last two characters, with
its kind, line and a hash; the text itself nowhere. Every sample is put together while the test runs (from fragments
and generated characters), so no complete credential is written in this file."""
import base64
import hashlib
import json
import secrets
import unittest

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


def private_key(kind="RSA"):
    body = base64.b64encode(hashlib.sha512(kind.encode()).digest() * 12).decode()
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    label = (kind + " " if kind else "") + "PRIVATE" + " KEY"
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----", lines


AWS_SECRET = chars("aws-secret", 40, "std").replace("=", "A")
LABEL = "aws_secret_" + "access_key"
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
    ("anthropic", "sk-" + "ant-" + "api03-" + chars("anthropic", 93) + "AA", "ANTHROPIC_API_KEY={}"),
    ("anthropic", "sk-" + "ant-" + "a" * 90, "x-api-key: {}"),
    ("openai", "sk-" + chars("openai-legacy", 48, "alnum"), "Authorization: Bearer {}"),
    ("openai", "sk-" + "proj-" + chars("openai-project", 156), "OPENAI_API_KEY={}"),
    ("slack", "xox" + "b-" + "1234567890123-" + "1234567890123-" + chars("slack-bot", 24, "alnum"), "SLACK={}"),
    ("slack", "xox" + "p-" + "1234567890-" + "1234567890-" + "1234567890123-" + chars("slack-user", 32, "alnum"),
     "token {}"),
    ("octopus_token", "oct" + "_" + secrets.token_urlsafe(32), "OCTOPUS_TOKEN={}"),   # as security.create_token mints it
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
        self.assertEqual(kinds, {"github", "gitlab", "aws_access_key", "aws_secret_key", "anthropic", "openai", "slack",
                                 "octopus_token", "private_key"})
        self.assertEqual({kind for kind, _, _ in SAMPLES}, kinds)
        for kind, secret, context in SAMPLES:
            with self.subTest(kind=kind, context=context):
                data = context.format(secret).encode()
                hits = sources.find_secrets(data)
                self.assertEqual([h["kind"] for h in hits], [kind])   # one span is reported once, as its own kind
                self.assertEqual(data[hits[0]["start"]:hits[0]["end"]], secret.encode())
                self.assertIsNotNone(sources.SUSPICIOUS.search(data))   # the publish and candidate scans know it too

    def test_anthropic_is_told_from_openai_and_both_are_found(self):
        anthropic = "sk-" + "ant-" + "api03-" + chars("a1", 93) + "AA"
        openai = "sk-" + chars("o1", 48, "alnum")
        hits = found(f"{anthropic}\n{openai}\n")
        self.assertEqual([h["kind"] for h in hits], ["anthropic", "openai"])
        self.assertEqual([h["line"] for h in hits], [1, 2])

    def test_lookalikes_are_not_secrets(self):
        lookalikes = [
            "oct_fixture", "oct" + "_" + chars("short", 42), "oct" + "_" + chars("long", 44),
            "sk-test", "sk-" + "ant-test", "task-" + "implement-the-login-page-and-its-tests-today",
            "disk-" + chars("disk", 40, "alnum"),
            "ghp_test_secret_value", "github_pat_example",
            chars("bare-url", 43), chars("bare-std", 40, "std"), base64.b64encode(b"just some text here!").decode(),
            "xoxb", "xoxb-", "xox" + "b-your-token-here", "xox" + "b-123-abc",
            "AKIA" + "abcdefghijklmnop", "ASIA" + "_PACIFIC_REGION_SALES", "ASIAPACIFIC",
            "AKIA" + chars("too-long", 20, "upper"),
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
