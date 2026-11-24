#!/usr/bin/env bash
# Package dist/RFScriptStudio into a tarball and a .deb.
#   tools/package_linux.sh <version>
set -euo pipefail
VERSION="${1:?version}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/RFScriptStudio"
[[ -x "$APP/rfscriptstudio" ]] || { echo "build first: $APP missing" >&2; exit 1; }

# ---------------------------------------------------------------- tarball
STAGE="$ROOT/build/tar/RFScriptStudio-$VERSION-linux-x86_64"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -a "$APP" "$STAGE/RFScriptStudio"
install -m755 "$ROOT/packaging/linux/install.sh" "$STAGE/install.sh"
install -m755 "$ROOT/packaging/linux/uninstall.sh" "$STAGE/uninstall.sh"
install -m755 "$ROOT/packaging/linux/linux-setup.sh" "$STAGE/hackrf-setup.sh"
cp "$ROOT/README.md" "$STAGE/README.md"
tar -C "$(dirname "$STAGE")" -czf "$DIST/RFScriptStudio-$VERSION-linux-x86_64.tar.gz" "$(basename "$STAGE")"
echo "tarball: $DIST/RFScriptStudio-$VERSION-linux-x86_64.tar.gz"

# -------------------------------------------------------------------- deb
PKG="$ROOT/build/deb/rf-script-studio_${VERSION}_amd64"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/rf-script-studio" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/lib/udev/rules.d"
cp -a "$APP/." "$PKG/opt/rf-script-studio/"
ln -s /opt/rf-script-studio/rfscriptstudio "$PKG/usr/bin/rfscriptstudio"
ln -s /opt/rf-script-studio/rfstudio-cli "$PKG/usr/bin/rfstudio-cli"
cp "$ROOT/assets/icon.png" "$PKG/usr/share/icons/hicolor/512x512/apps/rfscriptstudio.png"
cat > "$PKG/usr/share/applications/rfscriptstudio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RF Script Studio
Comment=Control and supervise HackRF Python scripts
Exec=/opt/rf-script-studio/rfscriptstudio
Icon=rfscriptstudio
Terminal=false
Categories=HamRadio;Utility;Development;
StartupWMClass=rfscriptstudio
EOF
sed -n '/^# HackRF devices/,/^EOF$/p' "$ROOT/packaging/linux/linux-setup.sh" | sed '$d' \
  > "$PKG/usr/lib/udev/rules.d/53-hackrf-rfscriptstudio.rules"

SIZE_KB=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<EOF
Package: rf-script-studio
Version: $VERSION
Section: hamradio
Priority: optional
Architecture: amd64
Installed-Size: $SIZE_KB
Depends: hackrf, libhackrf0, libusb-1.0-0, libxcb-cursor0, libxkbcommon-x11-0, libegl1, libfontconfig1, libdbus-1-3
Recommends: python3
Maintainer: RF Script Studio maintainers <rf-script-studio@users.noreply.github.com>
Description: Desktop control centre for HackRF Python scripts
 Generates a settings panel from a script's variables, supervises the
 script (auto-restart, hang detection), runs in the tray or as a background
 service, and drives the HackRF through pyhackrf2 (bundled) or hackrf_transfer.
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
groupadd -f plugdev
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  usermod -aG plugdev "$SUDO_USER" || true
fi
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=usb || true
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"
dpkg-deb --root-owner-group --build "$PKG" "$DIST/" >/dev/null
echo "deb: $DIST/rf-script-studio_${VERSION}_amd64.deb"
