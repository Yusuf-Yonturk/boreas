"""Keyboard RGB (Aura): ASUS Aura HID protocol over hidraw.

The packet layout, effect numbers, speeds and zone numbers are the ones asusctl's rog-aura uses
(rog-aura/src/builtin_modes.rs). The question that tells the kinds of keyboard apart (probe) was
learned from G-Helper. See THIRD_PARTY_NOTICES.md.

HID output packets with report ID 0x5D are sent to the N-KEY device:
  [5D B3 zone mode R G B speed direction random R2 G2 B2]  effect
  [5D B5 00 00 00]                                         set
  [5D B4]                                                  apply (the keyboard keeps it)

Besides the effects the keyboard runs by itself there are two kinds Boreas drives:
  zones    a static color for each of the four keyboard zones (zone byte 1-4)
  heatmap, gpu, battery    one static color that follows the machine; the service rewrites it
                           when it changes, without "apply", so nothing is stored in the keyboard
"""

from __future__ import annotations

import fcntl
import glob
import os
import stat
import time

from . import hw

AURA_ID = 0x5D
REPORT_LEN = 64
# product IDs of the Aura keyboard controller
PIDS = {0x1a30, 0x1854, 0x1869, 0x1866, 0x19b6, 0x1822, 0x1837, 0x184a, 0x183d, 0x8502,
        0x1807, 0x17e0, 0x1abe, 0x1b4c, 0x1b6e, 0x1b2c, 0x8854, 0x1ce7, 0x1ce6, 0x1bf2,
        0x1bf3, 0x1cd7, 0x1cd8, 0x4b42}

MODES = {"static": 0, "breathe": 1, "cycle": 2, "rainbow": 3, "star": 4, "rain": 5, "highlight": 6,
         "laser": 7, "ripple": 8, "strobe": 10, "comet": 11, "flash": 12}
# what the keyboard says it is when asked (probe): the effects it can run depend on it
KINDS = {0x02: "zones", 0x03: "perkey", 0x04: "single", 0x00: "single"}
PER_KEY_MODES = ("star", "rain", "highlight", "laser", "ripple", "comet", "flash")
SPEEDS = {"slow": 0xE1, "normal": 0xEB, "fast": 0xF5}
ZONES = "zones"
ZONE_COUNT = 4
FOLLOW_MODES = ("heatmap", "gpu", "battery")
ALL_MODES = (*MODES, ZONES, *FOLLOW_MODES)
# The colors of the follow modes, from one palette: blue, green, amber, red
BLUE, GREEN, AMBER, RED = (0, 140, 255), (0, 200, 80), (255, 170, 0), (255, 0, 0)
# CPU temperature: an idle machine is blue, a working one green, amber from the mid 70s, red at 90 °C
HEAT_STOPS = ((45, BLUE), (60, GREEN), (75, AMBER), (90, RED))
# battery charge: red when it is time to plug in, green when full
BATTERY_STOPS = ((15, RED), (50, AMBER), (100, GREEN))
GPU_COLORS = {"eco": GREEN, "standard": BLUE, "optimized": AMBER, "ultimate": RED}
GPU_COLOR_OTHER = AMBER  # a mode this table does not know

MSG_SET = bytes([AURA_ID, 0xB5, 0, 0, 0])
MSG_APPLY = bytes([AURA_ID, 0xB4])


def _has_aura_report(d: str) -> bool | None:
    """Whether the report descriptor contains "Report ID 0x5D" (85 5D); None if it cannot be read."""
    try:
        with open(d + "/device/report_descriptor", "rb") as f:
            return b"\x85\x5d" in f.read()
    except OSError:
        return None


def find_device() -> str | None:
    """The same PID can have several hidraw interfaces; pick the one with report 0x5D."""
    fallback = None
    for d in sorted(glob.glob(hw.p("/sys/class/hidraw/hidraw*"))):
        uevent = hw.read(d[len(hw.ROOT):] + "/device/uevent") or ""
        for line in uevent.splitlines():
            if line.startswith("HID_ID="):
                _, vid, pid = line[7:].split(":")
                if int(vid, 16) == 0x0B05 and int(pid, 16) in PIDS:
                    has = _has_aura_report(d)
                    if has:
                        return "/dev/" + os.path.basename(d)
                    if has is None and fallback is None:
                        fallback = "/dev/" + os.path.basename(d)
    return fallback


def message(mode: str, color: tuple[int, int, int], color2: tuple[int, int, int] | None = None,
            speed: str = "normal", zone: int = 0) -> bytes:
    """zone 0 = the whole keyboard, 1-4 = one zone. In a zone black means off, not a random color."""
    r, g, b = color
    r2, g2, b2 = color2 or color
    m = MODES[mode]
    random_flag = 0xFF if (r, g, b) == (0, 0, 0) and not zone else (1 if m == MODES["breathe"] else 0)
    return bytes([AURA_ID, 0xB3, zone, m, r, g, b, SPEEDS[speed], 0x00, random_flag, r2, g2, b2])


def probe() -> str | None:
    """Ask the keyboard what kind it is: "zones" (four zones), "perkey", "single"; None if it does not say.

    The question and where the kind is in the answer were learned from G-Helper (app/USB/AsusHid.cs,
    app/USB/Aura.cs): two wake-up packets, the query, then the answer as a feature report.
    Nothing is sent to anything that is not a character device."""
    dev = find_device()
    if dev is None:
        return None
    try:
        fd = os.open(hw.p(dev), os.O_RDWR)
    except OSError:
        return None
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            return None
        query = bytes([AURA_ID, 0x05, 0x20, 0x31, 0x00, 0x20])
        for pkt in (bytes([AURA_ID, 0xB9]), bytes([AURA_ID]) + b"ASUS Tech.Inc.", query):
            _send(fd, pkt)
            time.sleep(0.02)
        answer = bytearray(REPORT_LEN)
        answer[0] = AURA_ID
        fcntl.ioctl(fd, 0xC0004807 | (REPORT_LEN << 16), answer)  # HIDIOCGFEATURE
        return kind_of(bytes(answer))
    except OSError:
        return None
    finally:
        os.close(fd)


def kind_of(answer: bytes) -> str | None:
    if len(answer) < 10 or answer[:4] != bytes([AURA_ID, 0x05, 0x20, 0x31]):
        return None
    return KINDS.get(answer[9])


def modes_for(kind: str | None) -> list[str]:
    """The effects offered on a keyboard of this kind, in menu order: what asusctl's aura_support.ron
    lists for most models of that kind. An unknown keyboard gets the common five."""
    out = ["static"]
    if kind == "zones":
        out.append(ZONES)
    out += ["breathe", "cycle"]
    if kind != "single":
        out.append("rainbow")
    if kind == "perkey":
        out += ["star", "rain", "highlight", "laser", "ripple"]
    out.append("strobe")
    if kind == "perkey":
        out += ["comet", "flash"]
    return out + list(FOLLOW_MODES)


def scale(stops, value: float) -> tuple[int, int, int]:
    """The color at value on a scale of (value, color) stops, blended between neighbours."""
    if value <= stops[0][0]:
        return stops[0][1]
    for (lo, a), (hi, b) in zip(stops, stops[1:]):
        if value < hi:
            t = (value - lo) / (hi - lo)
            return tuple(round(x + (y - x) * t) for x, y in zip(a, b))
    return stops[-1][1]


def follow_color(mode: str, cpu_temp: int | None, battery: int | None, gpu_mode: str | None):
    """The color a follow mode shows now; None while its reading is missing."""
    if mode == "heatmap":
        return None if cpu_temp is None else scale(HEAT_STOPS, cpu_temp)
    if mode == "battery":
        return None if battery is None else scale(BATTERY_STOPS, battery)
    if mode == "gpu":
        return None if gpu_mode is None else GPU_COLORS.get(gpu_mode, GPU_COLOR_OTHER)
    raise ValueError(f"not a follow mode: {mode}")


def _send(fd: int, data: bytes) -> None:
    buf = bytearray(REPORT_LEN)
    buf[: len(data)] = data
    os.write(fd, bytes(buf))


def _write(packets) -> None:
    dev = find_device()
    if dev is None:
        raise ValueError("Aura keyboard not found")
    fd = os.open(hw.p(dev), os.O_WRONLY)
    try:
        for pkt in packets:
            _send(fd, pkt)
    finally:
        os.close(fd)


def apply(mode: str, color: tuple[int, int, int], color2=None, speed: str = "normal") -> None:
    _write((message(mode, color, color2, speed), MSG_SET, MSG_APPLY))


def apply_zones(colors) -> None:
    """A static color for each keyboard zone, left to right."""
    if len(colors) != ZONE_COUNT:
        raise ValueError(f"{ZONE_COUNT} zone colors expected, got {len(colors)}")
    _write((*(message("static", tuple(c), zone=i + 1) for i, c in enumerate(colors)), MSG_SET, MSG_APPLY))


def show(color: tuple[int, int, int]) -> None:
    """A static color that is not stored in the keyboard: the follow modes rewrite it as it changes."""
    _write((message("static", color), MSG_SET))
