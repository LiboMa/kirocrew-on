#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
npm ci --prefix "$PWD" --no-audit --no-fund
export PYTHONDONTWRITEBYTECODE=1
/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 -s - <<'PY'
import json
from pathlib import Path
import kiro_crew
from kiro_crew.config import KiroCrewConfig
from poc import SUPPORTED_VERSIONS
root=Path.cwd()
if kiro_crew.__version__ not in SUPPORTED_VERSIONS:
    raise SystemExit("Supported KiroCrew versions: " + ", ".join(sorted(SUPPORTED_VERSIONS)) +
                     "; other versions need contract verification.")
home=Path.home()/".kiro/crew"
path=home/"config.json"
data=json.loads(path.read_text())
backup=root/"backups/pre-poc-config.json"
backup.parent.mkdir(exist_ok=True, mode=0o700)
if not backup.exists():
    backup.write_bytes(path.read_bytes())
    backup.chmod(0o600)
cfg=KiroCrewConfig.load()
data.setdefault("agent",{})["session_sharing"]=False
data.setdefault("session",{})["pool_size"]=0
roots=data["agent"].setdefault("subagent_cwd_allowed_roots",list(cfg.agent.subagent_cwd_allowed_roots))
if str(root/"state/runs") not in roots:
    roots.append(str(root/"state/runs"))
path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
print("Configured existing ProviderRegistry experiment; existing Sessions retained.")
print("No automatic change to this customer's memory admission threshold.")
PY
