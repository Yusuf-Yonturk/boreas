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
