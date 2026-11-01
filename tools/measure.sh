#!/bin/bash
# measure.sh LABEL SECONDS: average iGPU busy, socket power and Tctl
K=$(dirname $(grep -l k10temp /sys/class/hwmon/*/name)); C=/sys/class/drm/card1/device; H=$(ls -d $C/hwmon/hwmon*)
n=0; b=0; p=0; t=0; end=$((SECONDS+$2))
while [ $SECONDS -lt $end ]; do
  b=$((b+$(cat $C/gpu_busy_percent))); p=$((p+$(cat $H/power1_input)/1000000)); t=$((t+$(cat $K/temp1_input)/1000)); n=$((n+1)); sleep 0.25
done
printf "%-22s iGPU=%3d%%  power=%2dW  Tctl=%d\n" "$1" $((b/n)) $((p/n)) $((t/n))
