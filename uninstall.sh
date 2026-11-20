#!/bin/sh
# Uninstalls Boreas and brings asusd / supergfxd / power-profiles-daemon back. Service settings stay in /etc/boreas.
set -e
if [ "$(id -u)" != 0 ]; then
    echo "Run as root: sudo ./uninstall.sh" >&2
    exit 1
fi
systemctl disable --now boreasd.service 2>/dev/null || true
rm -rf /usr/lib/boreas /usr/share/doc/boreas
rm -f /usr/bin/boreas /usr/bin/boreasd /usr/bin/boreasctl \
      /usr/lib/systemd/system/boreasd.service /usr/lib/systemd/system-sleep/boreas \
      /usr/share/applications/boreas.desktop /usr/share/icons/hicolor/scalable/apps/boreas.svg \
      /usr/share/icons/hicolor/16x16/apps/boreas.svg /usr/share/icons/hicolor/22x22/apps/boreas.svg \
      /usr/share/icons/hicolor/24x24/apps/boreas.svg
rm -rf /etc/xdg/boreas
systemctl daemon-reload
UNITS="asusd.service asus-shutdown.service supergfxd.service power-profiles-daemon.service"
MASKED=/etc/boreas/masked-units
if [ -f "$MASKED" ]; then
    # Only what install.sh masked comes back, in the state it had: a unit the user had masked
    # or disabled before Boreas stays the way they left it.
    back="" enable=""
    while read -r s was; do
        case " $UNITS " in *" $s "*) ;; *) continue ;; esac
        if [ "$(systemctl is-enabled "$s" 2>/dev/null)" = masked ]; then
            systemctl unmask "$s"
        fi
        if [ "$was" != disabled ]; then
            back="$back $s"  # a disabled unit was not running at boot either
        fi
        if [ "$was" = enabled ]; then
            enable="$enable $s"
        fi
    done < "$MASKED"
    rm -f "$MASKED"
else
    # installed by a version that kept no record: every mask is taken to be ours.
    # supergfxd is a normal enabled unit; asusd / asus-shutdown come from udev (static);
    # power-profiles-daemon is started by the desktop over D-Bus
    for s in $UNITS; do
        if [ "$(systemctl is-enabled "$s" 2>/dev/null)" = masked ]; then
            systemctl unmask "$s"
        fi
    done
    back="$UNITS" enable="supergfxd.service"
fi
systemctl daemon-reload
# $enable and $back are word lists
# shellcheck disable=SC2086
[ -z "$enable" ] || systemctl enable $enable 2>/dev/null || true
# shellcheck disable=SC2086
[ -z "$back" ] || systemctl start $back 2>/dev/null || true
echo "Removed. Settings are kept in /etc/boreas."
