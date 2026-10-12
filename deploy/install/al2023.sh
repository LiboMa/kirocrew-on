#!/usr/bin/env bash
# Idempotent host preparation for Amazon Linux 2023 x86_64. deploy/aws/deploy.sh runs it as root through SSM:
#   KIROCREW_ON_RELEASE=<commit> CF_DOMAIN=<dxxxx.cloudfront.net> ORIGIN_SECRET_PARAM=/kirocrew-on/<stack>/origin-secret \
#   AWS_REGION=us-east-1 bash <release>/deploy/install/al2023.sh <boundary>
# Writes no tool credentials: Claude Code and Codex use Amazon Bedrock through the instance role; kiro-cli needs a
# one-time device-flow login as crew-<boundary> (docs/OPERATIONS.md). Every download is pinned or checksum-verified.
# WORKER_HOST=1: a Worker host for Teams (D28) — KiroCrew, the tools and one dedicated boundary fronted by TLS; no
# platform API, admin or native entry. deploy.sh worker-host then signs its certificate and registers it as spare.
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
SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

log "系统依赖"
dnf install -y -q git tar gzip xz jq unzip make gcc gcc-c++ nginx "$PYTHON_PACKAGE" "$PYTHON_PACKAGE-pip" "$PYTHON_PACKAGE-devel"
PYTHON="$(command -v "$PYTHON_PACKAGE")"
merge_hosts() {  # add hosts to platform.toml allowed_hosts, keeping the entries already there
  "$PYTHON" - "$@" <<'PY'
import re, sys, tomllib
path, *required = sys.argv[1:]
text = open(path).read()
hosts = tomllib.loads(text).get("allowed_hosts", [])
line = "allowed_hosts = [" + ", ".join('"%s"' % h for h in hosts + [h for h in required if h not in hosts]) + "]"
text = re.sub(r"(?m)^allowed_hosts[ \t]*=.*$", lambda _: line, text, count=1) if re.search(r"(?m)^allowed_hosts", text) \
    else line + "\n" + text
open(path, "w").write(text)
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
  NODE_TARBALL="$(grep -oE 'node-v[0-9.]+-linux-x64\.tar\.xz' "$SCRATCH/SHASUMS256.txt" | head -1)"
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
  KIRO_SHA256="$(jq -r '.packages[] | select(.fileType=="zip" and .architecture=="x86_64" and (.download|contains("musl")|not)) | .sha256' \
    "$SCRATCH/kirocli-manifest.json" | head -1)"
  curl --proto '=https' --tlsv1.2 -fsSL "https://desktop-release.q.us-east-1.amazonaws.com/$RESOLVED/kirocli-x86_64-linux.zip" \
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
cat > "/etc/kirocrew-on/boundary-$BOUNDARY.env" <<EOF
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=$REGION
PATH=/opt/kirocrew-tools/bin:$NODE_ROOT/bin:/usr/local/bin:/usr/bin:/bin
EOF
chmod 0644 "/etc/kirocrew-on/boundary-$BOUNDARY.env"
if [ ! -f "$BASE/home/.codex/config.toml" ]; then
  install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE/home/.codex"
  printf 'model_provider = "amazon-bedrock"\nmodel = "openai.gpt-6-astra"\n' > "$BASE/home/.codex/config.toml"
  chown "$CREW:$CREW" "$BASE/home/.codex/config.toml"
fi

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
  merge_hosts /etc/kirocrew-on/platform.toml "$CF_DOMAIN" localhost:18918
fi
# Private CA for Worker hosts (D28): the key never leaves this host (root 0600); the platform trusts only its certificate.
if [ ! -s /etc/kirocrew-on/worker-ca.key ]; then
  ( umask 077; openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=KiroCrew ON Worker CA" \
      -keyout /etc/kirocrew-on/worker-ca.key -out /etc/kirocrew-on/worker-ca.pem 2>/dev/null )
fi
chmod 0600 /etc/kirocrew-on/worker-ca.key
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
  log "Worker 主机：专属边界，nginx 以 TLS 端口 ${WORKER_TLS_PORT:=8943} 转发回环 Worker"
  toml_set "$BOUNDARY_TOML" worker_isolation '"dedicated"'
  WORKER_PORT="$(grep -E '^worker_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
  sed -e "s#__WORKER_TLS_PORT__#$WORKER_TLS_PORT#g" -e "s#__WORKER_PORT__#$WORKER_PORT#g" \
    "$TARGET/deploy/nginx/kirocrew-worker.conf" > /etc/kirocrew-on/nginx-worker.conf
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
    setsebool -P httpd_can_network_connect 1
    semanage port -a -t http_port_t -p tcp "$WORKER_TLS_PORT" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$WORKER_TLS_PORT"
  fi
  # Loaded once deploy.sh worker-host tls has installed the certificate; until then nginx -t would fail.
  if [ -s /etc/kirocrew-on/worker-tls.crt ]; then
    install -m 0644 /etc/kirocrew-on/nginx-worker.conf /etc/nginx/conf.d/kirocrew-worker.conf
    nginx -t -q
    systemctl enable nginx >/dev/null
    systemctl reload-or-restart nginx
  fi
fi

if [ -n "${NATIVE_DOMAIN:-}" ]; then
  log "原生对话入口 https://$NATIVE_DOMAIN/（CloudFront → nginx 校验源站密钥 → 回环 Gateway）"
  NATIVE_PORT="${NATIVE_PORT:-8919}"
  toml_set "$BOUNDARY_TOML" crew_url "\"https://$NATIVE_DOMAIN\""
  toml_set "$BOUNDARY_TOML" crew_entry_ttl '"1h"'
  GATEWAY_PORT="$(grep -E '^gateway_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
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
  nginx -t -q
  systemctl enable nginx >/dev/null
  systemctl reload-or-restart nginx
fi

log "原生契约检查（以 $CREW 身份，隔离边界配置）"
CREW_ENV=(env HOME="$BASE/home" PATH="/opt/kirocrew-tools/bin:$NODE_ROOT/bin:/usr/local/bin:/usr/bin:/bin"
          PYTHONPATH="$TARGET")
# --check seeds the poc-* templates into KIRO_HOME and verifies every route before the native tests read them.
runuser -u "$CREW" -- "${CREW_ENV[@]}" "$KC/venv/bin/python" -s \
  -m composition.gateway --config "$BOUNDARY_TOML" --check
runuser -u "$CREW" -- "${CREW_ENV[@]}" OCTOPUS_CONFIG="$BOUNDARY_TOML" \
  "$KC/venv/bin/python" -s -m unittest discover -s "$TARGET/tests/native"
if [ "$KIROCREW_VERSION" = "$KIROCREW_ACCEPTED_VERSION" ]; then
  sed -i "s#^kirocrew_versions = .*#kirocrew_versions = [\"$KIROCREW_VERSION\"]#" "$BOUNDARY_TOML"
fi
log "切换到已通过检查的发布 $RELEASE 与 KiroCrew $KIROCREW_VERSION"
ln -sfn "$TARGET" /opt/kirocrew-on/current
ln -sfn "$KC" /opt/kirocrew/current

if [ -z "$WORKER_HOST" ]; then
  # platform:begin
# After the switch: a release before worker_ca_file would refuse to load platform.toml with the key (unknown keys fail).
toml_set /etc/kirocrew-on/platform.toml worker_ca_file '"/etc/kirocrew-on/worker-ca.pem"'
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
if ! INIT_OUT="$(cd /opt/kirocrew-on/current && runuser -u octopus -- env PYTHONPATH=/opt/kirocrew-on/current \
    OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" "${OCTO[@]}" init --username admin --password-env OCTOPUS_ADMIN_PASSWORD 2>&1)"; then
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

log "systemd"
install -m 0644 /opt/kirocrew-on/current/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable "octopus-gateway@$BOUNDARY" "octopus-worker@$BOUNDARY" >/dev/null
systemctl restart "octopus-gateway@$BOUNDARY"
systemctl restart "octopus-worker@$BOUNDARY"
if [ -z "$WORKER_HOST" ]; then
  # platform:begin
systemctl enable octopus-api >/dev/null
systemctl restart octopus-api
log "注册执行边界 Worker（已注册时跳过）"
WORKER_PORT="$(grep -E '^worker_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
(cd /opt/kirocrew-on/current && env PYTHONPATH=/opt/kirocrew-on/current OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  "${OCTO[@]}" bootstrap-worker --worker-url "http://127.0.0.1:$WORKER_PORT" \
  --token-file "$BASE/worker.token" --name "$BOUNDARY")
  # platform:end
fi
if [ -n "$WORKER_HOST" ]; then
  log "完成：Worker 主机 $BOUNDARY（KiroCrew $KIROCREW_VERSION）；下一步 deploy.sh worker-host tls / register，并以 $CREW 登录 kiro-cli"
else
  log "完成：KiroCrew ON $RELEASE，KiroCrew $KIROCREW_VERSION；kiro-cli 首次使用前需以 $CREW 登录（docs/OPERATIONS.md）"
fi
