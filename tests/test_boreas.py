"""Tests on a fake sysfs tree: python3 -m unittest discover -s tests"""

import contextlib
import errno
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

TMP = tempfile.mkdtemp(prefix="boreas-test-")
os.environ["BOREAS_SYSFS_ROOT"] = TMP + "/root"
os.environ["BOREAS_CONFIG"] = TMP + "/etc/config.json"
os.environ["BOREAS_SOCKET"] = TMP + "/boreas.sock"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from boreas import aura, config, daemon, fans, gpu, hw, ryzen  # noqa: E402
from boreas import client  # noqa: E402

R = TMP + "/root"
ARM = R + hw.ARMOURY
IGPU = "/sys/bus/pci/devices/0000:06:00.0"
DGPU = "/sys/bus/pci/devices/0000:01:00.0"


def put(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


def get(path):
    with open(path) as f:
        return f.read().strip()


def enter_eco():
    """Eco as the hardware shows it: dgpu_disable=1 and the dGPU gone from the PCI bus."""
    put(f"{ARM}/dgpu_disable/current_value", 1)
    shutil.rmtree(R + DGPU, ignore_errors=True)


def remember_dgpu():
    """What entering Eco through Boreas leaves behind: a built-in NVIDIA dGPU was seen here."""
    put(R + gpu.DGPU_SEEN, "seen")


def pci_bridge(name, child=False, control="auto"):
    """A PCIe port. Without a child it is what the dGPU's port looks like while the dGPU is
    off the bus (Eco, or a dGPU that has not come back yet)."""
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
    # the GPUs: AMD iGPU (Raphael) and the NVIDIA dGPU (RTX 4060 Laptop), as on the G713PV
    put(R + IGPU + "/vendor", "0x1002")
    put(R + IGPU + "/class", "0x030000")
    put(R + DGPU + "/vendor", "0x10de")
    put(R + DGPU + "/class", "0x030000")
    for n, desc in ((0, b"\x05\x01\x85\x01\x81\x00"), (1, b"\x06\x31\xff\x85\x5d\xb1\x02")):
        put(f"{R}/sys/class/hidraw/hidraw{n}/device/uevent",
            "HID_ID=0003:00000B05:000019B6\nHID_NAME=ASUSTek Computer Inc. N-KEY Device")
        with open(f"{R}/sys/class/hidraw/hidraw{n}/device/report_descriptor", "wb") as f:
            f.write(desc)
    put(R + "/dev/hidraw1", "")
    put(R + ryzen.CPUINFO, "processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 25\n"
        "model\t\t: 97\nmodel name\t: AMD Ryzen 9 7845HX with Radeon Graphics\n\nprocessor\t: 1\n")


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

    def test_fix_curve_last_point_covers_everything_above_it(self):
        # the firmware holds the last speed above the last point, so squeezing every point
        # under 70°C must not switch the safety floor off
        c = fans.fix_curve([20, 25, 30, 35, 40, 45, 50, 60, 2, 2, 2, 2, 2, 2, 2, 2])
        self.assertEqual(c[:8], [20, 25, 30, 35, 40, 45, 50, 60])
        self.assertEqual(c[8:], [2, 2, 2, 2, 2, 2, 2, fans.safety_floor(fans.HOLD_TEMP)])
        # a last point that is hotter than the hold temperature keeps its own floor
        self.assertEqual(fans.fix_curve([30, 40, 50, 60, 70, 80, 90, 110] + [0] * 8)[-1], 48)

    def test_unreadable_point_is_not_read_as_zero(self):
        os.remove(R + "/sys/class/hwmon/hwmon9/pwm1_auto_point6_pwm")
        self.assertIsNone(fans.read_curve("cpu"))
        self.assertIsNone(fans.factory_curve("cpu"))
        self.assertEqual(len(fans.read_curve("gpu")), 16)

    def test_a_speed_is_never_rounded_down_on_the_way_to_the_firmware(self):
        # the kernel reads pwm back as a percentage with the fraction cut off
        for pct in range(101):
            pwm = fans.pct_to_pwm(pct)
            self.assertGreaterEqual(pwm * 100 // 255, pct, pct)
            self.assertEqual(fans.pwm_to_pct(pwm), pct, pct)
        self.assertEqual((fans.pct_to_pwm(0), fans.pct_to_pwm(100), fans.pct_to_pwm(12)), (0, 255, 31))

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
    def test_silent_profile_uses_advertised_low_power_alias(self):
        put(R + hw.PLATFORM_PROFILE + "_choices", "low-power balanced performance")
        d = daemon.Daemon(config.Config())
        d.set_mode(hw.SILENT)
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "low-power")
        self.assertEqual(d.status()["mode"], hw.SILENT)
        self.assertEqual(d.status()["profile"], "low-power")

    def test_silent_profile_keeps_quiet_when_advertised(self):
        put(R + hw.PLATFORM_PROFILE + "_choices", "quiet low-power balanced performance")
        hw.set_profile("quiet")
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "quiet")

    def test_profile_alias_does_not_replace_other_modes(self):
        put(R + hw.PLATFORM_PROFILE + "_choices", "low-power balanced performance")
        for profile in ("balanced", "performance"):
            hw.set_profile(profile)
            self.assertEqual(get(R + hw.PLATFORM_PROFILE), profile)
        # No guessed alias when the kernel exposes neither spelling.
        put(R + hw.PLATFORM_PROFILE + "_choices", "balanced performance")
        hw.set_profile("quiet")
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "quiet")

    def test_attr_clamped(self):
        hw.set_attr("ppt_pl1_spl", 500)
        self.assertEqual(get(f"{ARM}/ppt_pl1_spl/current_value"), "130")
        with self.assertRaises(ValueError):
            hw.set_attr("gpu_mux_mode", 5)

    def test_attr_without_adjustable_range_is_skipped(self):
        # nv_dynamic_boost on battery: the firmware reports 0..0 and rejects every write.
        put(f"{ARM}/nv_dynamic_boost/current_value", 0)
        put(f"{ARM}/nv_dynamic_boost/min_value", 0)
        put(f"{ARM}/nv_dynamic_boost/max_value", 0)
        try:
            self.assertFalse(hw.set_attr("nv_dynamic_boost", 25))
            self.assertEqual(get(f"{ARM}/nv_dynamic_boost/current_value"), "0")
            # a real range writes as before
            put(f"{ARM}/nv_dynamic_boost/min_value", 5)
            put(f"{ARM}/nv_dynamic_boost/max_value", 25)
            self.assertTrue(hw.set_attr("nv_dynamic_boost", 25))
            self.assertEqual(get(f"{ARM}/nv_dynamic_boost/current_value"), "25")
        finally:
            put(f"{ARM}/nv_dynamic_boost/min_value", 5)
            put(f"{ARM}/nv_dynamic_boost/max_value", 25)
            put(f"{ARM}/nv_dynamic_boost/current_value", 25)

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


class TestPowerSource(Base):
    def test_usb_c_charger_counts_as_ac(self):
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        self.assertFalse(hw.ac_online())
        usb = R + "/sys/class/power_supply/ucsi-source-psy-USBC000:001"
        put(usb + "/type", "USB")
        put(usb + "/online", 0)
        self.assertFalse(hw.ac_online())  # a port with nothing charging through it
        put(usb + "/online", 1)
        self.assertTrue(hw.ac_online())
        put(R + "/sys/class/power_supply/BAT0/online", 1)  # a battery never counts
        put(usb + "/online", 0)
        self.assertFalse(hw.ac_online())

    def test_failed_switch_on_unplug_is_retried(self):
        # the firmware may refuse the profile write right at the unplug (EBUSY): the battery mode
        # must still land on a later tick, not wait for the next plug or unplug
        d = daemon.Daemon(config.Config())
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        real = hw.set_profile
        refused = []

        def set_profile(name):
            if not refused:
                refused.append(name)
                raise OSError(errno.EBUSY, "Device or resource busy")
            return real(name)
        with patch.object(daemon.hw, "set_profile", set_profile):
            d.tick()
            self.assertEqual(refused, ["quiet"])
            self.assertEqual(hw.get_profile(), "balanced")  # nothing of the battery mode ran yet
            for _ in range(daemon.ECO_RETRY_TICKS):
                d.tick()
        self.assertEqual(hw.get_profile(), "quiet")
        self.assertEqual(d.cfg["mode"], hw.SILENT)
        self.assertFalse(d.source_mode_pending)
        self.assertEqual(config.Config()["mode"], hw.SILENT)  # stored, not only in memory

    def test_refused_switch_is_quiet_and_keeps_the_rest_of_the_tick(self):
        # a firmware that never takes the write: one log line, a retry every ECO_RETRY_TICKS,
        # and the Optimized GPU switch and the rest of the tick still run
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.OPTIMIZED
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        tries, lines, gpu_targets = [], [], []

        def set_profile(name):
            tries.append(name)
            raise OSError(errno.EBUSY, "Device or resource busy")
        with patch.object(daemon.hw, "set_profile", set_profile), \
                patch.object(daemon, "log", lines.append), \
                patch.object(d, "apply_gpu_target", lambda t, **k: gpu_targets.append(t) or {}), \
                patch.object(d, "maintain_cpu_temp") as maintain:
            for _ in range(1 + 3 * daemon.ECO_RETRY_TICKS):
                d.tick()
        self.assertEqual(len(tries), 4)  # the unplug, then one try per ECO_RETRY_TICKS
        self.assertEqual(len([l for l in lines if "will retry" in l]), 1)
        self.assertEqual(gpu_targets, [gpu.OPTIMIZED])
        self.assertEqual(maintain.call_count, 3 * daemon.ECO_RETRY_TICKS)
        self.assertTrue(d.source_mode_pending)

    def _turbo_on_ac(self):
        put(R + hw.PLATFORM_PROFILE, "performance")
        d = daemon.Daemon(config.Config())
        d.cfg["mode_ac"] = d.cfg["mode"] = hw.TURBO
        return d

    def test_firmware_profile_change_at_unplug_is_not_taken_as_fn_f5(self):
        # the firmware drops the profile itself and refuses our write in the same moment:
        # the stored battery mode must survive and land on the retry
        d = self._turbo_on_ac()
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        real = hw.set_profile
        refused = []

        def set_profile(name):
            if not refused:
                refused.append(name)
                put(R + hw.PLATFORM_PROFILE, "balanced")
                raise OSError(errno.EBUSY, "Device or resource busy")
            return real(name)
        with patch.object(daemon.hw, "set_profile", set_profile):
            for _ in range(1 + daemon.ECO_RETRY_TICKS):
                d.tick()
        self.assertEqual(d.cfg["mode_battery"], hw.SILENT)
        self.assertEqual(hw.get_profile(), "quiet")
        self.assertFalse(d.source_mode_pending)

    def test_fn_f5_on_the_retry_tick_wins(self):
        d = self._turbo_on_ac()
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        real = hw.set_profile
        with patch.object(daemon.hw, "set_profile",
                          lambda n: (_ for _ in ()).throw(OSError(errno.EBUSY, "busy"))):
            for _ in range(daemon.ECO_RETRY_TICKS):
                d.tick()  # the unplug, then up to the tick before the retry
        self.assertTrue(d.source_mode_pending)
        put(R + hw.PLATFORM_PROFILE, "balanced")  # Fn+F5, in the same second as the retry
        with patch.object(daemon.hw, "set_profile", real):
            d.tick()
        self.assertEqual(d.cfg["mode"], hw.BALANCED)
        self.assertEqual(d.cfg["mode_battery"], hw.BALANCED)
        self.assertEqual(hw.get_profile(), "balanced")
        self.assertFalse(d.source_mode_pending)

    def test_editing_running_turbo_keeps_the_pending_battery_switch(self):
        d = self._turbo_on_ac()
        d.cfg["mode_battery"] = hw.SILENT
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        with patch.object(hw, "set_profile", side_effect=OSError(errno.EBUSY, "busy")):
            d.tick()
        self.assertTrue(d.source_mode_pending)
        d.handle({"cmd": "update_mode", "mode": hw.TURBO,
                  "data": {"fan_cpu": list(fans.DEFAULTS[hw.TURBO]["cpu"])}})
        for _ in range(daemon.ECO_RETRY_TICKS):
            d.tick()
        self.assertEqual(hw.get_profile(), "quiet")
        self.assertEqual(d.cfg["mode"], hw.SILENT)
        self.assertFalse(d.source_mode_pending)

    def test_a_chosen_mode_replaces_the_pending_switch(self):
        d = daemon.Daemon(config.Config())
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        real = hw.set_profile
        with patch.object(daemon.hw, "set_profile",
                          lambda n: (_ for _ in ()).throw(OSError(errno.EBUSY, "busy"))):
            d.tick()
        self.assertTrue(d.source_mode_pending)
        with patch.object(daemon.hw, "set_profile", real):
            d.set_mode(hw.TURBO)  # chosen on battery: stored for battery, not for AC
            for _ in range(daemon.ECO_RETRY_TICKS):
                d.tick()
        self.assertEqual(d.cfg["mode_battery"], hw.TURBO)
        self.assertEqual(d.cfg["mode"], hw.TURBO)  # the retry did not put Silent back
        self.assertFalse(d.source_mode_pending)

    def test_failed_reapply_on_resume_is_retried(self):
        d = self._turbo_on_ac()
        d.cfg["mode_battery"] = hw.SILENT
        d.cfg["gpu_target"] = gpu.OPTIMIZED
        put(R + "/sys/class/power_supply/ADP0/online", 0)  # unplugged during sleep
        real = hw.set_profile
        refused = []

        def set_profile(name):
            if not refused:
                refused.append(name)
                raise OSError(errno.EBUSY, "Device or resource busy")
            return real(name)
        with patch.object(hw, "set_profile", set_profile), \
                patch.object(d, "apply_gpu_target") as target, \
                patch.object(d, "eco_pending", return_value=False):
            d.reapply()
            self.assertEqual(refused, ["quiet"])
            self.assertEqual(hw.get_profile(), "performance")
            self.assertTrue(d.source_mode_pending)
            target.assert_called_once_with(gpu.OPTIMIZED)
            for _ in range(daemon.ECO_RETRY_TICKS):
                d.tick()
        self.assertEqual(hw.get_profile(), "quiet")
        self.assertEqual(d.cfg["mode"], hw.SILENT)
        self.assertEqual(d.cfg["mode_battery"], hw.SILENT)
        self.assertFalse(d.source_mode_pending)
        self.assertEqual(config.Config()["mode"], hw.SILENT)

    def test_firmware_profile_change_at_resume_is_not_taken_for_fn_f5(self):
        d = self._turbo_on_ac()
        d.cfg["mode_battery"] = hw.SILENT
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        real = hw.set_profile
        refused = []

        def set_profile(name):
            if not refused:
                refused.append(name)
                put(R + hw.PLATFORM_PROFILE, "balanced")  # the firmware changed it at resume
                raise OSError(errno.EBUSY, "Device or resource busy")
            return real(name)
        with patch.object(hw, "set_profile", set_profile):
            d.reapply()
            for _ in range(daemon.ECO_RETRY_TICKS):
                d.tick()
        self.assertEqual(d.cfg["mode_battery"], hw.SILENT)
        self.assertEqual(d.cfg["mode"], hw.SILENT)
        self.assertEqual(hw.get_profile(), "quiet")
        self.assertFalse(d.source_mode_pending)


class TestGpu(Base):
    def test_other_dgpu_present_counts_only_builtin_display_devices(self):
        cases = (
            ('0x10de', '0x030000', 'fixed', False),
            ('0x1002', '0x030000', 'fixed', True),
            ('0x1002', '0x040300', 'fixed', False),
            ('0x1002', '0x030200', 'removable', False),
            ('0x1002', '0x030200', 'fixed', True),
        )
        for vendor, cls, removable, expected in cases:
            with self.subTest(vendor=vendor, cls=cls, removable=removable):
                put(R + DGPU + '/vendor', vendor)
                put(R + DGPU + '/class', cls)
                put(R + DGPU + '/removable', removable)
                self.assertEqual(gpu.other_dgpu_present(), expected)
        egpu = R + '/sys/bus/pci/devices/0000:64:00.0'
        put(egpu + '/vendor', '0x10de')
        put(egpu + '/class', '0x030000')
        put(egpu + '/removable', 'removable')
        self.assertTrue(gpu.other_dgpu_present())
        put(egpu + '/removable', 'fixed')
        self.assertFalse(gpu.other_dgpu_present())

    def test_other_dgpu_present_ignores_a_device_whose_vendor_cannot_be_read(self):
        put(R + DGPU + '/vendor', '0x1002')
        put(R + DGPU + '/class', '0x030000')
        put(R + DGPU + '/removable', 'fixed')
        self.assertTrue(gpu.other_dgpu_present())
        os.unlink(R + DGPU + '/vendor')        # a read racing a rescan returns None
        self.assertFalse(gpu.other_dgpu_present())

    def test_nvidia_driver_installed_handles_missing_modinfo(self):
        self.assertFalse(gpu.nvidia_driver_loaded())
        with patch.object(gpu, '_run', side_effect=FileNotFoundError(errno.ENOENT, 'modinfo')) as run:
            self.assertFalse(gpu.nvidia_driver_installed())
        run.assert_called_once_with('modinfo', '-F', 'filename', 'nvidia')

    def test_nvidia_driver_installed_finds_the_module_of_another_kernel(self):
        # after a kernel upgrade without a reboot the running kernel's module directory is gone,
        # so modinfo fails although the driver is installed for the new kernel
        dep = R + "/usr/lib/modules/6.18.61-1-cachyos-lts/modules.dep"
        put(dep, "kernel/drivers/net/ethernet/intel/e1000e/e1000e.ko.zst:\n"
                 "kernel/drivers/gpu/drm/nouveau/nouveau.ko.zst: kernel/drivers/video/video.ko.zst\n")
        for failure in ({"return_value": False}, {"side_effect": FileNotFoundError(errno.ENOENT, "modinfo")}):
            with self.subTest(failure=failure), patch.object(gpu, "_run", **failure) as run:
                self.assertFalse(gpu.nvidia_driver_installed())
                with open(dep, "a") as f:
                    f.write("updates/dkms/nvidia.ko.zst: kernel/drivers/video/video.ko.zst\n"
                            "updates/dkms/nvidia-drm.ko.zst: updates/dkms/nvidia-modeset.ko.zst\n")
                self.assertTrue(gpu.nvidia_driver_installed())
                run.assert_called_with("modinfo", "-F", "filename", "nvidia")
                put(dep, "")

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

    def test_eco_reports_busy_for_a_non_utf8_process_name(self):
        self._fake_dgpu()
        with self._fake_proc({1510: ("placeholder", ["/dev/nvidia0"])}), \
                patch.object(gpu, "_run") as run:
            with open(f"{gpu.PROC}/1510/comm", "wb") as f:
                f.write(b"\xff\xfe")
            with self.assertRaises(gpu.GpuBusy) as raised:
                gpu.set_eco(True)
        self.assertEqual(raised.exception.users, ["\ufffd\ufffd(1510)"])
        run.assert_not_called()
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

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

    def test_leaving_eco_reports_a_dgpu_that_did_not_come_back(self):
        # dgpu_disable=0 was written but the dGPU never shows up on the bus: that is not Standard
        remember_dgpu()  # Boreas took this dGPU off the bus for Eco before
        enter_eco()
        pci_bridge("0000:00:01.1")  # the dGPU's port, empty while the dGPU is off the bus
        calls = self._gpu_calls()
        with self.assertRaises(OSError):
            gpu.set_eco(False)
        self.assertIn(("modprobe", "nvidia_drm"), calls)  # still loaded, binds when the dGPU appears late

    def test_leaving_eco_reports_a_slow_dgpu_when_the_driver_refuses_to_load(self):
        remember_dgpu()  # Boreas took this dGPU off the bus for Eco before
        enter_eco()
        pci_bridge("0000:00:01.1")  # the dGPU's port, empty while the dGPU is off the bus
        calls = self._gpu_calls(lambda cmd: cmd == ("modinfo", "-F", "filename", "nvidia"))
        with patch.object(gpu.subprocess, "run", return_value=Mock(returncode=1)):
            with self.assertRaises(gpu.DgpuSlow):
                gpu.set_eco(False)
        self.assertIn(("modprobe", "nvidia_drm"), calls)
        self.assertIn(("modinfo", "-F", "filename", "nvidia"), calls)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_leaving_eco_without_the_nvidia_driver_is_not_an_error(self):
        # an AMD dGPU: dgpu_present() cannot see it, so there is nothing to wait for
        enter_eco()
        self._gpu_calls(lambda cmd: cmd[0] not in ("modprobe", "modinfo"))
        with patch.object(gpu.subprocess, "run", return_value=Mock(returncode=1)):  # not the host's systemctl
            gpu.set_eco(False)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_ultimate_from_eco_with_a_slow_dgpu_still_writes_the_mux(self):
        # the dGPU comes up at the reboot Ultimate needs anyway
        enter_eco()
        pci_bridge("0000:00:01.1")  # the dGPU's port, empty while the dGPU is off the bus
        self._gpu_calls()
        gpu.set_mux(True)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")

    def test_ultimate_stops_on_any_other_error_leaving_eco(self):
        # only a slow dGPU is let through; a refusal (eBPF helper, firmware) still reaches the user
        with patch.object(gpu, "set_eco", side_effect=OSError("GPU engine rejected request")):
            with self.assertRaises(OSError):
                gpu.set_mux(True)
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "1")

    def test_leaving_eco_attaches_the_dgpu(self):
        enter_eco()
        calls = self._gpu_calls()
        real_write = hw.write

        def write(path, value):
            real_write(path, value)
            if path.endswith("/sys/bus/pci/rescan"):
                self._fake_dgpu()  # the firmware brought the dGPU back
        with patch.object(gpu.hw, "write", write):
            gpu.set_eco(False)
        self.assertIn(("modprobe", "nvidia_drm"), calls)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_standard_again_looks_for_a_dgpu_that_did_not_come_back(self):
        # dgpu_disable is 0 already, but the dGPU never showed up (DgpuSlow earlier): choosing
        # Standard again rescans instead of returning early
        remember_dgpu()  # Boreas took this dGPU off the bus for Eco before
        shutil.rmtree(R + DGPU)
        pci_bridge("0000:00:01.1")  # the dGPU's port, empty
        calls = self._gpu_calls()
        with patch.object(gpu.subprocess, "run", return_value=Mock(returncode=1)):
            with self.assertRaises(gpu.DgpuSlow):
                gpu.set_eco(False)
            self.assertTrue(os.path.exists(R + "/sys/bus/pci/rescan"))
            self.assertIn(("modprobe", "nvidia_drm"), calls)
            self.assertFalse(gpu.state()["has_eco"])
            real_write = hw.write

            def write(path, value):
                real_write(path, value)
                if path.endswith("/sys/bus/pci/rescan"):
                    self._fake_dgpu()  # this time it comes up
            with patch.object(gpu.hw, "write", write):
                gpu.set_eco(False)
            self.assertTrue(gpu.state()["has_eco"])  # Eco and Optimized are offered again
            os.remove(R + "/sys/bus/pci/rescan")
            calls.clear()
            gpu.set_eco(False)  # with the dGPU on the bus Standard stays a no-op
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(R + "/sys/bus/pci/rescan"))
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    EGPU = "/sys/bus/pci/devices/0000:64:00.0"

    def _egpu(self):
        """An NVIDIA card on USB4/Thunderbolt: the kernel marks it removable."""
        d = R + self.EGPU
        put(d + "/vendor", "0x10de")
        put(d + "/class", "0x030000")
        put(d + "/removable", "removable")
        put(d + "/power/runtime_status", "active")
        return d

    def test_eco_leaves_an_nvidia_egpu_on_the_bus(self):
        for m in gpu.NV_MODULES:
            put(R + f"/sys/module/{m}/refcnt", 0)
        egpu = self._egpu()
        put(R + DGPU + "/removable", "fixed")
        self._gpu_calls()
        removed = []
        real_write = hw.write

        def write(path, value):
            if path.endswith("/remove"):
                removed.append(path)
            real_write(path, value)
        with patch.object(gpu, "_nvidia_users", lambda ignore=(): []), patch.object(gpu.hw, "write", write):
            gpu.set_eco(True)
        self.assertEqual(removed, [DGPU + "/remove"])
        self.assertFalse(os.path.exists(egpu + "/remove"))
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")

    def test_leaving_eco_with_an_egpu_reports_the_missing_builtin_dgpu(self):
        # the eGPU is not the dGPU dgpu_disable brings back: it must not hide a dGPU that is late
        remember_dgpu()  # Boreas took this dGPU off the bus for Eco before
        enter_eco()
        pci_bridge("0000:00:01.1")
        self._egpu()
        self._gpu_calls()
        self.assertFalse(gpu.dgpu_present())
        self.assertIsNone(gpu.dgpu_runtime_status())
        with patch.object(gpu.subprocess, "run", return_value=Mock(returncode=1)):
            with self.assertRaises(gpu.DgpuSlow):
                gpu.set_eco(False)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def _bridge(self, name, child=False, control="auto"):
        return pci_bridge(name, child, control)

    def test_leaving_eco_wakes_sleeping_port(self):
        # G713PV: with the port asleep, dgpu_disable=0 waited 30 s in the firmware and failed with EIO
        enter_eco()
        port = self._bridge("0000:00:01.1")             # empty below in Eco: the dGPU's port
        busy = self._bridge("0000:00:02.1", child=True)  # has a device below: left alone
        self._gpu_calls()
        seen = {}
        real_set = hw.set_attr

        def set_attr(name, value):
            if name == "dgpu_disable":
                seen["port"] = get(port + "/power/control")
                seen["busy"] = get(busy + "/power/control")
                self._fake_dgpu()  # with the port awake the dGPU comes back
            return real_set(name, value)
        with patch.object(gpu.hw, "set_attr", set_attr):
            gpu.set_eco(False)
        self.assertEqual(seen, {"port": "on", "busy": "auto"})
        self.assertEqual(get(port + "/power/control"), "auto")  # power management is back on
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_port_restored_when_firmware_fails(self):
        enter_eco()
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

    def test_gpu_temp_with_ebpf_follows_the_engine_state(self):
        # eBPF backend: the helper's own status says whether the dGPU is awake. nvidia-smi runs
        # only in the standard policy with the device active; never in eco, never to wake it up
        put(R + "/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
        put(R + "/sys/bus/pci/devices/0000:01:00.0/class", "0x030000")
        put(R + "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status", "suspended")
        put(R + "/sys/class/power_supply/ADP0/online", 1)
        ran = []
        orig = daemon.subprocess.run
        daemon.subprocess.run = lambda *a, **k: (
            ran.append(a), daemon.subprocess.CompletedProcess(a, 0, stdout="75\n"))[1]
        self.addCleanup(gpu.gpu_engine.set_config_backend, None)
        try:
            d = daemon.Daemon(config.Config())  # start-up applies the configured backend (firmware)
            gpu.gpu_engine.set_config_backend("ebpf")
            d.gpu_temp_cache = (0.0, None)
            awake = {"engine": {"policy": "standard", "power_state": "D0", "runtime_status": "active"}}
            self.assertEqual(d.gpu_temp(awake), 75)
            d.gpu_temp_cache = (0.0, None)
            asleep = {"engine": {"policy": "standard", "power_state": "D3cold", "runtime_status": "suspended"}}
            self.assertIsNone(d.gpu_temp(asleep))
            d.gpu_temp_cache = (0.0, None)
            eco = {"engine": {"policy": "eco", "power_state": "D0", "runtime_status": "active"}}
            self.assertIsNone(d.gpu_temp(eco))
            d.gpu_temp_cache = (0.0, None)
            self.assertIsNone(d.gpu_temp({"engine": None}))  # helper not answering
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


class TestEcoNeedsNvidia(Base):
    """The firmware Eco path only knows how to detach an NVIDIA dGPU. On a Radeon dGPU
    (2022 Zephyrus G14, GA402R) it would write dgpu_disable=1 with amdgpu bound, which can
    freeze the machine, so Eco and Optimized are neither offered nor accepted there."""

    RADEON = "/sys/bus/pci/devices/0000:03:00.0"

    def radeon(self):
        """GA402R: Rembrandt iGPU and Radeon RX 6700S dGPU, both AMD (0x1002); no NVIDIA device."""
        shutil.rmtree(R + DGPU)
        put(R + hw.DMI_PRODUCT, "ROG Zephyrus G14 GA402RJ_GA402RJ")
        put(R + self.RADEON + "/vendor", "0x1002")
        put(R + self.RADEON + "/class", "0x030000")
        put(R + "/sys/module/amdgpu/refcnt", 2)

    def calls(self):
        calls = []
        gpu._run = lambda *cmd: (calls.append(cmd), cmd[0] not in ("modprobe", "modinfo"))[1]  # no NVIDIA driver
        return calls

    def saved_target(self):
        with open(os.environ["BOREAS_CONFIG"]) as f:
            return json.load(f).get("gpu_target")

    # ---- Radeon dGPU ----

    def test_radeon_state_does_not_offer_eco(self):
        self.radeon()
        st = gpu.state()
        self.assertFalse(st["has_eco"])
        self.assertTrue(st["has_mux"])
        self.assertEqual(st["mode"], gpu.STANDARD)
        self.assertEqual(gpu.eco_refusal(st), gpu.NEEDS_NVIDIA)
        helper = TMP + "/boreas-gpu-engine"
        put(helper, "")
        with patch.object(gpu.gpu_engine, "helper_path", return_value=helper):
            self.assertFalse(gpu.state()["ebpf_available"])  # the eBPF helper is NVIDIA only too

    def test_radeon_refuses_eco_and_optimized_requests(self):
        self.radeon()
        calls = self.calls()
        d = daemon.Daemon(config.Config())
        self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
        for target in (gpu.ECO, gpu.OPTIMIZED):
            with self.assertRaisesRegex(ValueError, "NVIDIA"):
                d.handle({"cmd": "set_gpu", "target": target})
            self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
            self.assertEqual(self.saved_target(), gpu.STANDARD)  # nothing to come back at boot
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        with self.assertRaises(ValueError):
            d.handle({"cmd": "set_gpu", "target": gpu.OPTIMIZED})
        with self.assertRaises(ValueError):
            d.apply_gpu_target(gpu.ECO, explicit=True)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        self.assertEqual(calls, [])  # no driver unload, no service touched

    def test_radeon_set_eco_refuses_before_touching_anything(self):
        # the last line of defence: whoever calls it, dgpu_disable=1 is not written
        self.radeon()
        calls = self.calls()
        with self.assertRaisesRegex(ValueError, "NVIDIA"):
            gpu.set_eco(True)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        self.assertEqual(calls, [])
        gpu.set_eco(False)  # Standard stays a no-op

    def test_radeon_standard_and_ultimate_still_work(self):
        self.radeon()
        self.calls()
        d = daemon.Daemon(config.Config())
        self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.STANDARD}), {})
        self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.ULTIMATE}), {"reboot": True})
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")
        self.assertEqual(gpu.state()["mode"], gpu.ULTIMATE)
        self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.STANDARD}), {"reboot": True})
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "1")
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_radeon_saved_eco_target_falls_back_to_standard_at_boot(self):
        self.radeon()
        calls = self.calls()
        for saved in (gpu.ECO, gpu.OPTIMIZED):
            c = config.Config()
            c["gpu_target"] = saved
            c.save()
            d = daemon.Daemon(config.Config())  # an existing config: not adopted from the hardware
            self.assertEqual(d.cfg["gpu_target"], saved)
            d.reapply(at_boot=True)  # must not raise: the display manager waits for the service
            self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
            self.assertEqual(self.saved_target(), gpu.STANDARD)
            self.assertEqual(d.notice, "Eco and Optimized need an NVIDIA dGPU. The GPU mode was set to Standard.")
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
            self.assertFalse(d.eco_pending())
        self.assertEqual(calls, [])  # a visible Radeon dGPU answers it: no modinfo needed

    def test_radeon_saved_optimized_on_unplug_falls_back(self):
        # the plug/unplug path applies Optimized without the user asking: same fallback, no crash
        self.radeon()
        self.calls()
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.OPTIMIZED
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        d.tick()
        self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
        self.assertIn("NVIDIA", d.notice)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")

    def test_radeon_saved_eco_with_nvidia_driver_installed_falls_back_at_boot(self):
        self.radeon()
        with patch.object(gpu, '_run', side_effect=lambda *cmd: cmd == ('modinfo', '-F', 'filename', 'nvidia')):
            self.assertTrue(gpu.nvidia_driver_installed())
            for saved in (gpu.ECO, gpu.OPTIMIZED):
                with self.subTest(saved=saved):
                    c = config.Config()
                    c['gpu_target'] = saved
                    c.save()
                    d = daemon.Daemon(config.Config())
                    d.reapply(at_boot=True)
                    self.assertEqual(d.cfg['gpu_target'], gpu.STANDARD)
                    self.assertEqual(self.saved_target(), gpu.STANDARD)
                    self.assertEqual(d.notice, 'Eco and Optimized need an NVIDIA dGPU. '
                                               'The GPU mode was set to Standard.')
                    self.assertEqual(get(f'{ARM}/dgpu_disable/current_value'), '0')

    def test_igpu_only_saved_eco_with_nvidia_driver_installed_falls_back_at_boot(self):
        # no built-in dGPU (no empty port where one would sit), but the NVIDIA module is on disk,
        # or an NVIDIA eGPU is attached: the saved choice must not be kept waiting forever
        shutil.rmtree(R + DGPU)
        egpu = R + "/sys/bus/pci/devices/0000:64:00.0"
        for with_egpu in (False, True):
            if with_egpu:
                put(egpu + "/vendor", "0x10de")
                put(egpu + "/class", "0x030000")
                put(egpu + "/removable", "removable")
            with patch.object(gpu, "_run", side_effect=lambda *cmd: cmd == ("modinfo", "-F", "filename", "nvidia")):
                self.assertTrue(gpu.nvidia_driver_installed())
                for saved in (gpu.ECO, gpu.OPTIMIZED):
                    with self.subTest(with_egpu=with_egpu, saved=saved):
                        c = config.Config()
                        c["gpu_target"] = saved
                        c.save()
                        d = daemon.Daemon(config.Config())
                        d.reapply(at_boot=True)
                        self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
                        self.assertEqual(self.saved_target(), gpu.STANDARD)
                        self.assertEqual(d.notice, "Eco and Optimized need an NVIDIA dGPU. "
                                                   "The GPU mode was set to Standard.")
                        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        self.assertFalse(os.path.exists(egpu + "/remove"))

    def test_missing_nvidia_in_ultimate_with_installed_driver_adopts_mux(self):
        shutil.rmtree(R + DGPU)
        put(f'{ARM}/gpu_mux_mode/current_value', 0)
        c = config.Config()
        c['gpu_target'] = gpu.ECO
        c.save()
        d = daemon.Daemon(config.Config())
        with patch.object(gpu, '_run', return_value=True), \
                patch.object(gpu, 'set_eco') as eco, patch.object(gpu, 'set_mux') as mux:
            d.reapply(at_boot=True)
        self.assertEqual(d.cfg['gpu_target'], gpu.ULTIMATE)
        self.assertEqual(self.saved_target(), gpu.ULTIMATE)
        self.assertEqual(d.notice, '')
        eco.assert_not_called()
        mux.assert_not_called()

    def test_radeon_leaving_eco_with_nvidia_driver_installed_is_not_slow(self):
        self.radeon()
        put(f'{ARM}/dgpu_disable/current_value', 1)
        shutil.rmtree(R + self.RADEON)
        real_write = hw.write

        def write(path, value):
            real_write(path, value)
            if path == '/sys/bus/pci/rescan':
                put(R + self.RADEON + '/vendor', '0x1002')
                put(R + self.RADEON + '/class', '0x030000')
        with patch.object(gpu, '_run', side_effect=lambda *cmd: cmd == ('modinfo', '-F', 'filename', 'nvidia')), \
                patch.object(gpu.subprocess, 'run', return_value=Mock(returncode=1)), \
                patch.object(gpu.hw, 'write', write):
            self.assertTrue(gpu.nvidia_driver_installed())
            gpu.set_eco(False)
        self.assertEqual(get(f'{ARM}/dgpu_disable/current_value'), '0')
        self.assertEqual(gpu.state()['mode'], gpu.STANDARD)
        self.assertFalse(gpu.state()['has_eco'])

    def test_radeon_eco_never_retried_in_the_background(self):
        self.radeon()
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.ECO  # as if written by hand
        self.assertFalse(d.eco_pending())
        with patch.object(gpu, "set_eco") as set_eco:
            for _ in range(daemon.ECO_RETRY_TICKS * 2):
                d.tick()
        set_eco.assert_not_called()

    def test_radeon_first_start_adopts_standard(self):
        self.radeon()
        d = daemon.Daemon(config.Config())
        self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
        self.assertEqual(self.saved_target(), gpu.STANDARD)

    def test_radeon_with_an_nvidia_egpu_still_refuses_eco(self):
        # an NVIDIA card on USB4/Thunderbolt is not the dGPU dgpu_disable switches off
        self.radeon()
        egpu = R + "/sys/bus/pci/devices/0000:64:00.0"
        put(egpu + "/vendor", "0x10de")
        put(egpu + "/class", "0x030000")
        put(egpu + "/removable", "removable")
        calls = self.calls()
        self.assertFalse(gpu.state()["has_eco"])
        d = daemon.Daemon(config.Config())
        with self.assertRaisesRegex(ValueError, "NVIDIA"):
            d.handle({"cmd": "set_gpu", "target": gpu.ECO})
        with self.assertRaisesRegex(ValueError, "NVIDIA"):
            gpu.set_eco(True)
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(egpu + "/remove"))

    def test_radeon_saved_eco_in_ultimate_adopts_ultimate_without_standard_notice(self):
        self.radeon()
        put(f"{ARM}/gpu_mux_mode/current_value", 0)  # Ultimate chosen in the BIOS
        c = config.Config()
        c["gpu_target"] = gpu.ECO
        c.save()
        d = daemon.Daemon(config.Config())
        d.reapply(at_boot=True)
        self.assertEqual(d.cfg["gpu_target"], gpu.ULTIMATE)
        self.assertEqual(self.saved_target(), gpu.ULTIMATE)
        self.assertNotIn("Standard", d.notice)
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")

    def test_mux_only_saved_eco_falls_back(self):
        # no dgpu_disable at all: nothing to switch off, the saved choice goes back to Standard
        shutil.rmtree(ARM + "/dgpu_disable")
        put(R + "/sys/module/nvidia/refcnt", 1)
        c = config.Config()
        c["gpu_target"] = gpu.OPTIMIZED
        c.save()
        d = daemon.Daemon(config.Config())
        d.reapply(at_boot=True)
        self.assertEqual(self.saved_target(), gpu.STANDARD)
        self.assertEqual(d.notice, "Eco and Optimized are not supported on this device. The GPU mode was set to Standard.")

    def test_radeon_already_in_eco_can_leave_it(self):
        # dgpu_disable=1 set elsewhere (Windows, asusctl): Eco shows, and leaving it works
        self.radeon()
        put(f"{ARM}/dgpu_disable/current_value", 1)
        shutil.rmtree(R + self.RADEON)  # in Eco the dGPU is off the bus
        self.calls()
        st = gpu.state()
        self.assertEqual(st["mode"], gpu.ECO)
        self.assertTrue(st["has_eco"])
        d = daemon.Daemon(config.Config())
        self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.STANDARD}), {})
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        put(R + self.RADEON + "/vendor", "0x1002")  # the Radeon is back on the bus
        put(R + self.RADEON + "/class", "0x030000")
        self.assertFalse(gpu.state()["has_eco"])  # and Eco is no longer offered

    def test_radeon_refuses_the_ebpf_engine(self):
        self.radeon()
        self.addCleanup(gpu.gpu_engine.set_config_backend, None)
        d = daemon.Daemon(config.Config())
        with patch.dict(os.environ), patch.object(gpu.gpu_engine, "get") as engine:
            os.environ.pop("BOREAS_GPU_BACKEND", None)
            with self.assertRaisesRegex(ValueError, "NVIDIA"):
                d.handle({"cmd": "set_gpu_backend", "backend": "ebpf"})
        engine.assert_not_called()  # the helper is not even started
        self.assertEqual(d.cfg["gpu_backend"], "firmware")
        self.assertFalse(gpu.gpu_engine.selected())

    # ---- NVIDIA dGPU (G713PV): unchanged ----

    def test_nvidia_offers_eco(self):
        st = gpu.state()
        self.assertTrue(st["has_eco"])
        self.assertIsNone(gpu.eco_refusal(st))

    def test_nvidia_in_eco_keeps_eco_although_the_dgpu_left_the_bus(self):
        enter_eco()
        self.assertFalse(gpu.dgpu_present())
        st = gpu.state()
        self.assertEqual(st["mode"], gpu.ECO)
        self.assertTrue(st["has_eco"])
        self.assertIsNone(gpu.eco_refusal(st))
        helper = TMP + "/boreas-gpu-engine"
        put(helper, "")
        with patch.object(gpu.gpu_engine, "helper_path", return_value=helper):
            self.assertTrue(gpu.state()["ebpf_available"])
        self.calls()
        d = daemon.Daemon(config.Config())
        self.assertEqual(d.cfg["gpu_target"], gpu.ECO)  # first start adopts Eco as before
        for target in (gpu.ECO, gpu.OPTIMIZED):
            put(R + "/sys/class/power_supply/ADP0/online", 0)
            self.assertEqual(d.handle({"cmd": "set_gpu", "target": target}), {})
            self.assertEqual(d.cfg["gpu_target"], target)
        d.reapply(at_boot=True)
        self.assertEqual(d.cfg["gpu_target"], gpu.OPTIMIZED)  # no fallback
        self.assertEqual(d.notice, "")
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")

    def test_nvidia_full_cycle(self):
        # Standard -> Eco -> Standard -> Optimized on battery -> AC, all through the firmware path
        for m in gpu.NV_MODULES:
            put(R + f"/sys/module/{m}/refcnt", 0)
        calls = []
        gpu._run = lambda *cmd: (calls.append(cmd), True)[1]
        real_write = hw.write

        def write(path, value):
            real_write(path, value)
            if path.endswith(DGPU + "/remove"):
                shutil.rmtree(R + DGPU)
            elif path.endswith("/sys/bus/pci/rescan") and get(f"{ARM}/dgpu_disable/current_value") == "0":
                put(R + DGPU + "/vendor", "0x10de")
                put(R + DGPU + "/class", "0x030000")
        with patch.object(gpu.hw, "write", write), patch.object(gpu, "_nvidia_users", lambda ignore=(): []):
            d = daemon.Daemon(config.Config())
            self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.ECO}), {})
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")
            self.assertFalse(gpu.dgpu_present())
            self.assertTrue(gpu.state()["has_eco"])
            self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.STANDARD}), {})
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
            self.assertTrue(gpu.dgpu_present())
            put(R + "/sys/class/power_supply/ADP0/online", 0)
            d.tick()  # the service sees the unplug
            self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.OPTIMIZED}), {})
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")
            put(R + "/sys/class/power_supply/ADP0/online", 1)
            d.tick()
            self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
            self.assertEqual(d.cfg["gpu_target"], gpu.OPTIMIZED)
        self.assertIn(("modprobe", "-r", "nvidia"), calls)
        self.assertIn(("modprobe", "nvidia_drm"), calls)
        self.assertEqual(d.notice, "")
        self.assertTrue(os.path.exists(R + gpu.DGPU_SEEN))  # Eco took a built-in NVIDIA dGPU off the bus

    def test_an_unrelated_empty_port_is_no_sign_of_a_dgpu(self):
        # an iGPU-only laptop with an empty Thunderbolt/M.2 port and the NVIDIA module on disk:
        # Boreas never took a built-in dGPU off the bus here, so none is expected
        shutil.rmtree(R + DGPU)
        pci_bridge("0000:00:07.0")
        rescans = []
        real_write = hw.write

        def write(path, value):
            if path.endswith("/sys/bus/pci/rescan"):
                rescans.append(path)
            real_write(path, value)
        with patch.object(gpu, "_run", side_effect=lambda *cmd: cmd[0] == "modinfo"), \
                patch.object(gpu.hw, "write", write):
            self.assertFalse(gpu.nvidia_dgpu_expected())
            for saved, notice in ((gpu.STANDARD, ""), (gpu.ECO, "Eco and Optimized need an NVIDIA dGPU. "
                                                                  "The GPU mode was set to Standard.")):
                with self.subTest(saved=saved):
                    c = config.Config()
                    c["gpu_target"] = saved
                    c.save()
                    d = daemon.Daemon(config.Config())
                    d.reapply(at_boot=True)
                    self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
                    self.assertEqual(d.notice, notice)
        self.assertEqual(rescans, [])  # Standard at boot does not go looking for a dGPU

    def test_nvidia_slow_to_come_back_keeps_the_saved_choice(self):
        # Standard after Eco, but the dGPU has not shown up on the bus yet (DgpuSlow): Eco is not
        # offered for now, nothing is written, and the saved Optimized is not overwritten
        remember_dgpu()  # Boreas took this dGPU off the bus for Eco before
        shutil.rmtree(R + DGPU)
        pci_bridge("0000:00:01.1")  # the dGPU's port, empty while the dGPU is off the bus
        put(R + "/sys/module/nvidia/refcnt", 3)  # the driver was loaded again when leaving Eco
        calls = self.calls()
        self.assertFalse(gpu.state()["has_eco"])
        c = config.Config()
        c["gpu_target"] = gpu.OPTIMIZED
        c.save()
        d = daemon.Daemon(config.Config())
        d.reapply(at_boot=True)
        put(R + "/sys/class/power_supply/ADP0/online", 0)
        d.tick()
        self.assertEqual(d.cfg["gpu_target"], gpu.OPTIMIZED)
        self.assertEqual(self.saved_target(), gpu.OPTIMIZED)
        self.assertEqual(d.notice, "")
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        self.assertEqual(calls, [])
        put(R + DGPU + "/vendor", "0x10de")  # the dGPU is back: Optimized works again
        put(R + DGPU + "/class", "0x030000")
        gpu._run = lambda *cmd: True
        with patch.object(gpu, "_nvidia_users", lambda ignore=(): []):
            put(R + "/sys/class/power_supply/ADP0/online", 1)
            d.tick()
            put(R + "/sys/class/power_supply/ADP0/online", 0)
            d.tick()
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "1")

    def test_nvidia_slow_to_come_back_with_an_unloaded_driver_keeps_the_saved_choice(self):
        remember_dgpu()  # Boreas took this dGPU off the bus for Eco before
        shutil.rmtree(R + DGPU)
        pci_bridge("0000:00:01.1")  # the dGPU's port, empty while the dGPU is off the bus
        self.assertFalse(gpu.nvidia_driver_loaded())
        c = config.Config()
        c["gpu_target"] = gpu.OPTIMIZED
        c.save()
        d = daemon.Daemon(config.Config())
        with patch.object(gpu, "_run", side_effect=lambda *cmd: cmd == ("modinfo", "-F", "filename", "nvidia")), \
                patch.object(gpu, "set_eco") as eco, patch.object(gpu, "set_mux") as mux:
            d.reapply(at_boot=True)
            put(R + "/sys/class/power_supply/ADP0/online", 0)
            d.tick()
        self.assertEqual(d.cfg["gpu_target"], gpu.OPTIMIZED)
        self.assertEqual(self.saved_target(), gpu.OPTIMIZED)
        self.assertEqual(d.notice, "")
        self.assertEqual(get(f"{ARM}/dgpu_disable/current_value"), "0")
        eco.assert_not_called()
        mux.assert_not_called()

    def test_ebpf_eco_keeps_eco_while_the_device_is_hidden(self):
        # in the eBPF engine's Eco the NVIDIA sysfs files are blocked for us: nothing to read
        shutil.rmtree(R + DGPU)
        self.addCleanup(gpu.gpu_engine.set_config_backend, None)
        gpu.gpu_engine.set_config_backend("ebpf")
        engine = Mock()
        engine.request.return_value = {"policy": "eco", "runtime_status": "suspended", "power_state": "D3cold"}
        with patch.dict(os.environ), patch.object(gpu.gpu_engine, "get", return_value=engine):
            os.environ.pop("BOREAS_GPU_BACKEND", None)
            st = gpu.state()
        self.assertEqual(st["mode"], gpu.ECO)
        self.assertTrue(st["has_eco"])
        self.assertIsNone(gpu.eco_refusal(st))


class TestCpuDetect(Base):
    def test_detect_dragon_range(self):
        self.assertEqual(ryzen.detect().name, "Dragon Range / Raphael")
        put(R + ryzen.CPUINFO, "vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 183\n")
        self.assertIsNone(ryzen.detect())  # Intel: no SMU
        put(R + ryzen.CPUINFO, "vendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel\t\t: 116\n")
        self.assertTrue(ryzen.detect().reapply)  # Phoenix: supported, drops the ceiling under load
        put(R + ryzen.CPUINFO, "vendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel\t\t: 33\n")
        self.assertIsNone(ryzen.detect())  # Vermeer: its command has a single source, left out

    def test_family_table_matches_the_kernel_module(self):
        # the module picks the command; both tables must list the same (family, model) pairs
        import re
        src = open(os.path.join(os.path.dirname(__file__), "..", "smu", "boreas_smu.c")).read()
        pairs = {(int(f, 16), int(m, 16)) for f, m in re.findall(r"\{\s*0x([0-9A-Fa-f]+)\s*,\s*0x([0-9A-Fa-f]+)\s*,", src)}
        self.assertEqual(pairs, set(ryzen.FAMILIES))

    def test_no_userspace_smu_transport(self):
        """The service reaches the SMU through the kernel bridge only; nothing opens PCI config space."""
        import inspect
        self.assertFalse({"Smu", "CpuTemp"} & set(dir(ryzen)))
        src = inspect.getsource(ryzen)
        for needle in ("/sys/bus/pci", "pwrite", "pread"):
            self.assertNotIn(needle, src)


class TestThermalDaemon(Base):
    def _daemon(self, temp):
        d = daemon.Daemon(config.Config())
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = temp
        clock = [100.0]
        timer = patch.object(daemon.time, "monotonic", side_effect=lambda: clock[0])
        timer.start()
        self.addCleanup(timer.stop)
        return d, None, clock

    def test_no_smu_access_and_target_edit(self):
        # without the opt-in the bridge is never called either: the power limits do the work
        with patch.object(ryzen.BridgeSmu, "send", side_effect=AssertionError("SMU forbidden")):
            d, _, clock = self._daemon(85)
            d.apply_mode(hw.BALANCED)
            self.assertTrue(fans.is_enabled("cpu"))
            put(R + "/sys/class/hwmon/hwmon5/temp1_input", 90000)
            clock[0] += 1
            d.tick()
            self.assertLess(hw.get_attr("ppt_pl1_spl").current, 55)
            with patch.object(hw, "set_profile") as profile:
                d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": 81}})
                profile.assert_not_called()
            self.assertEqual(d.status()["cpu_temp"]["target"], 81)
            self.assertEqual(d.status()["cpu_temp"]["control"], "power-feedback")
            d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": None}})
            self.assertEqual(hw.get_attr("ppt_pl1_spl").current, 55)
            self.assertFalse(fans.is_enabled("cpu"))

    def test_temperature_control_continues_while_the_service_lock_is_held(self):
        # a GPU switch or a hung systemctl holds d.lock for tens of seconds
        d, _, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        d.tick()
        self.assertFalse(d.cover_stalled_tick())  # the watcher is ticking: nothing to cover
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        stepped, caps = [], []

        def step():
            stepped.append(d.cover_stalled_tick())
            caps.append(hw.get_attr("ppt_pl1_spl").current)

        with d.lock:
            clock[0] += 10
            for _ in range(3):  # once stalled, every second gets its step
                clock[0] += 1
                t = threading.Thread(target=step)
                t.start()
                t.join(5)
        self.assertEqual(stepped, [True, True, True])
        self.assertTrue(55 > caps[0] > caps[1] > caps[2], caps)
        d.tick()
        self.assertFalse(d.cover_stalled_tick())  # the watcher is back: the fallback steps aside

    def test_shutdown_is_final_for_the_controller(self):
        d, _, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        self.assertEqual(d.cpu.target, 85)
        d.shutdown()
        self.assertIsNone(d.cpu.target)
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        clock[0] += 10
        self.assertFalse(d.cover_stalled_tick())           # the fallback thread
        d.maintain_cpu_temp()                               # the watcher
        self.assertFalse(d.apply_cpu_temp(85, d.cfg.mode(hw.BALANCED)))  # a request still in flight
        self.assertIsNone(d.cpu.target)
        self.assertEqual(hw.get_attr("ppt_pl1_spl").current, 55)

    def test_inactive_edit_and_invalid_values(self):
        d, _, _ = self._daemon(None)
        for value in (50, 100, "85", True, 85.5):
            with self.assertRaises(ValueError):
                d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": value}})
        with patch.object(hw, "set_attr") as write:
            d.handle({"cmd": "update_mode", "mode": hw.TURBO, "data": {"cpu_temp": 80}})
            write.assert_not_called()
        self.assertEqual(d.cfg.mode(hw.TURBO)["cpu_temp"], 80)

    def test_custom_power_caps_and_mode_change(self):
        d, _, clock = self._daemon(85)
        d.cfg.mode(hw.BALANCED).update(power_apply=True, ppt={"ppt_pl1_spl": 45})
        d.apply_mode(hw.BALANCED)
        self.assertEqual(d.cpu.ceilings["ppt_pl1_spl"], 45)
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        clock[0] += 1
        d.tick()
        d.apply_mode(hw.TURBO)
        self.assertEqual(hw.get_attr("ppt_pl1_spl").current, 45)
        self.assertIsNone(d.cpu.target)

    def test_external_profile_does_not_restore_old_caps(self):
        d, _, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        clock[0] += 1
        d.tick()
        hw.set_attr("ppt_pl1_spl", 35)
        hw.set_profile("quiet")
        d.tick()
        self.assertEqual(hw.get_attr("ppt_pl1_spl").current, 35)
        self.assertIsNone(d.cpu.target)

    def test_missing_fans_prevents_control_on_required_model(self):
        d, _, _ = self._daemon(85)
        with patch.object(fans, "available_fans", return_value=[]):
            d.apply_mode(hw.BALANCED)
        self.assertIsNone(d.cpu.target)
        self.assertIn("fan curve", d.cpu.error)

    def _cpufreq(self, governor="powersave", epp="performance", boost=1):
        """cpufreq files the ladder writes; the G713PV tree has none by default."""
        put(R + hw.CPU_BOOST_PATH, boost)
        for i in range(2):
            cpu = R + f"/sys/devices/system/cpu/cpu{i}/cpufreq"
            put(cpu + "/scaling_governor", governor)
            put(cpu + "/scaling_available_governors", "performance powersave")
            put(cpu + "/energy_performance_preference", epp)
            put(cpu + "/energy_performance_available_preferences",
                "default performance balance_performance balance_power power")

    def _policy(self):
        cpu = R + "/sys/devices/system/cpu/cpu0/cpufreq"
        return (get(cpu + "/scaling_governor"), get(cpu + "/energy_performance_preference"), get(R + hw.CPU_BOOST_PATH))

    def test_floor_edit_is_light_and_validated(self):
        d, _, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        for bad in (20, 200, "40", 40.0):
            with self.assertRaisesRegex(ValueError, "30..130 W"):
                d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp_floor_w": bad}})
        with patch.object(hw, "set_profile") as profile:
            d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp_floor_w": 40}})
            profile.assert_not_called()
        self.assertEqual(d.cpu.floor_w, 40)
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        for _ in range(10):
            clock[0] += 1
            d.tick()
        self.assertEqual(hw.get_attr("ppt_pl1_spl").current, 40)
        self.assertEqual(d.status()["cpu_temp"]["floor_w"], 40)
        d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp_floor_w": None}})
        self.assertIsNone(d.cpu.floor_w)

    def test_policy_written_before_controller_starts(self):
        d, _, _ = self._daemon(85)
        order = []
        with patch.object(d, "apply_cpu_policy", side_effect=lambda m: order.append("policy")), \
                patch.object(d, "apply_cpu_temp", side_effect=lambda t, m=None: order.append("target")):
            d.apply_mode(hw.BALANCED)
        self.assertEqual(order, ["policy", "target"])

    def test_ladder_runs_through_daemon_and_mode_change_restores(self):
        self._cpufreq()
        d, _, clock = self._daemon(85)
        d.cfg.mode(hw.BALANCED).update(cpu_boost="max")
        d.cfg.mode(hw.TURBO).update(cpu_boost="aggressive")
        d.apply_mode(hw.BALANCED)
        self.assertEqual(d.cpu.baseline.boost_mode, "max")
        self.assertEqual(d.cpu.ladder[0], ("epp", "balance_performance"))
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        lines = []
        with patch.object(daemon, "log", side_effect=lines.append):
            for _ in range(40):
                clock[0] += 1
                d.tick()
        self.assertEqual(d.status()["cpu_temp"]["state"], "limited")
        self.assertGreaterEqual(d.cpu.stage, 1)
        self.assertEqual(self._policy()[1], d.cpu.effective["epp"])
        self.assertNotEqual(self._policy()[1], "performance")
        self.assertTrue(any("stage 1/4" in l for l in lines), lines)
        d.apply_mode(hw.TURBO)
        self.assertEqual(d.cpu.stage, 0)
        self.assertEqual(self._policy(), ("powersave", "balance_performance", "1"))  # Turbo's baseline

    def test_firmware_reset_after_profile_does_not_latch(self):
        d, _, clock = self._daemon(85)
        d.cfg.mode(hw.BALANCED).update(power_apply=True, ppt={"ppt_pl1_spl": 45})
        d.apply_mode(hw.BALANCED)
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        clock[0] += 1
        d.tick()
        hw.set_attr("ppt_pl1_spl", 120)  # the delayed throttle-policy rerun undid the user's cap
        lines = []
        with patch.object(daemon, "log", side_effect=lines.append):
            clock[0] += 1
            d.tick()
        self.assertEqual(d.cpu.error, "")
        self.assertLess(hw.get_attr("ppt_pl1_spl").current, 45)
        self.assertTrue(any("re-asserted" in l for l in lines), lines)

    def test_clear_notice_is_not_undone_by_the_same_error(self):
        d, _, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        os.remove(R + "/sys/class/hwmon/hwmon5/temp1_input")
        clock[0] += 1
        d.tick()
        self.assertIn("sensor", d.status()["notice"])
        d.handle({"cmd": "clear_notice"})
        clock[0] += 1
        d.tick()
        self.assertEqual(d.status()["notice"], "")
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 90000)
        d.apply_mode(hw.BALANCED)  # a clean restart clears the record: a new fault is shown again
        os.remove(R + "/sys/class/hwmon/hwmon5/temp1_input")
        clock[0] += 1
        d.tick()
        self.assertIn("sensor", d.status()["notice"])

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

    def test_hot_episode_ends_when_the_threshold_changes(self):
        d, smn, clock = self._daemon(80)
        d.apply_mode(hw.BALANCED)
        lines = self._hot_run(d, clock, [86, 87])
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 88  # threshold 85 -> 93: 87 is no longer hot
        lines += self._hot_run(d, clock, [87, 87])
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 80  # back to 85: a new episode, logged again
        lines += self._hot_run(d, clock, [87])
        hot = [l for l in lines if "high temperature" in l]
        self.assertEqual(len(hot), 3, hot)
        self.assertIn("threshold changed to 93", hot[1])
        self.assertIn("threshold 85", hot[2])

    def test_target_change_of_the_running_mode_is_logged(self):
        d, smn, clock = self._daemon(82)
        d.apply_mode(hw.BALANCED)
        lines = []
        with patch.object(daemon, "log", side_effect=lines.append):
            d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": 85}})
            d.handle({"cmd": "update_mode", "mode": hw.BALANCED, "data": {"cpu_temp": 85}})
        hits = [l for l in lines if l.startswith("cpu target: 85°C")]
        self.assertEqual(len(hits), 1, lines)  # the change, not the unchanged resend

    def _hot_notice_setup(self):
        d, smn, clock = self._daemon(80)
        d.apply_mode(hw.BALANCED)
        return d, clock

    def test_hot_notice_after_a_minute_above_lowered_limit(self):
        d, clock = self._hot_notice_setup()
        self._hot_run(d, clock, [88] * 60)  # the episode opens on the first call: 59 s so far
        self.assertEqual(d.notice, "")
        self._hot_run(d, clock, [88])
        self.assertIn("88 °C", d.notice)
        self.assertIn("80 °C limit is not being held", d.notice)

    def test_hot_notice_waits_for_other_notice_and_comes_later(self):
        d, clock = self._hot_notice_setup()
        d.set_notice("something else")
        self._hot_run(d, clock, [88] * 70)
        self.assertEqual(d.notice, "something else")
        d.handle({"cmd": "clear_notice"})
        self._hot_run(d, clock, [88])
        self.assertIn("limit is not being held", d.notice)

    def test_hot_notice_not_raised_again_after_dismissal(self):
        d, clock = self._hot_notice_setup()
        self._hot_run(d, clock, [88] * 61)
        self.assertIn("limit is not being held", d.notice)
        d.handle({"cmd": "clear_notice"})
        self._hot_run(d, clock, [88] * 30)
        self.assertEqual(d.notice, "")

    def test_hot_notice_goes_away_with_the_episode_but_not_others(self):
        d, clock = self._hot_notice_setup()
        self._hot_run(d, clock, [88] * 61)
        self.assertIn("limit is not being held", d.notice)
        self._hot_run(d, clock, [70])
        self.assertEqual(d.notice, "")
        self._hot_run(d, clock, [88] * 61)
        d.set_notice("something else")
        self._hot_run(d, clock, [70])
        self.assertEqual(d.notice, "something else")

    def test_hot_notice_goes_away_when_the_threshold_changes(self):
        d, clock = self._hot_notice_setup()
        self._hot_run(d, clock, [88] * 61)
        self.assertIn("limit is not being held", d.notice)
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 90
        self._hot_run(d, clock, [88])
        self.assertEqual(d.notice, "")

    def test_no_hot_notice_without_lowered_limit(self):
        d, smn, clock = self._daemon(None)
        self._hot_run(d, clock, [96] * 120)
        self.assertEqual(d.notice, "")

    def test_hot_notice_clock_starts_again_with_a_new_limit(self):
        # 92 keeps the threshold at 95, so the episode goes on: the limit still gets its own minute
        d, smn, clock = self._daemon(None)
        self._hot_run(d, clock, [96] * 120)
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 92
        self._hot_run(d, clock, [96] * 60)
        self.assertEqual(d.notice, "")
        self._hot_run(d, clock, [96])
        self.assertIn("92 °C limit is not being held", d.notice)

    def test_hot_notice_does_not_outlive_its_limit(self):
        d, smn, clock = self._daemon(90)
        d.apply_mode(hw.BALANCED)
        self._hot_run(d, clock, [96] * 61)
        self.assertIn("90 °C limit is not being held", d.notice)
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = None  # the threshold stays 95
        self._hot_run(d, clock, [96] * 90)
        self.assertEqual(d.notice, "")

    def test_no_hot_notice_while_under_the_threshold(self):
        d, clock = self._hot_notice_setup()
        self._hot_run(d, clock, [88] + [84] * 70)  # 84 keeps the episode open, but is not over 85
        self.assertEqual(d.notice, "")

    def test_normal_temperature_not_logged(self):
        d, smn, clock = self._daemon(85)
        d.apply_mode(hw.BALANCED)
        lines = self._hot_run(d, clock, [60, 85, 89, 70])
        self.assertEqual([l for l in lines if "high temperature" in l], [])
        self.assertEqual(d.status()["temp_peak"]["max"], 89)



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
    def test_save_syncs_the_folder_after_the_rename(self):
        c = config.Config()
        synced = []
        real = os.fsync
        with patch.object(config.os, "fsync", side_effect=lambda fd: (synced.append(os.fstat(fd).st_mode), real(fd))[1]):
            c.save()
        import stat as st
        self.assertEqual([st.S_ISDIR(m) for m in synced], [False, True])

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

    def test_bad_values_are_sanitized_at_load(self):
        c = config.Config()
        c.mode(0).update(cpu_temp=96, cpu_boost="turbo", cpu_governor="ondemand", cpu_temp_floor_w="x", ppt=None)
        c.mode(1).update(cpu_temp="85", power_apply=1)
        c.mode(2).update(cpu_temp=50)
        c.save()
        c2 = config.Config()
        self.assertEqual([c2.mode(i)["cpu_temp"] for i in (0, 1, 2)], [None, None, None])
        self.assertEqual((c2.mode(0)["cpu_boost"], c2.mode(0)["cpu_governor"]), ("default", "default"))
        self.assertEqual((c2.mode(0)["cpu_temp_floor_w"], c2.mode(0)["ppt"]), (None, {}))
        self.assertIs(c2.mode(1)["power_apply"], True)
        # an older config without the floor key loads with the default
        del c2.mode(0)["cpu_temp_floor_w"]
        c2.save()
        self.assertIsNone(config.Config().mode(0)["cpu_temp_floor_w"])


class TestAuraZonesAndFollow(Base):
    """Zone colors and the colors that follow the machine (heatmap, GPU mode, battery)."""

    def setUp(self):
        super().setUp()
        open(R + "/dev/hidraw1", "w").close()

    def packets(self):
        """What was sent to the keyboard since the last call (each send starts the fake device file over)."""
        with open(R + "/dev/hidraw1", "rb") as f:
            data = f.read()
        open(R + "/dev/hidraw1", "w").close()
        return [data[i:i + 13] for i in range(0, len(data), 64)]

    def set_aura(self, d, **kw):
        return d.handle({"cmd": "set_aura", "color": [255, 0, 0], **kw})

    def test_zone_message(self):
        self.assertEqual(list(aura.message("static", (1, 2, 3), zone=3))[:7], [0x5D, 0xB3, 3, 0, 1, 2, 3])
        self.assertEqual(aura.message("static", (0, 0, 0), zone=1)[9], 0)   # off, not a random color

    def test_zone_colors_are_written_left_to_right_and_stored(self):
        aura.apply_zones([(255, 0, 0), (0, 255, 0), (0, 0, 255), (9, 9, 9)])
        p = self.packets()
        self.assertEqual([(x[2], tuple(x[4:7])) for x in p[:4]],
                         [(1, (255, 0, 0)), (2, (0, 255, 0)), (3, (0, 0, 255)), (4, (9, 9, 9))])
        self.assertEqual([x[1] for x in p[4:]], [0xB5, 0xB4])
        with self.assertRaises(ValueError):
            aura.apply_zones([(1, 1, 1)])

    def test_effects_follow_the_kind_of_keyboard(self):
        # the answer of a G713PV with a per-key keyboard, as read from the machine
        answer = bytes.fromhex("5d0520310020030100032305020246031501")
        self.assertEqual(aura.kind_of(answer), "perkey")
        self.assertEqual(aura.kind_of(answer[:9] + b"\x02" + answer[10:]), "zones")
        self.assertEqual(aura.kind_of(answer[:9] + b"\x04" + answer[10:]), "single")
        self.assertIsNone(aura.kind_of(answer[:9] + b"\x7f" + answer[10:]))
        self.assertIsNone(aura.kind_of(b"\x5d\x00\x00"))
        self.assertIsNone(aura.kind_of(bytes(64)))             # no answer: the buffer came back empty
        perkey, zones, single = (aura.modes_for(k) for k in ("perkey", "zones", "single"))
        for m in aura.PER_KEY_MODES:
            self.assertIn(m, perkey)
            self.assertNotIn(m, zones)
            self.assertNotIn(m, aura.modes_for(None))
        self.assertEqual(zones[:2], ["static", "zones"])
        self.assertNotIn("zones", perkey)
        self.assertNotIn("rainbow", single)
        self.assertEqual(aura.modes_for(None)[:5], ["static", "breathe", "cycle", "rainbow", "strobe"])
        for kind in ("perkey", "zones", "single", None):
            self.assertEqual(aura.modes_for(kind)[-3:], list(aura.FOLLOW_MODES))
            self.assertLessEqual(set(aura.modes_for(kind)), set(aura.ALL_MODES))

    def test_keyboard_is_asked_once_and_never_through_a_plain_file(self):
        self.assertIsNone(aura.probe())                         # the fake device is a file: nothing is sent
        self.assertEqual(self.packets(), [])
        d = TestDaemon.make(self)
        asked = []
        with patch.object(aura, "probe", side_effect=lambda: asked.append(1) or "perkey"):
            self.assertIn("star", d.status()["aura_modes"])
            self.assertIn("star", d.status()["aura_modes"])
        self.assertEqual(len(asked), 1)
        d = TestDaemon.make(self)
        with patch.object(aura, "probe", side_effect=lambda: asked.append(1)):
            self.assertNotIn("star", d.status()["aura_modes"])
            d.status()
        self.assertEqual(len(asked), 2)                         # no answer is not asked again either

    def test_per_key_effect_numbers(self):
        self.assertEqual([aura.message(m, (1, 2, 3))[3] for m in ("star", "rain", "highlight", "laser", "ripple",
                                                                 "comet", "flash")], [4, 5, 6, 7, 8, 11, 12])
        d = TestDaemon.make(self)
        self.set_aura(d, mode="star")
        self.assertEqual(self.packets()[0][3], 4)

    def test_color_scales(self):
        self.assertEqual(aura.scale(aura.HEAT_STOPS, 10), aura.BLUE)
        self.assertEqual(aura.scale(aura.HEAT_STOPS, 60), aura.GREEN)
        self.assertEqual(aura.scale(aura.HEAT_STOPS, 82.5), (255, 85, 0))     # halfway from amber to red
        self.assertEqual(aura.scale(aura.HEAT_STOPS, 104), aura.RED)
        self.assertEqual(aura.scale(aura.BATTERY_STOPS, 5), aura.RED)
        self.assertEqual(aura.scale(aura.BATTERY_STOPS, 100), aura.GREEN)
        self.assertEqual(aura.follow_color("gpu", None, None, "eco"), aura.GREEN)
        self.assertEqual(aura.follow_color("gpu", None, None, "ultimate"), aura.RED)
        self.assertEqual(aura.follow_color("gpu", None, None, "unknown"), aura.GPU_COLOR_OTHER)
        self.assertIsNone(aura.follow_color("heatmap", None, None, None))

    def test_set_zones_through_the_service(self):
        d = TestDaemon.make(self)
        zones = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]]
        self.set_aura(d, mode="zones", zones=zones)
        self.assertEqual(d.cfg["aura"]["zones"], zones)
        self.assertEqual([x[2] for x in self.packets()[:4]], [1, 2, 3, 4])
        for bad in (None, zones[:3], [[1, 2]] * 4, "red"):
            with self.assertRaises(ValueError, msg=bad):
                self.set_aura(d, mode="zones", zones=bad)
        self.assertEqual(d.cfg["aura"]["zones"], zones)   # a refused request changes nothing
        with self.assertRaises(ValueError):
            self.set_aura(d, mode="disco")

    def test_heatmap_writes_only_when_the_color_moved(self):
        d = TestDaemon.make(self)
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 60000)
        self.set_aura(d, mode="heatmap")
        p = self.packets()
        self.assertEqual(tuple(p[0][4:7]), aura.GREEN)
        self.assertEqual([x[1] for x in p], [0xB3, 0xB5])     # shown, not stored in the keyboard
        d.follow_aura()
        d.follow_aura()
        self.assertEqual(self.packets(), [])                    # same temperature: nothing written
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 60400)
        d.follow_aura()
        self.assertEqual(self.packets(), [])                    # a change too small to see
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 90000)
        d.follow_aura()
        self.assertEqual(tuple(self.packets()[0][4:7]), (255, 0, 0))

    def test_battery_and_gpu_modes(self):
        d = TestDaemon.make(self)
        self.set_aura(d, mode="battery")                        # the tree's battery is at 76 %
        self.assertEqual(tuple(self.packets()[0][4:7]), aura.scale(aura.BATTERY_STOPS, 76))
        self.set_aura(d, mode="gpu")
        self.assertEqual(tuple(self.packets()[0][4:7]), aura.BLUE)              # the tree is in Standard

    def test_follow_mode_is_put_back_by_reapply_and_left_by_a_plain_effect(self):
        d = TestDaemon.make(self)
        self.set_aura(d, mode="heatmap")
        self.packets()
        d.apply_extras()                                        # after sleep the keyboard forgot the color
        self.assertEqual([x[1] for x in self.packets()], [0xB3, 0xB5])
        self.set_aura(d, mode="static")
        self.packets()
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        d.follow_aura()
        self.assertEqual(self.packets(), [])

    def test_missing_keyboard_does_not_stop_the_tick(self):
        d = TestDaemon.make(self)
        self.set_aura(d, mode="heatmap")
        os.remove(R + "/dev/hidraw1")
        put(R + "/sys/class/hwmon/hwmon5/temp1_input", 95000)
        d.tick()
        d.tick()
        self.assertTrue(d.aura_follow_error)

    def test_stored_zones_and_follow_modes_survive_a_reload(self):
        c = config.Config()
        c["aura"] = {"mode": "zones", "color": [1, 2, 3], "color2": None, "speed": "normal",
                     "zones": [[1, 2, 3]] * 4}
        c.save()
        self.assertEqual(config.Config()["aura"]["mode"], "zones")
        for broken in ({"mode": "zones", "color": [1, 2, 3]},
                       {"mode": "zones", "color": [1, 2, 3], "zones": [[1, 2, 3]] * 5},
                       {"mode": "static", "color": [1, 2, 3], "zones": "x"}):
            data = {"aura": broken}
            config.sanitize_aura(data)
            self.assertIsNone(data["aura"], broken)
        ok = {"aura": {"mode": "heatmap", "color": [1, 2, 3]}}
        config.sanitize_aura(ok)
        self.assertIsNotNone(ok["aura"])


class TestDaemon(Base):
    def make(self):
        return daemon.Daemon(config.Config())

    def test_governor_only_update_holds_cpu_lock(self):
        d = self.make()
        held = []
        with patch.object(hw, "cpu_governor_choices", return_value=["default", "performance"]), \
                patch.object(hw, "set_cpu_governor",
                             side_effect=lambda value: held.append((d.lock._is_owned(), d.cpu_lock._is_owned()))):
            d.handle({"cmd": "update_mode", "mode": d.cfg["mode"],
                      "data": {"cpu_governor": "performance"}})
        self.assertEqual(held, [(True, True)])

    def test_boot_profile_failure_keeps_independent_controls(self):
        d = self.make()
        d.cfg["charge_limit"] = 60
        d.cfg["overdrive"] = None
        put(R + hw.PLATFORM_PROFILE, "low-power")
        d.cfg.mode(hw.BALANCED).update(power_apply=True, ppt={"ppt_pl1_spl": 100})
        error = OSError(errno.EOPNOTSUPP, "Operation not supported")
        with patch.object(hw, "set_profile", side_effect=error), \
                patch.object(fans, "write_curve") as curve, \
                patch.object(hw, "set_attr") as attr, \
                patch.object(d, "apply_gpu_target") as target:
            d.reapply(at_boot=True)
        self.assertEqual(get(R + "/sys/class/power_supply/BAT0/charge_control_end_threshold"), "60")
        target.assert_called_once_with(d.cfg["gpu_target"])
        curve.assert_not_called()
        attr.assert_not_called()
        self.assertFalse(d.custom_power)
        self.assertIn("startup performance mode", d.notice)
        self.assertIn("Operation not supported", d.notice)
        self.assertEqual(d.status()["profile"], "low-power")
        self.assertTrue(d.source_mode_pending)
        self.assertEqual(d.last_profile, "low-power")
        # The independent command path remains usable after the failed reapply.
        d.handle({"cmd": "set_charge_limit", "value": 70})
        self.assertEqual(d.status()["battery"]["limit"], 70)

    def test_failed_mode_change_keeps_the_running_mode_whole(self):
        d = self.make()
        d.cfg.mode(hw.BALANCED)["cpu_temp"] = 85
        d.apply_mode(hw.BALANCED)
        self.assertEqual(d.cpu.target, 85)
        error = OSError(errno.EOPNOTSUPP, "Operation not supported")
        with patch.object(hw, "set_profile", side_effect=error):
            with self.assertRaises(OSError):
                d.handle({"cmd": "set_mode", "mode": hw.TURBO})
        self.assertEqual(d.cfg["mode"], hw.BALANCED)
        self.assertEqual(d.cfg[d.source_key()], hw.BALANCED)  # a later save does not store Turbo
        self.assertEqual(d.cpu.target, 85)                    # the temperature target is still held
        self.assertEqual(d.status()["cpu_temp"]["state"], "active")

    def test_resume_retries_profile_but_explicit_mode_failure_is_reported(self):
        d = self.make()
        d.cfg["gpu_target"] = gpu.OPTIMIZED
        error = OSError(errno.EOPNOTSUPP, "Operation not supported")
        with patch.object(hw, "set_profile", side_effect=error), \
                patch.object(d, "apply_gpu_target") as target:
            d.reapply()  # resume retries the refused profile and still applies Optimized
            with self.assertRaises(OSError):
                d.handle({"cmd": "set_mode", "mode": hw.TURBO})
        target.assert_called_once_with(gpu.OPTIMIZED)
        self.assertEqual(get(R + hw.PLATFORM_PROFILE), "balanced")

    def test_boot_profile_retry_clears_startup_notice(self):
        d = self.make()
        d.cfg['mode_ac'] = hw.TURBO
        error = OSError(errno.EBUSY, 'Device or resource busy')
        with patch.object(hw, 'set_profile', side_effect=error):
            d.reapply(at_boot=True)
        self.assertTrue(d.source_mode_pending)
        self.assertTrue(d.notice.startswith('Could not apply the startup performance mode'))
        self.assertEqual(hw.get_profile(), 'balanced')
        for _ in range(daemon.ECO_RETRY_TICKS):
            d.tick()
        self.assertEqual(hw.get_profile(), 'performance')
        self.assertEqual(d.cfg['mode'], hw.TURBO)
        self.assertFalse(d.source_mode_pending)
        self.assertEqual(d.notice, '')

    def test_reading_a_factory_curve_keeps_the_pending_switch_and_the_notice(self):
        # the fan page reads the firmware curve 400 ms after the window connects; that re-applies
        # the running mode with write_profile=False, which is not the refused mode landing
        d = self.make()
        d.cfg['mode_ac'] = hw.TURBO
        with patch.object(hw, 'set_profile', side_effect=OSError(errno.EBUSY, 'busy')):
            d.reapply(at_boot=True)
        notice = d.notice
        self.assertTrue(notice.startswith('Could not apply the startup performance mode'))
        d.apply_mode(d.cfg['mode'], write_profile=False)
        self.assertTrue(d.source_mode_pending)
        self.assertEqual(d.notice, notice)
        for _ in range(daemon.ECO_RETRY_TICKS):
            d.tick()
        self.assertEqual(hw.get_profile(), 'performance')
        self.assertEqual(d.notice, '')

    def test_chosen_mode_clears_the_startup_notice(self):
        d = self.make()
        error = OSError(errno.EBUSY, 'Device or resource busy')
        with patch.object(hw, 'set_profile', side_effect=error):
            d.reapply(at_boot=True)
        self.assertTrue(d.notice.startswith('Could not apply the startup performance mode'))
        d.set_mode(hw.SILENT)
        self.assertEqual(hw.get_profile(), 'quiet')
        self.assertEqual(d.notice, '')

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

    def test_gpu_temp_target_does_not_wait_for_the_power_switch(self):
        d = self.make()
        m = d.cfg.mode(hw.TURBO)
        m.update(power_apply=False, ppt={"ppt_pl1_spl": 100}, nv={"nv_dynamic_boost": 20, "nv_temp_target": 80})
        spl, boost = get(f"{ARM}/ppt_pl1_spl/current_value"), get(f"{ARM}/nv_dynamic_boost/current_value")
        d.set_mode(hw.TURBO)
        self.assertEqual(get(f"{ARM}/nv_temp_target/current_value"), "80")
        self.assertEqual(get(f"{ARM}/ppt_pl1_spl/current_value"), spl)  # the limits stay with the switch
        self.assertEqual(get(f"{ARM}/nv_dynamic_boost/current_value"), boost)
        self.assertFalse(d.custom_power)

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

    def test_update_mode_rejects_unknown_power_attribute_names(self):
        # the name becomes part of a path root writes to: only the known limits are accepted
        victim = R + "/etc/victim"
        put(victim, "IMPORTANT")
        d = self.make()
        for key, name in (("ppt", "../../../../etc/victim"), ("nv", "../../../../etc/victim"),
                          ("ppt", "dgpu_disable"), ("nv", "ppt_pl1_spl")):
            with self.assertRaisesRegex(ValueError, key):
                d.handle({"cmd": "update_mode", "mode": 0, "data": {"power_apply": True, key: {name: 0}}})
        self.assertEqual(get(victim), "IMPORTANT")
        self.assertEqual(d.cfg.mode(0)["ppt"].get("../../../../etc/victim"), None)
        d.handle({"cmd": "update_mode", "mode": 0, "data": {"ppt": {"ppt_pl1_spl": 60}, "nv": {"nv_temp_target": 80}}})
        self.assertEqual(d.cfg.mode(0)["ppt"], {"ppt_pl1_spl": 60})

    def test_stored_unknown_power_attribute_names_are_dropped(self):
        entry = config.sanitize_mode({"base": 0, "ppt": {"../../../../etc/victim": 0, "ppt_pl1_spl": 60},
                                      "nv": {"x/y": 1, "nv_dynamic_boost": 10}})
        self.assertEqual(entry["ppt"], {"ppt_pl1_spl": 60})
        self.assertEqual(entry["nv"], {"nv_dynamic_boost": 10})

    def test_attr_names_cannot_leave_the_attribute_folders(self):
        victim = R + "/etc/victim"
        put(victim, "IMPORTANT")
        for name in ("../../../../etc/victim", "a/b", "..", ""):
            self.assertIsNone(hw.attr_path(name))
            with self.assertRaises(ValueError):
                hw.set_attr(name, 5)
        self.assertEqual(get(victim), "IMPORTANT")

    def test_factory_read_does_not_switch_off_a_curve_just_written(self):
        # asus-wmi: pwmN_enable=3 sends the throttle policy again, which switches every custom curve off
        real_write = hw.write

        def kernel_write(path, value):
            real_write(path, value)
            if path.endswith("_enable") and int(value) == 3:
                for n in (1, 2):
                    real_write(f"{os.path.dirname(path)}/pwm{n}_enable", 2)

        d = self.make()
        m = d.cfg.mode(hw.BALANCED)
        m.update(fans_apply=True, cpu_temp=85, fan_cpu=list(fans.DEFAULTS[hw.BALANCED]["cpu"]), fan_gpu=None)
        with patch.object(hw, "write", side_effect=kernel_write):
            d.apply_mode(hw.BALANCED)
        self.assertTrue(fans.is_enabled("cpu"))
        self.assertTrue(fans.is_enabled("gpu"))

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
        self.assertEqual(set(s["cpu_policy"]), {"governor", "epp", "boost"})
        for key in ("stage", "stage_max", "ladder", "floor_w", "effective", "baseline"):
            self.assertIn(key, s["cpu_temp"])
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
    def test_failed_connections_close_the_socket(self):
        created = []
        original = client.socket.socket

        def tracked(*args, **kwargs):
            sock = original(*args, **kwargs)
            created.append(sock)
            return sock

        with patch.object(client, "SOCKET", TMP + "/nonexistent.sock"), \
                patch.object(client.socket, "socket", side_effect=tracked):
            for _ in range(3):
                with self.assertRaises(client.DaemonError):
                    client.call("status")
        self.assertEqual(len(created), 3)
        self.assertTrue(all(sock.fileno() == -1 for sock in created))

    def _serve_raw(self, reply, hold=0.0):
        """A stand-in service on the socket that sends `reply` (bytes) and closes."""
        import socket
        path = TMP + "/raw.sock"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)

        def run():
            conn, _ = srv.accept()
            conn.recv(65536)
            time.sleep(hold)
            if reply:
                conn.sendall(reply)
            conn.close()
            srv.close()
        threading.Thread(target=run, daemon=True).start()
        return path

    def test_broken_answers_are_daemon_errors_not_tracebacks(self):
        for reply, hold in ((b'{"ok": true, "mo', 0.0), (b"", 0.0), (b"[1, 2]\n", 0.0), (b"", 0.6)):
            with patch.object(client, "SOCKET", self._serve_raw(reply, hold)):
                with self.assertRaises(client.DaemonError):
                    client.call("status", timeout=0.2)
        with open(os.devnull, "w") as null, patch.object(client, "SOCKET", self._serve_raw(b'{"ok": true, "mo')), \
                contextlib.redirect_stderr(null):
            self.assertEqual(client.main(["status"]), 1)

    def test_idle_connection_is_dropped(self):
        import socket
        sock = TMP + "/idle.sock"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(sock)
        with patch.object(daemon.Handler, "timeout", 0.2):
            srv = daemon.Server(sock, daemon.Handler)
            srv.daemon_obj = daemon.Daemon(config.Config())
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(3)
                    s.connect(sock)
                    started = time.time()
                    self.assertEqual(s.recv(16), b"")  # closed by the service, nothing was sent
                    self.assertLess(time.time() - started, 2.5)
            finally:
                srv.shutdown()
                srv.server_close()
        self.assertEqual(daemon.Handler.timeout, daemon.IDLE_TIMEOUT)

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
            self.assertEqual(client.main(["cpufloor", "45"]), 0)
            self.assertEqual(d.cfg.mode(hw.TURBO)["cpu_temp_floor_w"], 45)
            self.assertEqual(client.main(["cpufloor", "min"]), 0)
            self.assertIsNone(d.cfg.mode(hw.TURBO)["cpu_temp_floor_w"])
            # a line that never ends is cut off at the limit, and the service keeps answering
            import socket
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(10)
                c.connect(sock)
                c.sendall(b"x" * (daemon.MAX_REQUEST + 4096))
                reply = c.makefile().readline()
            self.assertEqual(json.loads(reply), {"ok": False, "error": "Request too large"})
            self.assertEqual(client.call("status")["profile"], "performance")
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
        self.requested = []

        def urlopen(req, *a, **k):
            self.requested.append(req.full_url)
            return R(payload)
        return patch.object(update.urllib.request, "urlopen", urlopen)

    def test_latest_is_a_published_release_with_the_helper(self):
        from boreas import update
        assets = [{"name": "boreas-gpu-engine"}, {"name": "boreas-gpu-engine.sha256"}]
        ok = {"tag_name": "v9.0.0", "draft": False, "prerelease": False, "assets": assets}
        with self._serve(json.dumps(ok).encode()):
            self.assertEqual(update.fetch_latest(), "9.0.0")
        self.assertEqual(self.requested, ["https://api.github.com/repos/Yusuf-Yonturk/boreas/releases/latest"])
        # not installable yet, or not a release at all: nothing is offered
        for bad in ({**ok, "assets": assets[:1]}, {**ok, "assets": []}, {**ok, "assets": None}, {**ok, "draft": True},
                    {**ok, "prerelease": True}, {**ok, "tag_name": "nightly"}, {**ok, "tag_name": "v9/../x"},
                    {**ok, "tag_name": None}, {"message": "API rate limit exceeded"}, [], "<html>"):
            with self.subTest(bad=bad):
                text = bad if isinstance(bad, str) else json.dumps(bad)
                self.assertIsNone(update.parse_release(text))

    def _pkexec(self, code=0):
        """pkexec and a trusted helper, with subprocess.run recorded instead of run."""
        import subprocess as sp
        from boreas import update
        self.seen = []
        self.run_kwargs = []
        self.code = code

        def fake_run(cmd, **kw):
            self.seen.append(cmd)
            self.run_kwargs.append(kw)
            return sp.CompletedProcess(cmd, self.code, "", "boom")
        no_proxy = {k: v for k, v in os.environ.items() if k.lower() != "https_proxy"}
        stack = contextlib.ExitStack()
        stack.enter_context(patch.dict(os.environ, no_proxy, clear=True))
        stack.enter_context(patch.object(update, "_root_tree", lambda folder: True))
        return stack, [patch.object(update.shutil, "which", lambda n: "/usr/bin/pkexec"),
                       patch.object(update, "_root_only", lambda path: True),
                       patch.object(update.subprocess, "run", fake_run)]

    def _patched(self, code=0):
        stack, patches = self._pkexec(code)
        for p in patches:
            stack.enter_context(p)
        return stack

    def test_run_update_hands_only_the_version_to_a_root_helper(self):
        # nothing the user can write runs as root: pkexec starts the installed helper, which
        # downloads the release into root's own folder
        from boreas import update
        with self._patched():
            self.assertEqual(update.run_update("9.0.0"), "9.0.0")
        self.assertEqual(self.seen, [["/usr/bin/pkexec", update.PYTHON, "-I", update.HELPER, "9.0.0"]])
        self.assertEqual(update.HELPER, "/usr/lib/boreas/boreas-update")

    def test_run_update_refuses_a_helper_the_user_could_change(self):
        from boreas import update
        stack, patches = self._pkexec()
        with stack:
            for p in (patches[0], patches[2]):
                stack.enter_context(p)
            with patch.object(update, "_root_only", lambda path: False):
                with self.assertRaises(RuntimeError):
                    update.run_update("9.0.0")
        self.assertEqual(self.seen, [])  # pkexec was never started

    def test_run_update_checks_the_code_the_helper_imports(self):
        from boreas import update
        stack, patches = self._pkexec()
        with stack:
            for p in patches:
                stack.enter_context(p)
            with patch.object(update, "_root_tree", lambda folder: folder != "/usr/lib/boreas/boreas"):
                with self.assertRaises(RuntimeError):
                    update.run_update("9.0.0")
        self.assertEqual(self.seen, [])

    def test_run_update_hands_the_https_proxy_over(self):
        # pkexec clears the environment; without this a proxy-only network could not update
        from boreas import update
        proxy = "http://proxyuser:proxypass@proxy.lan:3128"
        with self._patched():
            os.environ["https_proxy"] = proxy
            update.run_update("9.0.0")
        for secret in ("proxyuser", "proxypass", proxy):
            self.assertNotIn(secret, " ".join(self.seen[0]))
        self.assertEqual(self.seen[0][-2:], ["--proxy-stdin", "9.0.0"])
        self.assertEqual(self.run_kwargs[0]["input"], proxy + "\n")
        self.assertNotIn("stdin", self.run_kwargs[0])

    def test_run_update_normalizes_supported_proxies(self):
        from boreas import update
        for value, expected in (("proxy.lan:3128", "http://proxy.lan:3128"),
                                ("HTTP://proxy.lan:3128", "http://proxy.lan:3128"),
                                ("HTTPS://user:pass@proxy.lan:3128/", "https://user:pass@proxy.lan:3128/"),
                                ("http://[::1]:3128", "http://[::1]:3128"),
                                ("[2001:db8::1]:3128", "http://[2001:db8::1]:3128"),
                                ("http://user:pass@[::1]:3128", "http://user:pass@[::1]:3128"),
                                # RFC 3986 lets a user name or password use ~ ! $ & ' ( ) * + , ; =
                                ("http://ali~veli:p!a$s&w'o(r)d*+,;=@proxy.lan:3128",
                                 "http://ali~veli:p!a$s&w'o(r)d*+,;=@proxy.lan:3128")):
            with self.subTest(proxy=value), self._patched():
                os.environ["HTTPS_PROXY"] = value
                update.run_update("9.0.0")
                self.assertEqual(self.seen[0][-2:], ["--proxy-stdin", "9.0.0"])
                self.assertEqual(self.run_kwargs[0]["input"], expected + "\n")

    def test_run_update_rejects_invalid_proxy_before_pkexec(self):
        from boreas import update
        for bad in ("socks5://proxy.lan:1080", "file:///etc/passwd", "http://proxy.lan:3128 --x",
                    "http://proxy.lan:3128\nhttp://other:3128", "http://[1:2]:3128",
                    "http://proxy.lan:99999", "http://proxy.lan/path"):
            with self.subTest(proxy=bad), self._patched():
                os.environ["https_proxy"] = bad
                with self.assertRaisesRegex(RuntimeError, "(?i)proxy") as error:
                    update.run_update("9.0.0")
                self.assertNotIn(bad, str(error.exception))
                self.assertEqual(self.seen, [])

    def _helper(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "boreas-update")
        loader = importlib.machinery.SourceFileLoader("boreas_update_helper", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        old = os.umask(0o022)
        try:
            loader.exec_module(mod)
        finally:
            os.umask(old)
        return mod

    def test_helper_main_folds_every_failure_into_1(self):
        import io
        from boreas import update
        helper = self._helper()
        calls = []

        def install(v):
            calls.append((v, os.environ.get("https_proxy")))
            if self.fail_with:
                raise self.fail_with
        with patch.object(helper.os, "geteuid", lambda: 0), patch.object(update, "install_release", install), \
                patch.dict(os.environ, {}, clear=False):
            for self.fail_with, code in ((None, 0), (RuntimeError("exit code 126"), 1), (ValueError("x"), 1)):
                with self.subTest(fail=self.fail_with):
                    self.assertEqual(helper.main(["boreas-update", "9.0.0"]), code)
            self.fail_with = None
            with patch.object(helper.sys, "stdin", io.StringIO("http://p:3128\n")):
                self.assertEqual(helper.main(["boreas-update", "--proxy-stdin", "9.0.0"]), 0)
            self.assertEqual(calls[-1], ("9.0.0", "http://p:3128"))
            n = len(calls)
            for bad in (["--proxy", "file:///etc/passwd", "9.0.0"], ["--proxy", "9.0.0"], ["9.0.0", "9.0.1"]):
                with self.subTest(bad=bad):
                    self.assertEqual(helper.main(["boreas-update", *bad]), 1)
            self.assertEqual(len(calls), n)  # none of those reached the install

    def test_helper_refuses_a_release_that_is_not_newer(self):
        import io
        from boreas import update
        helper = self._helper()
        with patch.object(helper.os, "geteuid", return_value=0), \
                patch.object(update, "install_release") as install:
            for old in ("0.0.1", update.__version__):
                with self.subTest(version=old), \
                        patch.object(helper.sys, "stderr", io.StringIO()) as errors:
                    self.assertEqual(helper.main(["boreas-update", old]), 1)
                    self.assertIn("not newer", errors.getvalue())
            install.assert_not_called()

    def test_helper_reads_and_validates_proxy_from_stdin(self):
        import io
        from boreas import update
        helper = self._helper()
        proxy = "http://proxyuser:proxypass@proxy.lan:3128"
        with patch.object(helper.os, "geteuid", return_value=0), \
                patch.object(update, "install_release") as install, \
                patch.dict(os.environ, {}, clear=False), \
                patch.object(helper.sys, "stdin", io.StringIO(proxy + "\nignored\n")):
            self.assertEqual(helper.main(["boreas-update", "--proxy-stdin", "9.0.0"]), 0)
            self.assertEqual(os.environ["https_proxy"], proxy)
            install.assert_called_once_with("9.0.0")
            install.reset_mock()
            for bad in ("", "file:///etc/passwd\n", "http://proxy.lan:3128 --x\n", proxy + "\r\n"):
                with self.subTest(proxy=bad), patch.object(helper.sys, "stdin", io.StringIO(bad)), \
                        patch.object(helper.sys, "stderr", io.StringIO()) as errors:
                    self.assertEqual(helper.main(["boreas-update", "--proxy-stdin", "9.0.0"]), 1)
                    self.assertIn("proxy", errors.getvalue().lower())
            install.assert_not_called()

    def test_helper_subprocess_receives_proxy_on_stdin(self):
        import subprocess as sp
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        proxy = "http://proxyuser:proxypass@proxy.lan:3128"
        with tempfile.TemporaryDirectory(prefix="boreas-proxy-helper-") as lib:
            helper = os.path.join(lib, "boreas-update")
            shutil.copy(os.path.join(repo, "data", "boreas-update"), helper)
            pkg = os.path.join(lib, "boreas")
            os.mkdir(pkg)
            shutil.copy(os.path.join(repo, "boreas", "update.py"), pkg)
            # Replace installation and the root check only in this disposable subprocess.
            with open(os.path.join(pkg, "__init__.py"), "w") as f:
                # an older installed copy: the helper installs only a newer release
                f.write('__version__ = "8.0.0"\n'
                        'import os\nfrom . import update\nos.geteuid = lambda: 0\n'
                        'def install(version):\n    print(version, os.environ.get("https_proxy"))\n'
                        'update.install_release = install\n')
            for value, expected in ((proxy, proxy), ("proxy.lan:3128", "http://proxy.lan:3128"),
                                    ("HTTP://proxy.lan:3128", "http://proxy.lan:3128"),
                                    ("http://[::1]:3128", "http://[::1]:3128"),
                                    ("socks5://proxy.lan:1080", None), ("file:///etc/passwd", None),
                                    ("http://proxy.lan:99999", None), ("", None)):
                with self.subTest(proxy=value):
                    r = sp.run([sys.executable, "-I", helper, "--proxy-stdin", "9.0.0"],
                               input=value + "\n", capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0 if expected else 1, r.stderr)
                    if expected:
                        self.assertEqual(r.stdout.strip(), "9.0.0 " + expected)
                    else:
                        self.assertIn("proxy", r.stderr.lower())
                        self.assertEqual(r.stdout, "")

    def test_root_only_checks_the_file_and_every_folder_above_it(self):
        from boreas import update
        self.assertTrue(update._root_only("/usr/bin/python3") or os.stat("/usr/bin/python3").st_uid != 0)
        mine = os.path.join(TMP, "boreas-update")
        with open(mine, "w") as f:
            f.write("")
        self.assertFalse(update._root_only(mine))           # owned by the user running the tests
        self.assertFalse(update._root_only(mine + ".missing"))
        self.assertFalse(update._root_tree(TMP))             # a folder of the user's
        self.assertFalse(update._root_tree(TMP + "/missing"))

    def test_run_update_rejects_bad_versions_before_pkexec(self):
        from boreas import update
        with self._patched():
            for bad in ("", "main", "9.0.0/../../heads/main", None, "9.0.0 --x", "\u0669.\u0660"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    update.run_update(bad)
        self.assertEqual(self.seen, [])

    def test_run_update_handles_cancel_and_failure(self):
        from boreas import update
        with self._patched():
            self.code = 126
            with self.assertRaises(update.UpdateCancelled):
                update.run_update("9.0.0")
            self.code = 127
            with self.assertRaises(update.NoAuthorization):
                update.run_update("9.0.0")
            self.code = 1
            with self.assertRaises(RuntimeError) as e:
                update.run_update("9.0.0")
            self.assertEqual(str(e.exception), "boom")  # the helper's last line reaches the user

    def test_install_release_downloads_the_tag_and_runs_its_installer(self):
        import subprocess as sp
        from boreas import update
        good = self._tarball({"install.sh": b"#!/bin/sh\n", "boreas/__init__.py": b'__version__ = "9.0.0"\n'},
                             prefix="boreas-9.0.0/")
        ran = []

        def fake_run(cmd, **kw):
            ran.append((cmd, os.path.exists(cmd[-1])))
            return sp.CompletedProcess(cmd, self.code, "", "")
        self.code = 0
        with self._serve(good), patch.object(update.subprocess, "run", fake_run):
            update.install_release("9.0.0")
            self.assertEqual(self.requested, ["https://codeload.github.com/Yusuf-Yonturk/boreas/tar.gz/refs/tags/v9.0.0"])
            self.assertEqual(ran[-1][0][0], "/bin/sh")
            self.assertTrue(ran[-1][0][-1].endswith("/install.sh") and ran[-1][1])
            self.assertFalse(os.path.exists(ran[-1][0][-1]))  # the download folder is gone afterwards
            with self.assertRaises(ValueError):   # main moved on: a newer archive than the offered release
                update.install_release("8.9.0")
            with self.assertRaises(ValueError):   # an archive older than the offered release
                update.install_release("9.1.0")
            self.code = 3
            with self.assertRaises(RuntimeError):
                update.install_release("9.0.0")

    def test_install_release_keeps_the_download_folder_private(self):
        import stat
        import subprocess as sp
        from boreas import update
        good = self._tarball({"install.sh": b"#!/bin/sh\n", "boreas/__init__.py": b'__version__ = "9.0.0"\n'})
        modes = []

        def fake_run(cmd, **kw):
            folder = os.path.dirname(os.path.dirname(cmd[-1]))  # <tmp>/boreas-main/install.sh
            modes.append(stat.S_IMODE(os.stat(folder).st_mode))
            return sp.CompletedProcess(cmd, 0, "", "")
        old = os.umask(0)   # a lax umask must not matter
        try:
            with self._serve(good), patch.object(update.subprocess, "run", fake_run):
                update.install_release("9.0.0")
        finally:
            os.umask(old)
        self.assertEqual(modes, [0o700])

    def test_helper_script_exit_codes_are_not_mistaken_for_pkexec(self):
        # the helper folds every failure into 1, so 126/127 can only come from pkexec itself
        import subprocess as sp
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        lib = tempfile.mkdtemp(prefix="boreas-lib-")   # the installed layout: helper next to the code
        shutil.copy(os.path.join(repo, "data", "boreas-update"), lib)
        os.symlink(os.path.join(repo, "boreas"), os.path.join(lib, "boreas"))
        helper = os.path.join(lib, "boreas-update")
        try:
            for args, reason in (([], "usage"), (["not-a-version"], "Not a release version"),
                                 (["9.0.0"], "must run as root" if os.geteuid() else "")):
                with self.subTest(args=args):
                    if not reason:
                        continue  # running the tests as root would really install
                    r = sp.run([sys.executable, "-I", helper, *args], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 1)
                    self.assertIn(reason, r.stderr.strip().splitlines()[-1])
        finally:
            shutil.rmtree(lib)

    def test_download_extracts_project(self):
        from boreas import update
        good = self._tarball({"install.sh": b"#!/bin/sh\n", "boreas/__init__.py": b'__version__ = "9.0.0"\n'})
        d = tempfile.mkdtemp(prefix="boreas-upd-")
        with self._serve(good):
            root = update.download_source(d, "9.0.0")
        self.assertEqual(update.source_version(root), "9.0.0")
        self.assertTrue(os.access(os.path.join(root, "install.sh"), os.X_OK))
        shutil.rmtree(d)

    def test_unsafe_archive_is_rejected(self):
        from boreas import update
        for bad in ({"../evil.sh": b"x"}, {"/etc/evil": b"x"}):
            d = tempfile.mkdtemp(prefix="boreas-upd-")
            with self._serve(self._tarball({"install.sh": b"", **bad})):
                with self.assertRaises(ValueError):
                    update.download_source(d, "9.0.0")
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(d), "evil.sh")))
            shutil.rmtree(d)


class TestRobustness(Base):
    """A stored or received value must never keep the service from starting."""

    def _write_config(self, text):
        path = os.environ["BOREAS_CONFIG"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_mux_only_device_boots_and_reaches_ultimate(self):
        # gpu_mux_mode without dgpu_disable: Standard is the only non-Ultimate state
        shutil.rmtree(ARM + "/dgpu_disable")
        d = daemon.Daemon(config.Config())
        self.assertEqual(d.cfg["gpu_target"], gpu.STANDARD)
        d.reapply(at_boot=True)
        self.assertEqual(d.handle({"cmd": "set_gpu", "target": gpu.ULTIMATE}), {"reboot": True})
        self.assertEqual(get(f"{ARM}/gpu_mux_mode/current_value"), "0")
        with self.assertRaises(ValueError):
            gpu.set_eco(True)

    def test_gpu_failure_at_boot_does_not_stop_the_service(self):
        d = daemon.Daemon(config.Config())
        d.cfg["gpu_target"] = gpu.ECO
        for exc in (ValueError("no eco"), daemon.subprocess.TimeoutExpired("systemctl", 5)):
            with patch.object(gpu, "set_eco", side_effect=exc):
                d.reapply(at_boot=True)
            self.assertIn("Could not change the GPU mode", d.notice)
            with patch.object(gpu, "set_eco", side_effect=exc), self.assertRaises(type(exc)):
                d.apply_gpu_target(gpu.ECO, explicit=True)

    def test_postponed_eco_failure_does_not_skip_the_tick(self):
        d = daemon.Daemon(config.Config())
        d.ticks = daemon.ECO_RETRY_TICKS - 1
        put(R + hw.PLATFORM_PROFILE, "performance")   # an external change the same tick must still see
        with patch.object(d, "eco_pending", return_value=True), \
                patch.object(gpu, "set_eco", side_effect=OSError(errno.EIO, "firmware")):
            d.tick()
        self.assertEqual(d.cfg["mode"], hw.TURBO)
        self.assertTrue(d.eco_stuck)
        self.assertIn("postponed Eco", d.notice)

    def test_malformed_config_shapes_load(self):
        shapes = (
            "[]",
            '{"modes": []}',
            '{"modes": {"3": 5}}',
            '{"modes": {"x": {"base": 0}}}',
            '{"modes": {"3": {"base": 7}}, "mode_ac": 3}',
            '{"modes": {"99": {"base": 1}}}',
            '{"mode_ac": "abc", "mode": null, "mode_battery": 42}',
            '{"aura": 5, "gpu_target": "warp", "gpu_backend": 3, "charge_limit": "x"}',
            '{"aura": {"mode": "static", "color": [1, 2]}}',
            '{"modes": {"0": {"base": 0, "name": 4, "fans_apply": true, "fan_cpu": "zz", "fan_gpu": [1, 2]}}}',
            '{"modes": {"0": {"base": 2, "power_apply": true, "ppt": {"ppt_pl1_spl": [1]}, "nv": {"nv_temp_target": "8"}}}}',
            '{"modes": {"3": {"base": [1]}}}',
            '{"modes": {"' + "9" * 5000 + '": {"base": 1}}}',
            '{"aura": {"mode": ["static"], "color": [1, 2, 3]}}',
            '{"aura": {"mode": "static", "color": [1, 2, 3], "speed": []}}',
            '{"aura": {"mode": {}, "color": [1, 2, 3]}, "gpu_target": [], "gpu_backend": {}}',
        )
        for text in shapes:
            with self.subTest(text=text):
                build_tree()
                self._write_config(text)
                d = daemon.Daemon(config.Config())
                d.reapply(at_boot=True)
                st = d.status()
                self.assertTrue(all(d.cfg.has_mode(d.cfg[k]) for k in ("mode", "mode_ac", "mode_battery")))
                self.assertEqual([m["id"] for m in st["modes"]][:3], config.MODE_ORDER)
                self.assertEqual(d.cfg.mode(0)["base"], hw.BALANCED)
                self.assertIsInstance(d.cfg.mode(0)["name"], str)
                json.dumps(st)

    def test_valid_stored_values_survive_the_load_checks(self):
        c = config.Config()
        new = c.add_mode(hw.TURBO)
        c.mode(new).update(name="Oyun", fans_apply=True, fan_cpu=fans.DEFAULTS[hw.TURBO]["cpu"],
                           power_apply=True, ppt={"ppt_pl1_spl": 90, "ppt_pl2_sppt": None},
                           nv={"nv_temp_target": 80}, cpu_temp=85, cpu_temp_floor_w=45)
        c["mode_ac"] = new
        c["aura"] = {"mode": "breathe", "color": [255, 0, 10], "color2": None, "speed": "fast"}
        c["gpu_target"], c["gpu_backend"], c["charge_limit"] = gpu.OPTIMIZED, "ebpf", 60
        c.save()
        c2 = config.Config()
        self.assertEqual(c2.data, c.data)
        self.assertEqual(c2.load_error, "")

    def test_unreadable_config_is_kept_and_reported(self):
        import glob
        for old in glob.glob(os.environ["BOREAS_CONFIG"] + ".corrupt*"):   # left by the other tests
            os.remove(old)
        path = self._write_config('{"charge_limit": 60, "mo')
        d = daemon.Daemon(config.Config())
        self.assertIn("Settings file unreadable", d.notice)
        self.assertEqual(get(path + ".corrupt"), '{"charge_limit": 60, "mo')
        # like a first start: the values the hardware holds are adopted, not reset
        self.assertEqual(d.cfg["charge_limit"], 80)
        self.assertEqual(config.Config().load_error, "")
        # a second unreadable file does not replace the copy kept from the first
        self._write_config("second{")
        config.Config()
        self.assertEqual((get(path + ".corrupt"), get(path + ".corrupt.2")), ('{"charge_limit": 60, "mo', "second{"))

    def test_update_mode_rejects_unknown_mode_and_bad_types(self):
        d = daemon.Daemon(config.Config())
        for cmd in ("update_mode", "get_mode"):
            with self.assertRaises(ValueError):
                d.handle({"cmd": cmd, "mode": 9, "data": {"name": "ghost"}})
        self.assertEqual(d.cfg.mode(hw.BALANCED)["name"], "Balanced")
        bad = ({"ppt": [1]}, {"ppt": {"ppt_pl1_spl": "9"}}, {"nv": {"nv_temp_target": [1]}}, {"name": 5},
               {"fans_apply": "yes"}, {"fan_cpu": "zz"}, {"power_apply": 1}, [1])
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                d.handle({"cmd": "update_mode", "mode": 0, "data": data})
        self.assertEqual(d.cfg.mode(0), config.Config().mode(0))
        d.reapply(at_boot=True)
        ok = {"name": "Dengeli", "power_apply": True, "ppt": {"ppt_pl1_spl": 60, "ppt_pl2_sppt": None}, "fan_cpu": None}
        self.assertEqual(d.handle({"cmd": "update_mode", "mode": 0, "data": ok})["mode"]["ppt"]["ppt_pl1_spl"], 60)

    def test_default_curve_reads_the_firmware_curve_once(self):
        d = daemon.Daemon(config.Config())
        saved = fans.DEFAULTS[hw.TURBO]["gpu"]
        d.handle({"cmd": "update_mode", "mode": 0, "data": {"fans_apply": True, "fan_gpu": saved, "cpu_temp": 85}})
        c = R + "/sys/class/hwmon/hwmon9"
        factory = [45, 50, 55, 60, 65, 70, 75, 80] + [fans.pwm_to_pct(i * 12) for i in range(1, 9)]
        real_write = hw.write

        def firmware(path, value):   # asus-wmi: the read sends the throttle policy again
            real_write(path, value)
            if path.endswith("_enable") and value == 3:
                real_write(os.path.dirname(path) + "/pwm2_enable", 2)
                real_write(hw.ARMOURY + "/ppt_pl1_spl/current_value", 55)
        with patch.object(hw, "write", firmware):
            self.assertEqual(d.handle({"cmd": "default_curve", "fan": "cpu"})["curve"], factory)
        # the first read put the whole mode back: the other fan's curve and the target survive
        self.assertEqual((get(c + "/pwm2_enable"), fans.read_curve("gpu"), d.cpu.target), ("1", fans.fix_curve(saved), 85))
        with patch.object(d, "apply_mode", side_effect=AssertionError("the mode was applied again")), \
                patch.object(fans, "factory_curve", side_effect=AssertionError("the firmware curve was read again")):
            for _ in range(3):       # opening the page again asks for the same curve
                self.assertEqual(d.handle({"cmd": "default_curve", "fan": "cpu"})["curve"], factory)
            # a mode of another profile gets the built-in curve, and never a read of the wrong profile
            self.assertEqual(d.handle({"cmd": "default_curve", "fan": "cpu", "mode": hw.TURBO})["curve"],
                             fans.DEFAULTS[hw.TURBO]["cpu"])
        with self.assertRaises(ValueError):
            d.handle({"cmd": "default_curve", "fan": "turbine"})

    def test_clamped_values_are_stored_as_written(self):
        d = daemon.Daemon(config.Config())
        d.handle({"cmd": "set_charge_limit", "value": 20})
        d.handle({"cmd": "set_kbd", "brightness": 9})
        self.assertEqual((d.cfg["charge_limit"], d.cfg["kbd_brightness"]), (40, 3))

    def test_cli_rejects_non_numeric_arguments(self):
        with patch.object(client, "call", return_value={"mode": 0}), contextlib.redirect_stderr(open(os.devnull, "w")):
            for argv in (["charge", "abc"], ["kbd", "x"], ["cputemp", "hot"], ["cpufloor", "low"]):
                with self.subTest(argv=argv):
                    self.assertEqual(client.main(argv), 2)
