"""CPU temperature limit through the Ryzen SMU, reached only through the kernel SMU bridge.

The command is the one ryzenadj calls set_tctl_temp (MP1 0x3F on Dragon Range / Fire Range).
Boreas never touches the SMU mailbox from userspace: the raw PCI 0xB8/0xBC transport that
earlier versions carried was removed (docs/cpu-smu.md says why it could not be made safe).
"""

from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass

from . import hw

CPUINFO = "/proc/cpuinfo"
BRIDGE_DEVICE = "/dev/boreas-smu-probe"
BRIDGE_ENABLE_PARAM = "/sys/module/boreas_smu_probe/parameters/enable_hw"
BRIDGE_OPT_IN_ENV = "BOREAS_SMU_BRIDGE"
BRIDGE_ABI = 1
BOREAS_SMU_CALL = 0xC0204273  # _IOWR('B', 0x73, struct boreas_smu_request)

OK, FAILED, UNKNOWN_CMD, REJECTED_PREREQ, BUSY = 0x01, 0xFF, 0xFE, 0xFD, 0xFC
STATUS_NAMES = {FAILED: "failed", UNKNOWN_CMD: "unknown command",
                REJECTED_PREREQ: "prerequisite rejected", BUSY: "busy"}
RESPONSE_CODES = {OK, *STATUS_NAMES}
GET_SMU_VERSION = 0x02

# lowest / firmware default; 96 counts as "default"
MIN_TEMP, DEFAULT_TEMP = 75, 96


@dataclass(frozen=True)
class Family:
    name: str
    tctl: int


# (cpu family, model) -> tctl command; the same values as ryzenadj cpuid.c / api.c.
# Other families (Phoenix, Strix Point...) use other commands; not added untested.
FAMILIES = {
    (0x19, 0x61): Family("Dragon Range", tctl=0x3F),
    (0x1A, 0x44): Family("Fire Range", tctl=0x3F),
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
    """Experimental G713PV/G713PV.336 kernel SMU bridge (/dev/boreas-smu-probe).

    Requires explicit BOREAS_SMU_BRIDGE=1 opt-in and enable_hw=Y/1 in sysfs.
    There is no raw PCI 0xB8/0xBC path to fall back to. Version/command replies do not
    prove conflict-free firmware synchronization or physical thermal enforcement.
    """

    def __init__(self, family: Family | None = None, path: str | None = None,
                 enable_path: str | None = None):
        self.f = family if family is not None else detect()
        self.path = path
        self.enable_path = enable_path
        self.lock = threading.Lock()
        self.version: int | None = None
        self.error = ""

    def _dev_path(self) -> str:
        return self.path or hw.p(BRIDGE_DEVICE)

    def _enable_param_path(self) -> str:
        return self.enable_path or hw.p(BRIDGE_ENABLE_PARAM)

    def _hw_enabled(self) -> bool:
        try:
            with open(self._enable_param_path(), encoding="utf-8") as f:
                return f.read().strip().upper() in ("Y", "1")
        except OSError:
            return False

    def available(self) -> bool:
        if self.error or os.environ.get(BRIDGE_OPT_IN_ENV) != "1":
            return False
        f = self.f if self.f is not None else detect()
        if f is None:
            return False
        return os.path.exists(self._dev_path()) and self._hw_enabled()

    def check_access(self) -> None:
        if self.error:
            raise SmuAccessError(f"SMU bridge disabled: {self.error}")

    def _fault(self, reason: str) -> SmuAccessError:
        if not self.error:
            self.error = reason
        return SmuAccessError(f"SMU bridge disabled: {self.error}")

    def _ioctl(self, fd: int, buf: bytearray) -> None:
        import fcntl
        fcntl.ioctl(fd, BOREAS_SMU_CALL, buf, True)

    def send(self, cmd: int, arg: int = 0) -> tuple[int, int]:
        with self.lock:
            self.check_access()
            if os.environ.get(BRIDGE_OPT_IN_ENV) != "1":
                raise self._fault("SMU bridge opt-in (BOREAS_SMU_BRIDGE=1) is not enabled")
            if not self._hw_enabled():
                raise self._fault("SMU bridge enable_hw parameter is not Y/1")
            try:
                fd = os.open(self._dev_path(), os.O_RDWR)
            except OSError as exc:
                raise self._fault(f"Could not open SMU bridge: {exc.strerror or exc}") from exc
            try:
                buf = bytearray(struct.pack("<8I", BRIDGE_ABI, cmd, arg, 0, 0, 0, 0, 0))
                try:
                    self._ioctl(fd, buf)
                except OSError as exc:
                    raise self._fault(f"SMU bridge ioctl failed: {exc.strerror or exc}") from exc
                _, _, _, status, result, _, _, _ = struct.unpack("<8I", buf)
                if status not in RESPONSE_CODES:
                    raise self._fault(f"Invalid SMU bridge response ({hex(status)})")
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
            status, version = self.send(GET_SMU_VERSION, 0)
            if status != OK or not version:
                raise self._fault(
                    f"No reply to the SMU version query ({STATUS_NAMES.get(status, hex(status))})"
                )
            self.version = version
        status, _ = self.send(self.f.tctl, temp)
        if status != OK:
            raise self._fault(
                f"The SMU rejected the command ({STATUS_NAMES.get(status, hex(status))})"
            )
