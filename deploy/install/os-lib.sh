# shellcheck shell=bash
# What differs between the systems KiroCrew ON installs on (MVP 0.2.0 P2, design §4.2.2): Amazon Linux 2023 (dnf, SELinux)
# and Ubuntu 22.04 / 24.04 (apt, AppArmor). Sourced by al2023.sh. install.sh exports OS_ID and OS_VERSION after its
# preflight; without them (deploy.sh push) the system is read from /etc/os-release.

os_detect() {  # OS_ID (al2023 | ubuntu), OS_VERSION, PKG (dnf | apt), NGINX_GROUP; any other system stops here
  if [ -z "${OS_ID:-}" ]; then
    local file="${OS_RELEASE:-/etc/os-release}" id
    id="$(sed -n 's/^ID=//p' "$file" | tr -d "\"'")"
    OS_VERSION="$(sed -n 's/^VERSION_ID=//p' "$file" | tr -d "\"'")"
    case "$id" in amzn) OS_ID=al2023 ;; *) OS_ID="$id" ;; esac
  fi
  case "$OS_ID:$OS_VERSION" in
    al2023:2023) PKG=dnf; NGINX_GROUP=nginx ;;
    ubuntu:22.04|ubuntu:24.04) PKG=apt; NGINX_GROUP=www-data ;;
    *) echo "不支持这个系统（$OS_ID $OS_VERSION）：只支持 Amazon Linux 2023、Ubuntu 22.04 与 Ubuntu 24.04" >&2; return 2 ;;
  esac
}

os_packages() {  # os_packages base | browser: package names on this system
  case "$1:$PKG" in
    base:dnf) echo git tar gzip xz jq unzip make gcc gcc-c++ nginx "$PYTHON_PACKAGE" "$PYTHON_PACKAGE-pip" "$PYTHON_PACKAGE-devel" ;;
    base:apt)
      echo git tar gzip xz-utils jq unzip make gcc g++ nginx curl ca-certificates
      # 24.04 has Python 3.12; 22.04 gets it from uv (install_python)
      [ "$OS_VERSION" = 22.04 ] || echo "$PYTHON_PACKAGE" "$PYTHON_PACKAGE-venv" "$PYTHON_PACKAGE-dev" ;;
    # What the Agents' Playwright Chromium needs to open a page (reviews, frontend checks; EC2 2026-10-19 lacked them all).
    browser:dnf) echo nss nspr libdrm libX11 libXcomposite libXdamage libXext libXfixes libXrandr alsa-lib atk at-spi2-atk \
      at-spi2-core cairo cups-libs mesa-libgbm pango libxcb libxkbcommon ;;
    browser:apt)
      local t64=""   # 24.04 renamed the libraries whose ABI moved to a 64-bit time_t
      [ "$OS_VERSION" = 22.04 ] || t64=t64
      echo libnss3 libnspr4 libdrm2 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 "libasound2$t64" \
        "libatk1.0-0$t64" "libatk-bridge2.0-0$t64" "libatspi2.0-0$t64" libcairo2 "libcups2$t64" libgbm1 libpango-1.0-0 libxcb1 \
        libxkbcommon0 ;;
  esac
}

install_packages() {  # the system packages, nginx and (where the system has it) Python 3.12
  # shellcheck disable=SC2046   # package names are single words
  case "$PKG" in
    dnf)
      dnf install -y -q $(os_packages base)
      dnf install -y -q $(os_packages browser) ;;
    apt)
      DEBIAN_FRONTEND=noninteractive apt-get update -q
      DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends $(os_packages base) $(os_packages browser) ;;
  esac
}

install_python() {  # PYTHON: Python 3.12 — the system's, or on Ubuntu 22.04 a CPython uv manages (uv checks its hashes)
  if [ "$OS_ID:$OS_VERSION" != ubuntu:22.04 ]; then
    PYTHON="$(command -v "$PYTHON_PACKAGE")"
    return 0
  fi
  local root="${PYTHON_ROOT:-/opt/kirocrew-python}" asset="uv-$ARCH-unknown-linux-gnu" digest work
  case "$ARCH" in aarch64) digest="$UV_SHA256_AARCH64" ;; *) digest="$UV_SHA256_X86_64" ;; esac
  if [ ! -x "$root/bin/uv" ]; then
    work="$(mktemp -d)"
    curl --proto '=https' --tlsv1.2 -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$asset.tar.gz" \
      -o "$work/uv.tar.gz"
    if ! echo "$digest  $work/uv.tar.gz" | sha256sum -c - >/dev/null; then
      rm -rf "$work"
      echo "uv $UV_VERSION（$asset）的 sha256 与 deploy/versions.env 不符，未使用" >&2
      return 3
    fi
    tar -xzf "$work/uv.tar.gz" -C "$work"
    install -d -m 0755 "$root/bin"
    install -m 0755 "$work/$asset/uv" "$root/bin/uv"
    rm -rf "$work"
  fi
  UV_PYTHON_INSTALL_DIR="$root/cpython" UV_PYTHON_PREFERENCE=only-managed "$root/bin/uv" python install 3.12
  PYTHON="$(UV_PYTHON_INSTALL_DIR="$root/cpython" UV_PYTHON_PREFERENCE=only-managed "$root/bin/uv" python find 3.12)"
}

label_port() {  # label_port PORT...: let nginx listen on and proxy to these ports under SELinux; AppArmor needs nothing
  command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ] || return 0
  setsebool -P httpd_can_network_connect 1
  local port
  for port in "$@"; do
    semanage port -a -t http_port_t -p tcp "$port" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$port"
  done
}
