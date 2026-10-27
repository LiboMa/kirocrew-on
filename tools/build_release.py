"""Build a KiroCrew ON release (MVP 0.2.0 P1 Task 9, S1, design §4.4) with the standard library only:

- ``kirocrew_on-<version>-py3-none-any.whl``: the ``octopus`` and ``composition`` packages, the console build
  (``octopus/ui/dist``) and, under ``octopus/_release``, ``deploy/`` and ``skills/`` (found through ``octopus.release``);
- ``manifest.json``: the version, the channel, the wheel's sha256 and what the release was tested with
  (``deploy/versions.env``), schema ``deploy/release/manifest.schema.json``;
- ``SHA256SUMS``; and with ``--sign-key``, ``manifest.sig`` (``openssl dgst -sha256 -sign``).

With ``--index <feed>`` (MVP 0.2.0 P2 Task 3; needs ``--sign-key``, ``--downloads`` and ``--feed``) also the channel's
pointers and the installer:

- ``<feed>/<channel>/latest.json`` (``install.sh`` reads it when no ``--version`` is given) and ``versions.json``;
- ``<feed>/<channel>/simple/kirocrew-on/index.html``, a PEP 503 index whose links carry ``#sha256=`` (the pip channel);
- ``<out>/install.sh``: ``deploy/install/install.sh`` with the two addresses and the signing key's public half written in.

The wheel is reproducible: files in a fixed order with a fixed timestamp. Nothing is uploaded (the hosting comes with CI).

    npm --prefix web run build && python3 tools/build_release.py --out dist/releases/<版本> --sign-key key.pem \\
        --index dist/feed --downloads https://<下载域名>/releases --feed https://<更新域名>/feed
"""
import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("octopus", "composition")
# outside the packages in a checkout; octopus/_release in the wheel (install.sh puts them back where a checkout has them)
CARRIED = ("deploy", "skills", "package.json", "package-lock.json", "requirements-workflow.txt")
OWN = {"deploy/aws/stack.env"}   # this deployment's stack, domains and pins: not something to hand to another one
STAMP = (1980, 1, 1, 0, 0, 0)


def version():
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "octopus/__init__.py").read_text()).group(1)


def pins_file(path=ROOT / "deploy/versions.env"):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#") and "=" in line)


def node_sums(dist, given=None):
    """The sha256 of Node's linux .tar.xz for x64 and arm64, from the release's SHASUMS256.txt (or the file given)."""
    text = Path(given).read_text() if given else urllib.request.urlopen(
        f"https://nodejs.org/dist/{dist}/SHASUMS256.txt", timeout=30).read().decode()
    sums = {}
    for line in text.splitlines():
        digest, _, name = line.partition("  ")
        for arch in ("x64", "arm64"):
            if name == f"node-{dist}-linux-{arch}.tar.xz":
                sums[arch] = digest
    missing = {"x64", "arm64"} - set(sums)
    if missing:
        raise SystemExit(f"SHASUMS256.txt 里没有 Node {dist} 的 linux-{'/'.join(sorted(missing))}.tar.xz")
    return sums


def files():
    """(name in the wheel, path) for every file the wheel carries, in a fixed order: what git tracks under the packages,
    deploy/ and skills/ (an untracked file a checkout holds never ships), except a deployment's own settings, and the
    console build, which git ignores."""
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--", *PACKAGES, *CARRIED], check=True,
                             capture_output=True, text=True).stdout.split("\0")
    built = [p.relative_to(ROOT).as_posix() for p in (ROOT / "octopus/ui/dist").rglob("*") if p.is_file()]
    picked = []
    for relative in sorted(set(filter(None, tracked)) | set(built)):
        path = ROOT / relative
        if not path.is_file() or "__pycache__" in relative or relative.endswith(".pyc") or relative in OWN:
            continue
        top = relative.split("/", 1)[0]
        picked.append((relative if top in PACKAGES else f"octopus/_release/{relative}", path))
    return picked


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build_wheel(out, ver):
    if not (ROOT / "octopus/ui/dist/index.html").is_file():
        raise SystemExit("没有控制台构建：先执行 npm --prefix web run build")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    info = f"kirocrew_on-{ver}.dist-info"
    meta = {
        f"{info}/METADATA": (f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {ver}\nSummary: {project['description']}\n"
                             f"Requires-Python: {project['requires-python']}\n"),
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: kirocrew-on tools/build_release.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{info}/entry_points.txt": "[console_scripts]\n" + "".join(f"{k} = {v}\n" for k, v in project["scripts"].items()),
        f"{info}/top_level.txt": "".join(p + "\n" for p in PACKAGES),
    }
    wheel = out / f"kirocrew_on-{ver}-py3-none-any.whl"
    record = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        def add(name, data, mode=0o644):
            entry = zipfile.ZipInfo(name, STAMP)
            entry.external_attr = (0o100000 | mode) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
            record.append(f"{name},{record_hash(data)},{len(data)}")
        for name, path in files():
            add(name, path.read_bytes(), 0o755 if path.stat().st_mode & 0o111 else 0o644)
        for name, text in meta.items():
            add(name, text.encode())
        add(f"{info}/RECORD", ("\n".join(record + [f"{info}/RECORD,,"]) + "\n").encode())
    return wheel


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installer(out, downloads, feed, sign_key):
    """``install.sh`` with where releases live and the public key their manifests are signed with."""
    public = subprocess.run(["openssl", "pkey", "-in", sign_key, "-pubout"], check=True, capture_output=True,
                            text=True).stdout.strip()
    text = (ROOT / "deploy/install/install.sh").read_text()
    for placeholder, value in (('"__KIROCREW_ON_DOWNLOADS__"', f'"{downloads}"'), ('"__KIROCREW_ON_FEED__"', f'"{feed}"'),
                               ("'__KIROCREW_ON_PUBLIC_KEY__'", f"'{public}'")):
        if text.count(placeholder) != 1:
            raise SystemExit(f"install.sh 里找不到 {placeholder}")
        text = text.replace(placeholder, value)
    path = out / "install.sh"
    path.write_text(text)
    path.chmod(0o755)
    return path


def index(feed, channel, ver, wheel, downloads, requires):
    """The channel's latest.json, the versions it holds and its PEP 503 simple index."""
    folder = feed / channel
    (folder / "simple/kirocrew-on").mkdir(parents=True, exist_ok=True)
    listed = folder / "versions.json"
    versions = [v for v in (json.loads(listed.read_text()) if listed.exists() else []) if v["version"] != ver]
    versions.append({"version": ver, "wheel": wheel.name, "sha256": sha256(wheel)})
    listed.write_text(json.dumps(versions, indent=2) + "\n")
    (folder / "latest.json").write_text(json.dumps({"version": ver, "channel": channel}, indent=2) + "\n")
    links = "".join(f'<a href="{html.escape(f"{downloads}/{v["version"]}/{v["wheel"]}")}#sha256={v["sha256"]}" '
                    f'data-requires-python="{html.escape(requires)}">{html.escape(v["wheel"])}</a><br>\n' for v in versions)
    (folder / "simple/kirocrew-on/index.html").write_text(
        f"<!DOCTYPE html>\n<html><head><title>Links for kirocrew-on</title></head><body>\n{links}</body></html>\n")
    (folder / "simple/index.html").write_text(
        '<!DOCTYPE html>\n<html><head><title>Simple index</title></head><body>\n'
        '<a href="kirocrew-on/">kirocrew-on</a><br>\n</body></html>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description="构建 KiroCrew ON 发布物：wheel、manifest.json、SHA256SUMS（可选签名）")
    parser.add_argument("--out", required=True)
    parser.add_argument("--channel", choices=["preview", "stable"], default="preview")
    parser.add_argument("--sign-key", help="PEM 私钥：另出 manifest.sig")
    parser.add_argument("--node-sums", help="Node 的 SHASUMS256.txt（默认从 nodejs.org 下载）")
    parser.add_argument("--index", help="频道指针目录：写 <目录>/<频道>/latest.json 与 PEP 503 索引，另出带地址与公钥的 install.sh")
    parser.add_argument("--downloads", help="发布物的根地址（<地址>/<版本>/<文件>），--index 时必填")
    parser.add_argument("--feed", help="频道指针的根地址（<地址>/<频道>/latest.json），--index 时必填")
    args = parser.parse_args(argv)
    if args.index and not (args.sign_key and args.downloads and args.feed):
        parser.error("--index 需要 --sign-key、--downloads 与 --feed：install.sh 只装签过名的发布")
    for url in filter(None, (args.downloads, args.feed)):
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?", url) or url.endswith("/"):
            parser.error(f"地址要是 https://，末尾不带 /：{url}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ver, pins = version(), pins_file()
    wheel = build_wheel(out, ver)
    manifest = {
        "version": ver, "channel": args.channel, "wheel": {"name": wheel.name, "sha256": sha256(wheel)},
        # KiroCrew is built from this pinned commit until it ships a release wheel (S26's preconditions, P1b)
        "pins": {"kirocrew": {"source": "git", "git_ref": pins["KIROCREW_GIT_REF"],
                              "version": pins["KIROCREW_ACCEPTED_VERSION"]},
                 "node": {"version": pins["NODE_DIST"], "sha256": node_sums(pins["NODE_DIST"], args.node_sums)},
                 "kiro_cli": pins["KIRO_CLI_VERSION"], "claude_code": pins["CLAUDE_CODE_VERSION"],
                 "codex": pins["CODEX_VERSION"], "opencode": pins.get("OPENCODE_VERSION", "")},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if args.sign_key:
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", args.sign_key, "-out", str(out / "manifest.sig"),
                        str(out / "manifest.json")], check=True)
    names = [wheel.name, "manifest.json"] + (["manifest.sig"] if args.sign_key else [])
    if args.index:
        names.append(installer(out, args.downloads, args.feed, args.sign_key).name)
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        index(Path(args.index), args.channel, ver, wheel, args.downloads, project["requires-python"])
    (out / "SHA256SUMS").write_text("".join(f"{sha256(out / n)}  {n}\n" for n in names))
    print(json.dumps({"out": str(out), **manifest["wheel"]}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
