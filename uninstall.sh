#!/bin/sh
# Uninstalls Boreas and brings asusd / supergfxd / power-profiles-daemon back. Service settings stay in /etc/boreas.
set -e
if [ "$(id -u)" != 0 ]; then
    echo "Run as root: sudo ./uninstall.sh" >&2
    exit 1
fi
if [ -f "${BOREAS_PACKAGED_MARK:-/usr/lib/boreas/packaged}" ]; then
    echo "Boreas is installed as a package. Remove it with your package manager (pacman -R boreas or boreas-git)." >&2
    exit 1
fi
SRC="$(cd "$(dirname "$0")" && pwd)"
# stops boreasd and brings back the services the installer masked, in the state they had
sh "$SRC/data/boreas-setup" disable --legacy
rm -rf /usr/lib/boreas /usr/share/doc/boreas
rm -f /usr/bin/boreas /usr/bin/boreasd /usr/bin/boreasctl /usr/bin/boreas-setup \
      /usr/lib/systemd/system/boreasd.service /usr/lib/systemd/system-sleep/boreas \
      /usr/share/applications/boreas.desktop /usr/share/icons/hicolor/scalable/apps/boreas.svg \
      /usr/share/icons/hicolor/16x16/apps/boreas.svg /usr/share/icons/hicolor/22x22/apps/boreas.svg \
      /usr/share/icons/hicolor/24x24/apps/boreas.svg
rm -rf /etc/xdg/boreas
systemctl daemon-reload
echo "Removed. Settings are kept in /etc/boreas."
