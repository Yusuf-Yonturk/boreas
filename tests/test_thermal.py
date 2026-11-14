"""Feedback behavior, capability selection and failure handling."""
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from boreas import hw, ryzen, thermal


class TestController(unittest.TestCase):
    PL1 = thermal.POWER_ATTRS[0]

    def setUp(self):
        self.attrs = {n: hw.Attr(n, w, 10, 100) for n, w in zip(thermal.POWER_ATTRS, (50, 60, 70))}
        self.temp = 86  # one degree over the 85 °C target used below: the cut band
        self.now = 0.0
        self.writes = []
        self.cpufreq = {"epp": [], "boost": [], "gov": []}  # empty: no ladder unless enabled
        self.policy = {"governor": "default", "boost": True, "epp": "performance"}
        self.policy_writes = []
        for target, kwargs in (
            ("boreas.thermal.hw.get_attr", {"side_effect": self.attrs.get}),
            ("boreas.thermal.hw.set_attr", {"side_effect": self.write}),
            ("boreas.thermal.hw.temps", {"side_effect": lambda: {"cpu": self.temp}}),
            ("boreas.thermal.time.monotonic", {"side_effect": lambda: self.now}),
            ("boreas.ryzen.bridge_available", {"return_value": False}),
            ("boreas.thermal.hw.cpu_epp_files", {"side_effect": lambda: self.cpufreq["epp"]}),
            ("boreas.thermal.hw.cpu_boost_paths", {"side_effect": lambda: self.cpufreq["boost"]}),
            ("boreas.thermal.hw.cpu_governor_choices", {"side_effect": lambda: self.cpufreq["gov"]}),
            ("boreas.thermal.hw.set_cpu_governor", {"side_effect": lambda v: self.write_policy("governor", v)}),
            ("boreas.thermal.hw.set_boost_enabled", {"side_effect": lambda v: self.write_policy("boost", v)}),
            ("boreas.thermal.hw.set_epp", {"side_effect": lambda v: self.write_policy("epp", v)}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        self.c = thermal.Controller()

    def write(self, name, value):
        self.writes.append((name, value))
        self.attrs[name].current = value

    def write_policy(self, key, value):
        self.policy_writes.append((key, value))
        self.policy[key] = value

    def enable_ladder(self, boost_mode="max", governor="default"):
        self.cpufreq = {"epp": ["/epp0"], "boost": ["/boost"], "gov": ["default", "powersave", "performance"]}
        self.c.set_baseline(thermal.Baseline(boost_mode, governor, hw.BALANCED))
        self.policy = self.c.baseline.policy()

    def step(self, temp, count=1):
        self.temp = temp
        for _ in range(count):
            self.now += 1
            self.c.tick()

    def caps(self):
        return [a.current for a in self.attrs.values()]

    # ---- bands, slew, floor ----

    def test_hot_reduces_all_caps_and_respects_floor(self):
        self.c.start(85)
        self.assertEqual(self.caps(), [48, 58, 68])  # e = 1: 2 W
        self.step(95, 20)  # e = 10: 6 W per second
        self.assertEqual(self.caps(), [10, 10, 10])
        self.assertEqual(self.c.state, "limited")
        self.assertFalse(self.c.status()["hard_limit"])

    def test_cut_is_proportional_and_capped(self):
        self.c.start(85)
        self.step(87)  # e = 2: 4 W
        self.assertEqual(self.attrs[self.PL1].current, 44)
        self.step(90)  # e = 5: capped at 6 W
        self.assertEqual(self.attrs[self.PL1].current, 38)

    def test_hold_band_and_recovery_rate(self):
        self.c.start(85)
        self.step(95, 5)
        self.assertEqual(self.attrs[self.PL1].current, 18)
        n = len(self.writes)
        self.step(85, 5)  # e = 0 .. -2: hold
        self.step(83, 5)
        self.assertEqual(len(self.writes), n)
        self.assertEqual(self.c.state, "active")
        self.step(82)  # e = -3: recovery, +2 W every 2 s
        self.assertEqual(self.attrs[self.PL1].current, 20)
        self.step(82, 2)
        self.assertEqual(self.attrs[self.PL1].current, 22)
        self.step(70, 100)
        self.assertEqual(self.caps(), [50, 60, 70])

    def test_limited_and_cut_start_at_the_same_temperature(self):
        self.temp = 85
        self.c.start(85)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.c.state, "active")
        self.step(86, 30)
        self.assertEqual(self.c.state, "limited")

    def test_pl1_floor_clamps_requests_and_marks_limited(self):
        self.c.start(85, floor_w=40)
        self.step(95, 20)
        self.assertEqual(self.caps(), [40, 10, 10])
        self.assertEqual(self.c.state, "limited")
        self.assertEqual(self.c.status()["floor_w"], 40)
        self.step(70, 100)
        self.assertEqual(self.caps(), [50, 60, 70])

    def test_floor_above_ceiling_is_clamped_to_ceiling(self):
        self.c.start(85, floor_w=80)
        self.step(95, 5)
        self.assertEqual(self.attrs[self.PL1].current, 50)
        self.assertEqual(self.c.status()["floor_w"], 50)
        self.assertEqual(self.c.state, "active")  # the other rails still have room

    def test_new_floor_applies_without_restart(self):
        self.c.start(85)
        self.step(95, 10)
        self.assertEqual(self.attrs[self.PL1].current, 10)
        self.c.start(85, floor_w=30)  # same target, edited floor
        self.step(84)  # hold band: the floor is only a bound for the next moves
        self.step(82, 20)  # recovery raises PL1 above the floor normally
        self.assertGreaterEqual(self.attrs[self.PL1].current, 30)

    # ---- lifecycle ----

    def test_off_restores_original_caps(self):
        self.c.start(85)
        self.c.start(None)
        self.assertEqual(self.caps(), [50, 60, 70])
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

    def test_start_after_latched_error_retries(self):
        self.c.start(85)
        self.step(None)
        self.assertEqual(self.c.state, "error")
        self.temp = 86
        self.c.start(85)
        self.assertEqual(self.c.error, "")
        self.assertEqual(self.c.state, "active")
        self.assertTrue(any("retrying" in e for e in self.c.drain_events()))

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
        self.assertEqual(self.attrs[self.PL1].current, 48)
        self.step(70, 20)
        self.assertEqual(self.attrs[self.PL1].current, 48)
        self.c.stop()
        self.assertEqual(self.attrs[self.PL1].current, 50)

    def test_ignored_write_is_reported(self):
        with patch.object(hw, "set_attr"):
            self.c.start(85)
        self.assertIn("readback", self.c.error)

    def test_rounded_readback_is_followed(self):
        def rounding(name, value):
            self.write(name, value + 1)  # firmware rounds up by one
        with patch.object(hw, "set_attr", side_effect=rounding):
            self.c.start(85)
        self.assertEqual(self.c.error, "")
        self.assertEqual(self.c.written[self.PL1], 49)

    def test_restore_failure_preserves_recovery_information(self):
        self.c.start(85)
        with patch.object(hw, "set_attr", side_effect=OSError("unavailable")):
            self.c.stop()
        self.assertTrue(self.c.error)
        self.assertTrue(self.c.written)
        self.c.stop()
        self.assertFalse(self.c.error)
        self.assertEqual(self.caps(), [50, 60, 70])

    def test_stop_logs_rail_left_to_external_writer(self):
        self.c.start(85)
        self.attrs[self.PL1].current = 20
        self.c.stop()
        self.assertEqual(self.attrs[self.PL1].current, 20)
        self.assertEqual(self.attrs[thermal.POWER_ATTRS[1]].current, 60)
        self.assertTrue(any("left ppt_pl1_spl at 20 W" in e for e in self.c.drain_events()))
        self.assertEqual(self.c.written, {})

    # ---- re-sync instead of latching ----

    def test_external_change_inside_band_is_adopted(self):
        self.c.start(85)
        self.attrs[self.PL1].current = 30
        self.step(95)
        self.assertEqual(self.c.error, "")
        self.assertEqual(self.attrs[self.PL1].current, 24)  # adopted 30, then cut 6
        self.assertTrue(any("re-synced" in e for e in self.c.drain_events()))
        self.c.stop()
        self.assertEqual(self.attrs[self.PL1].current, 50)  # owned again, restored

    def test_external_raise_above_user_cap_is_reasserted_then_latches(self):
        self.c.start(85, user_caps={self.PL1: 50})
        for i in range(3):
            self.attrs[self.PL1].current = 90  # the firmware undid the user's cap
            self.step(85)
            self.assertEqual(self.c.error, "", i)
            self.assertEqual(self.attrs[self.PL1].current, 48)
        self.assertEqual(sum("re-asserted" in e for e in self.c.drain_events()), 3)
        self.attrs[self.PL1].current = 90
        self.step(85)
        self.assertIn("externally", self.c.error)
        self.c.stop()
        self.assertEqual(self.attrs[self.PL1].current, 90)  # not ours any more: left alone

    def test_reassert_budget_recovers_after_the_window(self):
        self.c.start(85, user_caps={self.PL1: 50})
        for _ in range(3):
            self.attrs[self.PL1].current = 90
            self.step(85)
        self.step(85, 61)
        self.attrs[self.PL1].current = 90
        self.step(85)
        self.assertEqual(self.c.error, "")

    def test_ceiling_recaptured_when_firmware_raises_without_user_cap(self):
        self.c.start(85)
        self.attrs[self.PL1].current = 90
        self.step(85)
        self.assertEqual(self.c.error, "")
        self.assertEqual(self.c.ceilings[self.PL1], 90)
        self.assertTrue(any("re-captured" in e for e in self.c.drain_events()))
        self.step(70, 100)
        self.assertEqual(self.attrs[self.PL1].current, 90)

    # ---- escalation ladder ----

    def test_ladder_is_built_below_the_baseline(self):
        self.enable_ladder("max", "default")
        self.assertEqual(self.c.ladder, [("epp", "balance_performance"), ("epp", "balance_power"),
                                         ("epp", "power"), ("boost", False)])
        self.enable_ladder("balanced", "default")
        self.assertEqual(self.c.ladder, [("epp", "power"), ("boost", False)])
        self.enable_ladder("disabled", "default")
        self.assertEqual(self.c.ladder, [])
        self.enable_ladder("max", "performance")
        self.assertEqual(self.c.ladder, [("boost", False), ("governor", "default"),
                                         ("epp", "balance_power"), ("epp", "power")])
        self.cpufreq["boost"] = []
        self.c.set_baseline(thermal.Baseline("max", "default"))
        self.assertEqual(self.c.ladder[-1], ("epp", "power"))

    def test_ladder_never_exceeds_baseline(self):
        order = thermal.EPP_LADDER
        for boost_mode in hw.CPU_BOOST_MODES:
            for governor in hw.CPU_GOVERNORS:
                self.enable_ladder(boost_mode, governor)
                base = self.c.baseline.policy()
                self.assertEqual(self.c.policy_for_stage(0), base)
                for k in range(1, len(self.c.ladder) + 1):
                    pol = self.c.policy_for_stage(k)
                    self.assertFalse(pol["boost"] and not base["boost"], (boost_mode, governor, k))
                    self.assertIn(pol["governor"], {base["governor"], "default"})
                    if base["governor"] != "performance":
                        self.assertGreaterEqual(order.index(pol["epp"]), order.index(base["epp"]))

    def test_ladder_engages_after_grace_and_dwell(self):
        self.enable_ladder("max", "default")
        self.c.start(85)
        self.step(88, 14)  # at the floor since t=8, hot since t=1, but 15 s from set_baseline
        self.assertEqual((self.c.stage, self.c.state), (0, "limited"))
        self.step(88)
        self.assertEqual(self.c.stage, 1)
        self.assertEqual(self.policy, {"governor": "default", "boost": True, "epp": "balance_performance"})
        self.step(88, 14)
        self.assertEqual(self.c.stage, 1)  # dwell
        self.step(88)
        self.assertEqual((self.c.stage, self.policy["epp"]), (2, "balance_power"))
        self.step(88, 30)
        self.assertEqual((self.c.stage, self.policy["epp"], self.policy["boost"]), (4, "power", False))
        self.assertEqual(self.c.status()["stage_max"], 4)
        self.assertTrue(any("stage 4/4 (boost off)" in e for e in self.c.drain_events()))

    def test_ladder_waits_for_the_floor(self):
        self.enable_ladder("max", "default")
        self.c.start(85)
        self.step(86, 30)  # 2 W/s from 48: the floor is reached at t=20
        self.assertEqual(self.attrs[self.PL1].current, 10)
        self.assertEqual(self.c.stage, 1)  # engaged only after 10 s at the floor
        self.assertEqual(len(self.policy_writes), 1)

    def test_ladder_releases_in_reverse_before_caps_recover(self):
        self.enable_ladder("max", "default")
        self.c.start(85)
        self.step(88, 30)
        self.assertEqual(self.c.stage, 2)
        self.step(70, 20)  # cool since t=31: 20 s of dwell before the first release
        self.assertEqual(self.c.stage, 2)
        self.assertEqual(self.attrs[self.PL1].current, 10)  # caps stay down while escalated
        self.step(70)
        self.assertEqual((self.c.stage, self.policy["epp"]), (1, "balance_performance"))
        self.step(70, 15)
        self.assertEqual((self.c.stage, self.policy["epp"]), (0, "performance"))
        self.assertEqual(self.attrs[self.PL1].current, 10)
        self.step(70, 70)  # +1 W/s on average: the 60 W rail needs 60 s
        self.assertEqual(self.caps(), [50, 60, 70])

    def test_performance_governor_ladder_skips_epp_until_governor_rung(self):
        self.enable_ladder("max", "performance")
        self.c.start(85)
        self.step(95, 15)
        self.assertEqual(self.policy_writes, [("boost", False)])
        self.step(95, 15)
        self.assertEqual(self.policy_writes[1:], [("governor", "default"), ("epp", "balance_performance")])

    def test_stop_restores_epp_boost_governor_to_baseline(self):
        self.enable_ladder("max", "performance")
        self.c.start(85)
        self.step(95, 60)
        self.assertEqual(self.c.stage, 4)
        self.c.stop()
        self.assertEqual(self.policy, {"governor": "performance", "boost": True, "epp": "performance"})
        self.assertEqual(self.c.stage, 0)
        self.assertEqual(self.caps(), [50, 60, 70])

    def test_stop_without_restore_writes_nothing(self):
        self.enable_ladder("max", "default")
        self.c.start(85)
        self.step(95, 30)
        n = len(self.policy_writes)
        self.c.stop(restore=False)
        self.assertEqual(len(self.policy_writes), n)
        self.assertEqual((self.c.stage, self.c.written), (0, {}))

    def test_set_baseline_resets_stage_without_touching_caps(self):
        self.enable_ladder("max", "default")
        self.c.start(85)
        self.step(88, 30)
        self.assertEqual(self.c.stage, 2)
        n = len(self.writes)
        self.c.set_baseline(thermal.Baseline("balanced", "default"))
        self.assertEqual((self.c.stage, self.c.ladder), (0, [("epp", "power"), ("boost", False)]))
        self.assertEqual(len(self.writes), n)
        self.assertEqual(self.attrs[self.PL1].current, 10)

    def test_rung_write_failure_disables_rung_and_keeps_caps_loop(self):
        self.enable_ladder("max", "default")
        self.c.start(85)
        with patch("boreas.thermal.hw.set_epp", side_effect=OSError("EPP rejected")):
            self.step(95, 60)
        self.assertEqual(self.c.error, "")
        self.assertEqual(self.c.state, "limited")
        self.assertEqual(self.c.ladder, [("boost", False)])  # the EPP rungs were dropped one by one
        self.assertEqual((self.c.stage, self.policy["boost"]), (1, False))
        self.assertTrue(any("rung skipped" in e for e in self.c.drain_events()))

    # ---- fast path and relapse hold (G713PV: 30 W requested, 50-60 W drawn, 93 °C) ----

    def pin_caps_at_floor(self):
        for a in self.attrs.values():
            a.min = a.current

    def test_fast_path_switches_boost_off_when_the_floor_does_not_help(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91  # e = 6
        self.c.start(85)
        self.step(91, 2)
        self.assertEqual((self.c.stage, self.policy["boost"]), (0, True))  # 2 s over: not yet
        self.step(91)
        self.assertEqual((self.c.stage, self.policy), (2, {"governor": "default", "boost": False, "epp": "power"}))
        events = self.c.drain_events()
        self.assertTrue(any("stage 2/2 (boost off, fast)" in e for e in events), events)

    def test_fast_path_waits_for_the_floor(self):
        self.enable_ladder("balanced", "default")
        self.temp = 91
        self.c.start(85)  # 50 W, floor 10 W: 6 W/s reaches it at t=6
        self.step(91, 9)  # the last rail reaches its floor at t=9
        self.assertEqual((self.caps(), self.c.state, self.c.stage), ([10, 10, 10], "limited", 0))
        self.step(91, 2)
        self.assertEqual(self.c.stage, 0)  # the grace counts at the floor, not during the descent
        self.step(91)
        self.assertEqual((self.c.stage, self.policy["boost"]), (2, False))

    def test_fast_path_ignores_short_bursts_and_small_overshoot(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 80
        self.c.start(85)
        for _ in range(4):  # 2 s bursts, each followed by a second below the threshold
            self.step(93, 2)
            self.step(89)
        self.assertEqual(self.c.stage, 0)
        self.step(80)
        self.step(89, 9)  # e = 4: under the fast threshold, the grace still applies
        self.assertEqual(self.c.stage, 0)

    def test_fast_path_without_a_boost_rung_keeps_the_gradual_ladder(self):
        self.enable_ladder("max", "default")
        self.cpufreq["boost"] = []
        self.c.set_baseline(thermal.Baseline("max", "default"))
        self.pin_caps_at_floor()
        self.temp = 95
        self.c.start(85)
        self.step(95, 14)
        self.assertEqual(self.c.stage, 0)
        self.step(95)
        self.assertEqual(self.c.stage, 1)

    def test_fast_path_boost_write_failure_drops_the_rung(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        with patch("boreas.thermal.hw.set_boost_enabled", side_effect=OSError("boost rejected")):
            self.step(91, 3)
        self.assertEqual((self.c.error, self.c.ladder, self.c.stage), ("", [("epp", "power")], 1))
        self.assertTrue(self.policy["boost"])

    def test_relapse_after_release_doubles_the_hold_and_calm_resets_it(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        self.step(91, 3)
        self.assertEqual(self.c.stage, 2)
        self.step(70, 20)
        self.assertEqual(self.c.stage, 2)
        self.step(70)  # 20 s cool: the boost rung is released
        self.assertEqual((self.c.stage, self.policy["boost"]), (1, True))
        self.step(91, 4)  # hot again at once (seen at the first tick, 3 s later): released too early
        self.assertEqual((self.c.stage, self.c.status()["release_dwell_s"]), (2, 40))
        self.step(70, 39)
        self.assertEqual(self.c.stage, 2)
        self.step(70)  # 40 s after the escalation
        self.assertEqual(self.c.stage, 1)
        self.step(70, 100)  # fully released and calm for longer than the relapse window
        self.assertEqual(self.c.stage, 0)
        self.step(91, 4)
        self.assertEqual((self.c.stage, self.c.status()["release_dwell_s"]), (2, 20))

    def test_release_hold_is_capped_and_reset_by_a_new_baseline(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        for _ in range(8):
            self.step(91, 4)
            self.step(70, self.c.status()["release_dwell_s"] + 1)
        self.assertEqual(self.c.status()["release_dwell_s"], thermal.RELEASE_DWELL_MAX)
        self.c.set_baseline(thermal.Baseline("balanced", "default"))
        self.assertEqual(self.c.status()["release_dwell_s"], thermal.RELEASE_DWELL)

    def relapse_until(self, dwell):
        self.step(91, 4)
        while self.c.status()["release_dwell_s"] < dwell:
            self.step(70, self.c.status()["release_dwell_s"] + 1)
            self.step(91, 4)
        self.assertEqual((self.c.stage, self.c.status()["release_dwell_s"]), (2, dwell))

    def test_long_hold_is_not_restarted_by_jitter_at_the_band_edge(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        self.relapse_until(320)
        for _ in range(6):  # 82 °C with one 83 °C sample a minute: never 320 s of unbroken cool
            self.step(82, 60)
            self.step(83)
        self.assertLess(self.c.stage, 2)

    def test_relapse_through_two_rungs_counts_once(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        self.step(91, 3)
        self.step(70, 21 + 15)  # both rungs released, the second a rung dwell after the first
        self.assertEqual(self.c.stage, 0)
        self.step(91, 4)  # two rungs in one tick are one relapse
        self.assertEqual((self.c.stage, self.c.status()["release_dwell_s"]), (2, 40))

    def test_new_target_or_floor_forgets_the_relapse_hold(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        self.relapse_until(80)
        self.c.start(85)  # the same values again: the mode was only saved
        self.assertEqual(self.c.status()["release_dwell_s"], 80)
        self.c.start(86)
        self.assertEqual(self.c.status()["release_dwell_s"], 20)

    def test_new_baseline_needs_fresh_evidence_for_the_fast_path(self):
        self.enable_ladder("balanced", "default")
        self.pin_caps_at_floor()
        self.temp = 91
        self.c.start(85)
        self.step(91, 3)
        self.assertEqual(self.c.stage, 2)
        self.c.set_baseline(thermal.Baseline("balanced", "default"))
        self.policy = self.c.baseline.policy()
        self.step(91, 3)
        self.assertEqual(self.c.stage, 0)
        self.step(91)
        self.assertEqual(self.c.stage, 2)

    def test_plant_where_only_boost_off_cools(self):
        # The measured G713PV shape: the power rails and the EPP do not move the temperature,
        # boost does. Synthetic, like the plants below: it checks the control flow, not a laptop.
        self.attrs[self.PL1].min = 30
        self.enable_ladder("balanced", "default")
        self.temp = 60
        self.c.start(82)
        actual, over = 60.0, 0
        for _ in range(900):
            actual += 0.6 * ((93 if self.policy["boost"] else 65) - actual)
            self.step(round(actual))
            over += self.temp >= 87
        self.assertLess(over, 90, f"{over} of 900 s at 87 °C or more")  # was about half the time
        self.assertFalse(self.c.error)

    # ---- plants ----

    def test_simple_thermal_plant_settles_near_target(self):
        # Synthetic plant validates feedback only, not a claim about a real laptop.
        self.temp = 60
        self.c.start(85)
        actual = 60.0
        for _ in range(300):
            actual += 0.08 * (40 + 1.2 * self.attrs[self.PL1].current - actual)
            self.step(round(actual))
        self.assertGreater(actual, 81)
        self.assertLess(actual, 86)
        self.assertFalse(self.c.error)

    def test_plant_ladder_engages_and_releases(self):
        EPP_HEAT = {"performance": 12, "balance_performance": 8, "balance_power": 4, "power": 0}
        self.attrs[self.PL1].min = 30
        self.enable_ladder("max", "default")
        actual = 60.0
        self.temp = 60

        def run(base, seconds):
            nonlocal actual
            for _ in range(seconds):
                heat = base + 1.2 * self.attrs[self.PL1].current + EPP_HEAT[self.policy["epp"]] + (6 if self.policy["boost"] else 0)
                actual += 0.08 * (heat - actual)
                self.step(round(actual))
        self.c.start(85)
        run(41, 300)  # at the floor: stage 2 settles at 87, stage 3 at 83
        self.assertEqual((self.c.stage, self.policy["epp"], self.policy["boost"]), (3, "power", True))
        self.assertTrue(82 <= actual <= 86)
        self.assertEqual(self.attrs[self.PL1].current, 30)
        run(0, 120)  # cool: rungs released in reverse, then caps recover
        self.assertEqual((self.c.stage, self.policy), (0, {"governor": "default", "boost": True, "epp": "performance"}))
        self.assertEqual(self.attrs[self.PL1].current, 50)
        run(47, 200)  # hotter: the boost rung is needed
        self.assertEqual((self.c.stage, self.policy["boost"]), (4, False))
        self.assertTrue(82 <= actual <= 86)
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
