#!/usr/bin/env bash
# Idempotent host preparation for Amazon Linux 2023 (x86_64 or aarch64). deploy/aws/deploy.sh runs it as root through SSM:
#   KIROCREW_ON_RELEASE=<commit> CF_DOMAIN=<dxxxx.cloudfront.net> [CONSOLE_DOMAIN=<控制台自定义域名>] ORIGIN_SECRET_PARAM=/kirocrew-on/<stack>/origin-secret \
#   AWS_REGION=us-east-1 bash <release>/deploy/install/al2023.sh <boundary>
# Writes no tool credentials: Claude Code and Codex use Amazon Bedrock through the instance role; kiro-cli needs a
# one-time device-flow login as crew-<boundary> (docs/OPERATIONS.md). Every download is pinned or checksum-verified.
# WORKER_HOST=1: a Worker host for Teams (D28) — KiroCrew, the tools and one dedicated boundary fronted by TLS; no
# platform API or admin. NATIVE_DOMAIN there is the host's own native distribution, which the platform host relays.
# deploy.sh worker-host then signs its certificate, configures the relay and registers it as spare.
set -euo pipefail
WORKER_HOST="${WORKER_HOST:-}"
BOUNDARY="${1:?用法: al2023.sh <边界名，如 personal>}"
[[ "$BOUNDARY" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "边界名只允许小写字母、数字和连字符" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "需要 root" >&2; exit 2; }
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
RELEASE="${KIROCREW_ON_RELEASE:?需要 KIROCREW_ON_RELEASE（本仓库发布的 commit）}"
REGION="${AWS_REGION:-us-east-1}"
# shellcheck source=../versions.env
source "$SOURCE/deploy/versions.env"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
# The steps of each boundary on this host (D49: one per person; the platform host may carry several).
# shellcheck source=boundary-lib.sh
. "$SOURCE/deploy/install/boundary-lib.sh"
# MVP 0.2.0 P1 (P §5.6): Node and kiro-cli builds for this host's CPU; another architecture stops here, before any change
# shellcheck source=arch-lib.sh
. "$SOURCE/deploy/install/arch-lib.sh"
host_arch
SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH"; [ -z "${BOUNDARY_DRAINING:-}" ] || rm -f "$BOUNDARY_DRAINING"; }   # never a stale mark
trap cleanup EXIT

log "系统依赖"
dnf install -y -q git tar gzip xz jq unzip make gcc gcc-c++ nginx "$PYTHON_PACKAGE" "$PYTHON_PACKAGE-pip" "$PYTHON_PACKAGE-devel"
# What the Agents' Playwright Chromium needs to open a page (reviews, frontend checks; EC2 2026-10-19 lacked all 16).
dnf install -y -q nss nspr libdrm libX11 libXcomposite libXdamage libXext libXfixes libXrandr alsa-lib atk at-spi2-atk at-spi2-core cairo cups-libs mesa-libgbm pango libxcb libxkbcommon
PYTHON="$(command -v "$PYTHON_PACKAGE")"
merge_hosts() {  # add hosts to platform.toml allowed_hosts, keeping the entries already there
  "$PYTHON" - "$@" <<'PY'
import re, sys, tomllib
path, *required = sys.argv[1:]
text = open(path).read()
hosts = tomllib.loads(text).get("allowed_hosts", [])
line = "allowed_hosts = [" + ", ".join('"%s"' % h for h in dict.fromkeys(hosts + required)) + "]"
text = re.sub(r"(?m)^allowed_hosts[ \t]*=.*$", lambda _: line, text, count=1) if re.search(r"(?m)^allowed_hosts", text) \
    else line + "\n" + text
open(path, "w").write(text)
PY
}
render_preview() {  # render_preview TEMPLATE SECRET_FILE PREVIEW_PORT API_PORT: the platform's remote preview server (D36);
  # the origin secret comes from its file, never a command line, and the gate from it as the API derives it
  "$PYTHON" -I - "$TARGET" "$@" <<'PY'
import re, sys
release, template, secret_file, preview_port, api_port = sys.argv[1:]
sys.path.insert(0, release)
from octopus.remote_preview import gate_value
secret = open(secret_file).read().strip()
if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", secret):
    sys.exit("源站密钥缺失或格式不符")
print(open(template).read().replace("__ORIGIN_SECRET__", secret).replace("__PREVIEW_GATE__", gate_value(secret))
      .replace("__PREVIEW_PORT__", preview_port).replace("__API_PORT__", api_port), end="")
PY
}
render_worker_preview() {  # render_worker_preview TEMPLATE TOKEN_FILE PORT: a Worker host's preview server; its check header
  # comes from the boundary's Worker token exactly as the platform derives it (octopus.remote_preview.verify_value)
  "$PYTHON" -I - "$TARGET" "$@" <<'PY'
import sys
release, template, token_file, port = sys.argv[1:]
sys.path.insert(0, release)
from octopus.remote_preview import verify_value
token = open(token_file).read().strip()
if len(token) < 32:
    sys.exit("Worker Token 缺失或过短")
print(open(template).read().replace("__PREVIEW_VERIFY__", verify_value(token)).replace("__PREVIEW_TLS_PORT__", port), end="")
PY
}
toml_unset() {  # toml_unset FILE KEY: remove a top-level key (an older release rejects keys it does not know)
  "$PYTHON" - "$1" "$2" <<'PY'
import re, sys
path, key = sys.argv[1:]
text = open(path).read()
open(path, "w").write(re.sub(rf"(?m)^{re.escape(key)}[ \t]*=.*\n?", "", text, count=1))
PY
}
toml_set() {  # top-level TOML key: replace its line, or insert it before the first table so it never lands in [tools]
  "$PYTHON" - "$1" "$2" "$3" <<'PY'
import re, sys
path, key, value = sys.argv[1:]
text, line = open(path).read(), f"{key} = {value}"
pattern = re.compile(rf"(?m)^{re.escape(key)}[ \t]*=.*$")
if pattern.search(text):
    text = pattern.sub(lambda _: line, text, count=1)
else:
    table = re.search(r"(?m)^\[", text)
    text = text[:table.start()] + line + "\n" + text[table.start():] if table else text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY
}

log "Node.js（$NODE_DIST，按 SHASUMS256.txt 校验）"
NODE_ROOT=/opt/kirocrew-node
if [ ! -x "$NODE_ROOT/bin/node" ]; then
  curl --proto '=https' -fsSL "https://nodejs.org/dist/$NODE_DIST/SHASUMS256.txt" -o "$SCRATCH/SHASUMS256.txt"
  NODE_TARBALL="$(node_tarball "$SCRATCH/SHASUMS256.txt")"
  [ -n "$NODE_TARBALL" ] || { echo "Node $NODE_DIST 没有 linux-$NODE_ARCH 的发布包" >&2; exit 3; }
  curl --proto '=https' -fsSL "https://nodejs.org/dist/$NODE_DIST/$NODE_TARBALL" -o "$SCRATCH/$NODE_TARBALL"
  (cd "$SCRATCH" && grep " $NODE_TARBALL\$" SHASUMS256.txt | sha256sum -c -)
  install -d "$NODE_ROOT"
  tar -xJf "$SCRATCH/$NODE_TARBALL" -C "$NODE_ROOT" --strip-components=1
fi
export PATH="$NODE_ROOT/bin:$PATH"
log "node $(node --version)"

log "OS 用户与目录"
# useradd does not create missing parents of --home-dir.
install -d -m 0755 /var/lib/octopus /var/lib/octopus/boundaries
if [ -z "$WORKER_HOST" ]; then
  # platform:begin
  id octopus >/dev/null 2>&1 || useradd --system --home-dir /var/lib/octopus/platform --shell /sbin/nologin octopus
  install -d -m 0700 -o octopus -g octopus /var/lib/octopus/platform
  install -d -m 0750 -o octopus -g octopus /var/log/kirocrew-on
  # platform:end
fi
CREW="crew-$BOUNDARY"
BASE="/var/lib/octopus/boundaries/$BOUNDARY"
install -d -m 0700 "$BASE"
id "$CREW" >/dev/null 2>&1 || useradd --system --home-dir "$BASE/home" --shell /bin/bash "$CREW"
chown "$CREW:$CREW" "$BASE"
install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE/home" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/logs"
install -d -m 0755 /etc/kirocrew-on /opt/kirocrew-on/releases /opt/kirocrew /opt/kirocrew-tools

log "KiroCrew ON 发布 $RELEASE"
TARGET="/opt/kirocrew-on/releases/$RELEASE"
prune_releases /opt/kirocrew-on/releases "$(readlink -f /opt/kirocrew-on/current || true)" "$TARGET" 3
find /opt/kirocrew-on/incoming -mindepth 1 -maxdepth 1 -mmin +1440 ! -name "*$RELEASE*" -exec rm -rf {} +
if [ ! -f "$TARGET/.installed" ]; then
  rm -rf "$TARGET"
  cp -a "$SOURCE" "$TARGET"
  (cd "$TARGET" && npm ci --omit=dev --no-audit --no-fund)
  # React console: built on the host into octopus/ui/dist (the repository carries sources and the lockfile only).
  [ -n "$WORKER_HOST" ] || (cd "$TARGET/web" && npm ci --no-audit --no-fund && npm run build)
  touch "$TARGET/.installed"
fi
[ -x /opt/kirocrew-on/venv/bin/python ] || "$PYTHON" -m venv /opt/kirocrew-on/venv
/opt/kirocrew-on/venv/bin/python -m pip install -q -r "$TARGET/requirements-workflow.txt"

log "KiroCrew 源码 $KIROCREW_GIT_URL @ $KIROCREW_GIT_REF"
KC="/opt/kirocrew/$KIROCREW_GIT_REF"
if [ ! -f "$KC/.installed" ] || ! "$KC/venv/bin/python" -c 'import kiro_crew' 2>/dev/null; then
  # Built at its final path (pip console-script shebangs embed it); a failed attempt is removed so the next run retries.
  rm -rf "$KC"
  trap 'rm -rf "$KC"; cleanup' EXIT
  SRC="$KC/src"
  git clone -q --filter=blob:none "$KIROCREW_GIT_URL" "$SRC"
  git -C "$SRC" checkout -q "$KIROCREW_GIT_REF"
  # Dashboard SPA for the native entry; setup.py stages it into the package (electron is not needed on servers).
  (cd "$SRC/website" && npm ci --no-audit --no-fund && npm run build)
  # setup.py packages the dashboard only after it is staged into src/kiro_crew/static/dist.
  (cd "$SRC" && PYTHONPATH=src "$PYTHON" -m kiro_crew.frontend stage .)
  "$PYTHON" -m venv "$KC/venv"
  "$KC/venv/bin/python" -m pip install -q "$SRC"
  "$KC/venv/bin/python" -c 'import kiro_crew'
  touch "$KC/.installed"
  trap cleanup EXIT
fi
DASHBOARD_CHECK='import kiro_crew, pathlib, sys; sys.exit(not (pathlib.Path(kiro_crew.__file__).parent / "static/dist/index.html").is_file())'
if ! "$KC/venv/bin/python" -c "$DASHBOARD_CHECK"; then
  # Installs made before the stage step lack the native dashboard: stage and reinstall in place (no rebuild of the venv).
  log "补齐 KiroCrew dashboard 前端"
  [ -f "$KC/src/website/dist/index.html" ] || (cd "$KC/src/website" && npm ci --no-audit --no-fund && npm run build)
  (cd "$KC/src" && PYTHONPATH=src "$KC/venv/bin/python" -m kiro_crew.frontend stage .)
  "$KC/venv/bin/python" -m pip install -q --no-deps --force-reinstall "$KC/src"
  "$KC/venv/bin/python" -c "$DASHBOARD_CHECK"
fi
KIROCREW_VERSION="$("$KC/venv/bin/python" -c 'import kiro_crew; print(kiro_crew.__version__)')"
log "KiroCrew $KIROCREW_VERSION"

log "kiro-cli（官方发布清单，sha256 校验）"
if ! command -v kiro-cli >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -fsSL "https://desktop-release.q.us-east-1.amazonaws.com/latest/manifest.json" \
    -o "$SCRATCH/kirocli-manifest.json"
  RESOLVED="$(jq -r .version "$SCRATCH/kirocli-manifest.json")"
  if [ "$KIRO_CLI_VERSION" != "latest" ] && [ "$KIRO_CLI_VERSION" != "$RESOLVED" ]; then
    echo "kiro-cli 清单版本 $RESOLVED 与固定版本 $KIRO_CLI_VERSION 不一致" >&2; exit 3
  fi
  read -r KIRO_DOWNLOAD KIRO_SHA256 <<<"$(kiro_package "$SCRATCH/kirocli-manifest.json")"
  [[ "$KIRO_DOWNLOAD" == "$RESOLVED/kirocli-$KIRO_ARCH-linux.zip" ]] && [[ "$KIRO_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "kiro-cli 清单里没有 $KIRO_ARCH 的 zip 包（或其 sha256）" >&2; exit 3; }
  curl --proto '=https' --tlsv1.2 -fsSL "https://desktop-release.q.us-east-1.amazonaws.com/$KIRO_DOWNLOAD" \
    -o "$SCRATCH/kirocli.zip"
  echo "$KIRO_SHA256  $SCRATCH/kirocli.zip" | sha256sum -c -
  unzip -q "$SCRATCH/kirocli.zip" -d "$SCRATCH"
  Q_INSTALL_GLOBAL=1 Q_SKIP_SETUP=1 "$SCRATCH/kirocli/install.sh"
fi
log "$(kiro-cli --version)"

log "Coding 工具（npm，固定版本）"
npm install --global --no-audit --no-fund --prefix /opt/kirocrew-tools \
  "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" "@openai/codex@$CODEX_VERSION" "opencode-ai@$OPENCODE_VERSION"

log "执行边界 $BOUNDARY 的环境（Bedrock 经实例角色）"
boundary_env "$BOUNDARY"
boundary_codex "$BOUNDARY"

log "平台与边界配置"
if [ -z "$WORKER_HOST" ]; then
  # platform:begin
if [ -n "${ORIGIN_SECRET_PARAM:-}" ]; then
  # Fetched beside the live file: a failed or empty fetch must never leave the API without its secret.
  umask 027
  aws ssm get-parameter --region "$REGION" --with-decryption --name "$ORIGIN_SECRET_PARAM" \
    --query Parameter.Value --output text > /etc/kirocrew-on/origin-secret.new
  umask 022
  [ "$(tr -d '[:space:]' < /etc/kirocrew-on/origin-secret.new | wc -c)" -ge 32 ] || {
    rm -f /etc/kirocrew-on/origin-secret.new; echo "源站密钥为空或过短" >&2; exit 4; }
  chown root:octopus /etc/kirocrew-on/origin-secret.new
  chmod 0640 /etc/kirocrew-on/origin-secret.new
  mv -f /etc/kirocrew-on/origin-secret.new /etc/kirocrew-on/origin-secret
fi
if [ ! -f /etc/kirocrew-on/platform.toml ]; then
  install -m 0640 -o root -g octopus "$TARGET/deploy/config/platform.toml.example" /etc/kirocrew-on/platform.toml
fi
if [ -n "${CF_DOMAIN:-}" ]; then
  merge_hosts /etc/kirocrew-on/platform.toml "$CF_DOMAIN" ${CONSOLE_DOMAIN:+"$CONSOLE_DOMAIN"} localhost:18918
fi
# Private CA for Worker hosts (D28): the key never leaves this host (root 0600); the platform trusts only its certificate.
if [ ! -s /etc/kirocrew-on/worker-ca.key ]; then
  ( umask 077; openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=KiroCrew ON Worker CA" \
      -keyout /etc/kirocrew-on/worker-ca.key -out /etc/kirocrew-on/worker-ca.pem 2>/dev/null )
fi
chmod 0600 /etc/kirocrew-on/worker-ca.key
# Master key of the sealed repository credentials (D35 §3.3): made here once and never leaves the host (not in SSM, not in
# backups); the API reads it, the execution users cannot. A database restored elsewhere needs this same file.
if [ ! -s /etc/kirocrew-on/credentials.key ]; then
  ( umask 077; openssl rand -base64 32 > /etc/kirocrew-on/credentials.key.new )
  mv -f /etc/kirocrew-on/credentials.key.new /etc/kirocrew-on/credentials.key
fi
chown root:octopus /etc/kirocrew-on/credentials.key
chmod 0640 /etc/kirocrew-on/credentials.key
chmod 0644 /etc/kirocrew-on/worker-ca.pem
  # platform:end
fi
BOUNDARY_TOML="/etc/kirocrew-on/boundary-$BOUNDARY.toml"
if [ ! -f "$BOUNDARY_TOML" ]; then
  sed "s#/boundaries/personal/#/boundaries/$BOUNDARY/#g" \
    "$TARGET/deploy/config/boundary.toml.example" > "$BOUNDARY_TOML"
  chown "root:$CREW" "$BOUNDARY_TOML"
  chmod 0640 "$BOUNDARY_TOML"
fi
if [ -n "$WORKER_HOST" ]; then
  log "Worker 主机：专属边界，nginx 以 TLS 端口 ${WORKER_TLS_PORT:=8943} 转发回环 Worker、${GATEWAY_TLS_PORT:=8944} 转发回环 Gateway"
  toml_set "$BOUNDARY_TOML" worker_isolation '"dedicated"'
  if [ -n "${NATIVE_DOMAIN:-}" ]; then
    # This host's own native distribution; the platform host relays it here (deploy.sh worker-host native, D28).
    toml_set "$BOUNDARY_TOML" crew_url "\"https://$NATIVE_DOMAIN\""
    toml_set "$BOUNDARY_TOML" crew_entry_ttl '"1h"'
  fi
  WORKER_PORT="$(grep -E '^worker_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
  GATEWAY_PORT="$(grep -E '^gateway_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
  sed -e "s#__WORKER_TLS_PORT__#$WORKER_TLS_PORT#g" -e "s#__WORKER_PORT__#$WORKER_PORT#g" \
    -e "s#__GATEWAY_TLS_PORT__#$GATEWAY_TLS_PORT#g" -e "s#__GATEWAY_PORT__#$GATEWAY_PORT#g" \
    "$TARGET/deploy/nginx/kirocrew-worker.conf" > /etc/kirocrew-on/nginx-worker.conf
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
    setsebool -P httpd_can_network_connect 1
    for port in "$WORKER_TLS_PORT" "$GATEWAY_TLS_PORT"; do
      semanage port -a -t http_port_t -p tcp "$port" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$port"
    done
  fi
  # Loaded once deploy.sh worker-host tls has installed the certificate; until then nginx -t would fail.
  if [ -s /etc/kirocrew-on/worker-tls.crt ]; then
    install -m 0644 /etc/kirocrew-on/nginx-worker.conf /etc/nginx/conf.d/kirocrew-worker.conf
    nginx -t -q
    systemctl enable nginx >/dev/null
    systemctl reload-or-restart nginx
  fi
fi

if [ -z "$WORKER_HOST" ] && [ -n "${NATIVE_DOMAIN:-}" ]; then
  log "原生对话入口 https://$NATIVE_DOMAIN/（CloudFront → nginx 校验源站密钥 → 回环 Gateway）"
  NATIVE_PORT="${NATIVE_PORT:-8919}"
  toml_set "$BOUNDARY_TOML" crew_url "\"https://$NATIVE_DOMAIN\""
  toml_set "$BOUNDARY_TOML" crew_entry_ttl '"1h"'
  GATEWAY_PORT="$(grep -E '^gateway_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
  # K03: through every public relay the native login ends with its access cookie (included by each one below)
  install -m 0644 "$TARGET/deploy/nginx/kirocrew-native-guard.conf" /etc/nginx/kirocrew-native-guard.conf
  # Rendered from files only: the origin secret never appears on a command line.
  umask 027
  "$PYTHON" - "$TARGET/deploy/nginx/kirocrew-native.conf" /etc/kirocrew-on/origin-secret "$NATIVE_PORT" "$GATEWAY_PORT" \
    > /etc/nginx/conf.d/kirocrew-native.conf.new <<'PY'
import re, sys
template, secret_file, native_port, gateway_port = sys.argv[1:]
secret = open(secret_file).read().strip()
if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", secret):
    sys.exit("源站密钥缺失或格式不符")
print(open(template).read().replace("__ORIGIN_SECRET__", secret).replace("__NATIVE_PORT__", native_port)
      .replace("__GATEWAY_PORT__", gateway_port), end="")
PY
  umask 022
  chown root:nginx /etc/nginx/conf.d/kirocrew-native.conf.new
  chmod 0640 /etc/nginx/conf.d/kirocrew-native.conf.new
  mv -f /etc/nginx/conf.d/kirocrew-native.conf.new /etc/nginx/conf.d/kirocrew-native.conf
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
    setsebool -P httpd_can_network_connect 1
    semanage port -a -t http_port_t -p tcp "$NATIVE_PORT" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$NATIVE_PORT"
  fi
  # Relays rendered before the guard existed (deploy.sh worker-host native / boundary native) include it from now on;
  # one nginx refuses is put back as it was, with a warning, rather than stopping the release.
  PATCHED_RELAYS=""
  restore_relays() {
    for relay in $PATCHED_RELAYS; do mv -f "${relay}.before-guard" "$relay"; done
    log "警告：原生中转加入刷新拦截后 nginx 校验未通过，已还原；这些中转的原生登录仍可经刷新延续（K03）"
  }
  for relay in /etc/nginx/conf.d/kirocrew-relay-*.conf; do
    [ -e "$relay" ] || continue
    grep -q "kirocrew-native-guard.conf" "$relay" && continue
    cp -p "$relay" "${relay}.before-guard"
    "$PYTHON" - "$relay" <<'GUARD_PY'
import sys
path = sys.argv[1]
text = open(path).read()
anchor = "    client_max_body_size 50m;\n"
if anchor not in text:
    sys.exit(0)   # not one of ours as rendered: left as it is
open(path, "w").write(text.replace(anchor, anchor + "    include /etc/nginx/kirocrew-native-guard.conf;   # K03\n", 1))
GUARD_PY
    PATCHED_RELAYS="$PATCHED_RELAYS $relay"
  done
  nginx -t -q || { restore_relays; nginx -t -q; }
  rm -f /etc/nginx/conf.d/kirocrew-relay-*.conf.before-guard
  systemctl enable nginx >/dev/null
  systemctl reload-or-restart nginx
fi

if [ -z "$WORKER_HOST" ]; then
  # platform:begin
if [ -n "${PREVIEW_DOMAIN:-}" ]; then
  log "远程预览入口 https://$PREVIEW_DOMAIN/（CloudFront → nginx auth_request → 会话应用，D36）"
  PREVIEW_PORT="${PREVIEW_PORT:-8929}"
  API_PORT="$(grep -E '^api_port' /etc/kirocrew-on/platform.toml | grep -oE '[0-9]+' || echo 8918)"
  umask 027
  render_preview "$TARGET/deploy/nginx/kirocrew-preview.conf" /etc/kirocrew-on/origin-secret "$PREVIEW_PORT" "$API_PORT" \
    > /etc/nginx/conf.d/kirocrew-preview.conf.new
  umask 022
  chown root:nginx /etc/nginx/conf.d/kirocrew-preview.conf.new
  chmod 0640 /etc/nginx/conf.d/kirocrew-preview.conf.new
  mv -f /etc/nginx/conf.d/kirocrew-preview.conf.new /etc/nginx/conf.d/kirocrew-preview.conf
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
    setsebool -P httpd_can_network_connect 1
    semanage port -a -t http_port_t -p tcp "$PREVIEW_PORT" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$PREVIEW_PORT"
  fi
  if ! nginx -t -q; then   # never leave a configuration nginx rejects for its next restart
    rm -f /etc/nginx/conf.d/kirocrew-preview.conf
    echo "远程预览的 nginx 配置未通过 nginx -t，已移除" >&2
    exit 1
  fi
  systemctl enable nginx >/dev/null
  systemctl reload-or-restart nginx
fi
  # platform:end
fi

log "原生契约检查（以 $CREW 身份，隔离边界配置）"
boundary_check "$BOUNDARY" "$TARGET" "$KC"
for OTHER in $(other_boundaries "$BOUNDARY"); do   # D49: every boundary on this host passes before the release switches
  # one an administrator stopped (systemctl disable) stays stopped: neither checked nor brought back by a push
  systemctl is-enabled --quiet "octopus-worker@$OTHER" || { log "边界 ${OTHER}：已停用，跳过检查"; continue; }
  log "原生契约检查：边界 $OTHER"
  boundary_check "$OTHER" "$TARGET" "$KC"
done
for NAME in "$BOUNDARY" $(other_boundaries "$BOUNDARY"); do
  boundary_versions "$NAME" "$KIROCREW_VERSION" "$KIROCREW_ACCEPTED_VERSION"
done
log "切换到已通过检查的发布 $RELEASE 与 KiroCrew $KIROCREW_VERSION"
ln -sfn "$TARGET" /opt/kirocrew-on/current
ln -sfn "$KC" /opt/kirocrew/current

if [ -z "$WORKER_HOST" ]; then
  # platform:begin
# After the switch: a release before worker_ca_file would refuse to load platform.toml with the key (unknown keys fail).
toml_set /etc/kirocrew-on/platform.toml worker_ca_file '"/etc/kirocrew-on/worker-ca.pem"'
toml_set /etc/kirocrew-on/platform.toml credentials_key_file '"/etc/kirocrew-on/credentials.key"'
# Each Session's preview on its own origin, https://<run-id>.<PREVIEW_SESSION_DOMAIN>/ (empty: the shared origin only;
# the key is then removed, so a rollback to a release before D36 phase 3 still starts).
if [ -n "${PREVIEW_SESSION_DOMAIN:-}" ]; then
  toml_set /etc/kirocrew-on/platform.toml preview_domain "\"$PREVIEW_SESSION_DOMAIN\""
else
  toml_unset /etc/kirocrew-on/platform.toml preview_domain
fi
if [ -n "${PREVIEW_DOMAIN:-}" ]; then
  toml_set /etc/kirocrew-on/platform.toml preview_url "\"https://$PREVIEW_DOMAIN\""
fi
OCTO=(/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml)
log "平台管理员（首次初始化；已有用户时跳过）"
# Generated here and readable by root only: the instance role (usable by every process on the host, the execution
# user included) has no access to it. Read it with deploy/aws/deploy.sh admin-password.
ADMIN_FILE=/etc/kirocrew-on/admin-password
NEW_ADMIN=""
if [ ! -s "$ADMIN_FILE" ]; then
  ( umask 077; "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(24))' > "$ADMIN_FILE.new" )
  mv -f "$ADMIN_FILE.new" "$ADMIN_FILE"
  NEW_ADMIN=1
fi
chown root:root "$ADMIN_FILE"
chmod 0600 "$ADMIN_FILE"
ADMIN_PASSWORD="$(cat "$ADMIN_FILE")"
# The password reaches the platform in its environment only (runuser -u keeps it), never a command line others can read
if ! INIT_OUT="$(cd /opt/kirocrew-on/current && OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" runuser -u octopus -- \
    env PYTHONPATH=/opt/kirocrew-on/current "${OCTO[@]}" init --username admin --password-env OCTOPUS_ADMIN_PASSWORD 2>&1)"; then
  case "$INIT_OUT" in
    *已有用户*)
      if [ -n "$NEW_ADMIN" ]; then
        rm -f "$ADMIN_FILE"
        echo "平台已有管理员，但 $ADMIN_FILE 不存在：按 docs/OPERATIONS.md「管理员密码」写入现有密码后重新发布" >&2
        exit 4
      fi
      log "管理员已存在" ;;
    *) echo "$INIT_OUT" >&2; exit 4 ;;
  esac
fi
  # platform:end
fi

if [ -n "$WORKER_HOST" ] && [ -n "${WORKER_PREVIEW_TLS_PORT:-}" ]; then
  # After the switch (an older release rejects unknown keys): the platform reaches this host's apps through its preview
  # port (D36 §5.2); the Worker reports it with the name in this host's certificate.
  toml_set "$BOUNDARY_TOML" preview_tls_port "$WORKER_PREVIEW_TLS_PORT"
  toml_set "$BOUNDARY_TOML" preview_tls_name "\"$BOUNDARY.worker.kirocrew-on\""
fi

log "systemd"
install -m 0644 /opt/kirocrew-on/current/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable "octopus-gateway@$BOUNDARY" "octopus-worker@$BOUNDARY" >/dev/null
boundary_restart "$BOUNDARY" "$KC"
if [ -n "$WORKER_HOST" ] && [ -n "${WORKER_PREVIEW_TLS_PORT:-}" ] && [ -s "$BASE/worker.token" ]; then
  log "远程预览端口 $WORKER_PREVIEW_TLS_PORT（TLS；只转发到回环 47000–47999，校验头由 Worker Token 派生）"
  ( umask 077; render_worker_preview "$TARGET/deploy/nginx/kirocrew-worker-preview.conf" "$BASE/worker.token" \
      "$WORKER_PREVIEW_TLS_PORT" > /etc/kirocrew-on/nginx-worker-preview.conf.new )
  mv -f /etc/kirocrew-on/nginx-worker-preview.conf.new /etc/kirocrew-on/nginx-worker-preview.conf
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
    semanage port -a -t http_port_t -p tcp "$WORKER_PREVIEW_TLS_PORT" 2>/dev/null ||
      semanage port -m -t http_port_t -p tcp "$WORKER_PREVIEW_TLS_PORT"
  fi
  # Loaded once deploy.sh worker-host tls has installed the certificate (it installs this file too).
  if [ -s /etc/kirocrew-on/worker-tls.crt ]; then
    install -m 0640 -o root -g nginx /etc/kirocrew-on/nginx-worker-preview.conf /etc/nginx/conf.d/kirocrew-worker-preview.conf
    if ! nginx -t -q; then
      rm -f /etc/nginx/conf.d/kirocrew-worker-preview.conf
      echo "远程预览的 nginx 配置未通过 nginx -t，已移除" >&2
      exit 1
    fi
    systemctl reload-or-restart nginx
  fi
fi
if [ -z "$WORKER_HOST" ]; then
  # platform:begin
systemctl enable octopus-api >/dev/null
systemctl restart octopus-api
log "注册执行边界 Worker（已注册时跳过）"
WORKER_PORT="$(grep -E '^worker_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
(cd /opt/kirocrew-on/current && OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" PYTHONPATH=/opt/kirocrew-on/current \
  "${OCTO[@]}" bootstrap-worker --worker-url "http://127.0.0.1:$WORKER_PORT" \
  --token-file "$BASE/worker.token" --name "$BOUNDARY")
  # platform:end
fi
# litestream:begin
if [ "${CONTROL_PLANE_ONLY:-}" = 1 ]; then   # MVP 0.2.0 (S31): a cluster control-plane host only, never one with Agents
  [ -z "$WORKER_HOST" ] || { echo "CONTROL_PLANE_ONLY 不能与 WORKER_HOST 同用" >&2; exit 2; }
  : "${STATE_BUCKET:?CONTROL_PLANE_ONLY=1 需要 STATE_BUCKET（kirocrew-on-state.yaml 的 StateBucketName）}"
  [[ "$STATE_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || { echo "STATE_BUCKET 不是有效的桶名" >&2; exit 2; }
  log "Litestream $LITESTREAM_VERSION（平台数据持续复制到 s3://$STATE_BUCKET/db/）"
  if [ "$KIRO_ARCH" = x86_64 ]; then LS_ARCH=x86_64; LS_SHA="$LITESTREAM_SHA256_X86_64"; else LS_ARCH=arm64; LS_SHA="$LITESTREAM_SHA256_AARCH64"; fi
  LS_HAVE="$(/usr/local/bin/litestream version 2>/dev/null || true)"   # 0.5.17 prints "0.5.17"
  if [ "${LS_HAVE#v}" != "$LITESTREAM_VERSION" ]; then
    LS_TAR="litestream-$LITESTREAM_VERSION-linux-$LS_ARCH.tar.gz"
    curl --proto '=https' -fsSL "https://github.com/benbjohnson/litestream/releases/download/v$LITESTREAM_VERSION/$LS_TAR" \
      -o "$SCRATCH/$LS_TAR"
    echo "$LS_SHA  $SCRATCH/$LS_TAR" | sha256sum -c -
    tar -xzf "$SCRATCH/$LS_TAR" -C "$SCRATCH" litestream
    install -m 0755 "$SCRATCH/litestream" /usr/local/bin/litestream
  fi
  sed -e "s#__STATE_BUCKET__#$STATE_BUCKET#" -e "s#__REGION__#$REGION#" \
    /opt/kirocrew-on/current/deploy/litestream/litestream.yml.template > /etc/kirocrew-on/litestream.yml.new
  chown root:octopus /etc/kirocrew-on/litestream.yml.new
  chmod 0640 /etc/kirocrew-on/litestream.yml.new
  mv -f /etc/kirocrew-on/litestream.yml.new /etc/kirocrew-on/litestream.yml
  install -m 0644 /opt/kirocrew-on/current/deploy/litestream/octopus-litestream.service /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable octopus-litestream.service >/dev/null
  systemctl restart octopus-litestream.service
fi
# litestream:end
# others:begin
# D49: then every other boundary on this host, one at a time — after the platform is up, so one person's drain or a
# Gateway that does not restart never holds up the API, personal or the others; failures are reported at the end.
FAILED_BOUNDARIES=""
for OTHER in $(other_boundaries "$BOUNDARY"); do
  systemctl is-enabled --quiet "octopus-worker@$OTHER" || { log "边界 ${OTHER}：已停用（systemctl disable），不重启"; continue; }
  log "边界 ${OTHER}：升级"
  boundary_env "$OTHER"
  boundary_codex "$OTHER"   # settings an existing boundary's Codex lacks (web search off), read by its next task
  systemctl enable "octopus-gateway@$OTHER" "octopus-worker@$OTHER" >/dev/null
  boundary_restart "$OTHER" "$KC" || FAILED_BOUNDARIES="$FAILED_BOUNDARIES $OTHER"
done
[ -z "$FAILED_BOUNDARIES" ] || { echo "这些边界未能重启：${FAILED_BOUNDARIES}（其余已完成）" >&2; exit 1; }
# others:end
if [ -n "$WORKER_HOST" ]; then
  log "完成：Worker 主机 $BOUNDARY（KiroCrew $KIROCREW_VERSION）；下一步 deploy.sh worker-host tls / register，并以 $CREW 登录 kiro-cli"
else
  log "完成：KiroCrew ON $RELEASE，KiroCrew $KIROCREW_VERSION；kiro-cli 首次使用前需以 $CREW 登录（docs/OPERATIONS.md）"
fi
