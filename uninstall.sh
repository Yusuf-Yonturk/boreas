#!/bin/sh
# Kaldırır ve asusd / supergfxd'yi geri açar. Servis ayarları /etc/boreas içinde kalır.
set -e
systemctl disable --now boreasd.service 2>/dev/null || true
rm -rf /usr/lib/boreas
rm -f /usr/bin/boreas /usr/bin/boreasd /usr/bin/boreasctl \
      /usr/lib/systemd/system/boreasd.service /usr/lib/systemd/system-sleep/boreas \
      /usr/share/applications/boreas.desktop /usr/share/icons/hicolor/scalable/apps/boreas.svg \
      /usr/share/icons/hicolor/16x16/apps/boreas.svg /usr/share/icons/hicolor/22x22/apps/boreas.svg \
      /usr/share/icons/hicolor/24x24/apps/boreas.svg
rm -rf /etc/xdg/boreas
systemctl daemon-reload
for s in asusd.service asus-shutdown.service supergfxd.service; do
    if [ "$(systemctl is-enabled "$s" 2>/dev/null)" = masked ]; then
        systemctl unmask "$s"
    fi
done
systemctl daemon-reload
# supergfxd normal etkin birimdir; asusd / asus-shutdown udev ile gelir (static)
systemctl enable supergfxd.service 2>/dev/null || true
systemctl start asusd.service asus-shutdown.service supergfxd.service 2>/dev/null || true
echo "Removed. Settings are kept in /etc/boreas."
