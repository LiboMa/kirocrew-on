#!/bin/sh
# KiroCrew ON 单机版一键安装（MVP 0.2.0 P2，设计 §4.2）。在一台 EC2 上：
#   curl -fsSL https://<下载域名>/install.sh | sudo sh                 安装，或补齐上次没装完的部分
#   curl -fsSL https://<下载域名>/install.sh | sudo sh -s -- --check    只做预检，不改动任何东西
# 经管道运行时 Ubuntu 的 sh 是 dash，所以本文件只用 POSIX sh；下载并验签之后的步骤才交给发布物里的 bash 脚本。
# 预检的每项数据都可以用 PREFLIGHT_* 改到别处读取（tests/test_install_preflight.py）。
set -eu

SUPPORTED="受支持的系统：Amazon Linux 2023、Ubuntu 22.04、Ubuntu 24.04（aarch64 或 x86_64）。"
RECOMMEND="推荐 ARM64（Graviton）：同等性能价格更低。这台是 x86_64，照常安装。"
MIN_FREE_GIB=40      # a first install: the tools, KiroCrew and room for the boundaries' work
RERUN_FREE_GIB=5     # an installed host only needs room for one more release
MIN_MEM_KB=7864320   # 7.5 GiB: an 8 GiB instance reports a little less, the kernel keeps some of it

usage() {
  cat >&2 <<'KC_USAGE'
用法：curl -fsSL https://<下载域名>/install.sh | sudo sh [-s -- 选项]
  --check   只做预检（系统、CPU 架构、root、systemd、磁盘、内存、EC2 实例元数据），不改动任何东西
KC_USAGE
  exit "$1"
}
fail() { printf '%s\n' "$*" >&2; exit 2; }

os_field() {  # os_field KEY FILE: a value of os-release, read rather than sourced
  sed -n "s/^$1=//p" "$2" | head -1 | tr -d "\"'"
}

detect_os() {  # detect_os FILE: OS_ID (al2023 | ubuntu), OS_VERSION, OS_NAME and PKG (dnf | apt); other systems stop
  [ -r "$1" ] || fail "读不到 $1，无法判断这是什么系统。${SUPPORTED}"
  OS_VERSION=$(os_field VERSION_ID "$1")
  case "$(os_field ID "$1"):${OS_VERSION}" in
    amzn:2023) OS_ID=al2023; OS_NAME="Amazon Linux 2023"; PKG=dnf ;;
    ubuntu:22.04|ubuntu:24.04) OS_ID=ubuntu; OS_NAME="Ubuntu ${OS_VERSION}"; PKG=apt ;;
    *) os_pretty=$(os_field PRETTY_NAME "$1")
       fail "不支持这个系统：${os_pretty:-$(os_field NAME "$1") ${OS_VERSION}}。${SUPPORTED}" ;;
  esac
}

detect_arch() {  # detect_arch MACHINE: ARCH (aarch64 | x86_64); other CPUs stop
  case "$1" in
    aarch64|arm64) ARCH=aarch64 ;;
    x86_64) ARCH=x86_64 ;;
    *) fail "不支持的 CPU 架构 $1：KiroCrew ON 只支持 x86_64 与 aarch64（Graviton）。" ;;
  esac
}

metadata() {  # EC2_REGION and EC2_INSTANCE_ID from IMDSv2, or a line in PROBLEMS
  imds=${PREFLIGHT_IMDS:-http://169.254.169.254}
  token=$(curl -fsS --max-time 2 -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 300" "${imds}/latest/api/token" \
          2>/dev/null) || token=""
  if [ -z "$token" ]; then
    PROBLEMS="${PROBLEMS}
- 连不上实例元数据（IMDSv2）：单机版要装在 EC2 上，从实例元数据取区域、实例 ID 和角色。若这台就是 EC2，请确认实例元数据已开启；在自己电脑上开发请用源码渠道（tools/local_stack.sh）。"
    return 0
  fi
  EC2_REGION=$(curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: ${token}" "${imds}/latest/meta-data/placement/region")
  EC2_INSTANCE_ID=$(curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: ${token}" "${imds}/latest/meta-data/instance-id")
}

requirements() {  # root, systemd, disk and memory; every unmet one goes into PROBLEMS
  if [ "${PREFLIGHT_UID:-$(id -u)}" != 0 ]; then
    PROBLEMS="${PROBLEMS}
- 需要 root：请用 sudo 运行（curl -fsSL https://<下载域名>/install.sh | sudo sh）。"
  fi
  if [ ! -d "${PREFLIGHT_SYSTEMD:-/run/systemd/system}" ]; then
    PROBLEMS="${PROBLEMS}
- 需要 systemd：这台机器不是由 systemd 启动的（容器里请用 Docker 渠道）。"
  fi
  disk=/var/lib
  [ -d "$disk" ] || disk=/
  FREE_KB=${PREFLIGHT_FREE_KB:-$(df -Pk "$disk" | awk 'NR == 2 {print $4}')}
  need=$MIN_FREE_GIB
  if [ -e "${PREFLIGHT_INSTALLED:-/opt/kirocrew-on/current}" ]; then
    need=$RERUN_FREE_GIB
  fi
  if [ "$FREE_KB" -lt $((need * 1048576)) ]; then
    PROBLEMS="${PROBLEMS}
- 空闲磁盘 $((FREE_KB / 1048576)) GiB，至少需要 ${need} GiB（${disk} 所在的卷；根卷建议 ≥ 50 GiB）。"
  fi
  MEM_KB=$(awk '/^MemTotal:/ {print $2}' "${PREFLIGHT_MEMINFO:-/proc/meminfo}")
  if [ "${MEM_KB:-0}" -lt "$MIN_MEM_KB" ]; then
    PROBLEMS="${PROBLEMS}
- 内存 $(gib "${MEM_KB:-0}") GiB，至少需要 8 GiB。"
  fi
}

gib() { awk -v kb="$1" 'BEGIN {printf "%.1f", kb / 1048576}'; }

preflight() {  # stops with code 2 before any change when this host cannot take the single-node edition
  command -v curl >/dev/null 2>&1 || fail "需要 curl。"
  detect_os "${PREFLIGHT_OS_RELEASE:-/etc/os-release}"
  detect_arch "${PREFLIGHT_MACHINE:-$(uname -m)}"
  [ "$ARCH" = aarch64 ] || printf '%s\n' "$RECOMMEND"
  PROBLEMS=""
  requirements
  metadata
  [ -z "$PROBLEMS" ] || fail "这台机器还不能安装 KiroCrew ON 单机版：${PROBLEMS}"
  export OS_ID OS_VERSION PKG ARCH EC2_REGION EC2_INSTANCE_ID
}

conclude() {
  printf '系统：%s %s（%s）\n' "$OS_NAME" "$ARCH" "$PKG"
  printf 'EC2：%s，实例 %s\n' "$EC2_REGION" "$EC2_INSTANCE_ID"
  printf '空闲磁盘 %s GiB，内存 %s GiB\n' "$((FREE_KB / 1048576))" "$(gib "$MEM_KB")"
  printf '%s\n' "预检通过：可以安装 KiroCrew ON 单机版。"
  [ "$ARCH" = aarch64 ] || printf '%s\n' "$RECOMMEND"
}

CHECK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    -h|--help) usage 0 ;;
    *) printf '不认识的选项：%s\n' "$1" >&2; usage 2 ;;
  esac
  shift
done

preflight
conclude
[ -n "$CHECK" ] || fail "这一版 install.sh 只做预检：请加 --check。"
