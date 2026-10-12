"""Validate PEM text from another host before deploy.sh pastes it into a shell script (worker-host tls, D28).

A Worker host is a lower-trust boundary: its output reaches a root script on the platform host only as one block of
base64 between the expected BEGIN/END lines, so it can neither end the heredoc nor carry shell code.
Usage: python3 -I deploy/aws/pem.py "CERTIFICATE REQUEST" < text   (prints the normalised PEM, exit 1 otherwise)
"""
import re
import sys

KINDS = {"CERTIFICATE REQUEST", "CERTIFICATE"}


def normalise(kind, text):
    """The PEM block with surrounding whitespace removed, or None when text is anything else."""
    text = text.strip()
    pattern = rf"-----BEGIN {kind}-----\n(?:[A-Za-z0-9+/=]{{1,76}}\n)+-----END {kind}-----"
    return text + "\n" if kind in KINDS and re.fullmatch(pattern, text) else None


if __name__ == "__main__":
    pem = normalise(sys.argv[1], sys.stdin.read()) if len(sys.argv) == 2 else None
    if pem is None:
        sys.exit("不是有效的 PEM " + (sys.argv[1] if len(sys.argv) == 2 else ""))
    sys.stdout.write(pem)
