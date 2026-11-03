"""Feedback behavior, capability selection and failure handling."""
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from boreas import hw, ryzen, thermal


class TestController(unittest.TestCase):
    def setUp(self):
        self.attrs = {n: hw.Attr(n, w, 10, 100) for n, w in zip(thermal.POWER_ATTRS, (50, 60, 70))}
        self.temp = 85
        self.now = 0.0
        self.writes = []
        for target, kwargs in (
            ("boreas.thermal.hw.get_attr", {"side_effect": self.attrs.get}),
            ("boreas.thermal.hw.set_attr", {"side_effect": self.write}),
            ("boreas.thermal.hw.temps", {"side_effect": lambda: {"cpu": self.temp}}),
            ("boreas.thermal.time.monotonic", {"side_effect": lambda: self.now}),
            ("boreas.ryzen.bridge_available", {"return_value": False}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        self.c = thermal.Controller()

    def write(self, name, value):
        self.writes.append((name, value))
        self.attrs[name].current = value

    def step(self, temp, count=1):
        self.temp = temp
        for _ in range(count):
            self.now += 1
            self.c.tick()

    def test_hot_reduces_all_caps_and_respects_floor(self):
        self.c.start(85)
        self.assertEqual([a.current for a in self.attrs.values()], [46, 56, 66])
        self.step(95, 20)
        self.assertEqual([a.current for a in self.attrs.values()], [10, 10, 10])
        self.assertEqual(self.c.state, "limited")
        self.assertFalse(self.c.status()["hard_limit"])

    def test_hysteresis_rate_limit_and_slow_recovery(self):
        self.c.start(85)
        n = len(self.writes)
        self.c.tick()
        self.assertEqual(len(self.writes), n)
        self.step(83, 10)
        self.assertEqual(len(self.writes), n)
        self.step(78, 5)
        self.assertEqual(len(self.writes), n)
        self.step(78)
        self.assertEqual(self.attrs[thermal.POWER_ATTRS[0]].current, 47)
        self.step(70, 100)
        self.assertEqual([a.current for a in self.attrs.values()], [50, 60, 70])

    def test_off_restores_original_caps(self):
        self.c.start(85)
        self.c.start(None)
        self.assertEqual([a.current for a in self.attrs.values()], [50, 60, 70])
        n = len(self.writes)
        self.step(100, 10)
        self.assertEqual(len(self.writes), n)

    def test_sensor_loss_latches_without_raising_power(self):
        self.c.start(85)
        n = len(self.writes)
        self.step(None)
        self.step(60, 30)
        self.assertEqual(len(self.writes), n)
        self.assertIn("sensor", self.c.error)

    def test_external_writer_stops_loop_and_is_not_overwritten_on_stop(self):
        self.c.start(85)
        self.attrs[thermal.POWER_ATTRS[0]].current = 30
        n = len(self.writes)
        self.step(95)
        self.assertEqual(len(self.writes), n)
        self.assertIn("externally", self.c.error)
        self.c.stop()
        self.assertEqual(self.attrs[thermal.POWER_ATTRS[0]].current, 30)

    def test_missing_ranges_prevent_any_write(self):
        self.attrs[thermal.POWER_ATTRS[1]].min = None
        self.c.start(85)
        self.assertTrue(self.c.error)
        self.assertEqual(self.writes, [])

    def test_partial_failure_is_latched_and_successful_writes_restored(self):
        def fail(name, value):
            if name == thermal.POWER_ATTRS[1]:
                raise OSError("firmware rejected")
            self.write(name, value)
        with patch.object(hw, "set_attr", side_effect=fail):
            self.c.start(85)
        self.assertEqual(self.c.state, "error")
        self.assertEqual(self.attrs[thermal.POWER_ATTRS[0]].current, 46)
        self.step(70, 20)
        self.assertEqual(self.attrs[thermal.POWER_ATTRS[0]].current, 46)
        self.c.stop()
        self.assertEqual(self.attrs[thermal.POWER_ATTRS[0]].current, 50)

    def test_ignored_write_is_reported(self):
        with patch.object(hw, "set_attr"):
            self.c.start(85)
        self.assertIn("readback", self.c.error)

    def test_restore_failure_preserves_recovery_information(self):
        self.c.start(85)
        with patch.object(hw, "set_attr", side_effect=OSError("unavailable")):
            self.c.stop()
        self.assertTrue(self.c.error)
        self.assertTrue(self.c.written)
        self.c.stop()
        self.assertFalse(self.c.error)
        self.assertEqual([a.current for a in self.attrs.values()], [50, 60, 70])

    def test_simple_thermal_plant_settles_near_target(self):
        # Synthetic plant validates feedback only, not a claim about a real laptop.
        self.temp = 60
        self.c.start(85)
        actual = 60.0
        for _ in range(300):
            actual += 0.08 * (40 + 1.2 * self.attrs[thermal.POWER_ATTRS[0]].current - actual)
            self.step(round(actual))
        self.assertGreater(actual, 78)
        self.assertLess(actual, 86)
        self.assertFalse(self.c.error)


class TestSmuBridgeBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dev = os.path.join(self.tmp.name, "boreas-smu-probe")
        self.enable_file = os.path.join(self.tmp.name, "enable_hw")
        open(self.dev, "wb").close()
        with open(self.enable_file, "w", encoding="utf-8") as f:
            f.write("Y\n")
        self.family = ryzen.FAMILIES[(0x19, 0x61)]
        self.calls = []
        self.responses = {ryzen.GET_SMU_VERSION: (ryzen.OK, 0x546900), 0x3F: (ryzen.OK, 0)}
        self.ioctl_err = None
        self.now = 10.0

        def fake_ioctl(fd, buf):
            abi, cmd, arg, st, res, r0, r1, r2 = struct.unpack("<8I", buf)
            self.assertEqual((abi, st, res, r0, r1, r2), (1, 0, 0, 0, 0, 0))
            self.calls.append((cmd, arg))
            if self.ioctl_err is not None:
                raise self.ioctl_err
            status, result = self.responses.get(cmd, (ryzen.OK, 0))
            buf[:] = struct.pack("<8I", abi, cmd, arg, status, result, 0, 0, 0)

        self.bridge = ryzen.BridgeSmu(
            family=self.family, path=self.dev, enable_path=self.enable_file
        )
        self.bridge._ioctl = fake_ioctl
        env = patch.dict(os.environ, {ryzen.BRIDGE_OPT_IN_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)
        p = patch("boreas.thermal.time.monotonic", side_effect=lambda: self.now)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch("boreas.thermal.hw.get_attr", return_value=None)
        p2.start()
        self.addCleanup(p2.stop)
        self.c = thermal.Controller(bridge=self.bridge)

    def test_smu_bridge_selected_and_reapplied_once_after_5s(self):
        self.assertEqual(self.c.status()["control"], "smu-bridge")
        self.assertFalse(self.c.status()["hard_limit"])
        self.c.start(83)
        self.assertEqual(self.calls, [(0x02, 0), (0x3F, 83)])
        self.assertEqual(self.c.applied, 83)
        for _ in range(4):
            self.now += 1.0
            self.c.tick()
        self.assertEqual(self.calls, [(0x02, 0), (0x3F, 83)])
        self.now += 1.0
        self.c.tick()
        self.assertEqual(self.calls, [(0x02, 0), (0x3F, 83), (0x3F, 83)])
        self.now += 10.0
        self.c.tick()
        self.assertEqual(len(self.calls), 3)
        self.c.stop(restore=True)
        self.assertEqual(self.calls[-1], (0x3F, 96))
        self.assertEqual(self.c.state, "off")

    def test_smu_bridge_external_profile_stop_skips_restore_write(self):
        self.c.start(83)
        n = len(self.calls)
        self.c.stop(restore=False)
        self.assertEqual(len(self.calls), n)

    def test_smu_bridge_fault_latches_without_retry_or_fallback(self):
        self.responses[0x3F] = (ryzen.FAILED, 0)
        self.c.start(83)
        self.assertEqual(self.c.state, "error")
        self.assertIsNone(self.c.applied)
        self.assertFalse(self.c.supported())
        n = len(self.calls)
        self.c.stop(restore=True)
        self.assertEqual(len(self.calls), n)
        self.assertEqual(self.c.state, "error")
        self.c.start(80)
        self.assertEqual(len(self.calls), n)

    def test_missing_enable_hw_or_opt_in_disables_bridge(self):
        os.unlink(self.enable_file)
        self.assertFalse(self.bridge.available())
        with self.assertRaises(ryzen.SmuAccessError):
            self.bridge.set(83)
        self.assertEqual(self.calls, [])

        with patch.object(ryzen, "detect", return_value=self.family), \
                patch.object(hw, "exists", side_effect=lambda p: p == ryzen.BRIDGE_DEVICE), \
                patch.object(hw, "read", return_value="Y"):
            self.assertFalse(ryzen.bridge_available())
        with patch.object(ryzen, "detect", return_value=self.family), \
                patch.object(hw, "exists", return_value=True), \
                patch.object(hw, "read", return_value="N"):
            self.assertFalse(ryzen.bridge_available())
        with patch.object(ryzen, "detect", return_value=self.family), \
                patch.object(hw, "exists", return_value=True), \
                patch.object(hw, "read", return_value="Y"):
            self.assertTrue(ryzen.bridge_available())
            with patch.dict(os.environ, {ryzen.BRIDGE_OPT_IN_ENV: "0"}):
                self.assertFalse(ryzen.bridge_available())


class TestBridgeOptInWarning(unittest.TestCase):
    def test_requested_but_unusable_bridge_is_reported(self):
        c = thermal.Controller()
        with patch.dict(os.environ, {ryzen.BRIDGE_OPT_IN_ENV: "1"}), \
                patch.object(c.bridge, "available", return_value=False):
            msg = c.opt_in_warning()
        self.assertIn("BOREAS_SMU_BRIDGE=1", msg)
        self.assertIn("power-feedback", msg)

    def test_no_warning_without_opt_in_or_when_usable(self):
        c = thermal.Controller()
        with patch.dict(os.environ, {}, clear=True), patch.object(c.bridge, "available", return_value=False):
            self.assertIsNone(c.opt_in_warning())
        with patch.dict(os.environ, {ryzen.BRIDGE_OPT_IN_ENV: "1"}), patch.object(c.bridge, "available", return_value=True):
            self.assertIsNone(c.opt_in_warning())
