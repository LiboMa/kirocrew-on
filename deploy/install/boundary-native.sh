#!/usr/bin/env bash
# One boundary's public native chat on the single-node edition (MVP 0.2.0 P2 Task 7, S30). The deployment kit
# (kirocrew-on single native <boundary>) made its distribution in the stack kirocrew-on-single and runs this as root
# through SSM:
#   bash /opt/kirocrew-on/current/deploy/install/boundary-native.sh <boundary> <relay port 8920–8928> <dxxxx.cloudfront.net>
# nginx relays the port to the boundary's loopback Gateway, checking the origin secret it renders from this host's own
# file (it never leaves the host); the distribution becomes the boundary's crew_url and its Gateway (once its tasks end)
# and Worker restart, as deploy.sh boundary native does on the cluster's first host.
set -euo pipefail
NAME="${1:-}" PORT="${2:-}" NATIVE="${3:-}"
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,19}$ ]] && [ "$NAME" != personal ] || { echo "边界名称不对：${NAME}" >&2; exit 2; }
[[ "$PORT" =~ ^89(2[0-8])$ ]] || { echo "中转端口只能是 8920–8928：${PORT}" >&2; exit 2; }
[[ "$NATIVE" =~ ^[a-z0-9]+\.cloudfront\.net$ ]] || { echo "原生分发的域名无效：${NATIVE}" >&2; exit 2; }
CURRENT=/opt/kirocrew-on/current
TOML="/etc/kirocrew-on/boundary-$NAME.toml"
CONF="/etc/nginx/conf.d/kirocrew-relay-boundary-$NAME.conf"
[ -f "$TOML" ] || { echo "主机上没有边界 ${NAME}：先在控制台「管理 › 执行环境」新建" >&2; exit 3; }
# shellcheck source=os-lib.sh
. "$CURRENT/deploy/install/os-lib.sh"
os_detect
for other in /etc/nginx/conf.d/kirocrew-relay-*.conf; do   # two relays on one port: the later would reach the earlier's Gateway
  if [ -e "$other" ] && [ "$other" != "$CONF" ] && grep -qE "^[[:space:]]*listen $PORT;" "$other"; then
    echo "中转端口 ${PORT} 已被 ${other} 使用" >&2
    exit 3
  fi
done
[ -s /etc/nginx/conf.d/kirocrew-native.conf ] || { echo "这台主机还没有原生入口（kirocrew-native.conf）：先重新运行 install.sh" >&2; exit 3; }
GATEWAY="$(grep -E '^gateway_port' "$TOML" | grep -oE '[0-9]+' | head -1)"
[[ "$GATEWAY" =~ ^[0-9]+$ ]] || { echo "${TOML} 没有 gateway_port" >&2; exit 3; }
umask 027
/opt/kirocrew-on/venv/bin/python -I - "$CURRENT/deploy/nginx/kirocrew-boundary.conf" /etc/kirocrew-on/origin-secret \
  "$NAME" "$PORT" "$GATEWAY" > "$CONF.new" <<'RENDER_PY'
import re, sys
template, secret_file, name, port, gateway = sys.argv[1:]
secret = open(secret_file).read().strip()
if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", secret):
    sys.exit("源站密钥缺失或格式不符")
text = open(template).read()
for key, value in {"__ORIGIN_SECRET__": secret, "__BOUNDARY__": name, "__RELAY_PORT__": port, "__GATEWAY_PORT__": gateway}.items():
    text = text.replace(key, value)
print(text, end="")
RENDER_PY
umask 022
install -d -m 0700 /var/log/nginx/kirocrew-relay
chown "root:$NGINX_GROUP" "$CONF.new"
chmod 0640 "$CONF.new"
mv -f "$CONF.new" "$CONF"
label_port "$PORT"
nginx -t -q || { rm -f "$CONF"; exit 1; }
systemctl reload nginx
sed -i "s#^crew_url = .*#crew_url = \"https://$NATIVE\"#" "$TOML"
grep -qx "crew_url = \"https://$NATIVE\"" "$TOML" || { echo "未能写入 ${TOML} 的 crew_url" >&2; exit 1; }
chown "root:crew-$NAME" "$TOML"
chmod 0640 "$TOML"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
NODE_ROOT=/opt/kirocrew-node REGION="${AWS_REGION:-us-east-1}"   # boundary-lib.sh reads both
trap '[ -z "${BOUNDARY_DRAINING:-}" ] || rm -f "$BOUNDARY_DRAINING"' EXIT   # never a stale drain mark
# shellcheck source=boundary-lib.sh
. "$CURRENT/deploy/install/boundary-lib.sh"
boundary_restart "$NAME" "$(readlink -f /opt/kirocrew/current)"
echo "原生入口：https://${NATIVE}/（中转 :${PORT} → 边界 ${NAME} 的 Gateway 127.0.0.1:${GATEWAY}）"
