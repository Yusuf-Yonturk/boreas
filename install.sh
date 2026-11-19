#!/bin/sh
# Install: sudo ./install.sh      For packaging: DESTDIR=... ./install.sh --files-only
set -e
D="${DESTDIR:-}"
SRC="$(cd "$(dirname "$0")" && pwd)"

# Fail before modifying files when root is required.
if [ -z "$D" ] && [ "$(id -u)" != 0 ]; then
    echo "Run as root: sudo ./install.sh" >&2
    exit 1
fi

# The Rust GPU helper is built by CI and attached to the GitHub Release of each version;
# the checkout does not carry it. It is resolved and verified before anything is written:
#   1. BOREAS_GPU_ENGINE=/path/to/boreas-gpu-engine (offline installs, developer builds)
#   2. data/bin/boreas-gpu-engine next to this script (release tarball or a copied build)
#   3. download for the version in boreas/__init__.py and check its sha256
# Installing the helper never selects the eBPF backend.
fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 -o "$2" "$1"
    else
        python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"
    fi
}
helper_tmp=""
if [ -n "${BOREAS_GPU_ENGINE:-}" ]; then
    helper_src="$BOREAS_GPU_ENGINE"
    if [ ! -f "$helper_src" ]; then
        echo "BOREAS_GPU_ENGINE does not point to a file: $helper_src" >&2
        exit 1
    fi
elif [ -f "$SRC/data/bin/boreas-gpu-engine" ]; then
    helper_src="$SRC/data/bin/boreas-gpu-engine"
else
    version=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$SRC/boreas/__init__.py")
    base="${BOREAS_GPU_ENGINE_URL:-https://github.com/Yusuf-Yonturk/boreas/releases/download/v$version}"
    helper_tmp=$(mktemp -d)
    trap 'rm -rf "$helper_tmp"' EXIT
    trap 'exit 130' HUP INT TERM  # stop here; the EXIT trap cleans up
    helper_src="$helper_tmp/boreas-gpu-engine"
    echo "Downloading the GPU helper for Boreas $version from $base"
    if ! fetch "$base/boreas-gpu-engine" "$helper_src" || ! fetch "$base/boreas-gpu-engine.sha256" "$helper_src.sha256"; then
        # The helper is optional: without it only the eBPF GPU backend is missing. A helper
        # that is already installed is left as it is.
        echo "Warning: could not download the GPU helper from $base" >&2
        echo "         Boreas is installed without it; the eBPF GPU backend stays unavailable (or keeps the" >&2
        echo "         helper already installed). Run install.sh again when the network is back, or use" >&2
        echo "         BOREAS_GPU_ENGINE=/path/to/boreas-gpu-engine." >&2
        helper_src=""
    else
        expected=$(cut -d' ' -f1 "$helper_src.sha256")
        actual=$(sha256sum "$helper_src" | cut -d' ' -f1)
        if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
            # a helper that arrived but does not match is never skipped over
            echo "GPU helper checksum mismatch: expected $expected, got $actual" >&2
            echo "Nothing was installed. Retry, or install offline with BOREAS_GPU_ENGINE=/path/to/boreas-gpu-engine" >&2
            exit 1
        fi
    fi
fi

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

# Install the verified helper, preserving locally modified managed copies.
if [ -n "$helper_src" ]; then
    helper_dest="$D/usr/lib/boreas/boreas-gpu-engine"
    helper_stamp="$helper_dest.sha256"
    local_helper="$D/etc/boreas/boreas-gpu-engine"
    if [ -f "$helper_dest" ] && ! cmp -s "$helper_src" "$helper_dest"; then
        existing_hash=$(sha256sum "$helper_dest" | cut -d' ' -f1)
        recorded_hash=$(cat "$helper_stamp" 2>/dev/null || true)
        if [ "$existing_hash" != "$recorded_hash" ]; then
            # A checksum identifies edits to managed files on subsequent upgrades.
            # Legacy files without a checksum are preserved but not auto-activated:
            # they may be stock helpers predating protocol negotiation.
            if [ -n "$recorded_hash" ] && [ ! -e "$local_helper" ] && [ ! -L "$local_helper" ]; then
                preserve="$local_helper"
            else
                preserve="$D/etc/boreas/preserved-gpu-engines/$existing_hash"
            fi
            install -Dm755 "$helper_dest" "$preserve"
            echo "Preserved existing GPU engine: $preserve"
        fi
    fi
    install -m755 "$helper_src" "$helper_dest"
    sha256sum "$helper_dest" | cut -d' ' -f1 > "$helper_stamp"
fi
install -Dm644 "$SRC/THIRD_PARTY_NOTICES.md" "$D/usr/share/doc/boreas/THIRD_PARTY_NOTICES.md"
install -Dm644 "$SRC/gpu-engine/vendor/LICENSE.cardwire" "$D/usr/share/doc/boreas/LICENSE.cardwire"

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

# mask asusd / supergfxd / power-profiles-daemon so they do not write to the same
# hardware (undo: uninstall.sh). asusd is a "static" unit and the asusctl udev rule
# starts it on every boot; power-profiles-daemon is D-Bus activated by the desktop.
# disable does not help, and through Conflicts= in boreasd they would stop us too.
# What is masked here is recorded with its previous state, so uninstall.sh brings back only
# that: a unit the user had masked or disabled before Boreas stays the way they left it.
MASKED=/etc/boreas/masked-units
systemctl daemon-reload
for s in asusd.service asus-shutdown.service supergfxd.service power-profiles-daemon.service; do
    was=$(systemctl is-enabled "$s" 2>/dev/null || true)
    if [ -n "$(systemctl list-unit-files --no-legend "$s" 2>/dev/null)" ] && [ "$was" != masked ]; then
        echo "masking: $s"
        systemctl mask --now "$s"
        install -d /etc/boreas
        grep -q "^$s " "$MASKED" 2>/dev/null || echo "$s $was" >> "$MASKED"
    fi
done
systemctl enable boreasd.service
systemctl restart boreasd.service  # so the old code does not keep running after an update
echo "Installed. UI: boreas   CLI: boreasctl status"
# the socket is root:wheel 0660; without the group it is root-only and the UI cannot connect
if ! getent group wheel >/dev/null 2>&1; then
    echo "Warning: there is no 'wheel' group, so only root can reach the service (sudo boreasctl status)."
    echo "         Create it and add your user: sudo groupadd wheel && sudo usermod -aG wheel <user>, then log in again."
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] &&
     ! id -nG "$SUDO_USER" 2>/dev/null | tr ' ' '\n' | grep -qx wheel; then
    echo "Warning: $SUDO_USER is not in the 'wheel' group, so the UI and boreasctl cannot reach the service."
    echo "         sudo usermod -aG wheel $SUDO_USER, then log in again."
fi
echo "If rog-control-center starts automatically, turn that off in its settings."
