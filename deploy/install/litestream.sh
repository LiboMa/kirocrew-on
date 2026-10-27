#!/usr/bin/env bash
# MVP 0.2.0 (P §5.2, S31): Litestream on a cluster control-plane host — the platform database replicated continuously to
# the state bucket's db/ (kirocrew-on-state.yaml). Only on a host that runs no boundary: Litestream needs its role to
# write the bucket, and every process on a host can use its role, the Agents included. Run as root after al2023.sh's
# control-plane install (P3):
#   STATE_BUCKET=<StateBucketName> [AWS_REGION=us-east-1] bash /opt/kirocrew-on/current/deploy/install/litestream.sh
set -euo pipefail
ETC="${ETC:-/etc/kirocrew-on}"
ON_CURRENT="${ON_CURRENT:-/opt/kirocrew-on/current}"
REGION="${AWS_REGION:-us-east-1}"
: "${STATE_BUCKET:?需要 STATE_BUCKET（kirocrew-on-state.yaml 的 StateBucketName）}"
[[ "$STATE_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || { echo "STATE_BUCKET 不是有效的桶名" >&2; exit 2; }
for toml in "$ETC"/boundary-*.toml; do
  [ ! -e "$toml" ] || { echo "这台主机有执行边界（${toml##*/}）：Litestream 只装在不跑边界的控制面主机上（S31）" >&2; exit 2; }
done
[ "$(id -u)" -eq 0 ] || { echo "需要 root" >&2; exit 2; }
# shellcheck source=../versions.env
source "$ON_CURRENT/deploy/versions.env"
# shellcheck source=arch-lib.sh
. "$ON_CURRENT/deploy/install/arch-lib.sh"
host_arch
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
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
  "$ON_CURRENT/deploy/litestream/litestream.yml.template" > "$ETC/litestream.yml.new"
chown root:octopus "$ETC/litestream.yml.new"
chmod 0640 "$ETC/litestream.yml.new"
mv -f "$ETC/litestream.yml.new" "$ETC/litestream.yml"
install -m 0644 "$ON_CURRENT/deploy/litestream/octopus-litestream.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable octopus-litestream.service >/dev/null
systemctl restart octopus-litestream.service
log "完成：Litestream 复制 /var/lib/octopus/platform/octopus.sqlite3 到 s3://$STATE_BUCKET/db/（journalctl -u octopus-litestream）"
