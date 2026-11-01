#!/bin/sh
# Install: sudo ./install.sh      For packaging: DESTDIR=... ./install.sh --files-only
set -e
D="${DESTDIR:-}"
SRC="$(cd "$(dirname "$0")" && pwd)"

# clear the code folder so modules left over from an older version do not linger
rm -rf "$D/usr/lib/boreas/boreas"
install -d "$D/usr/lib/boreas/boreas/gui"
install -m644 "$SRC"/boreas/*.py "$D/usr/lib/boreas/boreas/"
install -m644 "$SRC"/boreas/gui/*.py "$D/usr/lib/boreas/boreas/gui/"
install -Dm755 "$SRC/data/bin/boreas" "$D/usr/bin/boreas"
install -Dm755 "$SRC/data/bin/boreasd" "$D/usr/bin/boreasd"
install -Dm755 "$SRC/data/bin/boreasctl" "$D/usr/bin/boreasctl"
install -Dm644 "$SRC/data/boreasd.service" "$D/usr/lib/systemd/system/boreasd.service"
install -Dm755 "$SRC/data/boreas.sleep" "$D/usr/lib/systemd/system-sleep/boreas"
install -Dm644 "$SRC/data/boreas.desktop" "$D/usr/share/applications/boreas.desktop"
install -Dm644 "$SRC/data/boreas.svg" "$D/usr/share/icons/hicolor/scalable/apps/boreas.svg"
# simple icon for small sizes (title bar, tray): the curves are unreadable at 16-24 px
for s in 16 22 24; do
    install -Dm644 "$SRC/data/boreas-small.svg" "$D/usr/share/icons/hicolor/${s}x${s}/apps/boreas.svg"
done

[ "$1" = "--files-only" ] && exit 0

if [ "$(id -u)" != 0 ]; then
    echo "Run as root: sudo ./install.sh" >&2
    exit 1
fi

# UI language: asked (Enter = English or the previous choice). Without asking: sudo BOREAS_LANG=tr ./install.sh
CONF=/etc/xdg/boreas/boreas.conf
CODES="en tr es fr pt ru de zh hi ja ar"
cur=$(sed -n 's/^language=//p' "$CONF" 2>/dev/null || true)
lang="${BOREAS_LANG:-}"
if [ -z "$lang" ] && [ -t 0 ]; then
    echo "Interface language:"
    # two columns: Latin/Cyrillic on the left (fixed width), CJK/Devanagari on the right; Arabic alone at the bottom,
    # kept left-to-right by the LRM (U+200E) at the start so the terminal does not reverse the line
    cat <<'EOF'
   1) English                6) Русский
   2) Türkçe                 7) Deutsch
   3) Español                8) 简体中文
   4) Français               9) हिन्दी
   5) Português (Brasil)    10) 日本語
‎  11) العربية
EOF
    printf "Choose 1-11 [%s]: " "${cur:-en}"
    read -r ans || ans=""
    case "$ans" in
        ''|*[!0-9]*) lang="" ;;
        *) lang=$(echo "$CODES" | cut -d' ' -f"$ans" 2>/dev/null || true) ;;
    esac
fi
lang="${lang:-${cur:-en}}"
case " $CODES " in *" $lang "*) ;; *) echo "Unknown language '$lang', using English"; lang=en ;; esac
install -d /etc/xdg/boreas
printf '[General]\nlanguage=%s\n' "$lang" > "$CONF"
echo "Language: $lang (each user can change it from the globe button in the window)"

# UI dependencies (the service only needs python3)
python3 -c "import PySide6.QtSvg" 2>/dev/null ||
    echo "Warning: the UI needs PySide6 (Arch/CachyOS: sudo pacman -S pyside6)"
[ -d /usr/lib/qt6/plugins/platforms ] && ! ls /usr/lib/qt6/plugins/platforms/*wayland* >/dev/null 2>&1 &&
    echo "Warning: qt6-wayland is recommended for a sharp UI on Wayland (sudo pacman -S qt6-wayland)"
case "$lang" in
    zh|ja) fl="$lang"; pkg=noto-fonts-cjk ;;
    hi|ar) fl="$lang"; pkg=noto-fonts ;;
    *) fl="" ;;
esac
if [ -n "$fl" ] && command -v fc-list >/dev/null && [ -z "$(fc-list :lang="$fl" 2>/dev/null | head -n1)" ]; then
    echo "Warning: no font found for '$lang' (sudo pacman -S $pkg)"
fi

# mask asusd / supergfxd so they do not write to the same hardware (undo: uninstall.sh).
# asusd is a "static" unit and the asusctl udev rule starts it on every boot;
# disable does not help, and through Conflicts= in boreasd it would stop us too.
systemctl daemon-reload
for s in asusd.service asus-shutdown.service supergfxd.service; do
    if [ -n "$(systemctl list-unit-files --no-legend "$s" 2>/dev/null)" ] &&
       [ "$(systemctl is-enabled "$s" 2>/dev/null)" != masked ]; then
        echo "masking: $s"
        systemctl mask --now "$s"
    fi
done
systemctl enable boreasd.service
systemctl restart boreasd.service  # so the old code does not keep running after an update
echo "Installed. UI: boreas   CLI: boreasctl status"
echo "If rog-control-center starts automatically, turn that off in its settings."
