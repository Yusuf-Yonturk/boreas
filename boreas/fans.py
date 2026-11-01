"""Fan curves.

A curve is stored as a 16-byte array: 8 temperatures (°C) + 8 fan percentages.
The kernel (asus_custom_fan_curve hwmon) exposes the same 8 points as
pwmN_auto_pointM_{temp,pwm}, with pwm on a 0-255 scale. pwmN_enable: 1 = custom curve,
2 = firmware, 3 = reset to the firmware default. The kernel turns the custom curve off
when the profile changes, so it has to be written again after every mode change.
"""

from __future__ import annotations

import math

from . import hw

POINTS = 8
FANS = {"cpu": 1, "gpu": 2, "mid": 3}

# default curves (temperatures + percentages)
DEFAULTS = {
    hw.TURBO: {
        "cpu": [30, 63, 68, 72, 76, 80, 84, 98, 17, 26, 34, 41, 52, 67, 81, 90],
        "gpu": [30, 63, 68, 72, 76, 80, 84, 98, 22, 31, 38, 45, 57, 71, 85, 95],
    },
    hw.SILENT: {
        "cpu": [30, 49, 59, 66, 71, 80, 90, 100, 0, 0, 3, 12, 20, 28, 34, 41],
        "gpu": [30, 49, 59, 66, 71, 80, 90, 100, 0, 0, 4, 17, 27, 35, 40, 45],
    },
    hw.BALANCED: {
        "cpu": [58, 61, 64, 68, 72, 77, 81, 98, 8, 17, 22, 26, 34, 41, 48, 69],
        "gpu": [58, 61, 64, 68, 72, 77, 81, 98, 12, 22, 29, 31, 38, 45, 52, 74],
    },
}


def pct_to_pwm(pct: int) -> int:
    return round(max(0, min(100, pct)) * 255 / 100)


def pwm_to_pct(pwm: int) -> int:
    return round(max(0, min(255, pwm)) * 100 / 255)


def safety_floor(temp: int) -> int:
    """Above 70°C the fan runs at least (t - 70) * 1.2 %."""
    return max(0, math.ceil((temp - 70) * 1.2))


def fix_curve(curve: list[int]) -> list[int]:
    """Makes temperatures and speeds monotonically increasing (the kernel rejects a decreasing curve)."""
    if len(curve) != 2 * POINTS:
        raise ValueError("A curve must have 16 values")
    t = [max(0, min(110, int(x))) for x in curve[:POINTS]]
    s = [max(0, min(100, int(x))) for x in curve[POINTS:]]
    for i in range(1, POINTS):
        if t[i] < t[i - 1]:
            t[i] = t[i - 1]
    s = [max(v, safety_floor(t[i])) for i, v in enumerate(s)]
    for i in range(1, POINTS):
        if s[i] < s[i - 1]:
            s[i] = s[i - 1]
    return t + s


def curve_dir() -> str | None:
    return hw.hwmon_by_name("asus_custom_fan_curve")


def available_fans() -> list[str]:
    d = curve_dir()
    if not d:
        return []
    return [name for name, n in FANS.items() if hw.exists(f"{d}/pwm{n}_enable")]


def read_curve(fan: str) -> list[int] | None:
    d = curve_dir()
    n = FANS[fan]
    if not d or not hw.exists(f"{d}/pwm{n}_enable"):
        return None
    temps, speeds = [], []
    for i in range(1, POINTS + 1):
        temps.append(hw.read_int(f"{d}/pwm{n}_auto_point{i}_temp") or 0)
        speeds.append(pwm_to_pct(hw.read_int(f"{d}/pwm{n}_auto_point{i}_pwm") or 0))
    return temps + speeds


def factory_curve(fan: str) -> list[int] | None:
    """Reads the current profile's firmware curve from the kernel (resets it with enable=3)."""
    d = curve_dir()
    n = FANS[fan]
    if not d:
        return None
    hw.write(f"{d}/pwm{n}_enable", 3)
    return read_curve(fan)


def write_curve(fan: str, curve: list[int]) -> None:
    d = curve_dir()
    n = FANS[fan]
    if not d:
        raise ValueError("This device doesn't support custom fan curves")
    curve = fix_curve(curve)
    if not any(curve[POINTS:]):
        # an all-zero curve is never written
        raise ValueError("The fan curve is all zeros")
    for i in range(POINTS):
        hw.write(f"{d}/pwm{n}_auto_point{i + 1}_temp", curve[i])
        hw.write(f"{d}/pwm{n}_auto_point{i + 1}_pwm", pct_to_pwm(curve[POINTS + i]))
    hw.write(f"{d}/pwm{n}_enable", 1)


def disable_curve(fan: str) -> None:
    d = curve_dir()
    if d and hw.exists(f"{d}/pwm{FANS[fan]}_enable"):
        hw.write(f"{d}/pwm{FANS[fan]}_enable", 2)


def is_enabled(fan: str) -> bool:
    d = curve_dir()
    return bool(d) and hw.read_int(f"{d}/pwm{FANS[fan]}_enable") == 1
