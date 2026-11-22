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
# The enabled choice, explicit or the default on a CPU family the module knows. Files keep the
# choice across pkexec (in-app update) and systemd, which do not carry the installer's environment.
# smu-off is an explicit "no" that the default does not override.
OPT_IN_FILE = "/etc/boreas/smu"
OPT_OUT_FILE = "/etc/boreas/smu-off"
# what OPT_IN_FILE holds: a later version can tell its own default from a user's choice and take
# the default back for a family that turns out to misbehave
CHOICE_DEFAULT, CHOICE_USER = "default\n", "user\n"
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
    # run on hardware with this module (docs/cpu-smu.md); every family in FAMILIES is on by
    # default, this only records what has been measured
    verified: bool = False


_RAVEN = Family("Raven Ridge / Picasso / Dali")
_RENOIR = Family("Renoir / Lucienne / Cezanne", reapply=True)
_MOBILE = Family("Van Gogh / Mendocino / Rembrandt / Phoenix / Hawk Point", reapply=True)
_DRAGON = Family("Dragon Range / Raphael", verified=True)
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


def install_choice(env_value: str | None) -> str:
    """Save an explicit choice, or switch a CPU family the module knows on when nothing is saved."""
    on, off = hw.p(OPT_IN_FILE), hw.p(OPT_OUT_FILE)
    if env_value in (None, ""):
        if os.path.exists(on) or os.path.exists(off):
            return ""
        family = detect()
        if family is None:
            return ""
        enabled = True
        message = (f"CPU temperature ceiling: on by default for {family.name} "
                   "(BOREAS_SMU=0 switches it off)")
    elif env_value == "1":
        enabled = True
        message = "CPU temperature ceiling: on (requested by user)"
    elif env_value == "0":
        enabled = False
        message = "CPU temperature ceiling: off (requested by user)"
    else:
        return "Warning: BOREAS_SMU must be 0, 1 or empty; keeping the saved CPU temperature ceiling choice"

    os.makedirs(os.path.dirname(on), exist_ok=True)
    with open(on if enabled else off, "w") as f:
        f.write(CHOICE_DEFAULT if env_value in (None, "") else CHOICE_USER)
    try:
        os.unlink(off if enabled else on)
    except FileNotFoundError:
        pass
    return message


class BridgeSmu:
    """The boreas_smu kernel module (/dev/boreas-smu), used only when OPT_IN_FILE exists.

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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install-choice"])
    parser.parse_args()
    line = install_choice(os.environ.get("BOREAS_SMU"))
    if line:
        print(line)
