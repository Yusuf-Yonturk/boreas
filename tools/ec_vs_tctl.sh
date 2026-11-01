#!/bin/bash
# Reads the CPU temperature reported by the ASUS EC (DEVID 0x00120094) next to Tctl.
# Read-only; changes no settings. Run with sudo.
set -e
mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug
W=/sys/kernel/debug/asus-nb-wmi
K=$(dirname "$(grep -l k10temp /sys/class/hwmon/*/name)")
A=$(dirname "$(grep -lx acpitz /sys/class/hwmon/*/name)")
OUT=${OUT:-$(dirname "$0")/ec_vs_tctl.log}
echo "time      EC_Temp_CPU  Tctl  Tccd1  acpitz  (raw dsts)" | tee "$OUT"
for i in $(seq 1 30); do
    echo 0x00120094 > "$W/dev_id"
    raw=$(cat "$W/dsts")
    hex=$(echo "$raw" | grep -oE '0x[0-9a-fA-F]+$')
    ec=$(( hex & 0xFFFF ))
    printf "%s  %-11s  %-4s  %-5s  %-6s  %s\n" "$(date +%T)" "$ec" \
        $(( $(cat $K/temp1_input) / 1000 )) $(( $(cat $K/temp3_input) / 1000 )) \
        $(( $(cat $A/temp1_input) / 1000 )) "$raw" | tee -a "$OUT"
    sleep 1
done
chmod 644 "$OUT"
