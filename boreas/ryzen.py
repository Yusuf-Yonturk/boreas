"""CPU temperature limit through the Ryzen SMU (the same command as ryzenadj set_tctl_temp).

The SMU mailbox lives in the SMN address space. It is reached through the 0xB8/0xBC
index/data pair in the PCI config space of the root complex (00:00.0) (ryzenadj nb_smu_ops.h).
The kernel's amd_smn (k10temp) uses the separate 0x60/0x64 pair. This does not
serialize our accesses with other users of the 0xB8/0xBC pair or firmware.
Needs root and kernel lockdown turned off.
"""

from __future__ import annotations

import os
import json
import struct
import sys
import threading
import time
from dataclasses import dataclass

from . import hw

CPUINFO = "/proc/cpuinfo"
ROOT_COMPLEX = "/sys/bus/pci/devices/0000:00:00.0/config"
SMN_ADDR, SMN_DATA = 0xB8, 0xBC
BRIDGE_DEVICE = "/dev/boreas-smu-probe"
BRIDGE_ENABLE_PARAM = "/sys/module/boreas_smu_probe/parameters/enable_hw"
BRIDGE_OPT_IN_ENV = "BOREAS_SMU_BRIDGE"
BRIDGE_ABI = 1
BOREAS_SMU_CALL = 0xC0204273  # _IOWR('B', 0x73, struct boreas_smu_request)

OK, FAILED, UNKNOWN_CMD, REJECTED_PREREQ, BUSY = 0x01, 0xFF, 0xFE, 0xFD, 0xFC
STATUS_NAMES = {FAILED: "failed", UNKNOWN_CMD: "unknown command",
                REJECTED_PREREQ: "prerequisite rejected", BUSY: "busy"}
RESPONSE_CODES = {OK, *STATUS_NAMES}
TIMEOUT = 0.2  # mailbox response timeout (s)
GET_SMU_VERSION = 0x02

# lowest / firmware default; 96 counts as "default"
MIN_TEMP, DEFAULT_TEMP = 75, 96


@dataclass(frozen=True)
class Family:
    name: str
    msg: int
    rsp: int
    arg: int
    tctl: int
    get_limit: int | None = None


# (cpu family, model) → MP1 mailbox and tctl command; the same values as ryzenadj cpuid.c /
# nb_smu_ops.c / api.c. Other families (Phoenix, Strix Point…) need other addresses and commands; not added untested.
_RAPHAEL_MP1 = (0x03B10530, 0x03B1057C, 0x03B109C4)
FAMILIES = {
    (0x19, 0x61): Family("Dragon Range", *_RAPHAEL_MP1, tctl=0x3F, get_limit=0x23),
    (0x1A, 0x44): Family("Fire Range", *_RAPHAEL_MP1, tctl=0x3F),
}


class SmuError(Exception):
    pass


class SmuAccessError(SmuError):
    """An inconsistent transport disables further SMU access in this process."""


def other_writers() -> list[int]:
    """ryzenadj processes using the same 0xB8/0xBC pair (e.g. an old ryzenadj.service).

    There is no lock between processes: if the index/data writes of two writers interleave,
    the SMU can receive a wrong command, and they overwrite each other's limit as well.
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
            break  # the first core is enough
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
        self.diagnostics = os.environ.get("BOREAS_SMU_DIAGNOSTICS") == "1"
        self._diagnostic_count = 0
        self.error = ""

    def check_access(self) -> None:
        if self.error:
            raise SmuAccessError(f"SMU access disabled: {self.error}")

    def _fault(self, reason: str) -> SmuAccessError:
        if not self.error:
            self.error = reason
        return SmuAccessError(f"SMU access disabled: {self.error}")

    def _read_config(self, fd: int, offset: int) -> int:
        try:
            data = os.pread(fd, 4, offset)
        except OSError as exc:
            raise self._fault(f"PCI config read failed at {hex(offset)}: {exc}") from exc
        if len(data) != 4:
            raise self._fault(f"Short PCI config read at {hex(offset)}")
        return struct.unpack("<I", data)[0]

    def _write_config(self, fd: int, offset: int, value: int) -> None:
        try:
            size = os.pwrite(fd, struct.pack("<I", value), offset)
        except OSError as exc:
            raise self._fault(f"PCI config write failed at {hex(offset)}: {exc}") from exc
        if size != 4:
            raise self._fault(f"Short PCI config write at {hex(offset)}")

    def _check_selector(self, fd: int, addr: int) -> None:
        selected = self._read_config(fd, SMN_ADDR)
        if selected != addr:
            raise self._fault(f"SMN selector changed: expected {hex(addr)}, got {hex(selected)}")

    def _select(self, fd: int, addr: int) -> None:
        self.check_access()
        self._write_config(fd, SMN_ADDR, addr)
        self._check_selector(fd, addr)

    def _read(self, fd: int, addr: int) -> int:
        self._select(fd, addr)
        value = self._read_config(fd, SMN_DATA)
        self._check_selector(fd, addr)
        return value

    def _write(self, fd: int, addr: int, value: int) -> None:
        # These checks detect interference; they cannot make the two PCI
        # accesses atomic against firmware or uncoordinated kernel writers.
        self._select(fd, addr)
        self._write_config(fd, SMN_DATA, value)
        self._check_selector(fd, addr)

    def _wait(self, fd: int) -> int:
        deadline = time.monotonic() + TIMEOUT
        while True:
            v = self._read(fd, self.f.rsp)
            if v or time.monotonic() >= deadline:
                return v
            time.sleep(0.001)

    def _trace_invalid_reply(self, fd: int, value: int, cmd: int, arg: int, stage: str) -> None:
        if not self.diagnostics:
            return
        record = {"event": "smu_invalid_reply", "pid": os.getpid(),
                  "monotonic_ns": time.monotonic_ns(), "stage": stage,
                  "command": hex(cmd), "argument": arg,
                  "response_address": hex(self.f.rsp), "response": hex(value)}
        try:
            # Preserve the pointer left after the failed read, then reselect
            # only the known response register. No mailbox command is retried.
            record["selected_index_after_read"] = hex(struct.unpack("<I", os.pread(fd, 4, SMN_ADDR))[0])
            record["response_reread"] = hex(self._read(fd, self.f.rsp))
        except (OSError, SmuError) as exc:
            record["diagnostic_error"] = str(exc)
        print("SMU_DIAGNOSTIC " + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)

    def _trace_transaction(self, cmd: int, arg: int, status: int, result: int, sent: bool) -> None:
        # Observe a bounded command sequence without adding hardware accesses.
        if not self.diagnostics or self._diagnostic_count >= 64:
            return
        self._diagnostic_count += 1
        record = {"event": "smu_transaction", "pid": os.getpid(),
                  "monotonic_ns": time.monotonic_ns(), "command": hex(cmd),
                  "argument": arg, "status": hex(status), "result": hex(result),
                  "sent": sent}
        print("SMU_DIAGNOSTIC " + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)

    def send(self, cmd: int, arg: int = 0) -> tuple[int, int]:
        """Mailbox call: wait until idle, clear the response, write 6 arguments and the command, wait.

        Returns (status, arg0 response).
        """
        with self.lock:
            self.check_access()
            fd = os.open(self.path, os.O_RDWR)
            try:
                idle = self._wait(fd)
                if not idle:
                    self._trace_transaction(cmd, arg, BUSY, 0, False)
                    return BUSY, 0
                if idle not in RESPONSE_CODES:
                    self._trace_invalid_reply(fd, idle, cmd, arg, "before_command")
                    raise self._fault(f"Invalid SMU mailbox response ({hex(idle)}) before command")
                self._write(fd, self.f.rsp, 0)
                for i in range(6):
                    self._write(fd, self.f.arg + 4 * i, arg if i == 0 else 0)
                self._write(fd, self.f.msg, cmd)
                status = self._wait(fd)
                if not status:
                    self._trace_transaction(cmd, arg, 0, 0, True)
                    raise self._fault("Timed out waiting for the SMU command response")
                if status not in RESPONSE_CODES:
                    self._trace_invalid_reply(fd, status, cmd, arg, "after_command")
                    raise self._fault(f"Invalid SMU mailbox response ({hex(status)}) after command")
                result = self._read(fd, self.f.arg) if status == OK else 0
                self._trace_transaction(cmd, arg, status, result, True)
                return status, result
            finally:
                os.close(fd)


class CpuTemp:
    """The layer the service uses. Verifies the mailbox with a version query on first SMU access."""

    def __init__(self):
        self.family = detect()
        self.smu = Smu(self.family) if self.family and hw.exists(ROOT_COMPLEX) else None
        self.version: int | None = None
        self.error = ""
        self.applied = DEFAULT_TEMP  # firmware default at boot
        self.reported_limit: int | None = None
        self.readback_verified = False
        self.readback_error = ""
        self._readback_disabled = os.environ.get("BOREAS_SMU_READBACK") == "0"

    def supported(self) -> bool:
        return self.smu is not None and not self.error and not self.smu.error

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
        if self.smu is not None:
            self.smu.check_access()
        if not self.supported():
            raise SmuError(self.error or "This CPU is not supported")
        if not MIN_TEMP <= temp <= DEFAULT_TEMP:
            raise ValueError(f"CPU temperature limit must be {MIN_TEMP}..{DEFAULT_TEMP}")
        if self.version is None:
            self._probe()
        before = self.read_limit()
        self.smu.check_access()
        try:
            status, _ = self.smu.send(self.family.tctl, temp)
        except OSError as e:
            raise SmuError(f"Could not write to the SMU: {e.strerror or e}") from e
        if status != OK:
            raise SmuError(f"The SMU rejected the command ({STATUS_NAMES.get(status, hex(status))})")
        self.applied = temp
        after = self.read_limit()
        self.smu.check_access()
        # On some firmware 0x23 may return the fixed BIOS value. Trust it only once a
        # different old value has been seen to change to the target we wrote.
        if before is not None and before != temp and after == temp:
            self.readback_verified = True
        elif after != temp:
            self.readback_verified = False

    def read_limit(self) -> int | None:
        """Raphael MP1 0x23, arg0[7:0]: ZenStates-Core Zen4Settings /
        GetSystemConfiguredPowerLimit. That it is the live limit is verified separately.
        Unsupported queries fall back to periodic writes; inconsistent transport
        disables all SMU access until the service is explicitly restarted.
        """
        self.reported_limit = None
        if not self.supported() or self.family.get_limit is None or self._readback_disabled:
            return None
        try:
            status, value = self.smu.send(self.family.get_limit)
        except (OSError, SmuError) as e:
            self.readback_error = f"Could not read the limit: {getattr(e, 'strerror', None) or e}"
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


def bridge_available() -> bool:
    if os.environ.get(BRIDGE_OPT_IN_ENV) != "1":
        return False
    if detect() is None or not hw.exists(BRIDGE_DEVICE) or not hw.exists(BRIDGE_ENABLE_PARAM):
        return False
    return (hw.read(BRIDGE_ENABLE_PARAM) or "").strip().upper() in ("Y", "1")


class BridgeSmu:
    """Experimental G713PV/G713PV.336 kernel SMU bridge (/dev/boreas-smu-probe).

    Requires explicit BOREAS_SMU_BRIDGE=1 opt-in and enable_hw=Y/1 in sysfs.
    Never uses or falls back to raw PCI 0xB8/0xBC. Version/command replies do not
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
