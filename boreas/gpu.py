"""GPU modes: Eco / Standard / Optimized / Ultimate.

Mapping:
  Ultimate  = gpu_mux_mode 0 (display wired straight to the dGPU, needs a reboot)
  Standard  = gpu_mux_mode 1, dgpu_disable 0
  Eco       = gpu_mux_mode 1, dgpu_disable 1
  Optimized = Standard on AC, Eco on battery

Writing dgpu_disable while the driver is still bound can freeze the system, so the
NVIDIA modules are unloaded and the PCI device is removed first. If the modules are in
use (KWin, games, CUDA), Eco is postponed until the next boot.

That detach step only knows NVIDIA. On a machine with a Radeon dGPU (2022 Zephyrus G14,
GA402R) it would write dgpu_disable=1 with amdgpu still bound, so Eco and Optimized are
offered only when eco_capable() says the dGPU is NVIDIA. Standard and Ultimate work everywhere.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import time

from . import hw, gpu_engine

ECO, STANDARD, ULTIMATE, OPTIMIZED = "eco", "standard", "ultimate", "optimized"
NV_MODULES = ("nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia")
# NVIDIA services that keep the dGPU open: stopped before Eco, started again when leaving Eco
NV_SERVICES = ("nvidia-persistenced.service", "nvidia-powerd.service")
# /proc/PID/comm names (the kernel cuts them at 15 characters)
NV_SERVICE_COMMANDS = ("nvidia-persiste", "nvidia-powerd")
PROC = "/proc"
PCI_ADDR = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
# Why Eco and Optimized are refused (the GUI translates these texts)
NO_ECO = "Eco and Optimized are not supported on this device"
NEEDS_NVIDIA = "Eco and Optimized need an NVIDIA dGPU"


class GpuBusy(Exception):
    """hard: the driver, not a process, does not let go; retrying on its own is pointless."""

    def __init__(self, users: list[str], hard: bool = False):
        super().__init__("dGPU in use: " + ", ".join(users))
        self.users = users
        self.hard = hard


class DgpuSlow(OSError):
    """dgpu_disable=0 was written, but the dGPU has not appeared on the PCI bus yet."""


def supported() -> bool:
    return hw.get_attr("dgpu_disable") is not None or hw.get_attr("gpu_mux_mode") is not None


def state() -> dict:
    mux = hw.get_attr("gpu_mux_mode")
    eco = hw.get_attr("dgpu_disable")
    mux_v = mux.current if mux else None
    eco_v = eco.current if eco else None
    engine = None
    engine_error = None
    if gpu_engine.selected() and mux_v != 0:
        try:
            engine = gpu_engine.get().request("status")
        except OSError as exc:
            engine_error = str(exc)
    if mux_v == 0:
        mode = ULTIMATE
    elif gpu_engine.selected():
        mode = engine["policy"] if engine else "unknown"
    elif eco_v == 1:
        mode = ECO
    else:
        mode = STANDARD
    # kernel 7.3 asus-armoury: plain file; earlier versions: pending_reboot/current_value
    pending = hw.read_int(hw.ARMOURY + "/pending_reboot")
    if pending is None:
        pending = hw.read_int(hw.ARMOURY + "/pending_reboot/current_value")
    # The eBPF helper only starts with an NVIDIA offload GPU, and in its Eco the device's sysfs
    # files are hidden from us, so the PCI check below would read nothing.
    capable = gpu_engine.selected() or eco_capable(eco_v)
    return {
        "mode": mode,
        "mux": mux_v,
        "dgpu_disable": eco_v,
        "has_mux": mux is not None,
        "has_eco": eco is not None and capable,
        "ebpf_available": capable and os.path.isfile(gpu_engine.helper_path()),
        "backend": "ebpf" if gpu_engine.selected() else "firmware",
        "backend_error": engine_error,
        "engine": engine,
        "pending_reboot": bool(pending),
        "dgpu_runtime": engine.get("runtime_status") if engine else (None if gpu_engine.selected() else dgpu_runtime_status()),
    }


def nvidia_pci_devices() -> list[str]:
    """PCI functions of the built-in NVIDIA dGPU (GPU + HDMI audio). An eGPU (USB4/Thunderbolt,
    which the kernel marks "removable") is not the dGPU dgpu_disable switches off: it is neither
    counted nor removed from the bus."""
    out = []
    for d in sorted(glob.glob(hw.p("/sys/bus/pci/devices/*"))):
        device = d[len(hw.ROOT):]
        if hw.read(device + "/vendor") == "0x10de" and hw.read(device + "/removable") != "removable":
            out.append(device)
    return out


def internal_nvidia_present() -> bool:
    """An NVIDIA display device built into the laptop is on the bus."""
    return any((hw.read(d + "/class") or "").startswith("0x03") for d in nvidia_pci_devices())


dgpu_present = internal_nvidia_present


def other_dgpu_present() -> bool:
    """An iGPU plus a non-NVIDIA dGPU are visible among the built-in display devices."""
    vendors = []
    for d in glob.glob(hw.p("/sys/bus/pci/devices/*")):
        device = d[len(hw.ROOT):]
        if ((hw.read(device + "/class") or "").startswith("0x03") and
                hw.read(device + "/removable") != "removable"):
            vendor = hw.read(device + "/vendor")
            if vendor:  # unreadable (a rescan in progress): not counted
                vendors.append(vendor)
    return len(vendors) >= 2 and "0x10de" not in vendors


def nvidia_driver_loaded() -> bool:
    return _loaded("nvidia")


def nvidia_driver_installed() -> bool:
    if _loaded("nvidia"):
        return True
    try:
        if _run("modinfo", "-F", "filename", "nvidia"):
            return True
    except OSError:
        pass
    # After a kernel upgrade without a reboot the running kernel's module directory is gone and
    # modinfo fails although the driver is installed: look at every installed kernel.
    for dep in glob.glob(hw.p("/usr/lib/modules/*/modules.dep")):
        try:
            with open(dep, errors="replace") as f:
                if any("/nvidia.ko" in line for line in f):
                    return True
        except OSError:
            pass
    return False


# Written when Eco takes a built-in NVIDIA dGPU off the bus. An empty PCIe port proves nothing (a
# Thunderbolt or M.2 port is empty on most laptops), so only this says a dGPU belongs here.
DGPU_SEEN = "/etc/boreas/nvidia-dgpu"


def _remember_dgpu() -> None:
    try:
        os.makedirs(os.path.dirname(hw.p(DGPU_SEEN)), exist_ok=True)
        with open(hw.p(DGPU_SEEN), "w") as f:
            f.write("a built-in NVIDIA dGPU was taken off the PCI bus for Eco\n")
    except OSError:
        pass  # without it a dGPU that does not come back is reported less precisely, nothing more


def nvidia_dgpu_expected() -> bool:
    """A built-in NVIDIA dGPU belongs on the bus although none is visible now (it has not come
    back after leaving Eco): Boreas took one off the bus for Eco before, no other dGPU is built
    in and the NVIDIA driver is installed. The file checks come first, modinfo only after them."""
    return os.path.exists(hw.p(DGPU_SEEN)) and not other_dgpu_present() and nvidia_driver_installed()


def eco_capable(dgpu_disable: int | None) -> bool:
    """Whether the dGPU is one the Eco path knows how to detach (NVIDIA).

    True when a built-in NVIDIA display device (PCI class 0x03) is on the bus, or when the
    machine is already in Eco: an NVIDIA dGPU in Eco is off the bus, and leaving Eco must stay
    possible. The vendor of a Radeon dGPU says nothing on its own, since the iGPU is AMD (0x1002) too.
    """
    return dgpu_disable == 1 or internal_nvidia_present()


def eco_refusal(st: dict) -> str | None:
    """Why Eco and Optimized cannot be chosen in this state() (None: they can)."""
    if st.get("has_eco") or st.get("backend") == "ebpf":
        return None  # the eBPF helper only runs on an NVIDIA offload GPU and has its own Eco
    return NEEDS_NVIDIA if st.get("dgpu_disable") is not None else NO_ECO


def dgpu_runtime_status() -> str | None:
    for d in nvidia_pci_devices():
        cls = hw.read(d + "/class") or ""
        if cls.startswith("0x03"):
            return hw.read(d + "/power/runtime_status")
    return None


def _loaded(module: str) -> bool:
    return hw.exists(f"/sys/module/{module}")


def clean_comm(s: str) -> str:
    """Keep process names safe for single-line logs and notifications."""
    return "".join(c if c.isprintable() else "?" for c in s[:15])


def _comm(pid: str) -> str:
    try:
        with open(f"{PROC}/{pid}/comm", errors="replace", newline="") as f:
            return clean_comm(f.read().removesuffix("\n"))
    except OSError:  # the process exited in the meantime
        return "?"


def _fuser_pids(nodes: list[str]) -> set[str] | None:
    """PIDs using the devices according to fuser (open files and memory maps); None without fuser.

    fuser writes the PIDs to stdout and the file names and access letters to stderr, so only
    stdout is read and the process names come from /proc/PID/comm.
    """
    try:
        r = subprocess.run(["fuser", *nodes], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(r.stdout.split())


def _proc_pids(nodes: list[str]) -> set[str]:
    """Fallback without fuser (psmisc): looks at the /proc/PID/fd links."""
    wanted = set(nodes)
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except OSError:
        return set()
    out = set()
    for pid in pids:
        try:
            fds = os.listdir(f"{PROC}/{pid}/fd")
        except OSError:  # the process exited or access was denied
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{PROC}/{pid}/fd/{fd}")
            except OSError:
                continue
            if target in wanted or target.startswith("/dev/nvidia"):
                out.add(pid)
                break
    return out


def _nvidia_users(ignore: tuple[str, ...] = ()) -> list[str]:
    """Processes using the dGPU device files, as "command(PID)"."""
    nodes = glob.glob("/dev/nvidia*")
    for d in nvidia_pci_devices():
        nodes += [f"/dev/dri/{os.path.basename(x)}" for x in glob.glob(hw.p(d) + "/drm/*")
                  if os.path.basename(x).startswith(("card", "renderD"))]
    if not nodes:
        return []
    pids = _fuser_pids(nodes)
    if pids is None:
        pids = _proc_pids(nodes)
    pids.discard(str(os.getpid()))
    users = set()
    for pid in pids:
        if pid.isdigit() and (comm := _comm(pid)) not in ignore:
            users.add(f"{comm}({pid})")
    return sorted(users)


def _run(*cmd: str) -> bool:
    return subprocess.run(cmd, capture_output=True).returncode == 0


def _detach_dgpu() -> None:
    # Check before touching the services: if KWin etc. holds it, do not restart them on every try
    users = _nvidia_users(ignore=NV_SERVICE_COMMANDS)
    if users:
        raise GpuBusy(users)
    stopped = [svc for svc in NV_SERVICES if _run("systemctl", "is-active", "--quiet", svc)]
    for svc in stopped:
        _run("systemctl", "stop", svc)

    def restore():
        for svc in stopped:
            _run("systemctl", "start", svc)
    users = _nvidia_users()
    if users:
        restore()
        raise GpuBusy(users)
    for m in NV_MODULES:
        if _loaded(m) and not _run("modprobe", "-r", m):
            # do not leave it half done: reload what was loaded and restart the services
            _run("modprobe", "nvidia_drm")
            restore()
            raise GpuBusy([f"module {m} could not be unloaded"], hard=True)
    if internal_nvidia_present():
        _remember_dgpu()
    for d in reversed(nvidia_pci_devices()):
        try:
            hw.write(d + "/remove", 1)
        except OSError:
            pass


def _attach_dgpu(tries: int = 5) -> None:
    # after dgpu_disable=0 the GPU may not show up on the bus right away
    present = False
    for _ in range(tries):
        hw.write("/sys/bus/pci/rescan", 1)
        present = dgpu_present()
        if present:
            break
        time.sleep(1)
    # Try to load the driver even if the dGPU is late; it may refuse to load without a device.
    _run("modprobe", "nvidia_drm")
    for svc in NV_SERVICES:
        if subprocess.run(["systemctl", "is-enabled", svc], capture_output=True).returncode == 0:
            _run("systemctl", "start", svc)
    # dgpu_present() only knows built-in NVIDIA cards. A visible non-NVIDIA dGPU has already
    # returned, even if an NVIDIA driver happens to be installed on disk.
    if not present and nvidia_dgpu_expected():
        raise DgpuSlow(f"the dGPU did not appear on the PCI bus within {tries} s")


def _empty_bridges() -> list[str]:
    """PCI bridges with no device below them (in Eco this is the port the dGPU sits on)."""
    out = []
    for d in sorted(glob.glob(hw.p("/sys/bus/pci/devices/*"))):
        if not os.path.exists(d + "/secondary_bus_number"):
            continue
        # only real PCI devices ("0000:01:00.0"); the port's own services
        # (like "0000:00:01.1:pcie002") are always there and must not count
        children = [c for c in os.listdir(os.path.realpath(d)) if PCI_ADDR.fullmatch(c)]
        if not children:
            out.append(d[len(hw.ROOT):])
    return out


def _wake_bridges() -> dict[str, str]:
    """Keep the empty bridges awake; returns their previous power/control values.

    In Eco the dGPU's PCIe port goes into deep sleep (D3cold). If dgpu_disable=0 is written
    while the port sleeps, the firmware (ACPI) waits 30 s for the dGPU to power up and fails
    (measured on a G713PV); with the port awake the same write takes less than a second.
    """
    saved = {}
    for b in _empty_bridges():
        old = hw.read(b + "/power/control")
        if old and old != "on":
            try:
                hw.write(b + "/power/control", "on")  # the kernel wakes the port up as soon as this is written
                saved[b] = old
            except OSError:
                pass
    return saved


def _restore_bridges(saved: dict[str, str]) -> None:
    for b, old in saved.items():
        try:
            hw.write(b + "/power/control", old)
        except OSError:
            pass


def set_eco(enabled: bool) -> None:
    if gpu_engine.selected():
        gpu_engine.get().set_eco(enabled)
        return
    cur = hw.get_attr("dgpu_disable")
    if cur is None:
        if not enabled:
            return  # a MUX-only device: without dgpu_disable the dGPU is never off
        raise ValueError("Eco mode is not supported")
    if bool(cur.current) == enabled:
        if not enabled and not internal_nvidia_present() and nvidia_dgpu_expected():
            # Standard already, but the dGPU did not come back after Eco (DgpuSlow): look again,
            # with the empty ports awake as when leaving Eco
            saved = _wake_bridges()
            try:
                _attach_dgpu()
            finally:
                _restore_bridges(saved)
        return
    if enabled and not eco_capable(cur.current):
        # a Radeon dGPU: dgpu_disable=1 with amdgpu still bound can freeze the system
        raise ValueError(NEEDS_NVIDIA)
    if enabled:
        _detach_dgpu()
        try:
            hw.set_attr("dgpu_disable", 1)
        except (OSError, ValueError) as e:
            # the device was removed but Eco could not be written: bring the dGPU and the driver back
            try:
                _attach_dgpu()
            except OSError as attach_error:
                raise OSError(f"{e}; {attach_error}") from e
            raise
    else:
        saved = _wake_bridges()
        try:
            hw.set_attr("dgpu_disable", 0)
            _attach_dgpu()
        finally:
            _restore_bridges(saved)  # with the dGPU attached, the port does not sleep on its own


def set_mux(ultimate: bool) -> None:
    if hw.get_attr("gpu_mux_mode") is None:
        raise ValueError("MUX is not supported")
    if ultimate:
        # Eco has to be off first; if it is not, do not touch the MUX
        try:
            set_eco(False)
        except DgpuSlow:
            pass  # dgpu_disable=0 is written; the dGPU comes up at the reboot Ultimate needs anyway
        eco = hw.get_attr("dgpu_disable")
        if eco is not None and eco.current == 1:
            raise ValueError("Could not turn off Eco, did not switch to Ultimate")
    hw.set_attr("gpu_mux_mode", 0 if ultimate else 1)
