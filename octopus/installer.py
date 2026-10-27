"""`sudo kirocrew-on setup`: the pip channel's system steps (MVP 0.2.0 P2 Task 3, design §4.4). pip installed the package;
this lays it out as install.sh unpacks a verified wheel — the packages, with deploy/, skills/ and the npm and pip lists
where a checkout has them — and runs install.sh on it, so both channels share the preflight and every install step."""
import os
from pathlib import Path
import shutil

from . import __version__, release

INCOMING = Path("/opt/kirocrew-on/incoming")
CARRIED = ("deploy", "skills", "package.json", "package-lock.json", "requirements-workflow.txt")
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "_release")


def assemble(stage):
    import composition
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(release.PACKAGE, stage / "octopus", ignore=SKIP)
    shutil.copytree(Path(composition.__file__).resolve().parent, stage / "composition", ignore=SKIP)
    for name in CARRIED:
        source = release.path(name)
        if source.is_dir():
            shutil.copytree(source, stage / name, ignore=SKIP)
        else:
            shutil.copy2(source, stage / name)


def setup(incoming=INCOMING):
    if os.geteuid() != 0:
        raise SystemExit("需要 root：请以 root 身份运行 kirocrew-on setup")
    stage = incoming / f"{__version__}-pip"
    assemble(stage)
    script = stage / "deploy/install/install.sh"
    os.execvp("sh", ["sh", str(script), "--source", str(stage), "--version", __version__])
