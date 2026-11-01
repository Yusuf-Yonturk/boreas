"""Tests on a fake sysfs tree: python3 -m unittest discover -s tests"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TMP = tempfile.mkdtemp(prefix="boreas-test-")
os.environ["BOREAS_SYSFS_ROOT"] = TMP + "/root"
os.environ["BOREAS_CONFIG"] = TMP + "/etc/config.json"
os.environ["BOREAS_SOCKET"] = TMP + "/boreas.sock"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from boreas import aura, config, daemon, fans, gpu, hw, ryzen  # noqa: E402
from boreas import client  # noqa: E402

R = TMP + "/root"
ARM = R + hw.ARMOURY


def put(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


def get(path):
    with open(path) as f:
        return f.read().strip()


def build_tree():
    """The real layout, taken from a G713PV."""
    shutil.rmtree(R, ignore_errors=True)
    put(R + hw.PLATFORM_PROFILE, "balanced")
    put(R + hw.NB_WMI + "/charge_mode", 1)
    put(R + hw.PLATFORM_PROFILE + "_choices", "quiet balanced performance")
    attrs = {
        "ppt_pl1_spl": (55, 30, 130, None), "ppt_pl2_sppt": (66, 65, 130, None),
        "ppt_pl3_fppt": (65, 65, 130, None), "nv_dynamic_boost": (25, 5, 25, None),
        "nv_temp_target": (87, 75, 87, None), "panel_overdrive": (1, None, None, "0;1"),
        "dgpu_disable": (0, None, None, "0;1"), "gpu_mux_mode": (1, None, None, "0;1"),
    }
    for name, (cur, lo, hi, poss) in attrs.items():
        put(f"{ARM}/{name}/current_value", cur)
        if lo is not None:
            put(f"{ARM}/{name}/min_value", lo)
            put(f"{ARM}/{name}/max_value", hi)
        if poss:
            put(f"{ARM}/{name}/possible_values", poss)
    put(f"{ARM}/pending_reboot/current_value", 0)
    put(R + "/sys/class/hwmon/hwmon8/name", "asus")
    put(R + "/sys/class/hwmon/hwmon8/fan1_input", 4000)
    put(R + "/sys/class/hwmon/hwmon8/fan1_label", "cpu_fan")
    put(R + "/sys/class/hwmon/hwmon8/fan2_input", 4300)
    put(R + "/sys/class/hwmon/hwmon8/fan2_label", "gpu_fan")
    c = R + "/sys/class/hwmon/hwmon9"
    put(c + "/name", "asus_custom_fan_curve")
    for n in (1, 2):
        put(f"{c}/pwm{n}_enable", 2)
        for i in range(1, 9):
            put(f"{c}/pwm{n}_auto_point{i}_temp", 40 + i * 5)
            put(f"{c}/pwm{n}_auto_point{i}_pwm", i * 12)
    put(R + "/sys/class/hwmon/hwmon5/name", "k10temp")
    put(R + "/sys/class/hwmon/hwmon5/temp1_input", 54000)
    b = R + "/sys/class/power_supply/BAT0"
    put(b + "/type", "Battery")
    put(b + "/capacity", 76)
    put(b + "/status", "Charging")
    put(b + "/charge_control_end_threshold", 80)
    put(R + "/sys/class/power_supply/ADP0/type", "Mains")
    put(R + "/sys/class/power_supply/ADP0/online", 1)
    put(R + hw.KBD_LED + "/brightness", 3)
    put(R + hw.KBD_LED + "/max_brightness", 3)
    put(R + hw.DMI_PRODUCT, "ROG Strix G713PV_G713PV")
    for n, desc in ((0, b"\x05\x01\x85\x01\x81\x00"), (1, b"\x06\x31\xff\x85\x5d\xb1\x02")):
        put(f"{R}/sys/class/hidraw/hidraw{n}/device/uevent",
            "HID_ID=0003:00000B05:000019B6\nHID_NAME=ASUSTek Computer Inc. N-KEY Device")
        with open(f"{R}/sys/class/hidraw/hidraw{n}/device/report_descriptor", "wb") as f:
            f.write(desc)
    put(R + "/dev/hidraw1", "")
    put(R + ryzen.CPUINFO, "processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 25\n"
        "model\t\t: 97\nmodel name\t: AMD Ryzen 9 7845HX with Radeon Graphics\n\nprocessor\t: 1\n")
    put(R + ryzen.ROOT_COMPLEX, "")


class Base(unittest.TestCase):
    def setUp(self):
        build_tree()
        try:
            os.remove(os.environ["BOREAS_CONFIG"])
        except FileNotFoundError:
            pass
        daemon.time.sleep = lambda s: None  # skip the waits


class TestFans(Base):
    def test_fix_curve_monotonic(self):
        c = fans.fix_curve([30, 60, 50, 70, 80, 90, 100, 95, 10, 5, 20, 30, 40, 50, 60, 120])
        self.assertEqual(c[:8], [30, 60, 60, 70, 80, 90, 100, 100])
        self.assertEqual(c[8:], [10, 10, 20, 30, 40, 50, 60, 100])

    def test_fix_curve_safety_floor(self):
        # above 70°C speed >= (t - 70) * 1.2
        c = fans.fix_curve([30, 40, 50, 60, 70, 80, 90, 100, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(c[8:], [0, 0, 0, 0, 0, 12, 24, 36])

    def test_write_curve_rejects_all_zero(self):
        with self.assertRaises(ValueError):
            fans.write_curve("cpu", [30, 40, 50, 60, 61, 62, 63, 64] + [0] * 8)
        self.assertEqual(get(R + "/sys/class/hwmon/hwmon9/pwm1_enable"), "2")

    def test_pwm_scale(self):
        self.assertEqual(fans.pct_to_pwm(100), 255)
        self.assertEqual(fans.pct_to_pwm(50), 128)
        self.assertEqual(fans.pwm_to_pct(255), 100)

    def test_write_and_read_curve(self):
        curve = fans.DEFAULTS[hw.TURBO]["cpu"]
        fans.write_curve("cpu", curve)
        c = R + "/sys/class/hwmon/hwmon9"
        self.assertEqual(get(c + "/pwm1_enable"), "1")
        self.assertEqual(get(c + "/pwm1_auto_point8_temp"), "98")
        self.assertEqual(get(c + "/pwm1_auto_point8_pwm"), str(round(90 * 255 / 100)))
        self.assertEqual(fans.read_curve("cpu"), curve)

    def test_available(self):
        self.assertEqual(fans.available_fans(), ["cpu", "gpu"])


class TestHw(Base):
    def test_attr_clamped(self):
        hw.set_attr("ppt_pl1_spl", 500)
        self.assertEqual(get(f"{ARM}/ppt_pl1_spl/current_value"), "130")
        with self.assertRaises(ValueError):
            hw.set_attr("gpu_mux_mode", 5)

    def test_legacy_fallback(self):
        shutil.rmtree(ARM + "/panel_overdrive")
        put(R + hw.NB_WMI + "/panel_od", 0)
        self.assertEqual(hw.get_attr("panel_overdrive").current, 0)

    def test_status_bits(self):
        self.assertTrue(hw.ac_online())
        self.assertEqual(hw.fan_rpms(), {"cpu": 4000, "gpu": 4300})
        self.assertEqual(hw.temps()["cpu"], 54)
        self.assertEqual(hw.battery_info()["limit"], 80)
        hw.set_charge_limit(20)
        self.assertEqual(get(R + "/sys/class/power_supply/BAT0/charge_control_end_threshold"), "40")


class TestGpu(Base):
    def test_state(self):
        self.assertEqual(gpu.state()["mode"], gpu.STANDARD)
        put(f"{ARM}/dgpu_disable/current_value", 1)
        self.assertEqual(gpu.state()["mode"], gpu.ECO)
        put(f"{ARM}/gpu_mux_mode/current_value", 0)
        self.assertEqual(gpu.state()["mode"], gpu.ULTIMATE)

    def test_busy_defers_eco(self):
        orig = gpu._nvidia_users
        gpu._nvidia_users = lambda ignore=(): ["kwin_wayland(1234)"]
        gpu._run = lambda *a: True
        try:
            d = daemon.Daemon(config.Config())
            r = d.apply_gpu_target(gpu.ECO)
            self.assertTrue(r["deferred"])
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        finally:
            gpu._nvidia_users = orig

    def test_deferred_eco_retried(self):
        users = [["kwin_wayland(1234)"]]
        orig = gpu._nvidia_users
        gpu._nvidia_users = lambda ignore=(): users[0]
        gpu._run = lambda *a: True
        try:
            d = daemon.Daemon(config.Config())
            d.handle({"cmd": "set_gpu", "target": gpu.ECO})
            self.assertTrue(d.eco_pending())
            users[0] = []  # the session ended
            for _ in range(daemon.ECO_RETRY_TICKS):
                d.tick()
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")
            self.assertFalse(d.eco_pending())
        finally:
            gpu._nvidia_users = orig

    def test_ultimate_needs_reboot(self):
        gpu._run = lambda *a: True
        d = daemon.Daemon(config.Config())
        self.assertTrue(d.apply_gpu_target(gpu.ULTIMATE, explicit=True)["reboot"])
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")


    def test_pending_reboot_plain_file_keeps_ultimate_target(self):
        # Kernel 7.3 asus-armoury: pending_reboot is a plain file, not a directory
        gpu._run = lambda *a: True
        shutil.rmtree(f"{ARM}/pending_reboot")
        put(f"{ARM}/pending_reboot", 1)
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.ULTIMATE  # MUX written, waiting for a reboot
        self.assertTrue(gpu.state()["pending_reboot"])
        d.reapply()
        self.assertEqual(d.cfg["gpu_target"], gpu.ULTIMATE)

    def test_boot_adopts_external_mux_change(self):
        # at boot the MUX is not written; the hardware mode is adopted
        gpu._run = lambda *a: True
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.ECO
        put(f"{ARM}/gpu_mux_mode/current_value", 0)  # Ultimate was chosen in the BIOS
        d.reapply(at_boot=True)
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")
        self.assertEqual(d.cfg["gpu_target"], gpu.ULTIMATE)

        d.cfg["gpu_target"] = gpu.ULTIMATE
        put(f"{ARM}/gpu_mux_mode/current_value", 1)
        d.reapply(at_boot=True)
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "1")
        self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)

    def test_optimized_unplug_keeps_ultimate(self):
        gpu._run = lambda *a: True
        put(f"{ARM}/gpu_mux_mode/current_value", 0)
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.OPTIMIZED
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        d.tick()
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")
        self.assertEqual(get(f"{ARM}/pending_reboot/current_value"), "0")


    def _gpu_calls(self, run_ok=lambda cmd: True):
        calls = []
        gpu._run = lambda *cmd: (calls.append(cmd), run_ok(cmd))[1]
        return calls

    def test_busy_eco_does_not_bounce_services(self):
        # while KWin holds the dGPU, nvidia-persistenced must not be stopped and started on every try
        calls = self._gpu_calls()
        orig = gpu._nvidia_users
        gpu._nvidia_users = lambda ignore=(): ["kwin_wayland(1234)"]
        try:
            d = daemon.Daemon(config.Config())
            d.handle({"cmd": "set_gpu", "target": gpu.ECO})
            for _ in range(daemon.ECO_RETRY_TICKS * 2):
                d.tick()
        finally:
            gpu._nvidia_users = orig
        self.assertFalse([c for c in calls if c[:2] == ("systemctl", "stop")])

    def test_module_unload_failure_restores_and_stops_retry(self):
        for m in gpu.NV_MODULES:
            put(R + f"/sys/module/{m}/refcnt", 0)
        calls = self._gpu_calls(lambda cmd: cmd[:2] != ("modprobe", "-r"))
        orig = gpu._nvidia_users
        gpu._nvidia_users = lambda ignore=(): []
        try:
            d = daemon.Daemon(config.Config())
            r = d.handle({"cmd": "set_gpu", "target": gpu.ECO})
            self.assertTrue(r["deferred"])
            self.assertIn(("systemctl", "start", "nvidia-persistenced.service"), calls)
            n = len(calls)
            for _ in range(daemon.ECO_RETRY_TICKS * 2):
                d.tick()
            self.assertEqual(len(calls), n)  # do not try to unload the driver every 15 s
        finally:
            gpu._nvidia_users = orig
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def _fake_dgpu(self):
        d = R + "/sys/bus/pci/devices/0000:01:00.0"
        put(d + "/vendor", "0x10de")
        put(d + "/class", "0x030000")
        os.makedirs(d + "/drm/card1", exist_ok=True)
        for m in gpu.NV_MODULES:
            put(R + f"/sys/module/{m}/refcnt", 1)

    def _fake_proc(self, procs, fuser=False):
        """procs: {pid: (command, [open files])}. fuser=False: look at /proc as if fuser were missing."""
        proc = TMP + "/proc"
        shutil.rmtree(proc, ignore_errors=True)
        for pid, (comm, files) in procs.items():
            put(f"{proc}/{pid}/comm", comm)
            os.makedirs(f"{proc}/{pid}/fd")
            for i, path in enumerate(files):
                os.symlink(path, f"{proc}/{pid}/fd/{i}")
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(gpu, "PROC", proc))
        if not fuser:
            stack.enter_context(patch.object(gpu, "_fuser_pids", lambda nodes: None))
        return stack

    def test_fuser_pids_read_from_stdout(self):
        # fuser writes the PIDs to stdout and the file names and access letters to stderr
        self._fake_dgpu()
        seen = []

        def run(cmd, *a, **k):
            seen.append(cmd)
            return gpu.subprocess.CompletedProcess(
                cmd, 0, stdout="   883  1510  1651", stderr="/dev/nvidia0:  F.... \n/dev/dri/card1: m\n")
        procs = {883: ("nvidia-powerd", []), 1510: ("kwin_wayland", []), 1651: ("Xwayland", [])}
        with self._fake_proc(procs, fuser=True), patch.object(gpu.subprocess, "run", run):
            self.assertEqual(gpu._nvidia_users(ignore=gpu.NV_SERVICE_COMMANDS),
                             ["Xwayland(1651)", "kwin_wayland(1510)"])
        self.assertEqual(seen[0][0], "fuser")
        self.assertIn("/dev/dri/card1", seen[0])

    def test_nvidia_users_without_fuser_reads_proc(self):
        # without fuser (psmisc), /proc/PID/fd is used;
        # KWin counts; services (nvidia-powerd) and processes not touching the dGPU do not
        self._fake_dgpu()
        procs = {
            1510: ("kwin_wayland", ["/dev/null", "/dev/dri/card1"]),
            1651: ("Xwayland", ["/dev/dri/renderD129"]),
            883: ("nvidia-powerd", ["/dev/nvidia0", "/dev/nvidiactl"]),
            900: ("firefox", ["/dev/nvidia-caps/nvidia-cap1"]),
            42: ("bash", ["/dev/pts/0", "/dev/dri/card0"]),
        }
        os.makedirs(R + "/sys/bus/pci/devices/0000:01:00.0/drm/renderD129", exist_ok=True)
        with self._fake_proc(procs):
            self.assertEqual(gpu._nvidia_users(ignore=gpu.NV_SERVICE_COMMANDS),
                             ["Xwayland(1651)", "firefox(900)", "kwin_wayland(1510)"])
            self.assertIn("nvidia-powerd(883)", gpu._nvidia_users())

    def test_busy_message_names_kwin(self):
        # if KWin holds the dGPU, postpone without touching the driver and retry every 15 s
        self._fake_dgpu()
        calls = self._gpu_calls()
        with self._fake_proc({1510: ("kwin_wayland", ["/dev/dri/card1"])}):
            d = daemon.Daemon(config.Config())
            r = d.handle({"cmd": "set_gpu", "target": gpu.ECO})
        self.assertEqual(r["busy"], ["kwin_wayland(1510)"])
        self.assertFalse(d.eco_stuck)
        self.assertFalse([c for c in calls if c[0] == "modprobe"])
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_eco_stops_nvidia_powerd(self):
        # nvidia-powerd keeps the dGPU open: stop it and apply Eco, start it again when leaving Eco
        self._fake_dgpu()
        running = {"nvidia-powerd.service"}

        def run_ok(cmd):
            if cmd[:3] == ("systemctl", "is-active", "--quiet"):
                return cmd[3] in running
            if cmd[:2] == ("systemctl", "stop"):
                running.discard(cmd[2])
            return True
        calls = self._gpu_calls(run_ok)
        orig = gpu._nvidia_users

        def users(ignore=()):
            if "nvidia-powerd.service" not in running:  # its process ends when the service stops
                shutil.rmtree(TMP + "/proc/883", ignore_errors=True)
            return orig(ignore)
        with self._fake_proc({883: ("nvidia-powerd", ["/dev/nvidia0"])}), \
                patch.object(gpu, "_nvidia_users", users):
            gpu.set_eco(True)
        self.assertIn(("systemctl", "stop", "nvidia-powerd.service"), calls)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")

    def test_failed_eco_write_brings_dgpu_back(self):
        # if dgpu_disable cannot be written after the device was removed, the dGPU must not vanish
        self._fake_dgpu()
        calls = self._gpu_calls()
        real_set = hw.set_attr

        def set_attr(name, value):
            if name == "dgpu_disable":
                raise OSError(5, "Input/output error")
            return real_set(name, value)
        with patch.object(gpu, "_nvidia_users", lambda ignore=(): []), \
                patch.object(gpu.hw, "set_attr", set_attr):
            with self.assertRaises(OSError):
                gpu.set_eco(True)
        self.assertTrue(os.path.exists(R + "/sys/bus/pci/rescan"))  # the device was rescanned
        unload = calls.index(("modprobe", "-r", "nvidia"))
        self.assertIn(("modprobe", "nvidia_drm"), calls[unload:])  # the driver was reloaded
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def _bridge(self, name, child=False, control="auto"):
        d = R + f"/sys/bus/pci/devices/{name}"
        put(d + "/secondary_bus_number", 1)
        put(d + "/power/control", control)
        # real ports always have these, even with no device (G713PV: pcie002, pcie010)
        for svc in ("pcie002", "pcie010"):
            os.makedirs(f"{d}/{name}:{svc}", exist_ok=True)
        os.makedirs(d + "/pci_bus/0000:01", exist_ok=True)
        if child:
            os.makedirs(d + "/0000:02:00.0", exist_ok=True)
        return d

    def test_leaving_eco_wakes_sleeping_port(self):
        # G713PV: with the port asleep, dgpu_disable=0 waited 30 s in the firmware and failed with EIO
        put(f"{ARM}/dgpu_disable/current_value", 1)
        port = self._bridge("0000:00:01.1")             # empty below in Eco: the dGPU's port
        busy = self._bridge("0000:00:02.1", child=True)  # has a device below: left alone
        self._gpu_calls()
        seen = {}
        real_set = hw.set_attr

        def set_attr(name, value):
            if name == "dgpu_disable":
                seen["port"] = get(port + "/power/control")
                seen["busy"] = get(busy + "/power/control")
            return real_set(name, value)
        with patch.object(gpu.hw, "set_attr", set_attr):
            gpu.set_eco(False)
        self.assertEqual(seen, {"port": "on", "busy": "auto"})
        self.assertEqual(get(port + "/power/control"), "auto")  # power management is back on
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_port_restored_when_firmware_fails(self):
        put(f"{ARM}/dgpu_disable/current_value", 1)
        port = self._bridge("0000:00:01.1")
        self._gpu_calls()

        def set_attr(name, value):
            raise OSError(5, "Input/output error")
        with patch.object(gpu.hw, "set_attr", set_attr):
            with self.assertRaises(OSError):
                gpu.set_eco(False)
        self.assertEqual(get(port + "/power/control"), "auto")

    def test_sysfs_write_is_attempted_once(self):
        # with open() an error repeated the write on close (2 x 30 s of firmware waiting)
        tries = []

        def fail(fd, data):
            tries.append(data)
            raise OSError(5, "Input/output error")
        with patch.object(hw.os, "write", fail):
            with self.assertRaises(OSError):
                hw.write(hw.ARMOURY + "/dgpu_disable/current_value", 0)
        self.assertEqual(tries, [b"0\n"])

    def test_gpu_error_at_boot_does_not_crash(self):
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.STANDARD
        put(f"{ARM}/dgpu_disable/current_value", 1)
        with patch.object(gpu, "set_eco", side_effect=OSError(5, "Input/output error")):
            d.reapply(at_boot=True)  # the service must not crash at boot
            self.assertIn("Could not change the GPU mode", d.notice)
            with self.assertRaises(OSError):  # if the user chose it, the error goes back to them
                d.apply_gpu_target(gpu.STANDARD, explicit=True)

    def test_resume_after_unplug_applies_optimized(self):
        # the automatic GPU mode runs on resume; if unplugged during sleep, switch to Eco
        self._gpu_calls()
        orig = gpu._nvidia_users
        gpu._nvidia_users = lambda ignore=(): []
        try:
            d = daemon.Daemon(config.Config())
            d.cfg["gpu_target"] = gpu.OPTIMIZED
            put(R + "/sys/class/power_supply/ADP0/online", 0)
            d.handle({"cmd": "reapply"})
        finally:
            gpu._nvidia_users = orig
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")

    def test_gpu_temp_not_polled_on_battery_hybrid(self):
        # nvidia-smi keeps the dGPU awake; never query it on battery in hybrid mode
        put(R + "/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
        put(R + "/sys/bus/pci/devices/0000:01:00.0/class", "0x030000")
        put(R + "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status", "active")
        ran = []
        orig = daemon.subprocess.run
        daemon.subprocess.run = lambda *a, **k: (
            ran.append(a), daemon.subprocess.CompletedProcess(a, 0, stdout="50\n"))[1]
        try:
            d = daemon.Daemon(config.Config())
            put(R + "/sys/class/power_supply/ADP0/online", 0)
            self.assertIsNone(d.gpu_temp())
            put(R + "/sys/class/power_supply/ADP0/online", 1)
            d.gpu_temp_cache = (0.0, None)
            d.gpu_temp()
        finally:
            daemon.subprocess.run = orig
        self.assertEqual(len(ran), 1)

    def test_rejects_unknown_gpu_and_mode(self):
        d = daemon.Daemon(config.Config())
        with self.assertRaises(ValueError):
            d.handle({"cmd": "set_gpu", "target": "foo"})
        with self.assertRaises(ValueError):
            d.handle({"cmd": "set_mode", "mode": 42})
        self.assertNotEqual(d.cfg["gpu_target"], "foo")
        self.assertNotEqual(d.cfg["mode_ac"], 42)


class FakeSmn:
    """SMU mailbox: fills the response register when a command is written."""

    def __init__(self, fam, statuses=None, idle=ryzen.OK, read_limit=None, follow_writes=True):
        self.fam, self.statuses = fam, statuses or {}
        self.regs = {fam.rsp: idle}
        self.sent = []
        self.limit = read_limit
        self.follow_writes = follow_writes
        self.limit_queries = 0

    def read(self, fd, addr):
        return self.regs.get(addr, 0)

    def write(self, fd, addr, value):
        self.regs[addr] = value
        if addr != self.fam.msg:
            return
        if value == self.fam.get_limit:
            self.limit_queries += 1
            self.regs[self.fam.rsp] = self.statuses.get(
                value, ryzen.OK if self.limit is not None else ryzen.UNKNOWN_CMD)
            self.regs[self.fam.arg] = (55 << 16) | (self.limit or 0)
            return
        self.sent.append((value, self.regs.get(self.fam.arg, 0)))
        status = self.statuses.get(value, ryzen.OK)
        if value == ryzen.GET_SMU_VERSION and status == ryzen.OK:
            self.regs[self.fam.arg] = 0x4C4D00
        if value == self.fam.tctl and status == ryzen.OK and self.follow_writes and self.limit is not None:
            self.limit = self.regs[self.fam.arg]
        self.regs[self.fam.rsp] = status


def fake_smu(cpu, **kw):
    smn = FakeSmn(cpu.family, **kw)
    cpu.smu._read, cpu.smu._write = smn.read, smn.write
    return smn


class TestCpuTemp(Base):
    def test_detect_dragon_range(self):
        self.assertEqual(ryzen.detect().name, "Dragon Range")
        put(R + ryzen.CPUINFO, "vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 183\n")
        self.assertIsNone(ryzen.detect())
        put(R + ryzen.CPUINFO, "vendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel\t\t: 116\n")
        self.assertIsNone(ryzen.detect())  # Phoenix: a different mailbox, not supported

    def test_set_probes_version_then_sends_mp1_tctl(self):
        cpu = ryzen.CpuTemp()
        smn = fake_smu(cpu)
        cpu.set(85)
        cpu.set(80)
        # Raphael family: MP1 0x3F, one version query on first use
        self.assertEqual(smn.sent, [(0x02, 0), (0x3F, 85), (0x3F, 80)])
        self.assertEqual(cpu.version_str(), "76.77.0")
        self.assertEqual(cpu.applied, 80)

    def test_probe_failure_never_sends_tctl(self):
        cpu = ryzen.CpuTemp()
        smn = fake_smu(cpu, statuses={0x02: ryzen.UNKNOWN_CMD})
        with self.assertRaises(ryzen.SmuError):
            cpu.set(85)
        self.assertEqual(smn.sent, [(0x02, 0)])
        self.assertFalse(cpu.supported())

    def test_busy_mailbox_is_not_written(self):
        cpu = ryzen.CpuTemp()
        smn = fake_smu(cpu, idle=0)
        with self.assertRaises(ryzen.SmuError):
            cpu.set(85)
        self.assertEqual(smn.sent, [])

    def _daemon(self, temp, **smu_options):
        d = daemon.Daemon(config.Config())
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = temp
        smn = fake_smu(d.cpu, **smu_options)
        clock = [100.0]
        timer = patch.object(daemon.time, "monotonic", side_effect=lambda: clock[0])
        timer.start()
        self.addCleanup(timer.stop)
        return d, smn, clock

    def test_mode_applies_and_reapplies_after_firmware_reset(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        self.assertEqual(smn.sent[-1], (0x3F, 85))
        n = len(smn.sent)
        clock[0] += daemon.CPU_TEMP_REAPPLY - 0.1
        d.tick()
        self.assertEqual(len(smn.sent), n)
        # Even if the firmware resets the target twice, the watcher sends it again.
        for _ in range(2):
            smn.regs[smn.fam.arg] = 96
            clock[0] += daemon.CPU_TEMP_REAPPLY
            d.tick()
            self.assertEqual(smn.regs[smn.fam.arg], 85)
        self.assertEqual(len(smn.sent), n + 2)

    def test_readback_stops_writes_and_restores_drift_even_when_cpu_is_cool(self):
        d, smn, clock = self._daemon(85, read_limit=96)
        d.apply_mode(hw.BALANCED)
        self.assertTrue(d.cpu.readback_verified)
        n = len(smn.sent)
        # A high temperature does not mean the limit was cleared.
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 94000)
        for _ in range(4):
            clock[0] += 1
            d.tick()
        self.assertEqual(len(smn.sent), n)
        self.assertEqual(d.status()["cpu_temp"]["control"], "readback")
        smn.limit = 96  # firmware reset
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 54000)
        clock[0] += 1
        d.tick()
        self.assertEqual(smn.limit, 85)
        self.assertEqual(len(smn.sent), n + 1)

    def test_static_bios_value_never_enables_readback(self):
        d, smn, clock = self._daemon(85, read_limit=96, follow_writes=False)
        d.apply_mode(hw.BALANCED)
        self.assertFalse(d.cpu.readback_verified)
        self.assertEqual(d.cpu.reported_limit, 96)
        n = len(smn.sent)
        clock[0] += 1
        d.tick()
        self.assertEqual(len(smn.sent), n)
        clock[0] += 2
        d.tick()
        self.assertEqual(len(smn.sent), n + 1)
        self.assertEqual(d.status()["cpu_temp"]["control"], "adaptive")

    def test_already_matching_readback_needs_observed_transition(self):
        d, smn, clock = self._daemon(85, read_limit=85)
        d.apply_mode(hw.BALANCED)
        self.assertFalse(d.cpu.readback_verified)
        d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": 80}})
        self.assertTrue(d.cpu.readback_verified)
        self.assertEqual(d.cpu.reported_limit, 80)

    def test_readback_failure_falls_back_without_disabling_writes(self):
        d, smn, clock = self._daemon(85, read_limit=96)
        d.apply_mode(hw.BALANCED)
        smn.statuses[d.cpu.family.get_limit] = ryzen.UNKNOWN_CMD
        clock[0] += 3
        d.tick()
        self.assertTrue(d.cpu.supported())
        self.assertFalse(d.cpu.readback_verified)
        st = d.status()["cpu_temp"]
        self.assertIsNone(st["reported_limit"])
        self.assertIn("unknown", st["readback_error"])
        self.assertEqual(smn.sent[-1], (0x3F, 85))

    def test_invalid_readback_is_not_used(self):
        d, smn, clock = self._daemon(85, read_limit=0, follow_writes=False)
        d.apply_mode(hw.BALANCED)
        self.assertFalse(d.cpu.readback_verified)
        self.assertIsNone(d.cpu.reported_limit)
        self.assertTrue(d.cpu.supported())
        self.assertIn("Invalid", d.cpu.readback_error)

    def test_adaptive_intervals_follow_temperature_changes_immediately(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        # Even with the first 3 s interval set, a sudden +5°C overshoot refreshes within 1 s.
        for current, delay in ((90000, 1), (87000, 2), (86000, 3), (54000, 3)):
            put(R + "/sys/class/hwmon/hwmon5/temp1_input", current)
            n = len(smn.sent)
            clock[0] += delay - 0.1
            d.tick()
            self.assertEqual(len(smn.sent), n)
            clock[0] += 0.11
            d.tick()
            self.assertEqual(len(smn.sent), n + 1)
        os.remove(R + "/sys/class/hwmon/hwmon5/temp1_input")
        n = len(smn.sent)
        clock[0] += 2
        d.tick()
        self.assertEqual(len(smn.sent), n)
        clock[0] += 1
        d.tick()
        self.assertEqual(len(smn.sent), n + 1)

    def test_periodic_reapply_uses_current_mode(self):
        d, smn, clock = self._daemon(85)
        d.cfg.mode(hw.TURBO)["cpu_temp"] = 90
        d.apply_mode(hw.BALANCED)
        d.apply_mode(hw.TURBO)
        n = len(smn.sent)
        clock[0] += daemon.CPU_TEMP_REAPPLY
        d.tick()
        self.assertEqual(len(smn.sent), n + 1)
        self.assertEqual(smn.sent[-1], (0x3F, 90))

    def test_default_untouched_until_lowered(self):
        d, smn, clock = self._daemon(None)
        d.apply_mode(hw.BALANCED)
        self.assertEqual(smn.sent, [])  # never set: do not touch the SMU
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 85
        d.apply_mode(hw.BALANCED)
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = None
        d.apply_mode(hw.BALANCED)
        d.apply_mode(hw.BALANCED)
        # A lowered limit is sent right after the profile and again after the power writes.
        self.assertEqual(smn.sent, [(0x02, 0), (0x3F, 85), (0x3F, 85), (0x3F, 96)])
        clock[0] += daemon.CPU_TEMP_REAPPLY * 2
        d.tick()
        self.assertEqual(smn.sent[-1], (0x3F, 96))
        self.assertEqual(len(smn.sent), 4)

    def test_periodic_failure_retries_after_interval(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        smn.statuses[0x3F] = ryzen.BUSY
        clock[0] += daemon.CPU_TEMP_REAPPLY
        d.tick()
        self.assertIn("busy", d.notice)
        n = len(smn.sent)
        clock[0] += 1
        d.tick()
        self.assertEqual(len(smn.sent), n)
        smn.statuses[0x3F] = ryzen.OK
        clock[0] += daemon.CPU_TEMP_REAPPLY
        d.tick()
        self.assertEqual(len(smn.sent), n + 1)
        self.assertEqual(smn.sent[-1], (0x3F, 85))

    def test_switch_to_default_stops_periodic_writes(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        d.apply_mode(hw.TURBO)
        self.assertEqual(smn.sent[-1], (0x3F, 96))
        n = len(smn.sent)
        clock[0] += daemon.CPU_TEMP_REAPPLY * 3
        d.tick()
        self.assertEqual(len(smn.sent), n)

    def test_update_mode_validates_and_status_reports(self):
        d, smn, later = self._daemon(None)
        with self.assertRaises(ValueError):
            d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": 50}})
        self.assertIsNone(d.cfg.mode(hw.BALANCED)["cpu_temp"])
        d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": 88}})
        st = d.status()["cpu_temp"]
        self.assertEqual((st["applied"], st["min"], st["max"], st["cpu"]), (88, 75, 96, "Dragon Range"))

    def test_gui_temperature_edit_does_not_reset_profile_fans_or_power(self):
        d, smn, clock = self._daemon(85)
        d.cfg.mode(hw.BALANCED).update(power_apply=True, ppt={"ppt_pl1_spl": 55},
                                       fans_apply=True, fan_cpu=fans.DEFAULTS[hw.BALANCED]["cpu"])
        d.apply_mode(hw.BALANCED)
        # The GUI sends all fields; only the CPU temperature changed.
        data = dict(d.cfg.mode(hw.BALANCED), cpu_temp=81)
        with patch.object(hw, "set_profile") as profile, \
             patch.object(fans, "write_curve") as curve, \
             patch.object(hw, "set_attr") as power:
            for temp in (81, 81, None):  # applying again and Default do not reset the profile either
                data["cpu_temp"] = temp
                d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": data})
                self.assertEqual(smn.sent[-1], (0x3F, temp if temp else 96))
            profile.assert_not_called()
            curve.assert_not_called()
            power.assert_not_called()
        self.assertTrue(d.custom_power)

    def test_inactive_temperature_edit_only_saves(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        n = len(smn.sent)
        d.handle({"cmd": "update_mode", "mode": hw.TURBO, "data": {"cpu_temp": 80}})
        self.assertEqual(len(smn.sent), n)
        self.assertEqual(d.cfg.mode(hw.TURBO)["cpu_temp"], 80)

    def test_power_edit_still_reapplies_profile(self):
        d, smn, clock = self._daemon(85)
        with patch.object(hw, "set_profile", wraps=hw.set_profile) as profile:
            d.handle({"cmd": "update_mode", "mode": hw.BALANCED,
                      "data": {"power_apply": True, "ppt": {"ppt_pl1_spl": 50}}})
            profile.assert_called_once_with("balanced")
        self.assertEqual(get(f"{ARM}/ppt_pl1_spl/current_value"), "50")
        self.assertEqual(smn.sent[-1], (0x3F, 85))

    def test_mode_change_restores_limit_before_fans_and_power(self):
        # Writing the profile resets the firmware limit; the CPU must not be left without
        # a limit during the fan/power writes and the wait.
        d, smn, clock = self._daemon(85)
        d.cfg.mode(hw.BALANCED).update(power_apply=True, ppt={"ppt_pl1_spl": 55},
                                       fans_apply=True, fan_cpu=fans.DEFAULTS[hw.BALANCED]["cpu"])
        seen = []
        with patch.object(fans, "write_curve", side_effect=lambda *a: seen.append(list(smn.sent))), \
             patch.object(hw, "set_attr", side_effect=lambda *a: seen.append(list(smn.sent))):
            d.apply_mode(hw.BALANCED)
        self.assertTrue(seen)
        for sent in seen:
            self.assertEqual(sent[-1], (0x3F, 85))
        self.assertEqual(smn.sent[-1], (0x3F, 85))

    def test_limit_rewritten_often_while_firmware_settles(self):
        # G713PV: the firmware resets the limit once more ~3-4 s after a profile write.
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        tctl = lambda: sum(1 for cmd, _ in smn.sent if cmd == 0x3F)
        fake_sleep = patch.object(daemon.time, "sleep",
                                  side_effect=lambda s: clock.__setitem__(0, clock[0] + s))
        n = tctl()
        with fake_sleep:
            d.settle_sleep(daemon.POLL_SECONDS)
        self.assertEqual(tctl() - n, round(daemon.POLL_SECONDS / daemon.CPU_TEMP_SETTLE_STEP))
        clock[0] += daemon.CPU_TEMP_SETTLE
        n = tctl()
        with fake_sleep:
            d.settle_sleep(daemon.POLL_SECONDS)
        self.assertEqual(tctl(), n)

    def test_settle_does_not_touch_default_limit(self):
        d, smn, clock = self._daemon(None)
        d.apply_mode(hw.BALANCED)
        with patch.object(daemon.time, "sleep",
                          side_effect=lambda s: clock.__setitem__(0, clock[0] + s)):
            d.settle_sleep(daemon.POLL_SECONDS)
        self.assertEqual(smn.sent, [])

    def _hot_run(self, d, clock, temps):
        lines = []
        with patch.object(daemon, "log", side_effect=lines.append):
            for t in temps:
                put(R + "/sys/class/hwmon/hwmon5/temp1_input", t * 1000)
                clock[0] += 1
                d.track_temp()
        return lines

    def test_hot_episode_logged_once_with_peak(self):
        d, smn, clock = self._daemon(None)
        lines = self._hot_run(d, clock, [70, 94, 96, 99, 97, 93, 92, 70])
        hot = [l for l in lines if "°C" in l]
        self.assertEqual(len(hot), 2, hot)  # start + end, not every second
        self.assertIn("96°C", hot[0])
        self.assertIn("threshold 95", hot[0])
        self.assertIn("peak 99°C", hot[1])
        self.assertEqual(d.status()["temp_peak"]["max"], 99)

    def test_hot_threshold_follows_lowered_limit(self):
        d, smn, clock = self._daemon(80)
        d.apply_mode(hw.BALANCED)
        lines = self._hot_run(d, clock, [84, 86, 80])
        hot = [l for l in lines if "°C" in l]
        self.assertEqual(len(hot), 2, hot)
        self.assertIn("threshold 85", hot[0])

    def test_normal_temperature_not_logged(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        lines = self._hot_run(d, clock, [60, 85, 89, 70])
        self.assertEqual([l for l in lines if "high temperature" in l], [])
        self.assertEqual(d.status()["temp_peak"]["max"], 89)

    def test_other_smu_writer_is_reported(self):
        d, smn, clock = self._daemon(85)
        put(R + "/proc/4242/cmdline",
            "/usr/bin/bash\0-c\0while true; do /usr/bin/ryzenadj --tctl-temp=85; sleep 3; done\0")
        put(R + "/proc/77/cmdline", "/usr/bin/python3\0/usr/bin/boreasd\0")
        self.addCleanup(shutil.rmtree, R + "/proc/4242", True)
        self.addCleanup(shutil.rmtree, R + "/proc/77", True)
        self.assertEqual(ryzen.other_writers(), [4242])
        d.tick()
        self.assertEqual(d.status()["cpu_temp"]["conflict"], [4242])
        self.assertIn("ryzenadj", d.notice)
        shutil.rmtree(R + "/proc/4242")
        d.ticks = daemon.CONFLICT_CHECK_TICKS
        d.tick()
        self.assertEqual(d.status()["cpu_temp"]["conflict"], [])

    def test_unsupported_cpu_hidden(self):
        put(R + ryzen.CPUINFO, "vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 183\n")
        d = daemon.Daemon(config.Config())
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 85
        d.apply_mode(hw.BALANCED)
        self.assertIsNone(d.status()["cpu_temp"])
        self.assertEqual(d.notice, "")


class TestAura(Base):
    def test_message(self):
        m = aura.message("static", (255, 0, 0), speed="normal")
        self.assertEqual(list(m), [0x5D, 0xB3, 0, 0, 255, 0, 0, 0xEB, 0, 0, 255, 0, 0])
        self.assertEqual(aura.message("breathe", (0, 0, 0))[9], 0xFF)

    def test_find_device(self):
        self.assertEqual(aura.find_device(), "/dev/hidraw1")  # the interface with report 0x5D

    def test_apply_writes_output_reports(self):
        # HID output report, padded to 64 bytes
        open(R + "/dev/hidraw1", "w").close()
        aura.apply("static", (255, 0, 0))
        with open(R + "/dev/hidraw1", "rb") as f:
            data = f.read()
        self.assertEqual(len(data), 3 * 64)
        self.assertEqual(data[:5], bytes([0x5D, 0xB3, 0, 0, 255]))
        self.assertEqual(data[64:69], bytes([0x5D, 0xB5, 0, 0, 0]))
        self.assertEqual(data[128:130], bytes([0x5D, 0xB4]))


class TestConfig(Base):
    def test_modes(self):
        c = config.Config()
        self.assertEqual(c.mode_list(), [2, 0, 1])
        new = c.add_mode(hw.TURBO)
        self.assertEqual(new, 3)
        self.assertEqual(c.mode(3)["base"], hw.TURBO)
        self.assertEqual(c.next_mode(1), 3)
        self.assertEqual(c.next_mode(3), 2)
        c["mode_ac"] = 3
        c.remove_mode(3)
        self.assertEqual(c["mode_ac"], hw.BALANCED)

    def test_persist(self):
        c = config.Config()
        c["charge_limit"] = 60
        c.mode(1)["fans_apply"] = True
        c.save()
        c2 = config.Config()
        self.assertEqual(c2["charge_limit"], 60)
        self.assertTrue(c2.mode(1)["fans_apply"])


class TestDaemon(Base):
    def make(self):
        return daemon.Daemon(config.Config())

    def test_apply_turbo_with_fans_and_power(self):
        d = self.make()
        m = d.cfg.mode(hw.TURBO)
        m.update(fans_apply=True, fan_cpu=fans.DEFAULTS[1]["cpu"], fan_gpu=fans.DEFAULTS[1]["gpu"],
                 power_apply=True, ppt={"ppt_pl1_spl": 100}, nv={"nv_dynamic_boost": 20})
        d.set_mode(hw.TURBO)
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "performance")
        self.assertEqual(get(R + "/sys/class/hwmon/hwmon9/pwm2_enable"), "1")
        self.assertEqual(get(f"{ARM}/ppt_pl1_spl/current_value"), "100")
        self.assertEqual(get(f"{ARM}/nv_dynamic_boost/current_value"), "20")
        self.assertTrue(d.custom_power)
        self.assertEqual(config.Config()["mode_ac"], hw.TURBO)

    def test_mode_without_fans_disables_curve(self):
        d = self.make()
        put(R + "/sys/class/hwmon/hwmon9/pwm1_enable", 1)
        d.set_mode(hw.SILENT)
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "quiet")
        self.assertEqual(get(R + "/sys/class/hwmon/hwmon9/pwm1_enable"), "2")

    def test_external_profile_change(self):
        d = self.make()
        put(R + hw.PLATFORM_PROFILE, "performance")  # Fn+F5
        d.tick()
        self.assertEqual(d.cfg["mode"], hw.TURBO)
        self.assertEqual(d.cfg["mode_ac"], hw.TURBO)

    def test_custom_mode_survives_same_base(self):
        d = self.make()
        new = d.cfg.add_mode(hw.TURBO)
        d.set_mode(new)
        put(R + hw.PLATFORM_PROFILE, "performance")
        d.tick()
        self.assertEqual(d.cfg["mode"], new)

    def test_unplug_switches_mode(self):
        d = self.make()
        d.cfg["mode_battery"] = hw.SILENT
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        d.tick()
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "quiet")
        self.assertEqual(d.cfg["mode"], hw.SILENT)

    def test_charger_bounce_ignored(self):
        # wait until the power source settles; do nothing if it switched back
        d = self.make()
        d.cfg["mode_battery"] = hw.SILENT
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        daemon.time.sleep = lambda s: put(R + "/sys/class/power_supply/ADP0/online", 1)
        d.tick()
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "balanced")
        self.assertEqual(d.cfg["mode"], hw.BALANCED)

    def test_power_forces_fans_on_fan_required_model(self):
        # on the G713P and similar models the power limits need a custom fan curve
        d = self.make()
        d.cfg.mode(hw.TURBO).update(power_apply=True, ppt={"ppt_pl1_spl": 100})
        d.set_mode(hw.TURBO)
        self.assertEqual(get(R + "/sys/class/hwmon/hwmon9/pwm1_enable"), "1")
        self.assertEqual(get(f"{ARM}/ppt_pl1_spl/current_value"), "100")

        put(R + hw.DMI_PRODUCT, "ROG Zephyrus G14 GA401QM")
        put(R + "/sys/class/hwmon/hwmon9/pwm1_enable", 2)
        d.set_mode(hw.TURBO)
        self.assertEqual(get(R + "/sys/class/hwmon/hwmon9/pwm1_enable"), "2")

    def test_profile_always_written_without_toggle(self):
        # the profile is written every time (the firmware resets the power limits)
        d = self.make()
        d.cfg.mode(hw.TURBO).update(power_apply=True, ppt={"ppt_pl1_spl": 100})
        d.set_mode(hw.TURBO)
        new = d.cfg.add_mode(hw.TURBO)
        d.cfg.mode(new)["power_apply"] = False
        calls = []
        orig = hw.set_profile
        hw.set_profile = lambda v: (calls.append(v), orig(v))
        try:
            d.set_mode(new)
        finally:
            hw.set_profile = orig
        self.assertEqual(calls, ["performance"])
        self.assertFalse(d.custom_power)

    def test_reapply_extras(self):
        d = self.make()
        d.cfg["charge_limit"] = 60
        d.cfg["kbd_brightness"] = 1
        d.cfg["overdrive"] = 0
        d.reapply()
        self.assertEqual(get(R + "/sys/class/power_supply/BAT0/charge_control_end_threshold"), "60")
        self.assertEqual(get(R + hw.KBD_LED + "/brightness"), "1")
        self.assertEqual(get(f"{ARM}/panel_overdrive/current_value"), "0")

    def test_update_mode_fixes_curve(self):
        d = self.make()
        r = d.handle({"cmd": "update_mode", "mode": 0,
                      "data": {"fan_cpu": [30, 20, 50, 60, 70, 80, 90, 100, 0, 0, 0, 0, 0, 0, 0, 0],
                               "base": 1}})
        self.assertEqual(r["mode"]["fan_cpu"][1], 30)
        self.assertEqual(r["mode"]["base"], 0)

    def test_first_run_imports_hardware(self):
        put(R + hw.PLATFORM_PROFILE, "performance")
        put(f"{ARM}/gpu_mux_mode/current_value", 0)
        d = self.make()
        self.assertEqual(d.cfg["charge_limit"], 80)
        self.assertEqual(d.cfg["mode"], hw.TURBO)
        self.assertEqual(d.cfg["gpu_target"], gpu.ULTIMATE)
        self.assertFalse(config.Config().fresh)
        # the first start must not touch the GPU
        self.assertEqual(d.apply_gpu_target(d.cfg["gpu_target"]), {})

    def test_status_shape(self):
        s = self.make().status()
        self.assertEqual(s["fans"], {"cpu": 4000, "gpu": 4300})
        self.assertEqual(s["gpu"]["mode"], gpu.STANDARD)
        self.assertIn("ppt_pl1_spl", s["attrs"])
        self.assertTrue(s["aura_device"])
        json.dumps(s)


class TestBoot(Base):
    def test_waits_for_asus_drivers(self):
        # udev loads asus_wmi late at boot (~5 s); the service may start before it
        shutil.rmtree(R + "/sys/class/firmware-attributes")
        os.remove(R + hw.PLATFORM_PROFILE)
        steps = []

        def fake_sleep(s):
            steps.append(s)
            if len(steps) == 3:
                build_tree()
        daemon.time.sleep = fake_sleep
        self.assertTrue(daemon.wait_for_hardware(timeout=10))
        self.assertEqual(len(steps), 3)

    def test_wait_gives_up(self):
        shutil.rmtree(R + "/sys/class/firmware-attributes")
        os.remove(R + hw.PLATFORM_PROFILE)
        now = [0.0]
        daemon.time.sleep = lambda s: now.__setitem__(0, now[0] + s)
        orig = daemon.time.monotonic
        daemon.time.monotonic = lambda: now[0]
        try:
            self.assertFalse(daemon.wait_for_hardware(timeout=5))
        finally:
            daemon.time.monotonic = orig


class TestSocket(Base):
    def test_roundtrip(self):
        d = daemon.Daemon(config.Config())
        sock = daemon.SOCKET
        try:
            os.unlink(sock)
        except FileNotFoundError:
            pass
        srv = daemon.Server(sock, daemon.Handler)
        srv.daemon_obj = d
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            self.assertEqual(client.call("status")["profile"], "balanced")
            client.call("set_mode", mode=2)
            self.assertEqual(get(R + hw.PLATFORM_PROFILE), "quiet")
            with self.assertRaises(client.DaemonError):
                client.call("nope")
            self.assertEqual(client.main(["mode", "turbo"]), 0)
            self.assertEqual(get(R + hw.PLATFORM_PROFILE), "performance")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()


class TestSelfUpdate(unittest.TestCase):
    """In-app update: the archive is extracted safely, a pkexec cancel is handled separately (no network, no root)."""

    def _tarball(self, files: dict, prefix="boreas-main/") -> bytes:
        import io
        import tarfile as tf
        buf = io.BytesIO()
        with tf.open(fileobj=buf, mode="w:gz") as t:
            for name, data in files.items():
                info = tf.TarInfo(prefix + name if not name.startswith(("/", "..")) else name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                t.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def _serve(self, payload: bytes):
        import io
        from unittest.mock import patch
        from boreas import update

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return patch.object(update.urllib.request, "urlopen", lambda *a, **k: R(payload))

    def test_download_extracts_project(self):
        from boreas import update
        good = self._tarball({"install.sh": b"#!/bin/sh\n", "boreas/__init__.py": b'__version__ = "9.0.0"\n'})
        d = tempfile.mkdtemp(prefix="boreas-upd-")
        with self._serve(good):
            root = update.download_source(d)
        self.assertEqual(update.source_version(root), "9.0.0")
        self.assertTrue(os.access(os.path.join(root, "install.sh"), os.X_OK))
        shutil.rmtree(d)

    def test_unsafe_archive_is_rejected(self):
        from boreas import update
        for bad in ({"../evil.sh": b"x"}, {"/etc/evil": b"x"}):
            d = tempfile.mkdtemp(prefix="boreas-upd-")
            with self._serve(self._tarball({"install.sh": b"", **bad})):
                with self.assertRaises(ValueError):
                    update.download_source(d)
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(d), "evil.sh")))
            shutil.rmtree(d)

    def test_run_update_uses_pkexec_and_handles_cancel(self):
        import subprocess as sp
        from unittest.mock import patch
        from boreas import update
        good = self._tarball({"install.sh": b"#!/bin/sh\n", "boreas/__init__.py": b'__version__ = "9.0.0"\n'})
        seen = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return sp.CompletedProcess(cmd, self.code, "", "boom")
        with self._serve(good), patch.object(update.shutil, "which", lambda n: "/usr/bin/pkexec"), \
                patch.object(update.subprocess, "run", fake_run):
            self.code = 0
            self.assertEqual(update.run_update("9.0.0"), "9.0.0")
            self.assertEqual(seen[-1][:2], ["/usr/bin/pkexec", "/bin/sh"])
            self.assertTrue(seen[-1][2].endswith("install.sh"))
            self.code = 126
            with self.assertRaises(update.UpdateCancelled):
                update.run_update("9.0.0")
            self.code = 127
            with self.assertRaises(update.NoAuthorization):
                update.run_update("9.0.0")
            self.code = 1
            with self.assertRaises(RuntimeError):
                update.run_update("9.0.0")
            with self.assertRaises(ValueError):   # an archive older than expected is not installed
                update.run_update("9.1.0")

