"""Compatibility layer for the macOS PoC entry points; the composition lives in composition/."""
from pathlib import Path
import os

from composition import environment as _environment
from composition import templates as _templates
from composition.registry import BACKENDS as _CURRENT, LEGACY_BACKENDS as _LEGACY, SUPPORTED_VERSIONS
from composition.registry import PocProviderRegistry as _Registry
from octopus.config import load as _load

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
CREW = Path.home() / ".kiro/crew"
KIRO = Path.home() / ".kiro"
PORT = 5476
BACKENDS = {**_CURRENT, **_LEGACY}
RESOURCES = Path("/Applications/KiroCrew.app/Contents/Resources")
BIN = RESOURCES / "backend-dist/kirocrew-backend-arm64/bin"
PYTHON = BIN / "python3.12"


def _settings():
    adapters = ROOT / "node_modules/.bin"
    return _load(overrides={
        "crew_home": str(CREW), "kiro_home": str(KIRO), "gateway_port": PORT,
        "worker_data_dir": str(STATE / "runs"), "routing_log": str(ROOT / "evidence/provider-routing.jsonl"),
        "gateway_runtime_file": str(STATE / "gateway-runtime.json"), "log_dir": str(STATE),
        "kirocrew_python": str(PYTHON), "adapters_dir": str(adapters),
        "path_prepend": os.pathsep.join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin"]),
    })


def prepare_environment():
    _environment.prepare(_settings(), os.environ)


def seed():
    _templates.seed(crew_home=CREW, kiro_home=KIRO, allowed_roots=[STATE / "runs"], port=PORT, legacy_pipeline=True)


class PocProviderRegistry(_Registry):
    def __init__(self, original, routing_log=None):
        super().__init__(original, routing_log or ROOT / "evidence/provider-routing.jsonl")


def main():
    from composition.gateway import main as gateway_main
    import sys
    argv = ["--legacy-pipeline"] + (["--check"] if "--check" in sys.argv else [])
    os.environ.update({"OCTOPUS_CREW_HOME": str(CREW), "OCTOPUS_KIRO_HOME": str(KIRO),
                       "OCTOPUS_GATEWAY_PORT": str(PORT), "OCTOPUS_WORKER_DATA_DIR": str(STATE / "runs"),
                       "OCTOPUS_ROUTING_LOG": str(ROOT / "evidence/provider-routing.jsonl"),
                       "OCTOPUS_GATEWAY_RUNTIME_FILE": str(STATE / "gateway-runtime.json"),
                       "OCTOPUS_LOG_DIR": str(STATE), "OCTOPUS_KIROCREW_PYTHON": str(PYTHON),
                       "OCTOPUS_ADAPTERS_DIR": str(ROOT / "node_modules/.bin"),
                       "OCTOPUS_PATH_PREPEND": os.pathsep.join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin"])})
    return gateway_main(argv)


if __name__ == "__main__":
    main()
