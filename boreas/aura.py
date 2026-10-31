"""Klavye RGB (Aura): ASUS Aura HID protokolü, hidraw üzerinden.

N-KEY cihazına 0x5D raporlu HID output paketleri gönderilir:
  [5D B3 bölge mod R G B hız yön rastgele R2 G2 B2]  efekt
  [5D B5 00 00 00]                                  ayarla
  [5D B4]                                           uygula
"""

from __future__ import annotations

import glob
import os

from . import hw

AURA_ID = 0x5D
REPORT_LEN = 64
# Aura klavye denetleyicisi ürün kimlikleri
PIDS = {0x1a30, 0x1854, 0x1869, 0x1866, 0x19b6, 0x1822, 0x1837, 0x184a, 0x183d, 0x8502,
        0x1807, 0x17e0, 0x1abe, 0x1b4c, 0x1b6e, 0x1b2c, 0x8854, 0x1ce7, 0x1ce6, 0x1bf2,
        0x1bf3, 0x1cd7, 0x1cd8, 0x4b42}

MODES = {"static": 0, "breathe": 1, "cycle": 2, "rainbow": 3, "strobe": 10}
SPEEDS = {"slow": 0xE1, "normal": 0xEB, "fast": 0xF5}

MSG_SET = bytes([AURA_ID, 0xB5, 0, 0, 0])
MSG_APPLY = bytes([AURA_ID, 0xB4])


def _has_aura_report(d: str) -> bool | None:
    """Rapor tanımında "Report ID 0x5D" (85 5D) var mı; okunamazsa None."""
    try:
        with open(d + "/device/report_descriptor", "rb") as f:
            return b"\x85\x5d" in f.read()
    except OSError:
        return None


def find_device() -> str | None:
    """Aynı PID'nin birden çok hidraw arayüzü olabilir; 0x5D raporu olanı seç."""
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
            speed: str = "normal") -> bytes:
    r, g, b = color
    r2, g2, b2 = color2 or color
    m = MODES[mode]
    random_flag = 0xFF if (r, g, b) == (0, 0, 0) else (1 if m == MODES["breathe"] else 0)
    return bytes([AURA_ID, 0xB3, 0x00, m, r, g, b, SPEEDS[speed], 0x00, random_flag, r2, g2, b2])


def _send(fd: int, data: bytes) -> None:
    buf = bytearray(REPORT_LEN)
    buf[: len(data)] = data
    os.write(fd, bytes(buf))


def apply(mode: str, color: tuple[int, int, int], color2=None, speed: str = "normal") -> None:
    dev = find_device()
    if dev is None:
        raise ValueError("Aura keyboard not found")
    fd = os.open(hw.p(dev), os.O_WRONLY)
    try:
        for pkt in (message(mode, color, color2, speed), MSG_SET, MSG_APPLY):
            _send(fd, pkt)
    finally:
        os.close(fd)
