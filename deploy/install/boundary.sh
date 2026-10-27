#!/usr/bin/env bash
# Adds one KiroCrew boundary to the platform host for one person (D49): its own OS user, Gateway, Worker, kiro-cli login,
# memory and resource limits, on the release that is current, registered as that person's dedicated Worker. Running it
# again only fills in what is missing; the platform API is not restarted. deploy.sh boundary add runs it as root via SSM:
#   bash /opt/kirocrew-on/current/deploy/install/boundary.sh <name> --owner <username> [--memory <GiB>] [--cpu <percent>]
set -euo pipefail
NAME="${1:-}"
[ $# -eq 0 ] || shift
OWNER="" MEMORY="" CPU=""   # limits not given keep the boundary's own (else 8 GiB, 300%)
while [ $# -gt 0 ]; do
  case "$1" in
    --owner) OWNER="${2:-}"; shift 2 ;;
    --memory) MEMORY="${2:-}"; shift 2 ;;
    --cpu) CPU="${2:-}"; shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
USAGE="用法：boundary.sh <名称> --owner <平台用户名> [--memory <GiB>] [--cpu <百分比>]"
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,19}$ ]] && [ "$NAME" != personal ] || {
  echo "${USAGE}；名称为小写字母开头的 2–20 位小写字母、数字或连字符，不能是 personal" >&2; exit 2; }
[[ "$OWNER" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "${USAGE}（--owner 为平台用户名）" >&2; exit 2; }
[[ "${MEMORY:-8}" =~ ^[1-9][0-9]?$ ]] && [[ "${CPU:-300}" =~ ^[1-9][0-9]{1,3}$ ]] || {
  echo "${USAGE}（内存 1–99 GiB，CPU 10–9999%）" >&2; exit 2; }
OWNER="$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')"   # the platform keeps user names in lower case
[ "$(id -u)" -eq 0 ] || { echo "需要 root" >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ON_CURRENT="${ON_CURRENT:-/opt/kirocrew-on/current}"
NODE_ROOT="${NODE_ROOT:-/opt/kirocrew-node}"
REGION="${AWS_REGION:-us-east-1}"
PLATFORM_PYTHON="${PLATFORM_PYTHON:-/opt/kirocrew-on/venv/bin/python}"
ADMIN_FILE="${ADMIN_FILE:-/etc/kirocrew-on/admin-password}"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
# shellcheck source=boundary-lib.sh
. "$HERE/boundary-lib.sh"
# shellcheck source=../versions.env
source "$ON_CURRENT/deploy/versions.env"
RELEASE="$(readlink -f "$ON_CURRENT")"   # the check rewrites current paths to the release they point at
KC="$(readlink -f "$KC_CURRENT")"
KIROCREW_VERSION="$("$KC/venv/bin/python" -c 'import kiro_crew; print(kiro_crew.__version__)')"
CREW="crew-$NAME"
BASE="$DATA/$NAME"
TOML="$ETC/boundary-$NAME.toml"
platform() {  # bootstrap-worker as the admin: the password reaches it in its environment only, never a command line
  ( cd "$ON_CURRENT"
    OCTOPUS_ADMIN_PASSWORD="$(cat "$ADMIN_FILE")" PYTHONPATH="$ON_CURRENT" \
      "$PLATFORM_PYTHON" -m octopus --config "$ETC/platform.toml" bootstrap-worker "$@" --name "$NAME" --owner "$OWNER" )
}

# The platform refuses an unknown user or a second own environment before anything exists on the host.
log "边界 ${NAME}：核对平台用户 $OWNER"
platform --check-only >/dev/null

log "边界 ${NAME}：OS 用户与目录"
install -d -m 0700 "$BASE"
B_ID=""
field() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())[sys.argv[1]])' "$1"; }
if ! id "$CREW" >/dev/null 2>&1; then
  # MVP 0.2.0 (S9): a new boundary's identity and OS user id come from the platform, the same on every host that will
  # mount its volume (a re-run after a failure gets the same ones); a boundary made before keeps the id its host gave it.
  ALLOC="$(platform --allocate)"
  B_ID="$(printf '%s' "$ALLOC" | field id)"
  B_UID="$(printf '%s' "$ALLOC" | field uid)"
  [[ "$B_ID" =~ ^bnd-[0-9a-f]{32}$ ]] && [[ "$B_UID" =~ ^[0-9]+$ ]] || { echo "平台未返回有效的边界身份" >&2; exit 3; }
  if getent passwd "$B_UID" >/dev/null || getent group "$B_UID" >/dev/null; then
    echo "平台分配的 UID ${B_UID} 已被本机其他用户或组占用：请检查主机后重试" >&2; exit 3
  fi
  # Nobody may hold the id as a subordinate one: through newuidmap they could become this boundary's user.
  for map in ${SUBIDS:-/etc/subuid /etc/subgid}; do
    [ ! -f "$map" ] || ! awk -F: -v id="$B_UID" '$2 <= id && id < $2 + $3 { found = 1 } END { exit !found }' "$map" || {
      echo "UID ${B_UID} 落在 ${map} 里某个用户的从属 ID 段内：请先调整该段再重试" >&2; exit 3; }
  done
  # --system: no subordinate ids and no password ageing for a boundary's user (as boundaries were made before)
  groupadd --system -g "$B_UID" "$CREW"
  useradd --system -u "$B_UID" -g "$B_UID" --home-dir "$BASE/home" --shell /bin/bash "$CREW"
elif [ -f "$TOML" ] && ! grep -q '^boundary_id = ' "$TOML" && [ "$(id -u "$CREW")" -ge 70000 ] && [ "$(id -u "$CREW")" -lt 100000 ]; then
  # Made with a platform id (70000–99999, octopus/boundaries.py) by a run that stopped before its configuration was
  # written: the same identity again
  ALLOC="$(platform --allocate)"
  [ "$(printf '%s' "$ALLOC" | field uid)" = "$(id -u "$CREW")" ] || {
    echo "平台为边界 ${NAME} 记的 UID 与本机 ${CREW} 不同：请检查主机后重试" >&2; exit 3; }
  B_ID="$(printf '%s' "$ALLOC" | field id)"
  [[ "$B_ID" =~ ^bnd-[0-9a-f]{32}$ ]] || { echo "平台未返回有效的边界身份" >&2; exit 3; }
  { printf 'boundary_id = "%s"\n' "$B_ID"; cat "$TOML"; } > "$TOML.new"
  chown "root:$CREW" "$TOML.new"
  chmod 0640 "$TOML.new"
  mv -f "$TOML.new" "$TOML"
fi
chown "$CREW:$CREW" "$BASE"
install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE/home" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/logs"
boundary_env "$NAME"
boundary_codex "$NAME"

if [ ! -f "$TOML" ]; then
  GATEWAY_PORT="$(free_port 5481 5580 gateway_port)"
  WORKER_PORT="$(free_port 9001 9100 worker_port)"
  log "边界 ${NAME}：Gateway 127.0.0.1:${GATEWAY_PORT}，Worker 127.0.0.1:$WORKER_PORT"
  # Its own OS user, so dedicated; the native entry is the local end of `deploy.sh tunnel --boundary` (D49 R8).
  # MVP 0.2.0 (S8): its identity on the first line (before the file's tables), in this root-owned configuration
  { [ -z "$B_ID" ] || printf 'boundary_id = "%s"\n' "$B_ID"
  # MVP 0.2.0 (§6.4): kiro-cli signs in from the console the way this host's other boundaries do (top-level keys, so
  # before the file's tables)
  for other in "$ETC"/boundary-*.toml; do
    [ -f "$other" ] && grep -qE '^kiro_(license|identity_provider|idc_region) = ' "$other" || continue
    grep -E '^kiro_(license|identity_provider|idc_region) = "[A-Za-z0-9.:/_~-]*"$' "$other"; break
  done
  sed -e "s#/boundaries/personal/#/boundaries/$NAME/#g" \
      -e "s#^worker_port = .*#worker_port = $WORKER_PORT#" -e "s#^gateway_port = .*#gateway_port = $GATEWAY_PORT#" \
      -e 's#^worker_isolation = .*#worker_isolation = "dedicated"#' \
      -e "s#^crew_url = .*#crew_url = \"http://localhost:$((GATEWAY_PORT + 10000))\"#" \
      -e 's#^crew_entry_ttl = .*#crew_entry_ttl = "1h"#' \
      "$ON_CURRENT/deploy/config/boundary.toml.example"; } > "$TOML.new"
  chown "root:$CREW" "$TOML.new"
  chmod 0640 "$TOML.new"
  mv -f "$TOML.new" "$TOML"
fi
WORKER_PORT="$(toml_port "$TOML" worker_port)"
# A re-run without --memory / --cpu writes the limits it has: unchanged drop-ins do not drain and restart the Gateway.
LIMITS="$SYSTEMD/octopus-gateway@$NAME.service.d/limits.conf"
current() { grep -oE "^$1=[0-9]+" "$LIMITS" 2>/dev/null | grep -oE '[0-9]+$' || true; }
MEMORY="${MEMORY:-$(current MemoryMax)}"
CPU="${CPU:-$(current CPUQuota)}"
boundary_limits "$NAME" "${MEMORY:-8}" "${CPU:-300}"

log "边界 ${NAME}：原生契约检查（以 $CREW 身份）"
boundary_check "$NAME" "$RELEASE" "$KC"
boundary_versions "$NAME" "$KIROCREW_VERSION" "$KIROCREW_ACCEPTED_VERSION"

log "边界 ${NAME}：启动 Gateway 与 Worker"
systemctl daemon-reload
systemctl enable "octopus-gateway@$NAME" "octopus-worker@$NAME" >/dev/null
boundary_restart "$NAME" "$KC"
for _ in $(seq 1 30); do   # the Worker writes its token on its first start
  [ -s "$BASE/worker.token" ] && break
  sleep 1
done
[ -s "$BASE/worker.token" ] || { echo "Worker 未生成 $BASE/worker.token：journalctl -u octopus-worker@$NAME" >&2; exit 1; }

log "边界 ${NAME}：登记为 $OWNER 的专属执行环境"
# MVP 0.2.0 (N §3): a new Worker registers itself with a one-time code, its token read from its own file; one the
# platform has already (a re-run) has its connection refreshed by bootstrap-worker as before.
ISSUED="$(platform --issue-code)"
CODE="$(printf '%s' "$ISSUED" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("code", ""))')"
if [ -n "$CODE" ]; then
  API="$(printf '%s' "$ISSUED" | field platform)"
  ( cd "$ON_CURRENT"
    OCTOPUS_ENROLL_CODE="$CODE" PYTHONPATH="$ON_CURRENT" "$PLATFORM_PYTHON" -m octopus --config "$TOML" worker enroll \
      --platform "$API" --url "http://127.0.0.1:$WORKER_PORT" --token-file "$BASE/worker.token" )
else
  platform --worker-url "http://127.0.0.1:$WORKER_PORT" --token-file "$BASE/worker.token"
fi
log "完成：边界 ${NAME}（${OWNER}）。下一步：$OWNER 在控制台「我的设置 › 执行环境」里登录 Kiro"
