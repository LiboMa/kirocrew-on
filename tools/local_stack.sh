#!/usr/bin/env bash
# Local end-to-end stack from this checkout on isolated ports and data; never touches ~/.kiro.
#   tools/local_stack.sh init|start|stop|status|restart <gateway|worker|api>
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BASE="${LOCAL_E2E_DIR:-$ROOT/state/local-e2e}"
RUN="$BASE/run"
LOGS="$BASE/logs"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
KC_PY="${KIROCREW_PYTHON:-/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12}"
SERVICES=(gateway worker api)

init() {
  mkdir -p "$RUN" "$LOGS" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/platform"
  chmod 700 "$BASE"
  local kiro_cli
  kiro_cli="$(command -v kiro-cli || true)"
  if [ ! -f "$BASE/boundary.toml" ]; then
    cat > "$BASE/boundary.toml" <<EOF
worker_data_dir = "$BASE/worker"
worker_token_file = "$BASE/worker.token"
crew_home = "$BASE/crew"
kiro_home = "$BASE/kiro"
routing_log = "$BASE/provider-routing.jsonl"
gateway_runtime_file = "$BASE/gateway-runtime.json"
log_dir = "$LOGS"
worker_port = 8938
gateway_port = 6476
worker_isolation = "trusted-local"
approval_mode = "auto"
crew_url = "http://localhost:6476"
kirocrew_python = "$KC_PY"
adapters_dir = "$ROOT/node_modules/.bin"
path_prepend = ["$HOME/.local/bin", "/opt/homebrew/bin"]

[tools]
claude = "$HOME/.local/bin/claude"
codex = "$ROOT/node_modules/.bin/codex"
opencode = "/opt/homebrew/bin/opencode"
EOF
    if [ -n "$kiro_cli" ]; then echo "kiro_cli = \"$kiro_cli\"" >> "$BASE/boundary.toml"; fi
  fi
  if [ ! -f "$BASE/platform.toml" ]; then
    cat > "$BASE/platform.toml" <<EOF
data_dir = "$BASE/platform"
log_dir = "$LOGS"
api_port = 8948
engine_workers = 4
retry_unavailable = 2.0
EOF
  fi
}

pid_of() {
  local file="$RUN/$1.pid" pid
  [ -f "$file" ] || return 1
  pid="$(cat "$file")"
  # Only our own process: the command line must reference this stack's directory.
  ps -p "$pid" -o command= 2>/dev/null | grep -q -- "$BASE" && echo "$pid"
}

launch() {
  local service="$1"
  if pid_of "$service" >/dev/null; then echo "$service: already running"; return; fi
  case "$service" in
    gateway) env PYTHONPATH="$ROOT" nohup "$KC_PY" -s -m composition.gateway --config "$BASE/boundary.toml" >"$LOGS/gateway.log" 2>&1 & ;;
    worker)  nohup "$PY" -m octopus --config "$BASE/boundary.toml" worker >"$LOGS/worker.log" 2>&1 & ;;
    api)     nohup "$PY" -m octopus --config "$BASE/platform.toml" serve >"$LOGS/api.log" 2>&1 & ;;
  esac
  echo $! > "$RUN/$service.pid"
  case "$service" in
    gateway) "$PY" -m octopus --config "$BASE/boundary.toml" wait-ready --gateway --timeout 90 ;;
    worker)  "$PY" -m octopus --config "$BASE/boundary.toml" wait-ready --worker --timeout 60 ;;
    api)     "$PY" -m octopus --config "$BASE/platform.toml" wait-ready --api --timeout 60 ;;
  esac
  echo "$service: started"
}

halt() {
  local service="$1" pid
  pid="$(pid_of "$service" || true)"
  if [ -z "$pid" ]; then echo "$service: stopped"; rm -f "$RUN/$service.pid"; return; fi
  kill -TERM "$pid"
  for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then echo "$service: still running after SIGTERM (pid $pid)" >&2; return 1; fi
  rm -f "$RUN/$service.pid"
  echo "$service: stopped"
}

case "${1:-}" in
  init) init ;;
  start) init; for s in "${SERVICES[@]}"; do launch "$s"; done ;;
  stop) for ((i=${#SERVICES[@]}-1; i>=0; i--)); do halt "${SERVICES[$i]}"; done ;;
  status) for s in "${SERVICES[@]}"; do if pid_of "$s" >/dev/null; then echo "$s: running"; else echo "$s: stopped"; fi; done ;;
  restart) halt "${2:?service}"; launch "$2" ;;
  *) echo "用法: $0 init|start|stop|status|restart <gateway|worker|api>" >&2; exit 2 ;;
esac
