#!/usr/bin/env bash
# RF Script Studio installer (Linux tarball)
#
#   ./install.sh                  install for the current user (asks for sudo once
#                                 to install the HackRF packages + udev rules)
#   sudo ./install.sh --system    install system-wide into /opt
#   ./install.sh --no-deps        skip the HackRF/apt step
#   ./install.sh --with-python    also pip-install pyhackrf2 for the system Python
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM=0; DEPS=1; PYARG=""
for a in "$@"; do
  case "$a" in
    --system) SYSTEM=1 ;;
    --no-deps) DEPS=0 ;;
    --with-python) PYARG="--with-python" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

if [[ $SYSTEM -eq 1 ]]; then
  [[ $EUID -eq 0 ]] || { echo "--system needs root: sudo $0 $*" >&2; exit 1; }
  PREFIX=/opt/rf-script-studio; BIN=/usr/local/bin
  APPS=/usr/share/applications; ICONS=/usr/share/icons/hicolor/512x512/apps
else
  PREFIX="$HOME/.local/opt/rf-script-studio"; BIN="$HOME/.local/bin"
  APPS="$HOME/.local/share/applications"; ICONS="$HOME/.local/share/icons/hicolor/512x512/apps"
fi

echo "==> Installing RF Script Studio to $PREFIX"
rm -rf "$PREFIX"
mkdir -p "$PREFIX" "$BIN" "$APPS" "$ICONS"
cp -a "$HERE/RFScriptStudio/." "$PREFIX/"
ln -sf "$PREFIX/rfscriptstudio" "$BIN/rfscriptstudio"
ln -sf "$PREFIX/rfstudio-cli" "$BIN/rfstudio-cli"
cp "$PREFIX/_internal/assets/icon.png" "$ICONS/rfscriptstudio.png"
cat > "$APPS/rfscriptstudio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RF Script Studio
Comment=Control and supervise HackRF Python scripts
Exec=$PREFIX/rfscriptstudio
Icon=rfscriptstudio
Terminal=false
Categories=HamRadio;Utility;Development;
StartupWMClass=rfscriptstudio
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true

if [[ $DEPS -eq 1 ]]; then
  echo "==> Installing HackRF drivers and tools (hackrf, libhackrf, udev rules)"
  SETUP="$PREFIX/_internal/setup/linux-setup.sh"
  if [[ $EUID -eq 0 ]]; then
    bash "$SETUP" --user "${SUDO_USER:-root}" $PYARG
  else
    sudo bash "$SETUP" --user "$USER" $PYARG
  fi
fi

echo
echo "Done. Start it from your applications menu or run: rfscriptstudio"
[[ ":$PATH:" == *":$BIN:"* ]] || echo "Note: add $BIN to your PATH to use the rfscriptstudio command."
