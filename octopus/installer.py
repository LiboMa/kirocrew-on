"""`sudo kirocrew-on setup`: the pip channel's system steps (MVP 0.2.0 P2 Task 3, design §4.4). The wheel a channel
publishes carries install.sh with the release's addresses and public key written in (tools/build_release.py --index);
setup runs that install.sh for the package's own version, so the release it installs is downloaded and verified like
any other (review 2026-10-28: pip's check of an index on another domain is not trusted alone) and both channels share
the preflight and every install step. A package built without --index has no key and installs nothing."""
import os

from . import __version__, release


def setup():
    if os.geteuid() != 0:
        raise SystemExit("需要 root：请以 root 身份运行 kirocrew-on setup")
    script = release.path("deploy/install/install.sh")
    if "-----BEGIN PUBLIC KEY-----" not in script.read_text():
        raise SystemExit("这个包没有发布公钥（不是从发布频道装的）：请用发布站点上的 install.sh 安装")
    os.execvp("sh", ["sh", str(script), "--version", __version__])
