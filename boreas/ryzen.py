"""CPU temperature ceiling through the Ryzen SMU, reached only through the boreas_smu kernel module.

The command is the one RyzenAdj calls set_tctl_temp. The module (smu/boreas_smu.c) picks the
command and the MP1 mailbox for the CPU itself and holds the BIOS's own ACPI lock (\\_SB.AM00)
around every mailbox use; userspace only asks for "version" or "ceiling N °C". Boreas never
touches the SMU mailbox from userspace: the raw PCI 0xB8/0xBC transport that earlier versions
carried was removed (docs/cpu-smu.md says why).
"""

from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass

from . import hw

CPUINFO = "/proc/cpuinfo"
DEVICE = "/dev/boreas-smu"
# the user's opt-in, written by `BOREAS_SMU=1 ./install.sh`; a file because pkexec (in-app update)
# and systemd do not carry the installer's environment
OPT_IN_FILE = "/etc/boreas/smu"
ABI = 2
IOCTL = 0xC0204274  # _IOWR('B', 0x74, struct boreas_smu_req), 32 bytes
OP_VERSION, OP_SET_TCTL = 1, 2

OK, FAILED, UNKNOWN_CMD, REJECTED_PREREQ, BUSY = 0x01, 0xFF, 0xFE, 0xFD, 0xFC
STATUS_NAMES = {FAILED: "failed", UNKNOWN_CMD: "unknown command",
                REJECTED_PREREQ: "prerequisite rejected", BUSY: "busy"}
RESPONSE_CODES = {OK, *STATUS_NAMES}

# lowest / firmware default; 96 counts as "default"
MIN_TEMP, DEFAULT_TEMP = 75, 96


@dataclass(frozen=True)
class Family:
    name: str
    # Families that silently drop the ceiling under load (G-Helper re-applies every 30 s);
    # a G-Helper code comment is the only source, to be replaced by a measurement.
    reapply: bool = False
    # tested on real hardware with this module; the others are experimental
    verified: bool = False


_RAVEN = Family("Raven Ridge / Picasso / Dali")
_RENOIR = Family("Renoir / Lucienne / Cezanne", reapply=True)
_MOBILE = Family("Van Gogh / Mendocino / Rembrandt / Phoenix / Hawk Point", reapply=True)
_DRAGON = Family("Dragon Range / Raphael", verified=False)
_FIRE = Family("Fire Range / Granite Ridge")
_STRIX = Family("Strix Point / Krackan Point / Strix Halo")

# (cpu family, model) -> family, exactly as /proc/cpuinfo reports them. Must match the table in
# smu/boreas_smu.c; sources: G-Helper RyzenSmu.cs, PawnIO RyzenSMU.p, RyzenAdj cpuid.c / api.c,
# ryzen_smu smu.c (THIRD_PARTY_NOTICES.md). Matisse / Vermeer are left out: their command has a
# single source.
FAMILIES = {
    **{(0x17, m): _RAVEN for m in (0x11, 0x18, 0x20)},
    **{(0x17, m): _RENOIR for m in (0x60, 0x68)},
    (0x19, 0x50): _RENOIR,
    **{(0x17, m): _MOBILE for m in (0x90, 0x91, 0xA0)},
    **{(0x19, m): _MOBILE for m in (0x40, 0x44, 0x74, 0x75, 0x78, 0x7C)},
    (0x19, 0x61): _DRAGON,
    (0x1A, 0x44): _FIRE,
    **{(0x1A, m): _STRIX for m in (0x20, 0x24, 0x60, 0x68, 0x70)},
}


class SmuError(Exception):
    pass


class SmuAccessError(SmuError):
    """A bridge fault disables further SMU access in this process."""


def detect() -> Family | None:
    info = {}
    for line in (hw.read(CPUINFO) or "").splitlines():
        if not line.strip():
            break  # the first core is enough
        k, _, v = line.partition(":")
        info[k.strip()] = v.strip()
    if info.get("vendor_id") != "AuthenticAMD":
        return None
    try:
        return FAMILIES.get((int(info["cpu family"]), int(info["model"])))
    except (KeyError, ValueError):
        return None


class BridgeSmu:
    """The boreas_smu kernel module (/dev/boreas-smu), used only when the user opted in.

    A version or command reply does not prove that the ceiling holds physically; that is
    measured. Any fault latches for the life of the service and the caller falls back to
    power-feedback.
    """

    def __init__(self, family: Family | None = None, path: str | None = None,
                 opt_in_path: str | None = None):
        self.f = family if family is not None else detect()
        self.path = path
        self.opt_in_path = opt_in_path
        self.lock = threading.Lock()
        self.version: int | None = None
        self.error = ""

    def _dev_path(self) -> str:
        return self.path or hw.p(DEVICE)

    def _opt_in(self) -> bool:
        return os.path.exists(self.opt_in_path or hw.p(OPT_IN_FILE))

    def requested(self) -> bool:
        return self._opt_in()

    def available(self) -> bool:
        if self.error or not self._opt_in():
            return False
        if self.f is None:
            self.f = detect()
        return self.f is not None and os.path.exists(self._dev_path())

    def check_access(self) -> None:
        if self.error:
            raise SmuAccessError(f"SMU bridge disabled: {self.error}")

    def _fault(self, reason: str) -> SmuAccessError:
        if not self.error:
            self.error = reason
        return SmuAccessError(f"SMU bridge disabled: {self.error}")

    def _ioctl(self, fd: int, buf: bytearray) -> None:
        import fcntl
        fcntl.ioctl(fd, IOCTL, buf, True)

    def send(self, op: int, arg: int = 0) -> tuple[int, int]:
        with self.lock:
            self.check_access()
            if not self._opt_in():
                raise self._fault(f"SMU opt-in ({OPT_IN_FILE}) is gone")
            try:
                fd = os.open(self._dev_path(), os.O_RDWR)
            except OSError as exc:
                raise self._fault(f"Could not open the SMU module: {exc.strerror or exc}") from exc
            try:
                buf = bytearray(struct.pack("<8I", ABI, op, arg, 0, 0, 0, 0, 0))
                try:
                    self._ioctl(fd, buf)
                except OSError as exc:
                    raise self._fault(f"SMU module call failed: {exc.strerror or exc}") from exc
                _, _, _, status, result, _, _, _ = struct.unpack("<8I", buf)
                if status not in RESPONSE_CODES:
                    raise self._fault(f"Invalid SMU response ({hex(status)})")
                return status, result
            finally:
                os.close(fd)

    def set(self, temp: int) -> None:
        self.check_access()
        if self.f is None:
            self.f = detect()
        if self.f is None:
            raise SmuError("This CPU is not supported")
        if not MIN_TEMP <= temp <= DEFAULT_TEMP:
            raise ValueError(f"CPU temperature limit must be {MIN_TEMP}..{DEFAULT_TEMP}")
        if self.version is None:
            status, version = self.send(OP_VERSION, 0)
            if status != OK or not version:
                raise self._fault(
                    f"No reply to the SMU version query ({STATUS_NAMES.get(status, hex(status))})"
                )
            self.version = version
        status, _ = self.send(OP_SET_TCTL, temp)
        if status != OK:
            raise self._fault(
                f"The SMU rejected the command ({STATUS_NAMES.get(status, hex(status))})"
            )
