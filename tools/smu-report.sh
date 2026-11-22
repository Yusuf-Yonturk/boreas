#!/bin/bash
# smu-report.sh [--no-load]: a report on the experimental CPU temperature ceiling (docs/cpu-smu.md)
# for a GitHub issue. Needs Boreas installed with sudo BOREAS_SMU=1 ./install.sh (or the
# boreas-smu-dkms package) and a user that may run boreasctl. Changes no setting. Without --no-load
# it puts every CPU thread under stress-ng for 90 s and samples Tctl every 0.5 s.
# The report goes to stdout and to ./boreas-smu-report.md.
set -u
OUT=boreas-smu-report.md
LOAD=1
[ "${1:-}" = "--no-load" ] && LOAD=0

rd() { cat "$1" 2>/dev/null || echo "?"; }
cpuinfo() { awk -F': ' -v k="$1" '$1 ~ "^"k"[ \t]*$" {print $2; exit}' /proc/cpuinfo; }

K=""
for h in /sys/class/hwmon/hwmon*; do
    [ "$(rd "$h/name")" = k10temp ] && K="$h/temp1_input" && break
done
tctl() { [ -n "$K" ] && echo $(( $(cat "$K") / 1000 )) || echo "?"; }
mhz() { awk '/^cpu MHz/{s+=$4;n++} END{if(n) printf "%d", s/n; else print "?"}' /proc/cpuinfo; }

status=$(boreasctl status 2>&1)
field() { # field KEY: a value from the cpu_temp block of boreasctl status
    printf '%s' "$status" | python3 -c "import json,sys
try: print(json.load(sys.stdin)['cpu_temp'].get('$1'))
except Exception: print('?')"
}
mode_name=$(printf '%s' "$status" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin); print(next(m['name'] for m in d['modes'] if m['id']==d['mode']))
except Exception: print('?')")
target=$(field target)

{
echo "## Boreas SMU report"
echo
echo "| | |"
echo "|---|---|"
echo "| Laptop | $(rd /sys/class/dmi/id/product_name) |"
echo "| BIOS | $(rd /sys/class/dmi/id/bios_version) |"
echo "| CPU | $(cpuinfo 'model name') |"
printf '| Family / model | 0x%x / 0x%x |\n' "$(cpuinfo 'cpu family')" "$(cpuinfo model)"
echo "| Kernel | $(uname -r) |"
echo "| Boreas | $(sed -n 's/^__version__ = "\(.*\)"$/\1/p' /usr/lib/boreas/boreas/__init__.py 2>/dev/null) |"
echo "| Opt-in /etc/boreas/smu | $([ -f /etc/boreas/smu ] && echo yes || echo no) |"
echo "| Module loaded | $(grep -q '^boreas_smu ' /proc/modules && echo yes || echo no) |"
echo "| /dev/boreas-smu | $([ -e /dev/boreas-smu ] && echo yes || echo no) |"
echo "| Mode | $mode_name |"
echo "| control | $(field control) |"
echo "| state | $(field state) |"
echo "| target / applied | $target / $(field applied) |"
err=$(field error); echo "| error | ${err:-none} |"
echo "| smu_left | $(field smu_left) |"
echo
echo "Kernel log (boreas_smu):"
echo '```'
klog=$(journalctl -k -b --no-pager 2>/dev/null | grep 'boreas_smu:' | tail -n 8)
[ -n "$klog" ] || klog=$(dmesg 2>/dev/null | grep 'boreas_smu:' | tail -n 8)
echo "${klog:-(nothing readable; sudo dmesg | grep boreas_smu)}"
echo '```'
} | tee "$OUT"

if [ "$LOAD" = 0 ]; then
    exit 0
fi
if ! command -v stress-ng >/dev/null 2>&1; then
    echo "stress-ng is not installed: install it (sudo pacman -S stress-ng) or run with --no-load" | tee -a "$OUT"
    exit 1
fi
if [ -z "$K" ]; then
    echo "No k10temp sensor: the load test needs an AMD CPU" | tee -a "$OUT"
    exit 1
fi

echo >&2
echo "Load test: 10 s idle, 90 s stress-ng on every thread, 20 s cool down. Keep the laptop on AC." >&2
samples=$(mktemp)
trap 'rm -f "$samples"; kill "$pid" 2>/dev/null' EXIT
pid=""
t0=$SECONDS
phase() { # phase NAME SECONDS
    local end=$((SECONDS + $2))
    while [ $SECONDS -lt $end ]; do
        echo "$1 $((SECONDS - t0)) $(tctl) $(mhz)" >> "$samples"
        sleep 0.5
    done
}
phase idle 10
stress-ng --cpu 0 --cpu-method matrixprod --timeout 90s --quiet &
pid=$!
phase load 90
wait "$pid" 2>/dev/null
pid=""
phase cool 20

awk -v target="$target" '
$1=="idle" {ii++; it+=$3}
$1=="load" {
    n++; t[n]=$3; m[n]=$4; if ($3>peak) peak=$3
    if (target ~ /^[0-9]+$/ && $3>target+1) over++
}
END {
    printf "\nLoad test (Tctl every 0.5 s, 90 s on every thread):\n\n"
    printf "| | |\n|---|---|\n"
    printf "| Idle Tctl | %.1f °C |\n", (ii ? it/ii : 0)
    printf "| Peak Tctl under load | %d °C |\n", peak
    s=0; c=0; f=0
    for (i=n-59; i<=n; i++) if (i>0) {s+=t[i]; f+=m[i]; c++}
    printf "| Last 30 s average | %.1f °C, %d MHz |\n", (c ? s/c : 0), (c ? f/c : 0)
    if (target ~ /^[0-9]+$/) printf "| Samples above target + 1 °C | %d of %d |\n", over, n
    printf "\nTctl per 10 s of load (max / average):\n\n```\n"
    for (b=0; b<9; b++) {
        mx=0; s=0; c=0
        for (i=b*20+1; i<=b*20+20 && i<=n; i++) {if (t[i]>mx) mx=t[i]; s+=t[i]; c++}
        if (c) printf "%2d-%2ds  %d / %.1f\n", b*10, b*10+10, mx, s/c
    }
    printf "```\n"
}' "$samples" | tee -a "$OUT"
echo >&2
echo "Saved to $(pwd)/$OUT" >&2
