#!/usr/bin/env bash
# RF Script Studio - Linux HackRF setup
#
# Installs everything a HackRF needs on Linux:
#   * libhackrf + hackrf tools (hackrf_info, hackrf_transfer, ...)
#   * libusb and the Qt runtime libraries the desktop app uses
#   * udev rules so the board is usable without root
#   * adds the user to the "plugdev" group
#   * optionally (--with-python) pyhackrf2 + numpy for the *system* Python
#     (the app itself already bundles them; only needed for a custom interpreter)
#
# Usage:  sudo ./linux-setup.sh [--user NAME] [--with-python] [--no-packages]
set -euo pipefail

TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" && -n "${PKEXEC_UID:-}" ]]; then
  TARGET_USER="$(id -nu "$PKEXEC_UID")"
fi
WITH_PYTHON=0
PACKAGES=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) TARGET_USER="$2"; shift 2 ;;
    --with-python) WITH_PYTHON=1; shift ;;
    --no-packages) PACKAGES=0; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
  echo "This script needs root. Re-run with: sudo $0 $*" >&2
  exit 1
fi

install_optional() {  # install packages one by one, ignoring names a distro doesn't have
  local cmd="$1"; shift
  for p in "$@"; do $cmd "$p" >/dev/null 2>&1 || warn "optional package not available: $p"; done
}

if [[ $PACKAGES -eq 1 ]]; then
  if command -v apt-get >/dev/null; then
    say "Installing HackRF packages with apt"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y hackrf libhackrf0 libusb-1.0-0
    install_optional "apt-get install -y -qq" libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 \
      libfontconfig1 libdbus-1-3 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 \
      libxcb-randr0 libxcb-render-util0 libxcb-image0
  elif command -v dnf >/dev/null; then
    say "Installing HackRF packages with dnf"
    dnf install -y hackrf libusb1
    install_optional "dnf install -y -q" xcb-util-cursor xcb-util-wm xcb-util-keysyms libxkbcommon-x11 mesa-libEGL
  elif command -v pacman >/dev/null; then
    say "Installing HackRF packages with pacman"
    pacman -Sy --needed --noconfirm hackrf libusb
    install_optional "pacman -S --needed --noconfirm" xcb-util-cursor xcb-util-wm xcb-util-keysyms libxkbcommon-x11
  elif command -v zypper >/dev/null; then
    say "Installing HackRF packages with zypper"
    zypper --non-interactive install hackrf libusb-1_0-0
    install_optional "zypper --non-interactive install" libxcb-cursor0 libxkbcommon-x11-0
  else
    warn "No supported package manager found (apt, dnf, pacman, zypper)."
    warn "Install libhackrf and the hackrf tools manually, then re-run with --no-packages."
  fi
fi

say "Installing udev rules"
mkdir -p /etc/udev/rules.d
RULES=/etc/udev/rules.d/53-hackrf-rfscriptstudio.rules
cat > "$RULES" <<'EOF'
# HackRF devices - installed by RF Script Studio
ATTR{idVendor}=="1d50", ATTR{idProduct}=="604b", SYMLINK+="hackrf-jawbreaker-%k", MODE="660", GROUP="plugdev", TAG+="uaccess"
ATTR{idVendor}=="1d50", ATTR{idProduct}=="6089", SYMLINK+="hackrf-one-%k", MODE="660", GROUP="plugdev", TAG+="uaccess"
ATTR{idVendor}=="1d50", ATTR{idProduct}=="cc15", SYMLINK+="rad1o-%k", MODE="660", GROUP="plugdev", TAG+="uaccess"
ATTR{idVendor}=="1fc9", ATTR{idProduct}=="000c", SYMLINK+="nxp-dfu-%k", MODE="660", GROUP="plugdev", TAG+="uaccess"
EOF
groupadd -f plugdev
if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
  if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx plugdev; then
    say "$TARGET_USER is already in plugdev"
  else
    usermod -aG plugdev "$TARGET_USER"
    say "Added $TARGET_USER to plugdev (log out and back in for it to take effect)"
  fi
fi
if command -v udevadm >/dev/null; then
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=usb || true
fi

if [[ $WITH_PYTHON -eq 1 ]]; then
  say "Installing pyhackrf2 + numpy for the system Python"
  if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
    sudo -u "$TARGET_USER" -H python3 -m pip install --user --upgrade pyhackrf2 numpy \
      || sudo -u "$TARGET_USER" -H python3 -m pip install --user --break-system-packages --upgrade pyhackrf2 numpy \
      || warn "pip install failed - install python3-pip and retry"
  else
    python3 -m pip install --upgrade pyhackrf2 numpy || python3 -m pip install --break-system-packages --upgrade pyhackrf2 numpy
  fi
fi

if command -v hackrf_info >/dev/null; then
  say "hackrf_info: $(command -v hackrf_info)"
  say "Re-plug your HackRF, then run 'hackrf_info' (or press Detect in the app) to check it."
fi
say "HackRF setup complete."
