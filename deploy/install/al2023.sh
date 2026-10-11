#!/usr/bin/env bash
# Idempotent host preparation for Amazon Linux 2023 x86_64. Run as root from a kirocrew-on checkout:
#   sudo deploy/install/al2023.sh <boundary-name>
# Never starts services and never writes tool credentials; each tool is authenticated later as crew-<boundary>.
set -euo pipefail
BOUNDARY="${1:?用法: al2023.sh <边界名，如 personal>}"
[[ "$BOUNDARY" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "边界名只允许小写字母、数字和连字符" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "需要 root" >&2; exit 2; }
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../versions.env
source "$SOURCE/deploy/versions.env"
[ -n "$KIROCREW_WHEEL_URL" ] && [ -n "$KIROCREW_WHEEL_SHA256" ] || {
  echo "deploy/versions.env 中 KIROCREW_WHEEL_URL / KIROCREW_WHEEL_SHA256 未填写（见计划 Task 16）" >&2; exit 3; }

dnf install -y git tar gzip jq "$PYTHON_PACKAGE" "$NODE_PACKAGE"
PYTHON="$(command -v "$PYTHON_PACKAGE")"

# useradd does not create missing parents of --home-dir.
install -d -m 0755 /var/lib/octopus /var/lib/octopus/boundaries
id octopus >/dev/null 2>&1 || useradd --system --home-dir /var/lib/octopus/platform --shell /sbin/nologin octopus
CREW="crew-$BOUNDARY"
BASE="/var/lib/octopus/boundaries/$BOUNDARY"
id "$CREW" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$BASE/home" --shell /bin/bash "$CREW"

install -d -m 0700 -o octopus -g octopus /var/lib/octopus/platform
install -d -m 0750 -o octopus -g octopus /var/log/kirocrew-on
install -d -m 0700 -o "$CREW" -g "$CREW" "$BASE" "$BASE/home" "$BASE/worker" "$BASE/crew" "$BASE/kiro" "$BASE/logs"
install -d -m 0755 /etc/kirocrew-on /opt/kirocrew-on/releases /opt/kirocrew /opt/kirocrew-tools

RELEASE="$(git -C "$SOURCE" rev-parse --short HEAD)"
if [ ! -d "/opt/kirocrew-on/releases/$RELEASE" ]; then
  git -C "$SOURCE" archive --format=tar --prefix="$RELEASE/" HEAD | tar -x -C /opt/kirocrew-on/releases
fi
ln -sfn "/opt/kirocrew-on/releases/$RELEASE" /opt/kirocrew-on/current
[ -x /opt/kirocrew-on/venv/bin/python ] || "$PYTHON" -m venv /opt/kirocrew-on/venv
/opt/kirocrew-on/venv/bin/python -m pip install -r /opt/kirocrew-on/current/requirements-workflow.txt
(cd /opt/kirocrew-on/current && npm ci --omit=dev)

KC="/opt/kirocrew/$KIROCREW_VERSION"
if [ ! -f "$KC/.installed" ] || ! "$KC/venv/bin/python" -c 'import kiro_crew' 2>/dev/null; then
  # Build at the final path (pip console-script shebangs embed it) and mark success with a sentinel;
  # a failed download or pip run removes the partial install so the next invocation retries.
  rm -rf "$KC"
  trap 'rm -rf "$KC"' EXIT
  "$PYTHON" -m venv "$KC/venv"
  WHEEL="$(mktemp -d)/kirocrew-$KIROCREW_VERSION.whl"
  curl -fsSL "$KIROCREW_WHEEL_URL" -o "$WHEEL"
  echo "$KIROCREW_WHEEL_SHA256  $WHEEL" | sha256sum -c -
  "$KC/venv/bin/python" -m pip install "$WHEEL"
  "$KC/venv/bin/python" -c 'import kiro_crew'
  rm -rf "$(dirname "$WHEEL")"
  touch "$KC/.installed"
  trap - EXIT
fi
ln -sfn "$KC" /opt/kirocrew/current

npm install --global --prefix /opt/kirocrew-tools \
  "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" "@openai/codex@$CODEX_VERSION" "opencode-ai@$OPENCODE_VERSION"
if [ -n "$KIRO_CLI_URL" ] && [ ! -x /opt/kirocrew-tools/bin/kiro-cli ]; then
  ARCHIVE="$(mktemp -d)/kiro-cli.zip"
  curl -fsSL "$KIRO_CLI_URL" -o "$ARCHIVE"
  echo "$KIRO_CLI_SHA256  $ARCHIVE" | sha256sum -c -
  echo "kiro-cli 安装包已校验：$ARCHIVE；按 Task 16 记录的官方步骤安装到 /opt/kirocrew-tools/bin" >&2
fi

[ -f /etc/kirocrew-on/platform.toml ] || install -m 0640 -o root -g octopus \
  /opt/kirocrew-on/current/deploy/config/platform.toml.example /etc/kirocrew-on/platform.toml
if [ ! -f "/etc/kirocrew-on/boundary-$BOUNDARY.toml" ]; then
  sed "s#/boundaries/personal/#/boundaries/$BOUNDARY/#g" \
    /opt/kirocrew-on/current/deploy/config/boundary.toml.example > "/etc/kirocrew-on/boundary-$BOUNDARY.toml"
  chown "root:$CREW" "/etc/kirocrew-on/boundary-$BOUNDARY.toml"
  chmod 0640 "/etc/kirocrew-on/boundary-$BOUNDARY.toml"
fi

install -m 0644 /opt/kirocrew-on/current/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
echo "完成。下一步：以 $CREW 身份为各工具初始化认证，然后按 docs/OPERATIONS.md 启动服务。"
