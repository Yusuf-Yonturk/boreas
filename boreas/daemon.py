"""boreasd: the single service running as root (replaces asusd + supergfxd).

The UI sends one JSON request per line over /run/boreas.sock.
The service stores the settings and applies them again at boot, after sleep, on a mode
change (including Fn+F5 and the KDE power profile) and when the charger is plugged in or out.
"""

from __future__ import annotations

import grp
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import traceback

from . import aura, fans, gpu, hw, ryzen
from .config import BUILTIN, Config

SOCKET = os.environ.get("BOREAS_SOCKET", "/run/boreas.sock")
SOCKET_GROUP = "wheel"
POLL_SECONDS = 1.0
ECO_RETRY_TICKS = 15
CPU_TEMP_REAPPLY = 3.0  # The firmware can reset the limit even without a profile change.
# On the G713PV the firmware resets the limit once more ~3-4 s after a profile write;
# write it often during that time so the CPU is never left without a limit.
CPU_TEMP_SETTLE = 10.0
CPU_TEMP_SETTLE_STEP = 0.25
# High temperature log: 95 °C, or 5 °C above a lowered limit. Not every second:
# two lines per episode (start + end, with the peak) go to the journal.
HOT_TEMP = 95
HOT_OVER_LIMIT = 5
HOT_HYSTERESIS = 3
CONFLICT_CHECK_TICKS = 30  # scan for another SMU writer (ryzenadj), every ~30 s
CONFLICT_NOTICE = "ryzenadj is also writing the CPU limit"
GPU_TARGETS = (gpu.ECO, gpu.STANDARD, gpu.OPTIMIZED, gpu.ULTIMATE)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def wait_for_hardware(timeout: float = 20) -> bool:
    """asus_wmi / asus_armoury can be loaded late by udev (~5 s at boot); wait until they appear."""
    deadline = time.monotonic() + timeout
    while not (hw.exists(hw.PLATFORM_PROFILE) and hw.exists(hw.NB_WMI)):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)
    # armoury loads right after asus_wmi; older kernels may not have it at all
    deadline = min(deadline, time.monotonic() + 3)
    while not hw.exists(hw.ARMOURY) and time.monotonic() < deadline:
        time.sleep(0.25)
    return True


class Daemon:
    def __init__(self, config: Config | None = None):
        self.cfg = config or Config()
        self.lock = threading.RLock()
        self.last_profile = hw.get_profile()
        self.last_ac = hw.ac_online()
        self.custom_power = False
        self.eco_stuck = False  # the driver did not let go: retry only on request or at boot
        self.notice = ""
        self.gpu_temp_cache: tuple[float, int | None] = (0.0, None)
        self.ticks = 0
        self.cpu = ryzen.CpuTemp()
        self.cpu_temp_last_attempt: float | None = None
        self.cpu_settle_until = 0.0
        self.hot: dict | None = None  # the ongoing high temperature episode
        self.temp_peak: dict | None = None  # highest since the service started
        self.smu_conflict: list[int] = []
        if self.cfg.fresh:
            self.import_current()

    def import_current(self) -> None:
        """First start: keep the values asusd left behind, adopt the current mode and GPU mode."""
        c = self.cfg
        c["charge_limit"] = hw.battery_info().get("limit")
        c["kbd_brightness"] = hw.kbd_brightness()[0]
        od = hw.get_attr("panel_overdrive")
        c["overdrive"] = od.current if od else None
        base = hw.BASE_BY_PROFILE.get(hw.get_profile() or "", hw.BALANCED)
        c["mode"] = c[self.source_key()] = base
        if gpu.supported():
            c["gpu_target"] = gpu.state()["mode"]
        c.save()
        log(f"first start: adopted current settings (charge {c['charge_limit']}%, GPU {c['gpu_target']})")

    # ---- helpers ---------------------------------------------------------------

    def source_key(self, ac: bool | None = None) -> str:
        return "mode_ac" if (self.last_ac if ac is None else ac) else "mode_battery"

    def set_notice(self, msg: str) -> None:
        self.notice = msg
        log(msg)

    # ---- performance mode ------------------------------------------------------

    def apply_mode(self, mode_id: int, write_profile: bool = True) -> None:
        """Write order: profile → fans → power."""
        with self.lock:
            if not self.cfg.has_mode(mode_id):
                mode_id = hw.BALANCED
            m = self.cfg.mode(mode_id)
            profile = hw.PROFILE_BY_BASE[m["base"]]

            # the profile is written every time; even the same value makes the firmware
            # rerun the throttle policy and reset the custom power limits
            if write_profile:
                hw.set_profile(profile)
                time.sleep(0.1)
            self.last_profile = hw.get_profile()
            self.cfg["mode"] = mode_id
            self.custom_power = False
            # The profile also resets the firmware temperature limit. Write it back right away
            # so the CPU does not heat up unlimited during the fan/power writes; again at the end.
            self.apply_cpu_temp(m.get("cpu_temp"))

            # on these models the power limits do not hold without a custom fan curve
            force_fans = m["power_apply"] and not m["fans_apply"] and hw.fan_required()
            for fan in fans.available_fans():
                curve = m.get(f"fan_{fan}")
                try:
                    if force_fans and not curve:
                        curve = fans.factory_curve(fan)
                    if (m["fans_apply"] or force_fans) and curve:
                        fans.write_curve(fan, curve)
                    elif fans.is_enabled(fan):
                        fans.disable_curve(fan)
                except (OSError, ValueError) as e:
                    self.set_notice(f"Could not write the {fan} fan curve: {e}")

            if m["power_apply"]:
                time.sleep(0.5)
                for name, value in {**(m.get("ppt") or {}), **(m.get("nv") or {})}.items():
                    if value is None:
                        continue
                    try:
                        hw.set_attr(name, int(value))
                        self.custom_power = True
                    except (OSError, ValueError) as e:
                        self.set_notice(f"Could not write {name}: {e}")

            self.apply_cpu_temp(m.get("cpu_temp"))
            self.cpu_settle_until = time.monotonic() + CPU_TEMP_SETTLE

            log(f"mode: {m['name']} ({profile})"
                f"{' +fan' if m['fans_apply'] else ''}{' +power' if self.custom_power else ''}"
                f"{f' +cpu {self.cpu.applied}°C' if self.cpu.applied < ryzen.DEFAULT_TEMP else ''}")

    def apply_cpu_temp(self, temp: int | None) -> bool:
        """When going back to the default, writes only if we lowered the limit before."""
        if not self.cpu.supported():
            return False
        lowered = temp is not None and ryzen.MIN_TEMP <= temp < ryzen.DEFAULT_TEMP
        want = temp if lowered else ryzen.DEFAULT_TEMP
        if not lowered and self.cpu.applied == ryzen.DEFAULT_TEMP:
            return False
        # Failed writes count towards the refresh interval too.
        self.cpu_temp_last_attempt = time.monotonic()
        try:
            self.cpu.set(want)
        except (ryzen.SmuError, ValueError) as e:
            self.set_notice(f"Could not apply the CPU temperature limit: {e}")
            return False
        return True

    def check_smu_conflict(self) -> None:
        if not self.cpu.supported():
            return
        pids = ryzen.other_writers()
        if pids and pids != self.smu_conflict:
            self.set_notice(f"{CONFLICT_NOTICE} (pid {', '.join(map(str, pids))}): the two overwrite "
                            "each other's limit. Disable ryzenadj.service.")
        elif not pids and self.notice.startswith(CONFLICT_NOTICE):
            self.notice = ""
        self.smu_conflict = pids

    def maintain_cpu_temp(self) -> None:
        """Control through a verified readback; otherwise refresh based on temperature."""
        if not self.cpu.supported():
            return
        target = self.cfg.mode(self.cfg["mode"]).get("cpu_temp")
        lowered = target is not None and ryzen.MIN_TEMP <= target < ryzen.DEFAULT_TEMP
        interval = CPU_TEMP_REAPPLY
        if lowered:
            if self.cpu.readback_verified:
                actual = self.cpu.read_limit()
                if actual == target:
                    return  # If the target still holds, do not repeat the command even when the temperature is above it.
                if actual is not None:
                    interval = 1.0
            if not self.cpu.readback_verified:
                current = hw.temps().get("cpu")
                if current is not None:
                    excess = current - target
                    interval = 1.0 if excess >= 5 else 2.0 if excess >= 2 else CPU_TEMP_REAPPLY
        if (self.cpu_temp_last_attempt is None or
                time.monotonic() - self.cpu_temp_last_attempt >= interval):
            self.apply_cpu_temp(target)

    def _later(self, delay: float, fn) -> None:
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()

    def set_mode(self, mode_id: int) -> None:
        with self.lock:
            self.cfg[self.source_key()] = mode_id
            self.apply_mode(mode_id)
            self.cfg.save()

    # ---- GPU -------------------------------------------------------------------

    def apply_gpu_target(self, target: str | None, explicit: bool = False) -> dict:
        """explicit: chosen by the user. Only then is the MUX written (needs a reboot)."""
        if not target or not gpu.supported():
            return {}
        st = gpu.state()
        with self.lock:
            ultimate = st["mux"] == 0
            if st["has_mux"] and (target == gpu.ULTIMATE) != ultimate:
                if explicit:
                    gpu.set_mux(target == gpu.ULTIMATE)
                    return {"reboot": True}
                # if the MUX was changed elsewhere (BIOS etc.), adopt the hardware state
                if not st["pending_reboot"]:
                    self.cfg["gpu_target"] = st["mode"]
                    self.cfg.save()
                    log(f"GPU mode adopted from hardware: {st['mode']}")
                return {}
            if ultimate:
                return {}
            want_eco = target == gpu.ECO or (target == gpu.OPTIMIZED and not hw.ac_online())
            self.eco_stuck = False
            try:
                gpu.set_eco(want_eco)
            except gpu.GpuBusy as e:
                self.eco_stuck = e.hard
                self.set_notice(f"Eco postponed, dGPU in use: {', '.join(e.users)}. "
                                + ("It will be applied after a reboot." if e.hard else
                                   "It will be applied automatically when the dGPU is free (usually after a reboot)."))
                return {"busy": e.users, "deferred": True}
            except OSError as e:
                # the firmware rejected the request; do not let it crash the service at boot (and keep
                # the display manager waiting). If the user chose the mode, pass the error on to them.
                self.set_notice(f"Could not change the GPU mode: {e}")
                if explicit:
                    raise
                return {}
            return {}

    def eco_pending(self) -> bool:
        """Eco is wanted but could not be applied yet because the dGPU is busy."""
        target = self.cfg["gpu_target"]
        if target not in (gpu.ECO, gpu.OPTIMIZED) or self.eco_stuck or not gpu.supported():
            return False
        if target == gpu.OPTIMIZED and self.last_ac:
            return False
        st = gpu.state()
        return st["has_eco"] and st["mux"] != 0 and st["mode"] != gpu.ECO

    def gpu_temp(self) -> int | None:
        """nvidia-smi only runs when the dGPU is already awake (so it does not wake it up).

        Never queried on battery in hybrid mode: every query delays the dGPU going to sleep.
        """
        ts, val = self.gpu_temp_cache
        if time.monotonic() - ts < 5:
            return val
        val = None
        ultimate = (hw.get_attr("gpu_mux_mode") or hw.Attr("", None)).current == 0
        if gpu.dgpu_runtime_status() == "active" and (ultimate or hw.ac_online()):
            try:
                r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu",
                                    "--format=csv,noheader,nounits"],
                                   capture_output=True, text=True, timeout=3)
                val = int(r.stdout.strip().splitlines()[0])
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                pass
        self.gpu_temp_cache = (time.monotonic(), val)
        return val

    # ---- apply all settings ----------------------------------------------------

    def apply_extras(self) -> None:
        c = self.cfg
        steps = [
            ("charge_limit", lambda v: hw.set_charge_limit(v)),
            ("kbd_brightness", lambda v: hw.set_kbd_brightness(v)),
            ("overdrive", lambda v: hw.set_attr("panel_overdrive", v)),
            ("aura", lambda v: aura.apply(v["mode"], tuple(v["color"]),
                                          tuple(v["color2"]) if v.get("color2") else None,
                                          v.get("speed", "normal"))),
        ]
        for key, fn in steps:
            if c[key] is None:
                continue
            try:
                fn(c[key])
            except (OSError, ValueError, KeyError) as e:
                self.set_notice(f"Could not apply {key}: {e}")

    def reapply(self, at_boot: bool = False) -> None:
        with self.lock:
            self.last_ac = hw.ac_online()
            self.apply_extras()
            self.apply_mode(self.cfg[self.source_key()])
            if at_boot:
                self.apply_gpu_target(self.cfg["gpu_target"])
            elif self.cfg["gpu_target"] == gpu.OPTIMIZED:
                # the charger may have been plugged in or out during sleep (automatic GPU mode on resume)
                self.apply_gpu_target(gpu.OPTIMIZED)

    # ---- watcher ---------------------------------------------------------------

    def watch(self) -> None:
        while True:
            try:
                self.settle_sleep(POLL_SECONDS)
            except Exception:
                log(traceback.format_exc())
            try:
                self.tick()
            except Exception:
                log(traceback.format_exc())

    def settle_sleep(self, seconds: float) -> None:
        """Wait; for CPU_TEMP_SETTLE after a mode change, rewrite the limit often."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if time.monotonic() >= self.cpu_settle_until:
                time.sleep(left)
                return
            time.sleep(min(left, CPU_TEMP_SETTLE_STEP))
            with self.lock:
                self.apply_cpu_temp(self.cfg.mode(self.cfg["mode"]).get("cpu_temp"))

    def tick(self) -> None:
        ac = hw.ac_online()
        if ac != self.last_ac:
            # wait until the power source settles (at least 2 s);
            # some models also change the profile themselves on AC. Wait outside the lock.
            time.sleep(2)
            if hw.ac_online() != ac:
                return
        with self.lock:
            if ac != self.last_ac:
                self.last_ac = ac
                log(f"power source: {'AC' if ac else 'battery'}")
                self.apply_mode(self.cfg[self.source_key(ac)])
                if self.cfg["gpu_target"] == gpu.OPTIMIZED:
                    self.apply_gpu_target(gpu.OPTIMIZED)
                self.cfg.save()
                return

            self.ticks += 1
            if self.ticks % CONFLICT_CHECK_TICKS == 1:
                self.check_smu_conflict()
            if self.ticks % ECO_RETRY_TICKS == 0 and self.eco_pending():
                try:
                    gpu.set_eco(True)
                    self.set_notice("The postponed Eco mode has been applied.")
                except gpu.GpuBusy:
                    pass

            profile = hw.get_profile()
            if profile and profile != self.last_profile:
                # changed by Fn+F5 (kernel) or KDE / power-profiles-daemon
                base = hw.BASE_BY_PROFILE.get(profile, hw.BALANCED)
                cur = self.cfg["mode"]
                mode_id = cur if self.cfg.mode(cur)["base"] == base else base
                log(f"profile changed externally: {profile}")
                self.cfg[self.source_key()] = mode_id
                self.apply_mode(mode_id, write_profile=False)
                self.cfg.save()

            self.maintain_cpu_temp()
            self.track_temp()

    def track_temp(self) -> None:
        t = hw.temps().get("cpu")
        if t is None:
            return
        m = self.cfg.mode(self.cfg["mode"])
        if self.temp_peak is None or t > self.temp_peak["max"]:
            self.temp_peak = {"max": t, "at": time.strftime("%d.%m %H:%M:%S"), "mode": m["name"]}
        limit = m.get("cpu_temp")
        lowered = limit is not None and ryzen.MIN_TEMP <= limit < ryzen.DEFAULT_TEMP
        if self.hot is None:
            threshold = min(HOT_TEMP, limit + HOT_OVER_LIMIT) if lowered else HOT_TEMP
            if t >= threshold:
                self.hot = {"threshold": threshold, "max": t, "start": time.monotonic()}
                fans_rpm = ", ".join(f"{k} {v}" for k, v in hw.fan_rpms().items())
                log(f"high temperature: CPU {t}°C (threshold {threshold}, mode {m['name']}, "
                    f"limit {limit if lowered else 'default'}, fan {fans_rpm or '?'} rpm)")
            return
        self.hot["max"] = max(self.hot["max"], t)
        if t <= self.hot["threshold"] - HOT_HYSTERESIS:
            log(f"high temperature ended: peak {self.hot['max']}°C, "
                f"{time.monotonic() - self.hot['start']:.0f} s")
            self.hot = None

    # ---- status ----------------------------------------------------------------

    def status(self) -> dict:
        c = self.cfg
        attrs = {}
        for name in (*hw.PPT_ATTRS, *hw.NV_ATTRS, "panel_overdrive", "charge_mode"):
            a = hw.get_attr(name)
            if a is not None:
                attrs[name] = a.to_dict()
        kb, kb_max = hw.kbd_brightness()
        t = hw.temps()
        gt = self.gpu_temp()
        if gt is not None:
            t["gpu"] = gt
        return {
            "mode": c["mode"],
            "modes": [{"id": i, "name": c.mode(i)["name"], "base": c.mode(i)["base"],
                       "builtin": i in BUILTIN} for i in c.mode_list()],
            "mode_ac": c["mode_ac"],
            "mode_battery": c["mode_battery"],
            "profile": hw.get_profile(),
            "ac": hw.ac_online(),
            "battery": hw.battery_info(),
            "fans": hw.fan_rpms(),
            "fan_curves": fans.available_fans(),
            "temps": t,
            "gpu": gpu.state() if gpu.supported() else None,
            "gpu_target": c["gpu_target"],
            "kbd": {"brightness": kb, "max": kb_max},
            "aura": c["aura"],
            "aura_device": aura.find_device() is not None,
            "attrs": attrs,
            "custom_power": self.custom_power,
            "cpu_temp": ({"min": ryzen.MIN_TEMP, "max": ryzen.DEFAULT_TEMP, "applied": self.cpu.applied,
                          "reported_limit": self.cpu.reported_limit,
                          "readback_verified": self.cpu.readback_verified,
                          "readback_error": self.cpu.readback_error,
                          "control": "readback" if self.cpu.readback_verified else "adaptive",
                          "conflict": self.smu_conflict,
                          "cpu": self.cpu.family.name, "smu": self.cpu.version_str()}
                         if self.cpu.supported() else None),
            "temp_peak": self.temp_peak,
            "notice": self.notice,
        }

    # ---- commands --------------------------------------------------------------

    def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        c = self.cfg
        with self.lock:
            if cmd == "status":
                return self.status()
            if cmd == "set_mode":
                if not c.has_mode(int(req["mode"])):
                    raise ValueError(f"No such mode: {req['mode']}")
                self.set_mode(int(req["mode"]))
                return {}
            if cmd == "cycle_mode":
                self.set_mode(c.next_mode(c["mode"], bool(req.get("back"))))
                return {"mode": c["mode"]}
            if cmd == "get_mode":
                return {"mode": c.mode(int(req["mode"]))}
            if cmd == "update_mode":
                mode_id = int(req["mode"])
                entry = c.mode(mode_id)
                t = req["data"].get("cpu_temp")
                if t is not None and not ryzen.MIN_TEMP <= int(t) <= ryzen.DEFAULT_TEMP:
                    raise ValueError(f"CPU temperature limit must be {ryzen.MIN_TEMP}..{ryzen.DEFAULT_TEMP}")
                changed = set()
                for k, v in req["data"].items():
                    if k in entry and k != "base":
                        value = fans.fix_curve(v) if k.startswith("fan_") and v else v
                        if entry[k] != value:
                            changed.add(k)
                        entry[k] = value
                c.save()
                if mode_id == c["mode"]:
                    # The UI also sends the unchanged fields. When only the temperature
                    # was edited, do not reset the profile and disturb the fan/power settings.
                    if changed - {"cpu_temp", "name"}:
                        self.apply_mode(mode_id)
                    elif "cpu_temp" in changed or not changed:
                        self.apply_cpu_temp(entry.get("cpu_temp"))
                return {"mode": entry}
            if cmd == "default_curve":
                fan = req["fan"]
                base = c.mode(int(req.get("mode", c["mode"])))["base"]
                curve = None
                if base == c.mode(c["mode"])["base"]:
                    try:
                        curve = fans.factory_curve(fan)
                        self.apply_mode(c["mode"], write_profile=False)
                    except OSError:
                        curve = None
                if not curve:
                    curve = fans.DEFAULTS[base].get(fan, fans.DEFAULTS[base]["cpu"])
                return {"curve": curve}
            if cmd == "add_mode":
                new = c.add_mode(int(req.get("source", c["mode"])))
                c.save()
                return {"mode": new}
            if cmd == "remove_mode":
                c.remove_mode(int(req["mode"]))
                c.save()
                self.apply_mode(c[self.source_key()])
                return {}
            if cmd == "set_gpu":
                if req["target"] not in GPU_TARGETS:
                    raise ValueError(f"GPU mode must be one of {', '.join(GPU_TARGETS)}")
                c["gpu_target"] = req["target"]
                c.save()
                return self.apply_gpu_target(req["target"], explicit=True)
            if cmd == "set_charge_limit":
                hw.set_charge_limit(int(req["value"]))
                c["charge_limit"] = int(req["value"])
                c.save()
                return {}
            if cmd == "set_kbd":
                hw.set_kbd_brightness(int(req["brightness"]))
                c["kbd_brightness"] = int(req["brightness"])
                c.save()
                return {}
            if cmd == "set_aura":
                a = {"mode": req["mode"], "color": list(req["color"]),
                     "color2": list(req["color2"]) if req.get("color2") else None,
                     "speed": req.get("speed", "normal")}
                aura.apply(a["mode"], tuple(a["color"]),
                           tuple(a["color2"]) if a["color2"] else None, a["speed"])
                c["aura"] = a
                c.save()
                return {}
            if cmd == "set_overdrive":
                hw.set_attr("panel_overdrive", int(req["value"]))
                c["overdrive"] = int(req["value"])
                c.save()
                return {}
            if cmd == "reapply":
                self.reapply(at_boot=bool(req.get("boot")))
                return {}
            if cmd == "clear_notice":
                self.notice = ""
                return {}
        raise ValueError(f"bilinmeyen komut: {cmd}")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            try:
                req = json.loads(line)
                resp = {"ok": True, **(self.server.daemon_obj.handle(req) or {})}
            except Exception as e:
                log(traceback.format_exc())
                resp = {"ok": False, "error": str(e)}
            self.wfile.write((json.dumps(resp) + "\n").encode())
            self.wfile.flush()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main() -> None:
    if not wait_for_hardware():
        log("asus-nb-wmi / platform_profile not found, starting anyway")
    d = Daemon()
    log(f"boreasd starting, profile={d.last_profile}, ac={d.last_ac}")
    d.reapply(at_boot=True)

    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    server = Server(SOCKET, Handler)
    server.daemon_obj = d
    try:
        os.chown(SOCKET, 0, grp.getgrnam(SOCKET_GROUP).gr_gid)
        os.chmod(SOCKET, 0o660)
    except (KeyError, PermissionError):
        os.chmod(SOCKET, 0o600)
        log(f"group '{SOCKET_GROUP}' not found, socket is root-only")

    threading.Thread(target=d.watch, daemon=True).start()
    sd_notify("READY=1")
    server.serve_forever()


def sd_notify(msg: str) -> None:
    """Type=notify: the display manager starts after the GPU mode has been applied."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.sendto(msg.encode(), addr)


if __name__ == "__main__":
    main()
