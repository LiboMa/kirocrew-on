"""Sealed repository credentials (D35 §3.3): encrypt-then-MAC with no Python dependency.

The host's openssl encrypts with AES-256-CBC, its key derived by PBKDF2 from the master key it reads from the key file
itself (``-pass file:``): neither the master key nor the secret ever appears on a command line, where other users of
the host could read it. An HMAC-SHA256 over the ciphertext, keyed from the same master key, is checked before
anything is decrypted, so a changed ciphertext or another key fails instead of yielding wrong plaintext.
"""
import base64
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import subprocess

VERSION = "v1"
CIPHER = ["enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-md", "sha256"]
MIN_KEY = 32


class VaultError(RuntimeError):
    """A sealed credential that cannot be opened; the message never carries key or plaintext."""


def key_file(configured, data_dir):
    """The master key's file: the configured one (it must exist), or <data>/credentials.key, created 0600 once."""
    if configured:
        path = Path(configured)
        try:
            text = path.read_text().strip()
        except OSError:
            raise VaultError(f"凭证主密钥不可读：{path}") from None
        if len(text) < MIN_KEY:
            raise VaultError(f"凭证主密钥过短（至少 {MIN_KEY} 个字符）：{path}")
        return path
    path = Path(data_dir) / "credentials.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, "w") as file:
        file.write(base64.b64encode(secrets.token_bytes(32)).decode() + "\n")
    return path


def _mac_key(path):
    # openssl's -pass file: reads the first line; the key file holds a single line.
    return hmac.new(Path(path).read_text().strip().encode(), b"kirocrew-credentials-mac", hashlib.sha256).digest()


def _openssl(path, args, data):
    try:
        done = subprocess.run(["openssl", *CIPHER, *args, "-pass", "file:" + str(path)], input=data,
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise VaultError("openssl 不可用，无法处理凭证") from None
    if done.returncode:
        raise VaultError("凭证加解密失败")
    return done.stdout


def seal(path, plaintext):
    body = _openssl(path, ["-salt"], plaintext.encode())
    mac = hmac.new(_mac_key(path), body, hashlib.sha256).digest()
    return ".".join([VERSION, base64.b64encode(body).decode(), base64.b64encode(mac).decode()])


def open_(path, sealed):
    parts = sealed.split(".") if isinstance(sealed, str) else []
    if len(parts) != 3 or parts[0] != VERSION:
        raise VaultError("凭证密文格式无效")
    try:
        body, mac = base64.b64decode(parts[1], validate=True), base64.b64decode(parts[2], validate=True)
    except ValueError:
        raise VaultError("凭证密文格式无效") from None
    if not hmac.compare_digest(mac, hmac.new(_mac_key(path), body, hashlib.sha256).digest()):
        raise VaultError("凭证密文校验失败")
    try:
        return _openssl(path, ["-d"], body).decode()
    except UnicodeDecodeError:
        raise VaultError("凭证加解密失败") from None


def hint(secret):
    """The last four characters, to recognise a credential by."""
    return "…" + secret[-4:]
