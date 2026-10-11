#!/usr/bin/env bash
# Idempotent host preparation for Amazon Linux 2023 x86_64. deploy/aws/deploy.sh runs it as root through SSM:
#   KIROCREW_ON_RELEASE=<commit> CF_DOMAIN=<dxxxx.cloudfront.net> ORIGIN_SECRET_PARAM=/kirocrew-on/<stack>/origin-secret \
#   AWS_REGION=us-east-1 bash <release>/deploy/install/al2023.sh <boundary>
# Writes no tool credentials: Claude Code and Codex use Amazon Bedrock through the instance role; kiro-cli needs a
# one-time device-flow login as crew-<boundary> (docs/OPERATIONS.md). Every download is pinned or checksum-verified.
set -euo pipefail
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
dnf install -y -q git tar gzip xz jq unzip make gcc gcc-c++ "$PYTHON_PACKAGE" "$PYTHON_PACKAGE-pip" "$PYTHON_PACKAGE-devel"
PYTHON="$(command -v "$PYTHON_PACKAGE")"

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
id octopus >/dev/null 2>&1 || useradd --system --home-dir /var/lib/octopus/platform --shell /sbin/nologin octopus
CREW="crew-$BOUNDARY"
BASE="/var/lib/octopus/boundaries/$BOUNDARY"
install -d -m 0700 "$BASE"
id "$CREW" >/dev/null 2>&1 || useradd --system --home-dir "$BASE/home" --shell /bin/bash "$CREW"
chown "$CREW:$CREW" "$BASE"
install -d -m 0700 -o octopus -g octopus /var/lib/octopus/platform
install -d -m 0750 -o octopus -g octopus /var/log/kirocrew-on
install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE/home" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/logs"
install -d -m 0755 /etc/kirocrew-on /opt/kirocrew-on/releases /opt/kirocrew /opt/kirocrew-tools

log "KiroCrew ON 发布 $RELEASE"
TARGET="/opt/kirocrew-on/releases/$RELEASE"
if [ ! -f "$TARGET/.installed" ]; then
  rm -rf "$TARGET"
  cp -a "$SOURCE" "$TARGET"
  (cd "$TARGET" && npm ci --omit=dev --no-audit --no-fund)
  touch "$TARGET/.installed"
fi
ln -sfn "$TARGET" /opt/kirocrew-on/current
[ -x /opt/kirocrew-on/venv/bin/python ] || "$PYTHON" -m venv /opt/kirocrew-on/venv
/opt/kirocrew-on/venv/bin/python -m pip install -q -r /opt/kirocrew-on/current/requirements-workflow.txt

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
  "$PYTHON" -m venv "$KC/venv"
  "$KC/venv/bin/python" -m pip install -q "$SRC"
  "$KC/venv/bin/python" -c 'import kiro_crew'
  touch "$KC/.installed"
  trap cleanup EXIT
fi
ln -sfn "$KC" /opt/kirocrew/current
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
if [ -n "${ORIGIN_SECRET_PARAM:-}" ]; then
  umask 027
  aws ssm get-parameter --region "$REGION" --with-decryption --name "$ORIGIN_SECRET_PARAM" \
    --query Parameter.Value --output text > /etc/kirocrew-on/origin-secret
  umask 022
  chown root:octopus /etc/kirocrew-on/origin-secret
  chmod 0640 /etc/kirocrew-on/origin-secret
fi
if [ ! -f /etc/kirocrew-on/platform.toml ]; then
  install -m 0640 -o root -g octopus /opt/kirocrew-on/current/deploy/config/platform.toml.example /etc/kirocrew-on/platform.toml
fi
if [ -n "${CF_DOMAIN:-}" ]; then
  sed -i "s#^allowed_hosts = .*#allowed_hosts = [\"$CF_DOMAIN\"]#" /etc/kirocrew-on/platform.toml
fi
BOUNDARY_TOML="/etc/kirocrew-on/boundary-$BOUNDARY.toml"
if [ ! -f "$BOUNDARY_TOML" ]; then
  sed "s#/boundaries/personal/#/boundaries/$BOUNDARY/#g" \
    /opt/kirocrew-on/current/deploy/config/boundary.toml.example > "$BOUNDARY_TOML"
  chown "root:$CREW" "$BOUNDARY_TOML"
  chmod 0640 "$BOUNDARY_TOML"
fi

log "原生契约检查（以 $CREW 身份，隔离边界配置）"
CREW_ENV=(env HOME="$BASE/home" PATH="/opt/kirocrew-tools/bin:$NODE_ROOT/bin:/usr/local/bin:/usr/bin:/bin"
          PYTHONPATH=/opt/kirocrew-on/current)
# --check seeds the poc-* templates into KIRO_HOME and verifies every route before the native tests read them.
runuser -u "$CREW" -- "${CREW_ENV[@]}" /opt/kirocrew/current/venv/bin/python -s \
  -m composition.gateway --config "$BOUNDARY_TOML" --check
runuser -u "$CREW" -- "${CREW_ENV[@]}" OCTOPUS_CONFIG="$BOUNDARY_TOML" \
  /opt/kirocrew/current/venv/bin/python -s -m unittest discover -s /opt/kirocrew-on/current/tests/native
if [ "$KIROCREW_VERSION" = "$KIROCREW_ACCEPTED_VERSION" ]; then
  sed -i "s#^kirocrew_versions = .*#kirocrew_versions = [\"$KIROCREW_VERSION\"]#" "$BOUNDARY_TOML"
fi

OCTO=(/opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml)
if [ -n "${ADMIN_PASSWORD_PARAM:-}" ]; then
  log "平台管理员（首次初始化；已有用户时跳过）"
  ADMIN_PASSWORD="$(aws ssm get-parameter --region "$REGION" --with-decryption --name "$ADMIN_PASSWORD_PARAM" \
    --query Parameter.Value --output text)"
  if ! INIT_OUT="$(cd /opt/kirocrew-on/current && runuser -u octopus -- env PYTHONPATH=/opt/kirocrew-on/current \
      OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" "${OCTO[@]}" init --username admin --password-env OCTOPUS_ADMIN_PASSWORD 2>&1)"; then
    case "$INIT_OUT" in *已有用户*) log "管理员已存在" ;; *) echo "$INIT_OUT" >&2; exit 4 ;; esac
  fi
fi

log "systemd"
install -m 0644 /opt/kirocrew-on/current/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable "octopus-gateway@$BOUNDARY" "octopus-worker@$BOUNDARY" octopus-api >/dev/null
systemctl restart "octopus-gateway@$BOUNDARY"
systemctl restart "octopus-worker@$BOUNDARY"
systemctl restart octopus-api
if [ -n "${ADMIN_PASSWORD_PARAM:-}" ]; then
  log "注册执行边界 Worker（已注册时跳过）"
  WORKER_PORT="$(grep -E '^worker_port' "$BOUNDARY_TOML" | grep -oE '[0-9]+')"
  (cd /opt/kirocrew-on/current && env PYTHONPATH=/opt/kirocrew-on/current OCTOPUS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    "${OCTO[@]}" bootstrap-worker --worker-url "http://127.0.0.1:$WORKER_PORT" \
    --token-file "$BASE/worker.token" --name "$BOUNDARY")
fi
log "完成：KiroCrew ON $RELEASE，KiroCrew $KIROCREW_VERSION；kiro-cli 首次使用前需以 $CREW 登录（docs/OPERATIONS.md）"
