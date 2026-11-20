# shellcheck shell=bash
# Which build of each download fits a host (MVP 0.2.0 P1, P §5.6): x86_64 and aarch64 (Graviton), nothing else.
# Sourced by al2023.sh on the host and by deploy.sh on the admin's machine.

host_arch() {  # host_arch [uname -m]: sets NODE_ARCH and KIRO_ARCH for this host (or the machine given); fails on others
  local machine="${1:-$(uname -m)}"
  case "$machine" in
    x86_64) NODE_ARCH=x64; KIRO_ARCH=x86_64 ;;
    aarch64|arm64) NODE_ARCH=arm64; KIRO_ARCH=aarch64 ;;
    *) echo "不支持的 CPU 架构 ${machine}：KiroCrew ON 只支持 x86_64 与 aarch64（Graviton）" >&2; return 1 ;;
  esac
}

node_tarball() {  # node_tarball SHASUMS256.txt: the release's linux tarball (.tar.xz) for NODE_ARCH
  grep -oE "node-v[0-9.]+-linux-${NODE_ARCH:?先调用 host_arch}\.tar\.xz" "$1" | head -1
}

kiro_package() {  # kiro_package manifest.json: "<download path> <sha256>" of the glibc zip for KIRO_ARCH
  jq -r --arg arch "${KIRO_ARCH:?先调用 host_arch}" '.packages[] | select(.fileType == "zip" and .architecture == $arch
    and (.download | contains("musl") | not)) | "\(.download) \(.sha256)"' "$1" | head -1
}

ami_parameter() {  # ami_parameter "<SupportedArchitectures>": the public SSM parameter of the latest AL2023 AMI for them
  case " $1 " in
    *" arm64 "*) echo /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 ;;
    *" x86_64 "*) echo /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 ;;
    *) echo "机型的 CPU 架构「$1」不受支持：只支持 x86_64 与 arm64（Graviton）" >&2; return 1 ;;
  esac
}
