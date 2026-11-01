"""GPU modları: Eco / Standart / Optimized / Ultimate.

Eşleme:
  Ultimate  = gpu_mux_mode 0 (ekran doğrudan dGPU'da, yeniden başlatma gerekir)
  Standart  = gpu_mux_mode 1, dgpu_disable 0
  Eco       = gpu_mux_mode 1, dgpu_disable 1
  Optimized = prizde Standart, pilde Eco

dgpu_disable sürücü hâlâ bağlıyken yazılırsa sistem kilitlenebilir; bu yüzden
önce NVIDIA modülleri kaldırılır ve PCI aygıtı çıkarılır. Modüller kullanılıyorsa
(KWin, oyun, CUDA) Eco bir sonraki açılışa ertelenir.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import time

from . import hw

ECO, STANDARD, ULTIMATE, OPTIMIZED = "eco", "standard", "ultimate", "optimized"
NV_MODULES = ("nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia")
# dGPU'yu açık tutan NVIDIA servisleri: Eco öncesi durdurulur, Eco'dan çıkınca yeniden başlatılır
NV_SERVICES = ("nvidia-persistenced.service", "nvidia-powerd.service")
# /proc/PID/comm adları (çekirdek 15 karakterde keser)
NV_SERVICE_COMMANDS = ("nvidia-persiste", "nvidia-powerd")
PROC = "/proc"
PCI_ADDR = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")


class GpuBusy(Exception):
    """hard: süreç değil sürücü bırakmıyor; kendiliğinden yeniden denemenin anlamı yok."""

    def __init__(self, users: list[str], hard: bool = False):
        super().__init__("dGPU in use: " + ", ".join(users))
        self.users = users
        self.hard = hard


def supported() -> bool:
    return hw.get_attr("dgpu_disable") is not None or hw.get_attr("gpu_mux_mode") is not None


def state() -> dict:
    mux = hw.get_attr("gpu_mux_mode")
    eco = hw.get_attr("dgpu_disable")
    mux_v = mux.current if mux else None
    eco_v = eco.current if eco else None
    if mux_v == 0:
        mode = ULTIMATE
    elif eco_v == 1:
        mode = ECO
    else:
        mode = STANDARD
    # kernel 7.3 asus-armoury: düz dosya; önceki sürümler: pending_reboot/current_value
    pending = hw.read_int(hw.ARMOURY + "/pending_reboot")
    if pending is None:
        pending = hw.read_int(hw.ARMOURY + "/pending_reboot/current_value")
    return {
        "mode": mode,
        "mux": mux_v,
        "dgpu_disable": eco_v,
        "has_mux": mux is not None,
        "has_eco": eco is not None,
        "pending_reboot": bool(pending),
        "dgpu_runtime": dgpu_runtime_status(),
    }


def nvidia_pci_devices() -> list[str]:
    """dGPU'nun PCI fonksiyonları (GPU + HDMI ses)."""
    out = []
    for d in sorted(glob.glob(hw.p("/sys/bus/pci/devices/*"))):
        if hw.read(d[len(hw.ROOT):] + "/vendor") == "0x10de":
            out.append(d[len(hw.ROOT):])
    return out


def dgpu_present() -> bool:
    return any((hw.read(d + "/class") or "").startswith("0x03") for d in nvidia_pci_devices())


def dgpu_runtime_status() -> str | None:
    for d in nvidia_pci_devices():
        cls = hw.read(d + "/class") or ""
        if cls.startswith("0x03"):
            return hw.read(d + "/power/runtime_status")
    return None


def _loaded(module: str) -> bool:
    return hw.exists(f"/sys/module/{module}")


def _comm(pid: str) -> str:
    try:
        with open(f"{PROC}/{pid}/comm") as f:
            return f.read().strip()
    except OSError:  # süreç bu arada bitti
        return "?"


def _fuser_pids(nodes: list[str]) -> set[str] | None:
    """fuser ile aygıtları kullanan PID'ler (açık dosya ve bellek eşlemesi); fuser yoksa None.

    fuser PID'leri stdout'a, dosya adlarını ve erişim harflerini stderr'e yazar;
    bu yüzden yalnız stdout okunur ve süreç adları /proc/PID/comm'dan alınır.
    """
    try:
        r = subprocess.run(["fuser", *nodes], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(r.stdout.split())


def _proc_pids(nodes: list[str]) -> set[str]:
    """fuser (psmisc) yoksa yedek: /proc/PID/fd bağlantılarına bakar."""
    wanted = set(nodes)
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except OSError:
        return set()
    out = set()
    for pid in pids:
        try:
            fds = os.listdir(f"{PROC}/{pid}/fd")
        except OSError:  # süreç bitti ya da erişim yok
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
    """dGPU aygıt dosyalarını kullanan süreçler, "komut(PID)" biçiminde."""
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
    # Servislere dokunmadan önce bak: KWin vb. tutuyorsa her denemede servisi oynatma
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
            # yarıda kalmasın: yükleneni ve servisleri geri getir
            _run("modprobe", "nvidia_drm")
            restore()
            raise GpuBusy([f"module {m} could not be unloaded"], hard=True)
    for d in reversed(nvidia_pci_devices()):
        try:
            hw.write(d + "/remove", 1)
        except OSError:
            pass


def _attach_dgpu(tries: int = 5) -> None:
    # dgpu_disable=0 sonrası GPU veri yolunda hemen görünmeyebilir
    for _ in range(tries):
        hw.write("/sys/bus/pci/rescan", 1)
        if dgpu_present():
            break
        time.sleep(1)
    _run("modprobe", "nvidia_drm")
    for svc in NV_SERVICES:
        if subprocess.run(["systemctl", "is-enabled", svc], capture_output=True).returncode == 0:
            _run("systemctl", "start", svc)


def _empty_bridges() -> list[str]:
    """Altında aygıt olmayan PCI köprüleri (Eco'da dGPU'nun bağlı olduğu port böyledir)."""
    out = []
    for d in sorted(glob.glob(hw.p("/sys/bus/pci/devices/*"))):
        if not os.path.exists(d + "/secondary_bus_number"):
            continue
        # yalnız gerçek PCI aygıtları ("0000:01:00.0"); portun kendi servisleri
        # ("0000:00:01.1:pcie002" gibi) her zaman orada durur, sayılmamalı
        children = [c for c in os.listdir(os.path.realpath(d)) if PCI_ADDR.fullmatch(c)]
        if not children:
            out.append(d[len(hw.ROOT):])
    return out


def _wake_bridges() -> dict[str, str]:
    """Boş köprüleri uyanık tut; eski power/control değerlerini döndürür.

    Eco'da dGPU'nun PCIe portu derin uykuya (D3cold) geçer. Port uyurken dgpu_disable=0
    yazılırsa firmware (ACPI) dGPU'nun açılmasını 30 sn bekleyip hata verir (G713PV'de ölçüldü);
    port uyanıkken aynı yazma 1 sn'den kısa sürer.
    """
    saved = {}
    for b in _empty_bridges():
        old = hw.read(b + "/power/control")
        if old and old != "on":
            try:
                hw.write(b + "/power/control", "on")  # yazınca çekirdek portu hemen uyandırır
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
    cur = hw.get_attr("dgpu_disable")
    if cur is None:
        raise ValueError("Eco mode is not supported")
    if bool(cur.current) == enabled:
        return
    if enabled:
        _detach_dgpu()
        try:
            hw.set_attr("dgpu_disable", 1)
        except (OSError, ValueError):
            # aygıt çıkarıldı ama Eco yazılamadı: dGPU'yu ve sürücüyü geri getir
            _attach_dgpu()
            raise
    else:
        saved = _wake_bridges()
        try:
            hw.set_attr("dgpu_disable", 0)
            _attach_dgpu()
        finally:
            _restore_bridges(saved)  # dGPU bağlıyken port ondan bağımsız uyumaz


def set_mux(ultimate: bool) -> None:
    if hw.get_attr("gpu_mux_mode") is None:
        raise ValueError("MUX is not supported")
    if ultimate:
        # önce Eco kapanmalı, kapanmadıysa MUX'a dokunma
        set_eco(False)
        eco = hw.get_attr("dgpu_disable")
        if eco is not None and eco.current == 1:
            raise ValueError("Could not turn off Eco, did not switch to Ultimate")
    hw.set_attr("gpu_mux_mode", 0 if ultimate else 1)
