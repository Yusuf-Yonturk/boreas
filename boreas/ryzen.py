"""CPU sıcaklık limiti, Ryzen SMU üzerinden (ryzenadj set_tctl_temp ile aynı komut).

SMU'nun posta kutusu SMN adres uzayındadır. Ona kök kompleksin (00:00.0) PCI yapılandırma
alanındaki 0xB8/0xBC dizin/veri çiftiyle ulaşılır (ryzenadj nb_smu_ops.h). Çekirdeğin amd_smn'i
(k10temp) 0x60/0x64 çiftini kullandığı için çakışmaz. Root ve kapalı kernel lockdown gerekir.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from dataclasses import dataclass

from . import hw

CPUINFO = "/proc/cpuinfo"
ROOT_COMPLEX = "/sys/bus/pci/devices/0000:00:00.0/config"
SMN_ADDR, SMN_DATA = 0xB8, 0xBC

OK, FAILED, UNKNOWN_CMD, REJECTED_PREREQ, BUSY = 0x01, 0xFF, 0xFE, 0xFD, 0xFC
STATUS_NAMES = {FAILED: "failed", UNKNOWN_CMD: "unknown command",
                REJECTED_PREREQ: "prerequisite rejected", BUSY: "busy"}
TIMEOUT = 0.2  # posta kutusu yanıt süresi (sn)
GET_SMU_VERSION = 0x02

# en düşük / firmware varsayılanı; 96 "varsayılan" sayılır
MIN_TEMP, DEFAULT_TEMP = 75, 96


@dataclass(frozen=True)
class Family:
    name: str
    msg: int
    rsp: int
    arg: int
    tctl: int
    get_limit: int | None = None


# (cpu family, model) → MP1 posta kutusu ve tctl komutu; ryzenadj cpuid.c / nb_smu_ops.c / api.c
# ile aynı değerler. Diğer aileler (Phoenix, Strix Point…) başka adres ve komut ister; denenmeden eklenmedi.
_RAPHAEL_MP1 = (0x03B10530, 0x03B1057C, 0x03B109C4)
FAMILIES = {
    (0x19, 0x61): Family("Dragon Range", *_RAPHAEL_MP1, tctl=0x3F, get_limit=0x23),
    (0x1A, 0x44): Family("Fire Range", *_RAPHAEL_MP1, tctl=0x3F),
}


class SmuError(Exception):
    pass


def other_writers() -> list[int]:
    """Aynı 0xB8/0xBC çiftini kullanan ryzenadj süreçleri (ör. eski bir ryzenadj.service).

    Süreçler arası kilit yok: iki yazıcının dizin/veri yazımları araya girerse SMU'ya
    yanlış komut gidebilir, ayrıca birbirlerinin limitini ezerler.
    """
    pids = []
    try:
        entries = os.listdir(hw.p("/proc"))
    except OSError:
        return pids
    me = str(os.getpid())
    for e in entries:
        if not e.isdigit() or e == me:
            continue
        try:
            with open(hw.p(f"/proc/{e}/cmdline"), "rb") as f:
                cmdline = f.read()
        except OSError:
            continue
        if b"ryzenadj" in cmdline:
            pids.append(int(e))
    return sorted(pids)


def detect() -> Family | None:
    info = {}
    for line in (hw.read(CPUINFO) or "").splitlines():
        if not line.strip():
            break  # ilk çekirdek yeter
        k, _, v = line.partition(":")
        info[k.strip()] = v.strip()
    if info.get("vendor_id") != "AuthenticAMD":
        return None
    try:
        return FAMILIES.get((int(info["cpu family"]), int(info["model"])))
    except (KeyError, ValueError):
        return None


class Smu:
    def __init__(self, family: Family, path: str | None = None):
        self.f = family
        self.path = path or hw.p(ROOT_COMPLEX)
        self.lock = threading.Lock()

    def _read(self, fd: int, addr: int) -> int:
        os.pwrite(fd, struct.pack("<I", addr), SMN_ADDR)
        return struct.unpack("<I", os.pread(fd, 4, SMN_DATA))[0]

    def _write(self, fd: int, addr: int, value: int) -> None:
        os.pwrite(fd, struct.pack("<I", addr), SMN_ADDR)
        os.pwrite(fd, struct.pack("<I", value), SMN_DATA)

    def _wait(self, fd: int) -> int:
        deadline = time.monotonic() + TIMEOUT
        while True:
            v = self._read(fd, self.f.rsp)
            if v or time.monotonic() >= deadline:
                return v
            time.sleep(0.001)

    def send(self, cmd: int, arg: int = 0) -> tuple[int, int]:
        """Posta kutusu çağrısı: boşta bekle, yanıtı sıfırla, 6 argüman ve komutu yaz, yanıtı bekle.

        (durum, arg0 yanıtı) döner.
        """
        with self.lock:
            fd = os.open(self.path, os.O_RDWR)
            try:
                if not self._wait(fd):
                    return BUSY, 0
                self._write(fd, self.f.rsp, 0)
                for i in range(6):
                    self._write(fd, self.f.arg + 4 * i, arg if i == 0 else 0)
                self._write(fd, self.f.msg, cmd)
                status = self._wait(fd) or FAILED
                return status, self._read(fd, self.f.arg) if status == OK else 0
            finally:
                os.close(fd)


class CpuTemp:
    """Servisin kullandığı katman. SMU'ya ilk dokunuşta posta kutusunu sürüm sorgusuyla doğrular."""

    def __init__(self):
        self.family = detect()
        self.smu = Smu(self.family) if self.family and hw.exists(ROOT_COMPLEX) else None
        self.version: int | None = None
        self.error = ""
        self.applied = DEFAULT_TEMP  # açılışta firmware varsayılanı
        self.reported_limit: int | None = None
        self.readback_verified = False
        self.readback_error = ""
        self._readback_disabled = False

    def supported(self) -> bool:
        return self.smu is not None and not self.error

    def _probe(self) -> None:
        try:
            status, version = self.smu.send(GET_SMU_VERSION)
        except OSError as e:
            self.error = f"Could not access the SMU ({e.strerror or e}); kernel lockdown may be on"
            raise SmuError(self.error) from e
        if status != OK or not version:
            self.error = f"No reply to the SMU version query ({STATUS_NAMES.get(status, hex(status))})"
            raise SmuError(self.error)
        self.version = version

    def set(self, temp: int) -> None:
        if not self.supported():
            raise SmuError(self.error or "This CPU is not supported")
        if not MIN_TEMP <= temp <= DEFAULT_TEMP:
            raise ValueError(f"CPU temperature limit must be {MIN_TEMP}..{DEFAULT_TEMP}")
        if self.version is None:
            self._probe()
        before = self.read_limit()
        try:
            status, _ = self.smu.send(self.family.tctl, temp)
        except OSError as e:
            raise SmuError(f"Could not write to the SMU: {e.strerror or e}") from e
        if status != OK:
            raise SmuError(f"The SMU rejected the command ({STATUS_NAMES.get(status, hex(status))})")
        self.applied = temp
        after = self.read_limit()
        # 0x23 bazı firmware'lerde BIOS'un sabit değerini döndürebilir. Yalnız
        # farklı eski değerin yazdığımız hedefe geçtiği gözlenirse güven.
        if before is not None and before != temp and after == temp:
            self.readback_verified = True
        elif after != temp:
            self.readback_verified = False

    def read_limit(self) -> int | None:
        """Raphael MP1 0x23, arg0[7:0]: ZenStates-Core Zen4Settings /
        GetSystemConfiguredPowerLimit. Dinamik limit olduğu ayrıca doğrulanır.
        Okuma hatası sıcaklık yazma desteğini kapatmaz.
        """
        self.reported_limit = None
        if not self.supported() or self.family.get_limit is None or self._readback_disabled:
            return None
        try:
            status, value = self.smu.send(self.family.get_limit)
        except OSError as e:
            self.readback_error = f"Could not read the limit: {e.strerror or e}"
            self.readback_verified = False
            return None
        if status != OK:
            self.readback_error = f"Limit query: {STATUS_NAMES.get(status, hex(status))}"
            self._readback_disabled = status == UNKNOWN_CMD
        else:
            temp = value & 0xFF
            if 50 <= temp <= 115:
                self.reported_limit = temp
                self.readback_error = ""
                return temp
            self.readback_error = f"Invalid limit reply: {temp}"
            self._readback_disabled = True
        self.readback_verified = False
        return None

    def version_str(self) -> str | None:
        v = self.version
        return None if v is None else f"{v >> 16 & 0xFF}.{v >> 8 & 0xFF}.{v & 0xFF}"
