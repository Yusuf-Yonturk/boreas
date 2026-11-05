"""CPU boost policy writes must report partial failures and verify readback."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from boreas import hw


class CpuBoostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(hw, 'ROOT', str(self.root))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.put(hw.CPU_BOOST_PATH, '0')
        self.paths = [f'/sys/devices/system/cpu/cpu{i}/cpufreq/energy_performance_preference' for i in range(2)]
        for path in self.paths:
            self.put(path, 'power')
            self.put(str(Path(path).parent / 'energy_performance_available_preferences'), 'power balance_power balance_performance performance')

    def put(self, path, value):
        file = self.root / path.lstrip('/')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value + '\n')

    def test_max_applies_boost_and_all_cpu_preferences(self):
        hw.set_cpu_boost('max')
        self.assertEqual(hw.read(hw.CPU_BOOST_PATH), '1')
        self.assertEqual([hw.read(p) for p in self.paths], ['performance'] * 2)

    def test_epp_failure_is_reported_but_other_cpus_are_attempted(self):
        original = hw.write
        def write(path, value):
            if path == self.paths[0]:
                raise OSError('rejected')
            original(path, value)
        with patch.object(hw, 'write', side_effect=write):
            with self.assertRaisesRegex(OSError, 'rejected'):
                hw.set_cpu_boost('max')
        self.assertEqual(hw.read(self.paths[1]), 'performance')

    def test_successful_write_with_wrong_readback_is_failure(self):
        original = hw.write
        def write(path, value):
            if path != self.paths[0]:
                original(path, value)
        with patch.object(hw, 'write', side_effect=write):
            with self.assertRaisesRegex(OSError, 'readback mismatch'):
                hw.set_cpu_boost('max')

    def test_boost_readback_mismatch_is_failure(self):
        original = hw.write
        def write(path, value):
            if path != hw.CPU_BOOST_PATH:
                original(path, value)
        with patch.object(hw, 'write', side_effect=write):
            with self.assertRaisesRegex(OSError, 'boost readback mismatch'):
                hw.set_cpu_boost('max')
        self.assertEqual([hw.read(p) for p in self.paths], ['performance'] * 2)  # EPP still applied

    def test_disabled_without_boost_control_is_reported(self):
        (self.root / hw.CPU_BOOST_PATH.lstrip('/')).unlink()
        with self.assertRaisesRegex(OSError, 'cpufreq/boost is not available'):
            hw.set_cpu_boost('disabled')
        self.assertEqual([hw.read(p) for p in self.paths], ['power'] * 2)

    def test_default_without_boost_control_is_not_an_error(self):
        (self.root / hw.CPU_BOOST_PATH.lstrip('/')).unlink()
        hw.set_cpu_boost('max')
        self.assertEqual([hw.read(p) for p in self.paths], ['performance'] * 2)

    def test_per_policy_boost_files_are_used_when_global_is_missing(self):
        (self.root / hw.CPU_BOOST_PATH.lstrip('/')).unlink()
        per_cpu = [f'/sys/devices/system/cpu/cpu{i}/cpufreq/boost' for i in range(2)]
        for path in per_cpu:
            self.put(path, '1')
        hw.set_cpu_boost('disabled')
        self.assertEqual([hw.read(p) for p in per_cpu], ['0', '0'])


class CpuGovernorTests(unittest.TestCase):
    """The per-mode power profile is the cpufreq governor; 'default' restores the driver's own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(hw, 'ROOT', str(self.root))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.govs = [f'/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor' for i in range(2)]
        self.epps = [f'/sys/devices/system/cpu/cpu{i}/cpufreq/energy_performance_preference' for i in range(2)]
        for gov, epp in zip(self.govs, self.epps):
            self.put(gov, 'powersave')
            self.put(str(Path(gov).parent / 'scaling_available_governors'), 'performance powersave')
            self.put(epp, 'balance_performance')
            self.put(str(Path(epp).parent / 'energy_performance_available_preferences'),
                     'default performance balance_performance balance_power power')

    def put(self, path, value):
        file = self.root / path.lstrip('/')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value + '\n')

    def test_choices_follow_the_advertised_governors(self):
        self.assertEqual(hw.cpu_governor_choices(), ['default', 'powersave', 'performance'])
        self.put(str(Path(self.govs[1]).parent / 'scaling_available_governors'), 'performance powersave ondemand')
        self.assertEqual(hw.cpu_governor_choices(), ['default', 'powersave', 'performance'])
        # without a governor to come back to, nothing is offered rather than a one-way switch
        self.put(str(Path(self.govs[1]).parent / 'scaling_available_governors'), 'performance')
        self.assertEqual(hw.cpu_governor_choices(), [])

    def test_no_cpufreq_means_no_choices_and_default_is_a_no_op(self):
        with patch.object(hw, 'ROOT', str(self.root / 'empty')):
            self.assertEqual(hw.cpu_governor_choices(), [])
            hw.set_cpu_governor('default')
            with self.assertRaisesRegex(OSError, 'not available'):
                hw.set_cpu_governor('performance')

    def test_performance_is_written_to_every_policy_and_default_restores_powersave(self):
        hw.set_cpu_governor('performance')
        self.assertEqual([hw.read(g) for g in self.govs], ['performance'] * 2)
        hw.set_cpu_governor('default')
        self.assertEqual([hw.read(g) for g in self.govs], ['powersave'] * 2)

    def test_default_prefers_schedutil_when_the_driver_offers_it(self):
        for gov in self.govs:
            self.put(str(Path(gov).parent / 'scaling_available_governors'), 'conservative ondemand powersave performance schedutil')
            self.put(gov, 'performance')
        hw.set_cpu_governor('default')
        self.assertEqual([hw.read(g) for g in self.govs], ['schedutil'] * 2)

    def test_readback_mismatch_is_reported_for_each_policy(self):
        original = hw.write
        def write(path, value):
            if path != self.govs[0]:
                original(path, value)
        with patch.object(hw, 'write', side_effect=write):
            with self.assertRaisesRegex(OSError, 'cpu0.*governor readback mismatch'):
                hw.set_cpu_governor('performance')
        self.assertEqual(hw.read(self.govs[1]), 'performance')

    def test_invalid_governor_is_rejected(self):
        with self.assertRaises(ValueError):
            hw.set_cpu_governor('ondemand')

    def test_epp_is_skipped_under_the_performance_governor(self):
        # the kernel fixes EPP under 'performance' and rejects writes; that is not a failure
        self.put(self.govs[0], 'performance')
        self.put(hw.CPU_BOOST_PATH, '1')
        hw.set_cpu_boost('efficient')
        self.assertEqual([hw.read(e) for e in self.epps], ['balance_performance', 'power'])
