"""Prepare a pinned project checkout before native workflow dispatch.

Invoked by the KiroCrew coordinator through its existing Shell tool. No model, Coding CLI or new Gateway endpoint is
introduced. A private repository's token (D35 §5.1) reaches git only through GIT_ASKPASS and a 0600 file that exists
for the clone alone; it is never copied into the workspace, the remote URL or any record.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

HIDDEN = "[凭证已隐去]"

# Left out of a download (D36 §4.2) and of a published commit (D35 §5.2), ignored by .gitignore or not.
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "target", ".gradle",
             ".turbo", ".parcel-cache", ".cache"}
ENV_KEPT = {".env.example", ".env.sample"}
# What BLOCKS — the candidate's no_secrets gate, the publish scan and Knowledge items (D35 §5.2, D37 §3): GitHub
# (fine-grained, classic, OAuth, app), GitLab, AWS Access Key IDs that start AKIA (not the temporary ASIA ones), private
# keys. It stays exactly as it was before D57 until the owner approves blocking on more (no gate may block more than
# before without that approval); the wider SECRET_RULES below only mask and are listed as findings.
SUSPICIOUS = re.compile(rb"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
                        rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----")
# What a credential looks like (D57 §3.2), by kind, for masking what is shown or stored and listing what was masked
# (find_secrets, mask_text, checks.masked_tail) — never for blocking. Catch first: a lookalike costs a person's
# judgment (and is remembered, D57 §3.4), a missed key costs the key, so the rules take keys glued to their context,
# URL-encoded or JSON-escaped. At one place the first kind in this order names the span (anthropic before openai; a
# labelled AWS secret before the key-ID shape it may start with), and overlapping spans are reported, masked and hashed
# as one. A rule with a group named "secret" reports only that group (an AWS secret, not its label).
_AWS_SEPARATOR = rb"(?:[\s\"'\\:=>]|%2[27]|%3[ADad]){1,12}"   # = : => quotes, escaped or URL-encoded, a space
# A private key's line break in a file, or written as \n in JSON (as \\n when that JSON is itself a JSON string, …).
_LINE_BREAK = rb"(?:\r?\n|\\+[rn])"
_KEY_CHAR = rb"(?:[A-Za-z0-9+/=]|\\+/)"   # base64, and "/" as PHP's json_encode writes it
# How the key writes its line breaks, as seen right after its BEGIN: "lb" unset for real ones, else the backslashes
# before their n or r (one in JSON, two in JSON inside a JSON string, four, …).
_KEY_ESCAPING = rb"(?:(?=[ \t]*(?P<lb>\\+)[rn])|)"
# A header value runs to its own line's end: a real line break, or, when the key's are escaped, a backslash run before
# n or r that is one — whole escaped backslashes (two "lb" each), then "lb" exactly — so a path keeps its \n and \r
# (C:\Users\nick, \rob, D:\keys\new; C:\\Users\\nick in JSON) at any depth. Other escapes (\" \u0022 \/) and the
# backslashes right before the real end of the line (C:\keys\) are part of the value.
_KEY_VALUE = (rb"(?:[^\r\n\\]|\\+(?![\\rn])"
              rb"|(?(lb)(?=\\)(?:(?P=lb)(?P=lb))*(?!(?P=lb)(?P=lb))(?!(?P=lb)[rn])\\*|\\+)[rn])*")
# OpenPGP armour and PEM header lines (RFC 4880 §6.2, RFC 1421): only these names, so that an error message after a
# key header ("ModuleNotFoundError: …") is not taken for one.
_KEY_HEADERS = (rb"(?:(?:\s|\\+[nrt])+(?:Version|Comment|Hash|Charset|MessageID|Proc-Type|DEK-Info):" + _KEY_VALUE
                + rb")*")
# A line of the key's body when its end is not there: a whole line of base64, or a run of base64 with a digit, + / or =
# in it that is not followed by ":" or a "." that is not an ellipsis (base64 has neither): a word alone on a line is
# masked with it, and a line cut short before "..."; an error message after a key header ("python3.11: …",
# "/usr/bin/env: …", "/bin/sh: 1: …") is not.
_KEY_LINE = (rb"(?:" + _KEY_CHAR + rb"+(?=[ \t]*(?:" + _LINE_BREAK + rb"|\Z))|[A-Za-z]*(?:[0-9+/=]|\\+/)"
             + _KEY_CHAR + rb"*(?!:|\.(?!\.)|" + _KEY_CHAR + rb"))")
_KEY_BEGIN = rb"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
_KEY_END = rb"-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
SECRET_RULES = [
    ("github", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}")),
    ("gitlab", re.compile(rb"glpat-[A-Za-z0-9_-]{20,}")),
    # aws_secret_access_key, AWS_SECRET_ACCESS_KEY, SecretAccessKey (STS, IAM), aws_secret_key, Terraform's secret_key
    ("aws_secret_key", re.compile(rb"(?i:secret[_-]?(?:access[_-]?)?key)" + _AWS_SEPARATOR
                                  + rb"(?P<secret>[A-Za-z0-9/+]{40})(?![A-Za-z0-9/+=])")),
    # aws_session_token, SessionToken (STS), boto's aws_security_token, a presigned URL's X-Amz-Security-Token (encoded)
    ("aws_session_token", re.compile(rb"(?i:aws[_-]?session[_-]?token|sessiontoken|aws[_-]?security[_-]?token"
                                     rb"|x-amz-security-token)" + _AWS_SEPARATOR
                                     + rb"(?P<secret>(?:[A-Za-z0-9/+=]|%[0-9A-Fa-f]{2}){100,})")),
    # A key ID anywhere; a secret glued to it (or after one , ; : | or space, as in the IAM CSV) goes with it.
    ("aws_access_key", re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}(?:[,;:|\s]?[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=]))?")),
    ("anthropic", re.compile(rb"sk-ant-[A-Za-z0-9_-]{32,}")),
    # Project, service-account and admin keys, and a legacy "sk-" with letters and digits only: anywhere, as these rules
    # only mask (after a colour code, an escape, inside disk-… alike).
    ("openai", re.compile(rb"sk-(?:(?:proj|svcacct|admin)-[A-Za-z0-9_-]{32,}|[A-Za-z0-9]{32,})")),
    ("slack", re.compile(rb"(?:xox[abprs]|xapp|xoxe(?:\.xox[bp])?)-[0-9]+-[A-Za-z0-9-]{10,}")),
    # The platform's own API Token, as security.create_token makes it: "oct_" and token_urlsafe(32), exactly.
    ("octopus_token", re.compile(rb"oct_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")),
    # The whole block — its header lines, then the body, in a file or JSON-escaped (once or more) — when its end comes
    # before any other "-----", so masking leaves none of the key; else the header and the body lines after it.
    ("private_key", re.compile(_KEY_BEGIN + _KEY_ESCAPING + _KEY_HEADERS
                               + rb"(?:(?:[A-Za-z0-9+/=\s:,.]|\\+[nrt/]|-(?!----))*?" + _KEY_END
                               + rb"|(?:(?:[ \t]*" + _LINE_BREAK + rb")+[ \t]*" + _KEY_LINE + rb")*)")),
]
_PRIVATE_KEY = [kind for kind, _ in SECRET_RULES].index("private_key")
_KEY_BEGINS, _KEY_ENDS = re.compile(_KEY_BEGIN), re.compile(_KEY_END)
# Where a text holds a private key's BEGIN or END (owner's ruling, D57 6b), every run of 40 or more base64 characters in
# it ("\/" as JSON may write "/") is masked as private_key, whatever shape the key's lines are printed in (a diff of an
# indented key, a summary that repeats it after another prefix): display only, as verdicts are judged on the text as
# written, so a SHA or a digest next to a key is masked too. A run begins after no base64 (but may after a backslash),
# so each is tried once; what it begins with that is no key's text is left out of its mask (_NOT_KEY_TEXT), and the
# rest of it is masked however short.
_KEY_MARKER = re.compile(rb"-----(?:BEGIN|END) [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
_NEAR_A_KEY = re.compile(rb"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=](?:[A-Za-z0-9+/=]|\\+/){39,}")
# The letter of an escaped line break right before it ("\n", "\r") or the rest of an escaped character ("\u94a5",
# JSON's ensure_ascii: a mask after its backslash would break the escape), and a label's tail (AWS_SECRET_ACCESS_KEY= is
# read "KEY=", aws_session_token= "token="): base64 has an "=" only at its end, so what is before one inside a run is a
# label, shown, and its value is what the precise rules name (aws_secret_key, aws_session_token).
_NOT_KEY_TEXT = re.compile(rb"(?:(?<=\\)(?:[nr]|u[0-9A-Fa-f]{4}))?(?:[A-Za-z][A-Za-z0-9]{0,31}=(?=[A-Za-z0-9+/]|\\+/))?")
# A key's lines are one span (one mask, one finding) where what is between two private_key spans is where a line ends
# and the next line's prefix: 32 bytes at most, the whole chain 8 KB at most (about a diff of two 4096-bit keys). A
# line ends after spaces, its string's closing quote and a list's comma, in a line break, a shell's backslash and line
# break, or an escaped one (JSON, repr; "\r\n" as one) — and when a string literal ends there (pprint, pytest -vv's
# diff of a dict, adjacent literals in Python or C, "…\n" + in Java or JavaScript), its closing quote, a "," "+" or "\"
# and the line break too (the quote is needed, so that an escaped blank line still splits) — or in what separates a
# list's strings on one line (', ', ","); the next line's prefix is spaces, pytest's "E", a diff's marker, a string's
# opening quote, or what the line before had before its text but for its digits (docker compose's "web-1  | ", cat -n,
# grep -n's "key.pem-26-", a log's timestamps: _PREFIX_SHAPE). Never across a key's END or BEGIN, a blank line or any
# other line in between, and never for a hex digest (a SHA, sha256sum's), which no key's line is: each of those is its
# own.
_CHAIN = 8192
_KEY_LINE_END = re.compile(rb"[ \t]*(?:\\*['\"],?[ \t]*)?(?:\r?\n|\\\r?\n|\\+(?:r\\+)?[nr]"
                           rb"(?:[ \t]*\\*['\"][ \t]*[,+\\]?[ \t]*\r?\n|\\\r?\n)?)|\\*['\"], ?\\*['\"]")
_KEY_LINE_PREFIX = re.compile(rb"(?:E[ \t]+)?[ \t]*(?:[-+?][ \t]*)?(?:\\*['\"])?[ \t]*")
_ANY_LINE_END = re.compile(rb"\n|\\+[nr]|\\*['\"], ?\\*['\"]")
_DIGEST = re.compile(rb"[0-9a-f]+|[0-9A-F]+")
# The lines of base64 right before a private key's END, read backwards from it (so that finding where they begin costs
# what they are long): whole lines, each with the END line's own prefix "q" (docker compose's "web-1  | ", pytest's
# "E         + ", grep's "key.pem-" / "key.pem-26-", an indent; captured from the prefix put before a NUL), or, when the
# END follows an escaped line break, JSON-escaped lines ("\/" reads "/\" backwards), the first of them after a line
# break, the string's quote (", ', \") or an ellipsis's "."; above at least one of those, also after a label's "=" or
# after a space (": ", "the key ").
_BACKWARDS_BASE64 = rb"(?:[A-Za-z0-9+=]|/\\*)+"
_LINES_BEFORE = re.compile(rb"(?P<q>[^\0]*)\0(?:\n\r?[ \t]*" + _BACKWARDS_BASE64 + rb"(?P=q)(?=\n|\Z))+")
_ESCAPED_LINE = rb"(?:n\\+(?:r\\+)?|r\\+)[ \t]*" + _BACKWARDS_BASE64
_ESCAPED_LINES_BEFORE = re.compile(rb"[ \t]*(?:" + _ESCAPED_LINE + rb"[ \t]*(?=[nr]\\|[\"'.]|\Z))+(?:" + _ESCAPED_LINE
                                   + rb"(?:[ \t]*(?==)|[ \t]+))?")
# A diff's lines, whose prefix is the END line's but for the diff's marker column: pytest's ndiff after its
# "E         " ("- ", "+ ", "  ", and "? " lines of ^ - + under a changed line), a unified diff's "-", "+", " ". Read
# backwards too, the END line's prefix put before two NULs as "a" (after the column) and "b" (before it).
_DIFF_MARKS = b"-+ ?"
_DIFF_LINES_BEFORE = re.compile(rb"(?P<a>[^\0]*)\0(?P<b>[^\0]*)\0(?:(?:\n\r?[-+^][-+^ \t]*\?(?P=b))?\n\r?[ \t]*"
                                + _BACKWARDS_BASE64 + rb"(?P=a)[-+ ?](?P=b)(?=\n|\Z))+")
# The line git heads a hunk with ("@@ -16,7 +16,7 @@ <the last line above it that starts with a letter>"): in a key,
# one of its lines.
_HUNK_HEADER = re.compile(rb"@@ -[0-9]+(?:,[0-9]+)? \+[0-9]+(?:,[0-9]+)? @@ ")
_NO_WORD = re.compile(rb"[0-9+/=]")   # what base64 has and a word does not
_PREFIX_SHAPE = bytes.maketrans(b"0123456789:", b"0000000000-")   # grep: "key.pem:26:" heads its lines "key.pem-25-"
_BASE64 = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
_BASE64_BYTES = bytes(sorted(_BASE64))


_ESCAPE = re.compile(r"\\(?:u[0-9A-Fa-f]{4}|.)|.", re.S)   # a character as JSON (or repr) writes it: \/, \n, 钥
# A key's run of characters may take the first ones of a key glued to it ("sk-…sk-…": the first run takes the second's
# "sk"): a rule's next match is looked for from this many bytes before the end of the one before (or right after its
# start), so the second is found too, and both are one span. Longer than any prefix a rule's run may take.
_GLUE = 32


def _masked(value):
    """A credential as it may be shown (D57 §3.3): its first four and last two characters; a value too short to keep
    most of it hidden that way is shown as the ellipsis alone. Its characters as written, escapes whole: what is shown
    never ends or begins inside one (``abc\\/…`` shows ``abc``, a ``\\u00e9`` at its end is not shown), so JSON text
    that held a credential still parses."""
    text = value.decode(errors="replace")
    if len(text) < 12:
        return "…"
    head, tail = "", ""
    for piece in _ESCAPE.finditer(text):
        if len(head) + len(piece.group()) > 4:
            break
        head += piece.group()
    # its last characters, read from a place no escape spans — none of the 6 characters before it is a backslash — so a
    # long one is never read whole
    at = len(text) - 8
    while at > 0 and (backslash := text.rfind("\\", max(at - 6, 0), at)) >= 0:
        at = backslash
    for piece in reversed(_ESCAPE.findall(text, max(at, 0))):
        if len(tail) + len(piece) > 2:
            break
        tail = piece + tail
    return head + "…" + tail


def _matches(pattern, data):
    """``pattern``'s matches in ``data``, each looked for from ``_GLUE`` bytes before the end of the one before (never
    before its start): one that begins inside the one before (a key glued to it) is found too. Linear: each match is
    looked through again for at most ``_GLUE`` bytes."""
    at = 0
    while (match := pattern.search(data, at)) is not None:
        yield match
        at = max(match.start() + 1, match.end() - _GLUE)


def find_secrets(data, extra=(), cut=None, dropped=True):
    """Where ``data`` (bytes) holds what looks like a credential, in order: ``{kind, start, end, line, masked, hash}``,
    ``start``/``end`` the span in ``data``, ``line`` from 1, ``hash`` the span's SHA-256 (hex). ``extra`` are values to
    be found as they are (a Worker Token, which has no prefix to know it by): kind ``worker_token``, named before any
    rule. ``cut``: where the shown part of ``data`` begins (an output's tail kept to its last bytes), ``dropped``:
    whether what came before ``data`` was dropped. A private key's END after ``cut`` with no BEGIN between them belongs
    to a key whose BEGIN is out of view — dropped with that head, or before ``cut`` and not yet ended — so everything
    from ``cut`` through the END is one private_key. And whether or not its BEGIN is there, the lines of base64 right
    before an END are masked with it (the end of a key printed by tail, grep -B or a diff); an END with none right before
    it (a hint that names it) is only a line."""
    data = bytes(data)
    spans = []   # (start, priority, end, kind)
    for value in extra:
        value = value.encode() if isinstance(value, str) else bytes(value or b"")
        at = data.find(value) if value else -1   # an empty (or unset) value is never "found" everywhere
        while at >= 0:
            spans.append((at, -1, at + len(value), "worker_token"))
            at = data.find(value, at + len(value))
    for priority, (kind, pattern) in enumerate(SECRET_RULES):
        for match in _matches(pattern, data):
            start, end = match.span("secret") if "secret" in pattern.groupindex else match.span()
            spans.append((start, priority, end, kind))
    if _KEY_MARKER.search(data):   # a key's marker in view: every long run of base64 around it
        spans += [(_NOT_KEY_TEXT.match(data, match.start()).end(), _PRIVATE_KEY, match.end(), "private_key")
                  for match in _NEAR_A_KEY.finditer(data)]
    floor = 0
    for match in _KEY_ENDS.finditer(data):   # the lines of a key right before its END, whether or not its BEGIN is there
        start = _lines_before(data, floor, match.start())
        if start is not None:
            spans.append((start, _PRIVATE_KEY, match.end(), "private_key"))
        floor = match.end()
    if cut is not None:
        for match in _KEY_ENDS.finditer(data, max(cut - 64, 0)):   # an END the cut runs through counts too
            if match.end() > cut:
                if not _KEY_BEGINS.search(data, cut, match.start()) and (dropped or _open_key(data, match.start())):
                    spans.append((cut, _PRIVATE_KEY, match.end(), "private_key"))
                break
    # spans that overlap or touch become their union, named by the first; so do a key's lines (_key_line_after)
    merged = []   # [start, end, kind, the shape of its last line's prefix, where that line's text begins]
    for start, _, end, kind in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            continue
        prefix = _key_line_after(data, merged[-1], start, end) if merged and kind == merged[-1][2] == "private_key" \
            else None
        if prefix is not None:
            merged[-1][1:] = [end, kind, prefix, start]
        else:
            merged.append([start, end, kind, _prefix_before(data, start) if kind == "private_key" else None, start])
    found, line, counted = [], 1, 0
    for start, end, kind, _, _ in merged:
        line += data.count(b"\n", counted, start)
        counted = start
        found.append({"kind": kind, "start": start, "end": end, "line": line, "masked": _masked(data[start:end]),
                      "hash": hashlib.sha256(data[start:end]).hexdigest()})
    return found


def _key_line_after(data, chain, start, end):
    """The shape (_PREFIX_SHAPE) of the prefix of the line the private_key span ``data[start:end]`` begins after, when
    it is the next of the key's lines in ``chain`` (a private_key span, as ``find_secrets`` merges them); else None.
    See _CHAIN."""
    gap = data[chain[1]:start]
    if len(gap) > 32 or end - chain[0] > _CHAIN or data.startswith(b"-----", start) or _ends_a_key(data, chain[1]) \
            or _DIGEST.fullmatch(data, start, end) or _DIGEST.fullmatch(data, chain[4], chain[1]):
        return None
    line_end = _KEY_LINE_END.match(gap)
    if line_end is None:
        return None
    prefix = gap[line_end.end():]
    shape = prefix.translate(_PREFIX_SHAPE)
    return shape if _KEY_LINE_PREFIX.fullmatch(prefix) or shape == chain[3] else None


def _ends_a_key(data, at):
    """Whether ``data[:at]`` ends with a private key's END (not its BEGIN)."""
    window = max(at - 256, 0)
    return data.endswith(b"-----", 0, at) \
        and data.rfind(b"-----END ", window, at) > data.rfind(b"-----BEGIN ", window, at)


def _prefix_before(data, start):
    """The shape (_PREFIX_SHAPE) of what comes before ``start`` on its line (after a line break, an escaped one, a
    list's separator), when the line begins at most 32 bytes before it; else None."""
    last = None
    for last in _ANY_LINE_END.finditer(data, max(start - 33, 0), start):
        pass
    prefix = data[last.end():start] if last else data[:start] if start <= 32 else None
    return None if prefix is None else prefix.translate(_PREFIX_SHAPE)


def _lines_before(data, floor, at):
    """Where the lines of base64 right before the private-key END at ``at`` begin (not before ``floor``, the end of the
    END before it), or None when none come right before it (an END only named: "hint: expected -----END …", a grep hit
    with its context left out)."""
    newline = data.rfind(b"\n", floor, at)   # never further back than ``floor``: one pass over the data for all ENDs
    line = newline + 1 if newline >= 0 else 0 if floor == 0 else floor - 1   # floor - 1: the line began before it
    start = max(line, floor)
    before = data[start:at].rstrip(b" \t")
    if before[-2:-1] == b"\\" and before[-1:] in (b"n", b"r"):   # in an escaped string: its lines, escaped
        found = _ESCAPED_LINES_BEFORE.match(data[start:at][::-1])
        start = at - found.end() if found else None
    elif line >= floor and b"\0" not in data[line:at]:
        prefix, heads = _line_prefix(data[line:at].translate(_PREFIX_SHAPE))
        lines, took = data[floor:line].translate(_PREFIX_SHAPE)[::-1], 0
        for head in heads:   # where a diff's marker column may be: the one whose lines go furthest back
            found = (_DIFF_LINES_BEFORE if head.count(b"\0") == 2 else _LINES_BEFORE).match(head + lines)
            took = max(took, found.end() - len(head) if found else 0)
        top = line - took   # where the first of the lines begins
        start = top + len(prefix) if took else None
        if took and top > floor:   # the first of them may be a value at the end of the line above
            newline = data.rfind(b"\n", floor, top - 1)
            value = _value_above(data, newline + 1, top - 1) if newline >= 0 or floor == 0 else None
            start = start if value is None else value
    else:
        start = None
    while start is not None and data[start:start + 1] in (b" ", b"\t"):
        start += 1
    return start


def _line_prefix(prefix):
    """What each line before an END whose line starts with ``prefix`` (its shape) starts with: (that prefix, the heads
    to put before the lines read backwards, one for each place a diff's marker column may be — or the prefix as it is).
    Base64 glued to the END is the key's own, so the prefix ends in none — but for a unified diff's "+" alone or after
    no base64, which marks the line. The marker column may differ from the END line's: ndiff's when the prefix ends in
    "- ", "+ ", "  " or "? "; a unified diff's when it ends in "-", "+", " " or "?", or when it is one of "-", "+", " "
    and an indent (indented text: a YAML Secret, a test fixture); pytest's ndiff of indented text after its
    "E         ". Only where a line's own characters begin is never in doubt (what comes right before them is no
    base64), so each is read in linear time."""
    glued = prefix[-1:] == b"+" and (len(prefix) == 1 or prefix[-2] not in _BASE64)
    if not glued:
        prefix = prefix.rstrip(_BASE64_BYTES)
    columns = []
    if len(prefix) >= 2 and prefix[-2] in _DIFF_MARKS and prefix[-1:] == b" ":
        columns.append(len(prefix) - 2)   # ndiff: "- ", "+ ", "  ", "? "
    if prefix[-1:] and prefix[-1] in _DIFF_MARKS:
        columns.append(len(prefix) - 1)   # a unified diff's whole prefix: "-", "+", " "
    if len(prefix) >= 2 and prefix[0] in b"-+ " and not prefix[1:].strip(b" \t"):
        columns.append(0)   # a unified diff of indented text: the marker, then the indent
    if len(prefix) > 10 and prefix[:10] == b"E" + b" " * 9 and prefix[10] in _DIFF_MARKS:
        columns.append(10)   # pytest's ndiff of indented text: "E", nine spaces, the marker, a space, the indent
    heads = []
    for column in dict.fromkeys(columns):
        after, before = prefix[column + 1:][::-1], prefix[:column][::-1]
        first = (after or before)[:1]   # what comes right before a line's own characters, read backwards
        if not first or first[0] not in _BASE64:
            heads.append(after + b"\0" + before + b"\0")
    return prefix, heads or [prefix[::-1] + b"\0"]


def _value_above(data, start, end):
    """Where the value that ends the line ``data[start:end]`` begins, when it is base64 after a label's "=" or ": ",
    quoted or not (PRIVATE_KEY=…, PRIVATE_KEY="…, key: '…), after a bare space when it is 10 characters or more with a
    digit, "+", "/" or "=" in it (using key MIIE…; not a word: "Loading configuration"), or after the header git heads a
    hunk with (the last line above the hunk that starts with a letter: in a key, one of its lines); None when it ends in
    none."""
    row = data[start:end].rstrip(b" \t\r")
    at = len(row.rstrip(_BASE64_BYTES))
    label = row.find(b"=", at, len(row.rstrip(b"=")))   # an "=" before the value's own characters ends a label
    at = label + 1 if label >= 0 else at
    head = row[:at]
    unquoted = head[:-1] if head[-1:] in (b'"', b"'") else head
    named = unquoted.rstrip(b" \t")[-1:] == b"=" or (unquoted.rstrip(b" \t")[-1:] == b":"
                                                      and unquoted[-1:] in (b" ", b"\t"))
    spaced = head[-1:] in (b" ", b"\t") and len(row) - at >= 10 and _NO_WORD.search(row, at) is not None
    return start + at if at < len(row) and (named or spaced or _HUNK_HEADER.fullmatch(head)) else None


def _open_key(data, before):
    """Whether the last private-key BEGIN in ``data`` before ``before`` has no END after it there."""
    begin = None
    for begin in _KEY_BEGINS.finditer(data, 0, before):
        pass
    return begin is not None and not _KEY_ENDS.search(data, begin.end(), before)


def mask_text(text, extra=(), memo=None):
    """``text`` with every credential ``find_secrets`` finds replaced by its masked form, and what was found (``start``/
    ``end`` in characters of ``text``). Masking masked text changes nothing. ``memo``: what ``premask`` made of the
    texts known before a write transaction, for the same ``extra`` — looked up there, not masked again."""
    if memo is not None and memo.extra == tuple(extra or ()) and text in memo:
        shown, found = memo[text]
        return shown, [dict(hit) for hit in found]
    data = text.encode(errors="surrogatepass")
    found = find_secrets(data, [v.encode(errors="surrogatepass") if isinstance(v, str) else v for v in extra or ()])
    pieces, at, chars = [], 0, 0
    for hit in found:
        start, end = hit["start"], hit["end"]
        between = data[at:start].decode(errors="surrogatepass")
        secret = len(data[start:end].decode(errors="surrogatepass"))
        pieces += [between, hit["masked"]]
        chars += len(between)
        hit["start"], hit["end"] = chars, chars + secret
        chars, at = chars + secret, end
    return "".join(pieces) + data[at:].decode(errors="surrogatepass"), found


def mask_value(value, extra=(), memo=None):
    """``value`` as JSON holds it (dicts, lists, strings, numbers) with every string masked as ``mask_text`` masks it —
    a dict's keys too (an Agent's outcome names its own) — and what was found, in order. A copy: the value itself, which
    verdicts read, is left as it was (D57 §3.3). ``memo`` as for ``mask_text``."""
    found = []

    def walk(item):
        if isinstance(item, str):
            text, hits = mask_text(item, extra, memo)
            found.extend(hits)
            return text
        if isinstance(item, dict):
            return {(walk(key) if isinstance(key, str) else key): walk(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(child) for child in item]
        return item
    return walk(value), found


class Premasked(dict):
    """What ``mask_text`` makes of texts known before a write transaction (``premask``), with its ``extra``."""

    def __init__(self, extra=()):
        super().__init__()
        self.extra = tuple(extra or ())


def premask(value, extra=()):
    """Every string in ``value`` (walked as ``mask_value`` walks it) masked, and every mask that changed one masked
    again (as the journal does with what is stored), for ``mask_text``'s ``memo``: what is stored of an Attempt is
    masked before its write transaction, which then only writes (D57 §3.3) — the same text as masking it there, as
    masking is a function of the text and ``extra`` alone."""
    memo = Premasked(extra)

    def walk(item):
        if isinstance(item, str):
            if item not in memo:
                memo[item] = mask_text(item, extra)
                shown = memo[item][0]
                if shown not in memo:   # it held a credential: its mask, as the journal masks it again
                    memo[shown] = mask_text(shown, extra)
        elif isinstance(item, dict):
            for key, child in item.items():
                walk(key)
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)
    walk(value)
    return memo


def env_file(name):
    """A .env file or folder (or .env.<anything>), which holds secrets; the examples stay."""
    return (name == ".env" or name.startswith(".env.")) and name not in ENV_KEPT


def left_out(path):
    """Whether a project path is never packed or published: a dependency or cache folder, or a .env file or folder."""
    parts = path.split("/")
    return any(part in SKIP_DIRS or env_file(part) for part in parts[:-1]) or env_file(parts[-1]) \
        or any(parts[i:i + 2] == [".next", "cache"] for i in range(len(parts) - 1))


# The same rules as git pathspecs: excluded folders are never even read into git's object store.
LEFT_OUT = tuple(f":(exclude,glob)**/{name}/**" for name in sorted(SKIP_DIRS)) + (
    ":(exclude,glob)**/.next/cache/**", ":(exclude,glob)**/.env", ":(exclude,glob)**/.env/**",
    ":(exclude,glob)**/.env.*", ":(exclude,glob)**/.env.*/**")
KEPT = tuple(f":(glob)**/{name}" for name in sorted(ENV_KEPT))
COMMITTER = ("-c", "user.name=KiroCrew ON", "-c", "user.email=kirocrew-on@localhost", "-c", "commit.gpgsign=false")
OVERLAP = 4096   # longer than any match SUSPICIOUS needs, so a match across two read chunks is still found


def publish_candidate(project, message, branch, scratch):
    """The commit a publish offers: HEAD plus the workspace's pending changes, without what a download leaves out (a .env
    or dependency folder the project forgot to ignore stays in the workspace, uncommitted). Built in a temporary index in
    ``scratch`` (outside the project) and not yet on any branch: the workspace's branch and index move only once the
    commit has been checked (``advance``). HEAD itself when nothing is pending."""
    current = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise ValueError(f"工作区不在分支 {branch}（当前 {current}），未发布")
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    index = Path(scratch) / ".publish-index"
    env = {"GIT_INDEX_FILE": str(index)}
    for leftover in (index, index.with_name(index.name + ".lock")):   # from an interrupted publish
        leftover.unlink(missing_ok=True)
    try:
        _git(project, "read-tree", head, env=env)
        _git(project, "add", "-A", "--", ".", *LEFT_OUT, env=env)
        kept = [path for path in _git_bytes(project, "ls-files", "-z", "-o", "-m", "-d", "--exclude-standard", "--", *KEPT,
                                            env=env).split(b"\0") if path and not left_out(path.decode(errors="replace"))]
        if kept:
            _git(project, "add", "-A", "--", *(b":(literal)" + path for path in kept), env=env)
        tree = _git(project, "write-tree", env=env)
    finally:
        for leftover in (index, index.with_name(index.name + ".lock")):
            leftover.unlink(missing_ok=True)
    if tree == _git(project, "rev-parse", head + "^{tree}"):
        return head
    return _git(project, *COMMITTER, "commit-tree", tree, "-p", head, "-m", message)   # no hooks, no signing


def unpublishable(project, base, head, env=None):
    """What in ``base..head`` must not be published, as names to show (never content): every object new since ``base`` is
    read as git stores it — any file type, any name, every commit message — and every path a new commit introduces is
    checked for a .env, so a secret committed and later removed is found in the history the bundle carries. Fails
    closed: any git failure is an error, not a pass."""
    commits = _git(project, "rev-list", head, "--not", base, env=env).split()
    objects = _git(project, "rev-list", "--objects", "--no-object-names", head, "--not", base, env=env).split()
    if not commits:
        return []
    new, introduced, found = set(objects), {}, []   # introduced: new blob → the paths new commits put it at
    tokens = _git_bytes(project, "diff-tree", "--stdin", "-r", "-z", "-m", "--root", "--no-renames", "--no-commit-id",
                        stdin="".join(c + "\n" for c in commits).encode(), env=env).split(b"\0")
    i = 0
    while i < len(tokens):
        fields = tokens[i][1:].split(b" ") if tokens[i].startswith(b":") else []
        if len(fields) == 5 and i + 1 < len(tokens):   # ":<mode> <mode> <sha> <sha> <status>", then the path
            sha, path = fields[3].decode(), tokens[i + 1].decode(errors="replace")
            if sha in new:
                introduced.setdefault(sha, set()).add(path)
                if any(env_file(part) for part in path.split("/")):
                    found.append(path)
            i += 2
        else:
            i += 1
    for sha, kind in _suspicious_objects(project, objects, env):
        found.extend([f"提交 {sha[:7]} 的说明"] if kind == "commit" else sorted(introduced.get(sha) or {sha[:12]}))
    return list(dict.fromkeys(found))


def _suspicious_objects(project, objects, env=None):
    """(sha, type) of the blobs and commits among ``objects`` whose stored bytes match SUSPICIOUS, read through one
    ``git cat-file --batch`` in chunks (a large file is never held whole)."""
    hits = []
    with subprocess.Popen(["git", "cat-file", "--batch"], cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}) as process:
        def feed():
            try:
                process.stdin.write("".join(o + "\n" for o in objects).encode())
                process.stdin.close()
            except (OSError, ValueError):   # git ended, or the pipe was closed after a failure
                pass
        threading.Thread(target=feed, daemon=True).start()
        try:
            for _ in objects:
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise ValueError("Git 对象读取失败：" + b" ".join(header).decode(errors="replace")[:200])
                sha, kind, remaining = header[0].decode(), header[1].decode(), int(header[2])
                tail, hit = b"", False
                while remaining:
                    chunk = process.stdout.read(min(remaining, 1 << 20))
                    if not chunk:
                        raise ValueError("Git 对象读取中断")
                    remaining -= len(chunk)
                    window = tail + chunk
                    hit = hit or (kind != "tree" and SUSPICIOUS.search(window) is not None)
                    tail = window[-OVERLAP:]
                process.stdout.read(1)   # the newline after each object
                if hit:
                    hits.append((sha, kind))
        finally:
            process.kill()
    return hits


SHA = re.compile(r"[0-9a-f]{40}")


def task_candidate(project, message, branch, scratch, parent, base, start=None):
    """A task's candidate commit (D37 §3): the workspace's pending changes committed the way a publish commits them,
    everything new since ``parent`` (the previous candidate, else the clone's baseline) checked, and the branch moved only
    when nothing in it looks like a credential or a .env. An Agent that rewrote history leaves ``parent`` behind: the
    candidate is then marked ``rewritten`` and checked from the baseline."""
    parent = parent or base
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    candidate = publish_candidate(project, message, branch, scratch)
    rewritten = subprocess.run(["git", "merge-base", "--is-ancestor", parent, candidate], cwd=project,
                               capture_output=True, stdin=subprocess.DEVNULL, timeout=60).returncode != 0
    since = base if rewritten else parent
    secrets = unpublishable(project, since, candidate) if candidate != since else []
    if not secrets:
        advance(project, branch, candidate, head)
    known = subprocess.run(["git", "cat-file", "-e", parent + "^{commit}"], cwd=project, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60).returncode == 0
    files = insertions = deletions = 0
    names = []   # what the task delivered: artifacts_locked compares later tasks' changes with it (D37 §11.2)
    for entry in _git_bytes(project, "diff", "--numstat", "-z", "--no-renames", parent if known else since,
                            candidate).split(b"\0"):
        fields = entry.split(b"\t", 2)
        if len(fields) == 3:
            files += 1
            names.append(fields[2].decode(errors="replace"))
            insertions += int(fields[0]) if fields[0].isdigit() else 0   # "-" for a binary file
            deletions += int(fields[1]) if fields[1].isdigit() else 0
    changed = _git(project, "rev-parse", candidate + "^{tree}") != _git(project, "rev-parse", (parent if known else since) + "^{tree}")
    if start and subprocess.run(["git", "cat-file", "-e", start + "^{commit}"], cwd=project, capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=60).returncode == 0:
        # the task's deliverables are what it changed since it started (D37 §11.2): a retry's revert is not a change
        names = changed_names(project, start, candidate)
    return {"sha": candidate, "parent": parent, "changed": changed, "files": files, "insertions": insertions,
            "deletions": deletions, "rewritten": rewritten, "secrets": secrets, "names": names[:200],
            **({"names_truncated": True} if len(names) > 200 else {})}


def baseline_tree(project, base, start, candidate):
    """The Session's base with the task's own change (``start`` → ``candidate``) applied three-way (D37 §11.1): what its
    tests must fail on. ``ValueError`` (the gate is skipped, never judged) when there is no earlier change to compare with,
    or the change does not apply cleanly — e.g. the test task also edited a file that the base does not have."""
    tree = lambda commit: _git(project, "rev-parse", commit + "^{tree}")
    if tree(base) == tree(start):
        raise ValueError("会话中此前没有源码改动：测试无法与改动之前比较")
    done = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", "--merge-base=" + start,
                           base, candidate], cwd=project, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    lines = done.stdout.decode(errors="replace").splitlines()
    if done.returncode == 1:
        conflicts = [line for line in lines[1:] if line.strip()][:10]
        raise ValueError("测试任务的改动无法叠加到基线（与之冲突：" + "、".join(conflicts) + "），例如它同时修改了实现文件")
    if done.returncode != 0 or not lines or not SHA.fullmatch(lines[0].strip()):
        raise ValueError("无法生成基线：" + scrub(done.stderr.decode(errors="replace"))[:200])
    return lines[0].strip()


def baseline_apply(project, base, start, candidate):
    """Switch the project's tracked files to ``baseline_tree``, in place (D37 §11.1): the untracked ones — dependencies,
    virtual environments — stay. Refused (``ValueError``, nothing switched) when that would lose workspace state that is
    not in the candidate: uncommitted edits to tracked files (e.g. a tracked .env.development left out of candidates), or a
    local file where the baseline has one. ``baseline_restore`` undoes it."""
    target = baseline_tree(project, base, start, candidate)
    edited = [n for n in _git_bytes(project, "diff", "--name-only", "-z", "--no-renames", candidate).decode(
        errors="replace").split("\0") if n]
    if edited:
        raise ValueError("工作区有未进入候选的已跟踪改动（" + "、".join(edited[:10]) + "），切到基线会覆盖它们")
    added = [n for n in _git_bytes(project, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=A", candidate,
                                   target).decode(errors="replace").split("\0") if n]
    taken = []
    for name in added:
        parts = Path(name).parts
        parents = [Path(project).joinpath(*parts[:k]) for k in range(1, len(parts))]   # a local file where it needs a folder
        path = Path(project) / name
        if path.exists() or path.is_symlink() or any(p.exists() and not p.is_dir() for p in parents):
            taken.append(name)
    if taken:
        raise ValueError("工作区中的 " + "、".join(taken[:10]) + " 不在候选中，而基线有同名文件：切到基线会覆盖它")
    _git(project, "read-tree", "-u", "--reset", target)


def changed_names(project, since, candidate):
    """The files ``candidate`` changes relative to ``since`` (where its task started; D37 §11.2)."""
    raw = _git_bytes(project, "diff", "--name-only", "-z", "--no-renames", since, candidate).decode(errors="replace")
    return [name for name in raw.split("\0") if name]


def untracked(project):
    """The workspace's untracked files, neither ignored nor left out of a candidate, sorted and relative to the project
    (a nested repository is one entry, ``sub/``): what a review starts with (D57 §4)."""
    raw = _git_bytes(project, "--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                     "--no-renames")
    names = (entry[3:].decode(errors="replace") for entry in raw.split(b"\0") if entry[:3] == b"?? ")
    return sorted(name for name in names if name and not left_out(name.rstrip("/") + ("/x" if name.endswith("/") else "")))


def dirty_paths(project):
    """What the workspace has beyond its HEAD — e.g. the files a check run wrote (.coverage, reports): they are the
    platform's, not the next task's deliverables (D37 §11.2)."""
    raw = _git_bytes(project, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    return [entry[3:].decode(errors="replace") for entry in raw.split(b"\0") if len(entry) > 3][:200]


def baseline_restore(project, candidate):
    """Tracked files and the index back to ``candidate``; untracked files untouched (D37 §11.1)."""
    _git(project, "read-tree", "-u", "--reset", candidate)


DIFF_BYTES, DIFF_LINES = 200_000, 4000   # D56: what the console shows of one candidate's diff


def candidate_diff(project, sha, parent):
    """What a candidate changed against ``parent`` — the candidate before it, or the Session's baseline (D56): each file's
    line counts (all of them), and the unified diff clipped at DIFF_BYTES or DIFF_LINES. Read only; a commit the Session
    does not have is refused (ValueError). What a download and a publish leave out (.env and the like) is never shown —
    only how many such files changed; the repository's own diff settings (textconv, prefixes) are not used."""
    for commit in (sha, parent):
        _git(project, "cat-file", "-e", commit + "^{commit}")
    plain = ("--no-textconv", "--no-ext-diff", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")
    files, hidden = [], []
    for record in _git_bytes(project, "diff", "--numstat", "-z", *plain, parent, sha, "--").split(b"\0"):
        parts = record.decode(errors="replace").split("\t", 2)   # "<added>\t<deleted>\t<path>", "-" for a binary file
        if len(parts) == 3 and parts[2]:
            added, deleted, path = parts
            if left_out(path):
                hidden.append(path)
                continue
            binary = added == "-"
            files.append({"path": path, "insertions": 0 if binary else int(added), "deletions": 0 if binary else int(deleted),
                          "binary": binary})
    excluded = [":(top,literal,exclude)" + path for path in hidden]
    raw = _git_bytes(project, "-c", "core.quotePath=false", "diff", "--no-color", *plain, parent, sha, "--", ".", *excluded) \
        if files else b""
    lines = raw[:DIFF_BYTES].decode(errors="replace").splitlines(keepends=True)
    return {"files": files, "hidden": len(hidden), "diff": "".join(lines[:DIFF_LINES]),
            "truncated": len(raw) > DIFF_BYTES or len(lines) > DIFF_LINES}


def ancestor(project, old, new, env=None):
    """Whether ``old`` is an ancestor of (or is) ``new``."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", old, new], cwd=project, capture_output=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}).returncode == 0


def change_stats(project, old, new, env=None):
    """What ``old..new`` changed, as a candidate records it (D37): files, inserted and deleted lines, the paths."""
    names, insertions, deletions = [], 0, 0
    for record in _git_bytes(project, "diff", "--numstat", "-z", "--no-renames", old, new, "--", env=env).split(b"\0"):
        parts = record.decode(errors="replace").split("\t", 2)
        if len(parts) == 3 and parts[2]:
            names.append(parts[2])
            insertions += int(parts[0]) if parts[0].isdigit() else 0
            deletions += int(parts[1]) if parts[1].isdigit() else 0
    return {"files": len(names), "insertions": insertions, "deletions": deletions, "names": names}


def clean_against(project, expected):
    """Whether the workspace is still ``expected`` (D37 §6, a review must not change code): HEAD, and uncommitted changes
    to tracked files outside what a download and a publish leave out."""
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    # Untracked files are what checks and test runs leave (.coverage, reports/, dist/): never part of a commit.
    raw = _git_bytes(project, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=no")
    changed = [path for path in (entry[3:].decode(errors="replace") for entry in raw.split(b"\0") if len(entry) > 3)
               if not left_out(path.rstrip("/") + ("/x" if path.endswith("/") else ""))]
    return {"head": head, "clean": head == expected and not changed, "changed_files": changed}


def restore(project, paths):
    """Tracked files back to HEAD, in the index and the working tree (D46 P4: what a read-only task changed)."""
    # one HEAD lacks is removed; names are names, not patterns (a file called "*" restores itself only)
    _git(project, "--literal-pathspecs", "restore", "--source=HEAD", "--staged", "--worktree", "--", *paths)


def rewind(project, expected, keep=None, into=None):
    """A review's changes put back (D57 §4): commits made on top of the candidate ``expected`` dropped — the candidate and
    what came before it, the implementer's, are never touched — and what remains changed in tracked files restored: what a
    check counts (``clean_against``), and nothing else. ``reverted`` lists those tracked files. Given ``into`` (outside
    the project), what they had — staged, in the working tree, in the dropped commits — is first kept in a patch there
    (``_keep_patch``) that ``git apply`` restores on the candidate, named by ``patch`` ("review-removed/tracked.patch"):
    D73 puts people in the same workspace, and their edits during a review are never lost. Then, given ``keep`` (the
    untracked files the review started with), the untracked files it created are moved into ``into``, never deleted:
    ``removed`` lists them (``_move_out``); ``listing_failed`` when they could not be listed (none moved, the tracked
    files put back all the same). Its limits:

    - without ``keep`` the untracked files the reviewer created (and never committed) are kept: the next candidate's
      ``git add -A`` takes them; an untracked file in ``keep`` the reviewer changed stays as it is;
    - an untracked file outside what candidates leave out that a reviewer's ``git add -A`` swept into its commit (a report
      the checks left) is removed along with that commit;
    - a reviewer's changes to tracked files that candidates leave out (``node_modules``, a .env) are not undone; such a
      file a dropped commit added stays in the workspace, untracked again; ignored files are never touched.

    ``ValueError``, with nothing moved, when HEAD is not ``expected`` or a commit on top of it, or ``into`` is in the
    project."""
    if into is not None and Path(into).resolve().is_relative_to(Path(project).resolve()):
        raise ValueError("移出审查新建文件的目录不能在项目中")
    head = _git(project, "rev-parse", "--verify", "HEAD^{commit}")
    rewound = 0
    if head != expected:
        if not ancestor(project, expected, head, env=SAFE_GIT_ENV):
            raise ValueError(f"会话分支不在候选 {expected[:7]} 之上（HEAD {head[:7]}），不能撤回")
        rewound = int(_git(project, "rev-list", "--count", f"{expected}..{head}", env=SAFE_GIT_ENV))
        # HEAD only: the dropped commits' changes become staged changes, put back below like any other
        _git(project, "-c", "core.hooksPath=" + os.devnull, "reset", "-q", "--soft", expected, env=SAFE_GIT_ENV)
    reverted, kept = clean_against(project, expected)["changed_files"], None
    if reverted and into is not None:   # a copy of what is put back, before it is
        kept = _keep_patch(project, expected, reverted, Path(into))
    if reverted:
        restore(project, reverted)
    if rewound:   # what is left staged (a left-out file a dropped commit added) back to untracked; files untouched
        _git(project, "-c", "core.hooksPath=" + os.devnull, "reset", "-q", env=SAFE_GIT_ENV)
    removed = [] if keep is None else _move_out(project, keep, into)
    return {**clean_against(project, expected), "reverted": reverted, "rewound": rewound, "removed": removed or [],
            **({"listing_failed": True} if removed is None else {}),
            **({"patch": f"{Path(into).name}/{kept}"} if kept else {})}


def _keep_patch(project, expected, paths, into):
    """``paths``' changes from ``expected`` (the index, the working tree and the commits after it alike) written to a
    new ``tracked.patch`` in ``into`` (``tracked-2.patch``… when taken), as ``git apply`` takes it on ``expected``:
    binary, no renames, no external diff or text conversion, a/ and b/ whatever the configuration says. Its name; None
    when the diff is empty."""
    diff = _git_bytes(project, "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "--literal-pathspecs",
                      "diff", "--binary", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", expected,
                      "--", *paths, env=SAFE_GIT_ENV)
    if not diff:
        return None
    try:   # never put back what could not be kept: the rewind fails instead (review_bound's fail), the edits stay
        into.mkdir(parents=True, exist_ok=True)
        for number in range(1, 1000):
            name = "tracked.patch" if number == 1 else f"tracked-{number}.patch"
            try:
                with open(into / name, "xb") as stream:
                    stream.write(diff)
                return name
            except FileExistsError:
                continue
    except OSError as exc:
        raise ValueError(f"审查改动的副本未能保存，未撤回：{exc.strerror or exc}") from None
    raise ValueError("审查改动的副本无处保存，未撤回")


def _move_out(project, keep, into):
    """The untracked files not in ``keep`` moved into ``into`` at the same relative paths, sorted: never deleted, a link
    moved as a link (never followed). A path that would resolve outside the project, one whose place in ``into`` is taken,
    one that cannot be moved and a folder git lists as one entry (``tools/``, a repository the reviewer made) holding a file
    of ``keep`` stay where they are. Folders the moves leave empty are removed, never the project. None, with nothing
    moved, when the untracked files cannot be listed (a large workspace past git's time)."""
    try:
        names = untracked(project)
    except ValueError:   # after the tracked files were put back: that stands
        return None
    root, keep, removed = Path(project).resolve(), set(keep), []
    for name in names:
        parts = name.rstrip("/").split("/")
        if name in keep or name.startswith("/") or any(part in {"", ".", ".."} for part in parts) \
                or name.endswith("/") and any(path.startswith(name) for path in keep):
            continue
        source, target = root.joinpath(*parts), Path(into).joinpath(*parts)
        if not source.parent.resolve().is_relative_to(root) or not os.path.lexists(source) or os.path.lexists(target):
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))   # a rename; across file systems a copy that keeps links as links
        except OSError:
            continue
        removed.append(name)
        folder = source.parent
        while folder != root:
            try:
                folder.rmdir()   # only when empty
            except OSError:
                break
            folder = folder.parent
    return sorted(removed)


def advance(project, branch, commit, old):
    """Put the checked ``commit`` on ``branch`` (still at ``old``) and the workspace's index to match; files untouched."""
    if commit == old:
        return
    if _git(project, "rev-parse", "--verify", "HEAD^{commit}") != old \
            or _git(project, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise ValueError("发布期间工作区的分支被改动，未发布，请重试")
    _git(project, "reset", "-q", "--mixed", commit)


def scrub(text, secret=None):
    """Git output as it may be shown or stored: no URL userinfo, no Authorization / PRIVATE-TOKEN line, no secret."""
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1", str(text))
    text = re.sub(r"(?im)^.*\b(authorization|private-token)\s*:.*$", HIDDEN, text)
    return text.replace(secret, HIDDEN) if secret else text


# Authenticated git ignores the system and the user's global configuration — an agent of the same OS user could
# redirect the token there (url.<x>.insteadOf) or add a helper — speaks HTTPS only and follows no redirect.
SAFE_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
SAFE_GIT_ARGS = ("-c", "credential.helper=", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                 "-c", "http.followRedirects=false")


@contextmanager
def askpass(run, auth):
    """GIT_ASKPASS answering git's Username / Password prompts from a 0600 file, both removed when the clone ends. It
    answers only the prompts for its own host (and user), so the token goes nowhere else."""
    host, user = str(auth.get("host", "")), str(auth.get("username", ""))
    if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host) or not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        raise ValueError("凭证的主机或用户名无效")
    folder = run / ".askpass"
    shutil.rmtree(folder, ignore_errors=True)   # left by a crash
    folder.mkdir(mode=0o700)
    try:
        secret = folder / "secret"
        fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(auth["secret"])
        script = folder / "askpass.sh"
        script.write_text("#!/bin/sh\ncase \"$1\" in\n"
                          f"  \"Username for 'https://{host}'\"*) printf '%s\\n' {shlex.quote(user)} ;;\n"
                          f"  \"Password for 'https://{user}@{host}'\"*) cat {shlex.quote(str(secret))} ;;\n"
                          "  *) exit 1 ;;\nesac\n")
        script.chmod(0o700)
        yield {**SAFE_GIT_ENV, "GIT_ASKPASS": str(script)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def read_record(run):
    path = run / "project.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("项目记录不能是符号链接。")
    return json.loads(path.read_text())


def _write(run, value):
    temporary = run / (".project-" + uuid.uuid4().hex + ".json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, run / "project.json")


def _git(cwd, *args, env=None):
    return _git_bytes(cwd, *args, env=env).decode(errors="replace").strip()


def _git_bytes(cwd, *args, env=None, stdin=None):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                                capture_output=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError(f"Git 操作超时（{args[0] if args else 'git'}，180 秒）") from None
    if result.returncode:
        raise ValueError(f"Git 操作失败（exit {result.returncode}）："
                         + (result.stderr.strip() or result.stdout.strip())[-3000:].decode(errors="replace"))
    return result.stdout


def normalize_source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError("请明确提供用户指定的 Git 仓库，或选择新建项目。")
    source = source.strip()
    if any(ord(c) < 32 for c in source) or source.startswith("-"):
        raise ValueError("仓库来源包含非法字符。")
    if source.startswith(("https://", "ssh://")):
        url = urlsplit(source)
        if not url.hostname or url.password or url.query or url.fragment:
            raise ValueError("仓库地址不能包含密码、查询参数或片段；使用 Git 原有认证。")
        if url.scheme == "https" and url.username:
            raise ValueError("HTTPS 仓库地址不能嵌入凭据；使用 Git credential helper。")
        return source, "git"
    if re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s]+", source):
        return source, "git"
    path = Path(source).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("支持 HTTPS、SSH 仓库或绝对路径的本地 Git 仓库。")
    return str(path.resolve()), "local-git"


def project_dir(run):
    record = read_record(run)
    if not record or record.get("status") != "ready":
        raise ValueError("项目源码尚未就绪，请先完成 workspace 准备。")
    expected = run / "workspace/project"
    if (run / "workspace").is_symlink() or expected.is_symlink():
        raise ValueError("项目目录不能通过符号链接指向其他项目。")
    if record.get("directory") != "workspace/project" or not expected.is_dir():
        raise ValueError("已记录的项目目录不存在或不属于本次运行。")
    if record["kind"] != "new":
        if record["kind"] not in {"git", "local-git"} or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", str(record.get("commit", ""))):
            raise ValueError("项目记录的源码类型或 commit 无效。")
        if not (expected / ".git").is_dir() or (expected / ".git").is_symlink():
            raise ValueError("项目 Git 元数据缺失或被替换；停止派发并核查。")
        # The baseline commit must still exist. User-authorized commits may
        # advance HEAD, so readiness does not force-reset later development.
        _git(expected, "cat-file", "-e", record["commit"] + "^{commit}")
    return expected


def adopt_project(run, record, bundle):
    """A Session's project taken over from another boundary (D74): its branch with the whole history, from ``bundle``,
    as the same repository — the same commits, branch and baseline (``record``, the project record the platform kept),
    so its candidates and reviews still hold. The bundle must hold this Session's branch alone, and the baseline must
    be its ancestor; a cloned source keeps its origin, which agents cannot push to; a new project has none."""
    run, bundle = Path(run), Path(bundle)
    if not run.is_dir() or not (run / "workflow-run.json").is_file():
        raise ValueError("缺少有效 Workflow Run。")
    branch = "octopus/" + run.name
    if not isinstance(record, dict) or record.get("branch") != branch or record.get("kind") not in {"new", "git"} \
            or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", str(record.get("commit", ""))):
        raise ValueError("接手的项目记录无效：分支、来源类型或基线 commit 不符。")
    workspace = run / "workspace"
    if workspace.is_symlink():
        raise ValueError("运行工作区不能是符号链接。")
    workspace.mkdir(exist_ok=True)
    destination = workspace / "project"
    if destination.exists() or destination.is_symlink():
        raise ValueError("项目目录已存在；接手只在新的工作区中进行。")
    heads = _git(workspace, "bundle", "list-heads", str(bundle)).splitlines()
    if len(heads) != 1 or heads[0].partition(" ")[2] != "refs/heads/" + branch:
        raise ValueError("接手的内容须只含此会话的分支。")
    staging = workspace / (".adopt-" + uuid.uuid4().hex)
    try:
        _git(workspace, "clone", "-q", "--no-hardlinks", "-b", branch, "--", str(bundle), str(staging))
        head = _git(staging, "rev-parse", "HEAD")
        if subprocess.run(["git", "-C", str(staging), "merge-base", "--is-ancestor", record["commit"], head],
                          capture_output=True).returncode != 0:
            raise ValueError("接手的分支不是从此会话的基线开始的。")
        if record["kind"] == "git":
            _git(staging, "remote", "set-url", "origin", record["source"])
            _git(staging, "remote", "set-url", "--push", "origin", "DISABLED_NO_PUSH")
        else:
            _git(staging, "remote", "remove", "origin")
        os.replace(staging, destination)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("接手的源码准备失败：" + scrub(exc)) from None
    state = {"schema": 1, "status": "ready", "kind": record["kind"], "source": record.get("source"),
             "requested_ref": record.get("requested_ref"), "directory": "workspace/project", "commit": record["commit"],
             "branch": branch, "adopted": True, "at": time.time()}
    _write(run, state)
    return state


def prepare_project(run, *, source=None, ref=None, new=False, auth=None):
    run = Path(run)
    if not run.is_dir() or not (run / "workflow-run.json").is_file():
        raise ValueError("缺少有效 Workflow Run。")
    if new == bool(source):
        raise ValueError("明确选择 --source 仓库，或 --new 新建项目。")
    if new and ref:
        raise ValueError("新建项目不使用仓库 ref。")
    source, kind = (None, "new") if new else normalize_source(source)
    ref = ref.strip() if isinstance(ref, str) else ref
    if ref is not None and (not ref or ref.startswith("-") or "\x00" in ref):
        raise ValueError("仓库 ref 无效。")
    workspace = run / "workspace"
    if workspace.is_symlink():
        raise ValueError("运行工作区不能是符号链接。")
    workspace.mkdir(exist_ok=True)
    destination = workspace / "project"
    with (run / ".project.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read_record(run)
        if previous and previous.get("status") == "ready":
            if (previous["source"], previous["requested_ref"], previous["kind"]) != (source, ref, kind):
                raise ValueError("本次源码来源已经固定；更换来源或 ref 请创建新运行。")
            project_dir(run)
            return previous
        if destination.exists() or destination.is_symlink():
            raise ValueError("项目目录已有未登记文件；保留文件，请核实来源后处理。")
        state = {"schema": 1, "status": "preparing", "kind": kind, "source": source,
                 "requested_ref": ref, "directory": "workspace/project", "at": time.time()}
        _write(run, state)
        staging = workspace / (".checkout-" + uuid.uuid4().hex)
        try:
            if new:
                # Like a cloned source: a repository on octopus/<run> with an empty first commit, so the download
                # carries the history and the agents' commits have a baseline. The platform's own commit runs no hooks.
                staging.mkdir()
                branch = "octopus/" + run.name
                _git(staging, "init", "-q", "-b", branch)
                _git(staging, "-c", "user.name=KiroCrew ON", "-c", "user.email=kirocrew-on@localhost",
                     "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--allow-empty",
                     "-m", "KiroCrew ON：新建项目")
                commit = _git(staging, "rev-parse", "HEAD")
            else:
                if kind == "local-git":
                    source_path = Path(source)
                    _git(source_path, "rev-parse", "--show-toplevel")
                    if not ref and _git(source_path, "status", "--porcelain", "--untracked-files=normal"):
                        raise ValueError("本地来源有未提交改动；本操作检出 Git 版本，不会隐式丢弃或提交改动。"
                                         "请用 --ref 明确选择已提交基线，或先保存所需源码版本。")
                if auth:
                    # No other credential helper answers; the remote keeps the plain URL; agents cannot push with it.
                    with askpass(run, auth) as env:
                        _git(workspace, *SAFE_GIT_ARGS, "clone", "--no-hardlinks", "--no-checkout", "--",
                             source, str(staging), env=env)
                    _git(staging, "remote", "set-url", "--push", "origin", "DISABLED_NO_PUSH")
                else:
                    _git(workspace, "clone", "--no-hardlinks", "--no-checkout", "--", source, str(staging))
                selected = ref or "HEAD"
                try:
                    commit = _git(staging, "rev-parse", "--verify", "--end-of-options", selected + "^{commit}")
                except ValueError:
                    if not ref:
                        raise
                    commit = _git(staging, "rev-parse", "--verify", "--end-of-options", "origin/" + ref + "^{commit}")
                branch = "octopus/" + run.name
                _git(staging, "checkout", "-b", branch, commit)
                if not any(p.name != ".git" for p in staging.iterdir()):
                    raise ValueError("仓库检出不含工作文件，无法作为已有项目开始研发。")
            os.replace(staging, destination)
            state.update(status="ready", commit=commit, branch=branch, at=time.time())
            _write(run, state)
            return state
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            reason = scrub(exc, auth and auth.get("secret"))
            state.update(status="failed", error=reason, at=time.time())
            if staging.exists():
                state["retained_attempt"] = str(staging.relative_to(run))
            _write(run, state)
            raise ValueError("源码准备失败：" + reason) from None
