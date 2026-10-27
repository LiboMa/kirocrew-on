#!/bin/sh
# KiroCrew ON 单机版一键安装（MVP 0.2.0 P2，设计 §4.2、§4.4）。在一台 EC2 上：
#   curl -fsSL https://<下载域名>/install.sh | sudo sh                 安装 stable 频道的最新版，或补齐上次没装完的部分
#   curl -fsSL https://<下载域名>/install.sh | sudo sh -s -- --channel preview | --version 0.2.0rc1
#   curl -fsSL https://<下载域名>/install.sh | sudo sh -s -- --check    只做预检，不改动任何东西
# 经管道运行时 Ubuntu 的 sh 是 dash，所以本文件只用 POSIX sh；验签之后的安装步骤交给发布物里的 al2023.sh（bash）。
# 发布物：manifest.sig 先用下面内嵌的公钥验签，wheel 再按清单的 sha256 校验，任何一项不符都停在改动之前（不提供只校验
# 哈希的退路）。地址与公钥由 tools/build_release.py --index 写入；仓库里的这份没有，只能 --check 或 --source。
# 预检的每项数据都可以用 PREFLIGHT_* 改到别处读取，INSTALL_FS_ROOT 改 /opt 与 /etc 的位置（tests/test_install_*.py）。
set -eu

DOWNLOADS="__KIROCREW_ON_DOWNLOADS__"   # <版本>/manifest.json、manifest.sig、wheel
FEED="__KIROCREW_ON_FEED__"             # <频道>/latest.json
PUBLIC_KEY='__KIROCREW_ON_PUBLIC_KEY__'
FS_ROOT=${INSTALL_FS_ROOT:-}
INCOMING="${FS_ROOT}/opt/kirocrew-on/incoming"
ADMIN_FILE="${FS_ROOT}/etc/kirocrew-on/admin-password"
BOUNDARY=personal

SUPPORTED="受支持的系统：Amazon Linux 2023、Ubuntu 22.04、Ubuntu 24.04（aarch64 或 x86_64）。"
RECOMMEND="推荐 ARM64（Graviton）：同等性能价格更低。这台是 x86_64，照常安装。"
MIN_FREE_GIB=40      # a first install: the tools, KiroCrew and room for the boundaries' work
RERUN_FREE_GIB=5     # an installed host only needs room for one more release
MIN_MEM_KB=7864320   # 7.5 GiB: an 8 GiB instance reports a little less, the kernel keeps some of it

usage() {
  cat >&2 <<'KC_USAGE'
用法：curl -fsSL https://<下载域名>/install.sh | sudo sh [-s -- 选项]
  --check             只做预检（系统、CPU 架构、root、systemd、磁盘、内存、EC2 实例元数据），不改动任何东西
  --channel <频道>    stable（默认）或 preview：装该频道的最新版
  --version <版本>    装指定的版本，例如 0.2.0rc1
  --from <目录>       从本地目录取发布物（目录里是 feed/<频道>/latest.json 与 releases/<版本>/）
  --source <目录>     已展开的发布（kirocrew-on setup 用）：不下载，直接执行安装步骤
KC_USAGE
  exit "$1"
}
fail() { printf '%s\n' "$*" >&2; exit 2; }
release_fail() { printf '%s\n' "$*" >&2; exit 3; }

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
  if [ -e "${PREFLIGHT_INSTALLED:-${FS_ROOT}/opt/kirocrew-on/current}" ]; then
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
}

fetch() {  # fetch feed|releases PATH DEST: from the --from directory, or over HTTPS
  if [ -n "$FROM" ]; then
    cp "$FROM/$1/$2" "$3" 2>/dev/null
  elif [ "$1" = feed ]; then
    curl --proto '=https' --tlsv1.2 -fsSL "$FEED/$2" -o "$3"
  else
    curl --proto '=https' --tlsv1.2 -fsSL "$DOWNLOADS/$2" -o "$3"
  fi
}

json_line() {  # json_line FILE KEY...: the values, one line, space-separated (read after the signature checked out)
  python3 -I - "$@" <<'KC_JSON'
import json, sys
data = json.load(open(sys.argv[1]))
values = []
for key in sys.argv[2:]:
    value = data
    for part in key.split("."):
        value = value.get(part, "") if isinstance(value, dict) else ""
    values.append(str(value) or "-")
print(" ".join(values))
KC_JSON
}

take_release() {  # STAGE: the release VERSION (or the channel's latest), signature and wheel checked, unpacked as a checkout
  case "$PUBLIC_KEY" in
    *"BEGIN PUBLIC KEY"*) ;;
    *) fail "这份 install.sh 没有内嵌发布公钥，无法验签：请用发布站点上的 install.sh（或 --check 只做预检）。" ;;
  esac
  if [ -z "$FROM" ]; then
    case "$DOWNLOADS$FEED" in *__KIROCREW_ON_*) fail "这份 install.sh 没有写入下载地址：请用发布站点上的 install.sh，或加 --from <目录>。" ;; esac
  fi
  for tool in openssl python3 sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || release_fail "需要 ${tool}。"
  done
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  if [ -z "$VERSION" ]; then
    fetch feed "$CHANNEL/latest.json" "$WORK/latest.json" || release_fail "频道 ${CHANNEL} 里还没有发布（取不到 ${CHANNEL}/latest.json）。"
    VERSION=$(json_line "$WORK/latest.json" version)
    valid_version "$VERSION" || release_fail "频道 ${CHANNEL} 的 latest.json 写的版本不对：${VERSION}"
  fi
  fetch releases "$VERSION/manifest.json" "$WORK/manifest.json" || release_fail "没有版本 ${VERSION} 的发布物（取不到 ${VERSION}/manifest.json）。"
  fetch releases "$VERSION/manifest.sig" "$WORK/manifest.sig" || release_fail "版本 ${VERSION} 没有签名（manifest.sig）：不安装未签名的发布物。"
  printf '%s\n' "$PUBLIC_KEY" > "$WORK/release-key.pem"
  openssl dgst -sha256 -verify "$WORK/release-key.pem" -signature "$WORK/manifest.sig" "$WORK/manifest.json" >/dev/null 2>&1 ||
    release_fail "版本 ${VERSION} 的 manifest.json 签名校验失败：它不是这份 install.sh 认可的发布，或在途中被改动。未做任何改动。"
  set -- $(json_line "$WORK/manifest.json" version channel wheel.name wheel.sha256)
  [ "$1" = "$VERSION" ] || release_fail "签名的清单是版本 ${1} 的，不是要装的 ${VERSION}：未做任何改动。"
  RELEASE_CHANNEL=$2 WHEEL=$3 WHEEL_SHA=$4
  [ "$WHEEL" = "kirocrew_on-${VERSION}-py3-none-any.whl" ] || release_fail "清单里的 wheel 名称不对：${WHEEL}"
  fetch releases "$VERSION/$WHEEL" "$WORK/$WHEEL" || release_fail "取不到 ${VERSION}/${WHEEL}。"
  [ "$(sha256sum "$WORK/$WHEEL" | cut -d ' ' -f 1)" = "$WHEEL_SHA" ] ||
    release_fail "${WHEEL} 的 sha256 与签名的清单不符：未做任何改动。"
  STAGE="$INCOMING/$VERSION"
  if [ "$(cat "$STAGE/.verified" 2>/dev/null || true)" != "$WHEEL_SHA" ]; then
    rm -rf "$STAGE"
    mkdir -p "$STAGE"
    python3 -I - "$WORK/$WHEEL" "$STAGE" <<'KC_UNPACK'
import os, shutil, sys, zipfile
wheel, stage = sys.argv[1:]
with zipfile.ZipFile(wheel) as archive:   # extract() keeps every path inside stage
    for entry in archive.infolist():
        path = archive.extract(entry, stage)
        mode = (entry.external_attr >> 16) & 0o777
        if mode:
            os.chmod(path, mode)
carried = os.path.join(stage, "octopus", "_release")
for name in os.listdir(carried):   # deploy/, skills/, package.json …: where a checkout has them (al2023.sh copies the tree)
    shutil.move(os.path.join(carried, name), os.path.join(stage, name))
os.rmdir(carried)
KC_UNPACK
    printf '%s\n' "$WHEEL_SHA" > "$STAGE/.verified"
  fi
  printf '已验签：KiroCrew ON %s（%s）\n' "$VERSION" "$RELEASE_CHANNEL"
}

valid_version() { printf '%s' "$1" | grep -Eq '^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$'; }

run_steps() {  # the install steps of the release in STAGE (al2023.sh: every one of them fills in only what is missing)
  NEW_PASSWORD=""
  [ -s "$ADMIN_FILE" ] || NEW_PASSWORD=1
  KIROCREW_ON_RELEASE="$VERSION" AWS_REGION="$EC2_REGION" bash "$STAGE/deploy/install/al2023.sh" "$BOUNDARY" ||
    { printf '%s\n' "安装步骤没有完成（原因见上面的输出）。处理后再运行一次即可：已完成的步骤会跳过，只补缺。" >&2; exit 1; }
}

finish() {
  printf '\n完成：KiroCrew ON %s 单机版\n' "$VERSION"
  printf '控制台：先在你的电脑上运行 aws ssm start-session --region %s --target %s --document-name AWS-StartPortForwardingSession --parameters portNumber=8918,localPortNumber=8918，再打开 http://localhost:8918\n' "$EC2_REGION" "$EC2_INSTANCE_ID"
  printf '%s\n' "管理员：admin"
  if [ -n "$NEW_PASSWORD" ] && [ -s "$ADMIN_FILE" ]; then
    printf '初始密码：%s（只打印这一次；另存在 /etc/kirocrew-on/admin-password，仅 root 可读）\n' "$(cat "$ADMIN_FILE")"
  else
    printf '%s\n' "密码：首次安装时已打印；忘了可运行 sudo cat /etc/kirocrew-on/admin-password"
  fi
  printf '%s\n' "下一步：登录控制台，在「我的设置 › 执行环境」里点「登录 Kiro」，用设备码登录 Kiro，然后就可以提交会话。"
  [ "$ARCH" = aarch64 ] || printf '%s\n' "$RECOMMEND"
}

CHECK="" CHANNEL=stable VERSION="" FROM="" SOURCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --channel) [ $# -ge 2 ] || usage 2; CHANNEL=$2; shift ;;
    --version) [ $# -ge 2 ] || usage 2; VERSION=$2; shift ;;
    --from) [ $# -ge 2 ] || usage 2; FROM=$2; shift ;;
    --source) [ $# -ge 2 ] || usage 2; SOURCE=$2; shift ;;
    -h|--help) usage 0 ;;
    *) printf '不认识的选项：%s\n' "$1" >&2; usage 2 ;;
  esac
  shift
done
case "$CHANNEL" in stable|preview) ;; *) fail "频道只有 stable 与 preview：${CHANNEL}" ;; esac
[ -z "$VERSION" ] || valid_version "$VERSION" || fail "版本号不对：${VERSION}（例如 0.2.0 或 0.2.0rc1）"
[ -z "$FROM" ] || [ -d "$FROM" ] || fail "没有这个目录：${FROM}"

preflight
conclude
if [ -n "$CHECK" ]; then
  [ "$ARCH" = aarch64 ] || printf '%s\n' "$RECOMMEND"
  exit 0
fi
if [ -n "$SOURCE" ]; then
  [ -f "$SOURCE/deploy/install/al2023.sh" ] || fail "${SOURCE} 不是展开的发布（没有 deploy/install/al2023.sh）"
  [ -n "$VERSION" ] || fail "--source 需要同时给出 --version"
  STAGE=$SOURCE
else
  take_release
fi
run_steps
finish
