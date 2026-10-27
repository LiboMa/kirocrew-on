"""Files of a release that sit outside the octopus package in a checkout (deploy/, skills/): from the checkout when
running from source, else from the copy the wheel carries in octopus/_release (MVP 0.2.0 P1, tools/build_release.py)."""
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
CHECKOUT = PACKAGE.parent


def path(name):
    """``name`` (e.g. "skills/kirocrew-on/SKILL.md") in the checkout, or in the installed wheel."""
    here = CHECKOUT / name
    return here if here.exists() else PACKAGE / "_release" / name
