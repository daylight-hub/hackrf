#!/usr/bin/env bash
# Removes RF Script Studio (user install, or system install when run with sudo --system).
# HackRF packages and udev rules are left in place; your settings stay in ~/.config/rfscriptstudio.
set -euo pipefail
if [[ "${1:-}" == "--system" ]]; then
  PREFIX=/opt/rf-script-studio; BIN=/usr/local/bin; APPS=/usr/share/applications
  ICONS=/usr/share/icons/hicolor/512x512/apps
else
  PREFIX="$HOME/.local/opt/rf-script-studio"; BIN="$HOME/.local/bin"; APPS="$HOME/.local/share/applications"
  ICONS="$HOME/.local/share/icons/hicolor/512x512/apps"
fi
systemctl --user disable --now rfscriptstudio.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/rfscriptstudio.service" "$HOME/.config/autostart/rfscriptstudio.desktop"
rm -rf "$PREFIX"
rm -f "$BIN/rfscriptstudio" "$BIN/rfstudio-cli" "$APPS/rfscriptstudio.desktop" "$ICONS/rfscriptstudio.png"
echo "RF Script Studio removed."
