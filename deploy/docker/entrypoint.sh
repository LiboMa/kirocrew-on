#!/usr/bin/env bash
# The Docker channel's processes (MVP 0.2.0 P2 Task 8, S25): one platform and one boundary, personal, in one container.
# It starts as root only to prepare the volume and the configuration; the API runs as octopus, the Gateway and the
# Worker — and so every Agent — as crew-personal. The first start prints the administrator's password once (docker
# logs); Kiro signs in from the console (「我的设置 › 执行环境」 › 「登录 Kiro」). When one process ends, the container ends
# with it and the restart policy brings all three back.
set -euo pipefail
BOUNDARY=personal
CREW="crew-$BOUNDARY"
ETC=/var/lib/octopus/etc DATA=/var/lib/octopus/boundaries
BASE="$DATA/$BOUNDARY" CURRENT=/opt/kirocrew-on/current
REGION="${AWS_REGION:-us-east-1}" NODE_ROOT=/opt/kirocrew-node
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
toml_drop() {  # toml_drop FILE KEY: a key a container has nothing for (no CloudFront origin secret, no Worker hosts' CA)
  sed -i "/^$2[[:space:]]*=/d" "$1"
}

install -d -m 0755 /var/lib/octopus "$DATA" "$ETC"
install -d -m 0700 -o octopus -g octopus /var/lib/octopus/platform /var/log/kirocrew-on
install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE" "$BASE/home" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/logs"
if [ ! -f "$ETC/platform.toml" ]; then
  install -m 0640 -o root -g octopus "$CURRENT/deploy/config/platform.toml.example" "$ETC/platform.toml"
  toml_drop "$ETC/platform.toml" origin_secret_file
  toml_drop "$ETC/platform.toml" worker_ca_file
fi
TOML="$ETC/boundary-$BOUNDARY.toml"
if [ ! -f "$TOML" ]; then
  install -m 0640 -o root -g "$CREW" "$CURRENT/deploy/config/boundary.toml.example" "$TOML"
fi
# shellcheck source=../install/boundary-lib.sh
ETC="$ETC" DATA="$DATA" . "$CURRENT/deploy/install/boundary-lib.sh"
boundary_env "$BOUNDARY"
boundary_codex "$BOUNDARY"

ADMIN_FILE="$ETC/admin-password" NEW_ADMIN=""
if [ ! -s "$ADMIN_FILE" ]; then
  ( umask 077; /opt/kirocrew-on/venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(24))' > "$ADMIN_FILE" )
  NEW_ADMIN=1
fi
ADMIN_PASSWORD="$(cat "$ADMIN_FILE")"
INIT="$(cd "$CURRENT" && OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" runuser -u octopus -- env PYTHONPATH="$CURRENT" \
  OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" /opt/kirocrew-on/venv/bin/python -m octopus --config "$ETC/platform.toml" init \
  --username admin --password-env OCTOPUS_ADMIN_PASSWORD 2>&1)" || case "$INIT" in *已有用户*) ;; *) echo "$INIT" >&2; exit 4 ;; esac

ENV_FILE="$ETC/boundary-$BOUNDARY.env"
mapfile -t TOOLS_ENV < <(grep -E '^[A-Z_]+=' "$ENV_FILE")
PIDS=()
stop() { kill -TERM "${PIDS[@]}" 2>/dev/null || true; wait || true; }
trap 'stop; exit 0' TERM INT
log "Gateway（$CREW）"
(cd "$BASE" && runuser -u crew-personal -- env PYTHONPATH="$CURRENT" HOME="$BASE/home" "${TOOLS_ENV[@]}" /opt/kirocrew/current/venv/bin/python -m composition.gateway --config "$TOML") &
PIDS+=($!)
runuser -u "$CREW" -- env PYTHONPATH="$CURRENT" /opt/kirocrew-on/venv/bin/python -m octopus --config "$TOML" wait-ready --gateway --timeout 90
log "Worker（$CREW）"
(cd "$BASE" && runuser -u crew-personal -- env PYTHONPATH="$CURRENT" HOME="$BASE/home" "${TOOLS_ENV[@]}" /opt/kirocrew-on/venv/bin/python -m octopus --config "$TOML" worker) &
PIDS+=($!)
log "平台 API（octopus）"
(cd "$CURRENT" && runuser -u octopus -- env PYTHONPATH="$CURRENT" /opt/kirocrew-on/venv/bin/python -m octopus --config "$ETC/platform.toml" serve) &
PIDS+=($!)
runuser -u octopus -- env PYTHONPATH="$CURRENT" /opt/kirocrew-on/venv/bin/python -m octopus --config "$ETC/platform.toml" wait-ready --api --timeout 60
for _ in $(seq 1 30); do [ -s "$BASE/worker.token" ] && break; sleep 1; done   # the Worker writes it on its first start
WORKER_PORT="$(grep -E '^worker_port' "$TOML" | grep -oE '[0-9]+')"
(cd "$CURRENT" && OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" PYTHONPATH="$CURRENT" /opt/kirocrew-on/venv/bin/python -m octopus \
  --config "$ETC/platform.toml" bootstrap-worker --worker-url "http://127.0.0.1:$WORKER_PORT" --token-file "$BASE/worker.token" \
  --name "$BOUNDARY" >/dev/null)
log "KiroCrew ON 已启动：http://localhost:8918（docker run 时 -p 127.0.0.1:8918:8918），管理员 admin"
[ -z "$NEW_ADMIN" ] || log "初始密码：${ADMIN_PASSWORD}（只打印这一次；另存在卷里的 etc/admin-password）"
log "下一步：登录控制台，在「我的设置 › 执行环境」里点「登录 Kiro」"
wait -n "${PIDS[@]}"
status=$?
log "有进程退出（${status}），容器随之结束"
stop
exit "$status"
