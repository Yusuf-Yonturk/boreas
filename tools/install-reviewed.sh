#!/bin/sh
# Install reviewed files with a backup, leaving the GPU in Standard for live tests.
set -eu
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo sh tools/install-reviewed.sh" >&2; exit 1; }
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STAGE=$(mktemp -d /tmp/boreas-reviewed.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
DESTDIR="$STAGE" sh "$REPO/install.sh" --files-only
"$STAGE/usr/lib/boreas/boreas-gpu-engine" --protocol-info
if [ -e /etc/boreas/boreas-gpu-engine ]; then
    # boreasd prefers the local override, so the staged helper above is not the one that runs.
    echo "Local helper override /etc/boreas/boreas-gpu-engine is in use; checking it too" >&2
    /etc/boreas/boreas-gpu-engine --protocol-info
fi
python3 -m compileall -q "$STAGE/usr/lib/boreas/boreas"
BACKUP="/var/lib/boreas/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m700 "$BACKUP"
for path in /usr/lib/boreas /etc/boreas /usr/lib/systemd/system/boreasd.service /usr/bin/boreas /usr/bin/boreasd /usr/bin/boreasctl; do
    if [ -e "$path" ]; then
        cp -a --parents "$path" "$BACKUP"
    fi
done
printf '%s\n' "$BACKUP" > /var/lib/boreas/last-reviewed-backup
# Never request a MUX change as a side effect of installing reviewed files.
/usr/bin/boreasctl status | python3 -c 'import json,sys; g=json.load(sys.stdin).get("gpu") or {}; sys.exit("Leave Ultimate / finish the pending reboot before this live test" if g.get("mux") == 0 or g.get("pending_reboot") else 0)'
# Use the current service to leave firmware Eco before changing runtime files.
/usr/bin/boreasctl gpu standard
systemctl stop boreasd.service
if ! sh "$REPO/install.sh" --files-only; then
    echo "Install failed. Backup: $BACKUP. Service remains stopped; restore backup before restarting." >&2
    exit 1
fi
systemctl daemon-reload
systemctl start boreasd.service
systemctl is-active --quiet boreasd.service
/usr/bin/boreasctl status
printf '\nReviewed installation ready. Backup: %s\n' "$BACKUP"
