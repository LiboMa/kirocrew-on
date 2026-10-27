#!/usr/bin/env bash
# The Docker channel's image (MVP 0.2.0 P2 Task 8, S25), built from the release in /opt/kirocrew-on/current: the same
# packages, Python, Node, KiroCrew, kiro-cli and Coding tools a host gets from al2023.sh (versions from deploy/versions.env,
# every download checked), the platform's OS user and one boundary's. There is no systemd in a container:
# entrypoint.sh starts the API, the Gateway and the Worker.
set -euo pipefail
cd /opt/kirocrew-on/current
# shellcheck source=../versions.env
. deploy/versions.env
# shellcheck source=../install/arch-lib.sh
. deploy/install/arch-lib.sh
host_arch
# shellcheck source=../install/os-lib.sh
. deploy/install/os-lib.sh
os_detect
ARCH="$KIRO_ARCH"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
# what the minimal image lacks and a host has: users, runuser, ps, openssl, archives
dnf install -y -q shadow-utils util-linux procps-ng findutils openssl tar xz gzip which
install_packages
install_python

NODE_ROOT=/opt/kirocrew-node
curl --proto '=https' -fsSL "https://nodejs.org/dist/$NODE_DIST/SHASUMS256.txt" -o "$SCRATCH/SHASUMS256.txt"
NODE_TARBALL="$(node_tarball "$SCRATCH/SHASUMS256.txt")"
[ -n "$NODE_TARBALL" ] || { echo "Node $NODE_DIST 没有 linux-$NODE_ARCH 的发布包" >&2; exit 3; }
curl --proto '=https' -fsSL "https://nodejs.org/dist/$NODE_DIST/$NODE_TARBALL" -o "$SCRATCH/$NODE_TARBALL"
(cd "$SCRATCH" && grep " $NODE_TARBALL\$" SHASUMS256.txt | sha256sum -c -)
install -d "$NODE_ROOT"
tar -xJf "$SCRATCH/$NODE_TARBALL" -C "$NODE_ROOT" --strip-components=1
export PATH="$NODE_ROOT/bin:$PATH"

# KiroCrew ON itself: the ACP adapters, the console (from source; a release unpacked from the wheel has it built)
npm ci --omit=dev --no-audit --no-fund
if [ -d web ]; then (cd web && npm ci --no-audit --no-fund && npm run build && rm -rf node_modules); fi
"$PYTHON" -m venv /opt/kirocrew-on/venv
/opt/kirocrew-on/venv/bin/python -m pip install -q -r requirements-workflow.txt

KC="/opt/kirocrew/$KIROCREW_GIT_REF"
SRC="$KC/src"
git clone -q --filter=blob:none "$KIROCREW_GIT_URL" "$SRC"
git -C "$SRC" checkout -q "$KIROCREW_GIT_REF"
(cd "$SRC/website" && npm ci --no-audit --no-fund && npm run build && rm -rf node_modules)
(cd "$SRC" && PYTHONPATH=src "$PYTHON" -m kiro_crew.frontend stage .)
"$PYTHON" -m venv "$KC/venv"
"$KC/venv/bin/python" -m pip install -q "$SRC"
"$KC/venv/bin/python" -c 'import kiro_crew'
ln -sfn "$KC" /opt/kirocrew/current

curl --proto '=https' --tlsv1.2 -fsSL "https://desktop-release.q.us-east-1.amazonaws.com/latest/manifest.json" \
  -o "$SCRATCH/kirocli-manifest.json"
RESOLVED="$(jq -r .version "$SCRATCH/kirocli-manifest.json")"
if [ "$KIRO_CLI_VERSION" != "latest" ] && [ "$KIRO_CLI_VERSION" != "$RESOLVED" ]; then
  echo "kiro-cli 清单版本 $RESOLVED 与固定版本 $KIRO_CLI_VERSION 不一致" >&2; exit 3
fi
read -r KIRO_DOWNLOAD KIRO_SHA256 <<<"$(kiro_package "$SCRATCH/kirocli-manifest.json")"
[[ "$KIRO_DOWNLOAD" == "$RESOLVED/kirocli-$KIRO_ARCH-linux.zip" ]] && [[ "$KIRO_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "kiro-cli 清单里没有 $KIRO_ARCH 的 zip 包（或其 sha256）" >&2; exit 3; }
curl --proto '=https' --tlsv1.2 -fsSL "https://desktop-release.q.us-east-1.amazonaws.com/$KIRO_DOWNLOAD" -o "$SCRATCH/kirocli.zip"
echo "$KIRO_SHA256  $SCRATCH/kirocli.zip" | sha256sum -c -
unzip -q "$SCRATCH/kirocli.zip" -d "$SCRATCH"
Q_INSTALL_GLOBAL=1 Q_SKIP_SETUP=1 "$SCRATCH/kirocli/install.sh"

npm install --global --no-audit --no-fund --prefix /opt/kirocrew-tools \
  "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" "@openai/codex@$CODEX_VERSION" "opencode-ai@$OPENCODE_VERSION"

# The platform's user and the one boundary's, with fixed ids so a volume from an earlier image keeps its owners; the
# configuration lives on the volume (/etc/kirocrew-on → /var/lib/octopus/etc), written at the first start.
useradd --system --uid 60001 --home-dir /var/lib/octopus/platform --shell /sbin/nologin octopus
useradd --system --uid 70000 --home-dir /var/lib/octopus/boundaries/personal/home --shell /bin/bash crew-personal
ln -sfn /var/lib/octopus/etc /etc/kirocrew-on
install -m 0755 deploy/install/kirocrew-on /usr/local/bin/kirocrew-on
dnf clean all
rm -rf /var/cache/dnf /root/.npm /root/.cache
