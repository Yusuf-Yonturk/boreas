"""GPU backend routing must never fall back to driver unloads after an eBPF error."""
import os
import unittest
from unittest.mock import Mock, patch
from boreas import gpu, gpu_engine, hw


class RoutingTests(unittest.TestCase):
    def test_ebpf_never_uses_firmware_even_on_failure(self):
        backend = Mock()
        backend.set_eco.side_effect = OSError("engine failed")
        with patch.dict(os.environ, BOREAS_GPU_BACKEND="ebpf"), patch.object(gpu_engine, "get", return_value=backend), patch.object(gpu, "_detach_dgpu") as detach, patch.object(hw, "set_attr") as write:
            with self.assertRaises(OSError):
                gpu.set_eco(True)
            detach.assert_not_called()
            write.assert_not_called()

    def test_hidden_state_is_read_from_engine(self):
        backend = Mock()
        backend.request.return_value = {"policy": "eco", "runtime_status": "active", "power_state": "D0", "users": ["game(1)"]}
        with patch.dict(os.environ, BOREAS_GPU_BACKEND="ebpf"), patch.object(gpu_engine, "get", return_value=backend), patch.object(hw, "get_attr", return_value=Mock(current=1)), patch.object(hw, "read_int", return_value=0), patch.object(gpu, "dgpu_runtime_status") as probe:
            st = gpu.state()
            self.assertEqual(st["mode"], "eco")
            self.assertEqual(st["dgpu_runtime"], "active")  # blocked != asleep
            self.assertEqual(st["engine"]["power_state"], "D0")
            probe.assert_not_called()

    def test_failed_status_is_unknown_not_standard(self):
        backend = Mock()
        backend.request.side_effect = OSError("unavailable")
        with patch.dict(os.environ, BOREAS_GPU_BACKEND="ebpf"), patch.object(gpu_engine, "get", return_value=backend), patch.object(hw, "get_attr", return_value=None), patch.object(hw, "read_int", return_value=0):
            st = gpu.state()
            self.assertEqual(st["mode"], "unknown")
            self.assertIn("unavailable", st["backend_error"])

    def test_mux_ultimate_does_not_start_engine(self):
        with patch.dict(os.environ, BOREAS_GPU_BACKEND="ebpf"), patch.object(gpu_engine, "get") as get, patch.object(hw, "get_attr", return_value=Mock(current=0)), patch.object(hw, "read_int", return_value=0):
            self.assertEqual(gpu.state()["mode"], "ultimate")
            get.assert_not_called()

    def test_firmware_is_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(gpu_engine.selected())

    def test_existing_cardwire_refused_before_spawn(self):
        engine = gpu_engine.Engine()
        with patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=0)), patch.object(gpu_engine.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(OSError, "cardwired"):
                engine.request("eco")
            popen.assert_not_called()

    def test_hard_eco_refused_before_spawn(self):
        engine = gpu_engine.Engine()
        with patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=1)), patch.object(hw, "get_attr", return_value=Mock(current=1)), patch.object(gpu_engine.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(OSError, "firmware Eco"):
                engine.request("eco")
            popen.assert_not_called()

    def test_request_failure_restores_stopped_powerd(self):
        engine = gpu_engine.Engine()
        engine.request = Mock(side_effect=OSError("failed"))
        with patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=0)) as run:
            with self.assertRaises(OSError):
                engine.set_eco(True)
            self.assertFalse(engine.powerd_stopped)
            self.assertIn(unittest.mock.call(["systemctl", "start", "nvidia-powerd.service"], timeout=10), run.call_args_list)

class TransportTests(unittest.TestCase):
    def helper(self, script):
        import subprocess
        import sys
        engine = gpu_engine.Engine()
        engine.process = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self.addCleanup(engine.close)
        return engine

    def test_real_pipe_roundtrip_and_cleanup(self):
        engine = self.helper('import sys,json\nfor line in sys.stdin:\n print(json.dumps({"ok":True,"status":{"policy":"standard"}}),flush=True)')
        self.assertEqual(engine.request("status")["policy"], "standard")
        engine.close()
        self.assertIsNotNone(engine.process.poll())
        engine.close()  # cleanup is idempotent

    def test_eof_is_failure_without_restarting(self):
        engine = self.helper('import sys\nsys.stdin.readline()')
        with self.assertRaisesRegex(OSError, "disconnected"):
            engine.request("status")
        child = engine.process
        with self.assertRaisesRegex(OSError, "exited"):
            engine.request("status")
        self.assertIs(engine.process, child)

    def test_malformed_responses_disconnect(self):
        for payload in ['[]', '{"ok":true}', '{"ok":1,"status":{}}', '{"ok":true,"status":{"policy":"invented"}}', 'not-json']:
            with self.subTest(payload=payload):
                engine = self.helper('import sys\nsys.stdin.readline()\nprint(' + repr(payload) + ',flush=True)\nsys.stdin.read()')
                with self.assertRaises(OSError):
                    engine.request("status")
                self.assertIsNotNone(engine.process.poll())

    def test_success_with_wrong_policy_is_not_successful_transition(self):
        engine = self.helper('import sys,json\nfor line in sys.stdin:\n print(json.dumps({"ok":True,"status":{"policy":"standard"}}),flush=True)')
        with self.assertRaisesRegex(OSError, "requested policy"):
            engine.request("eco")
        self.assertIsNotNone(engine.process.poll())

    def test_timeout_terminates_helper(self):
        engine = self.helper('import sys,time\nsys.stdin.readline()\ntime.sleep(30)')
        with patch.object(gpu_engine, "REQUEST_TIMEOUT", 0.05):
            with self.assertRaisesRegex(OSError, "timed out"):
                engine.request("status")
        self.assertIsNotNone(engine.process.poll())

    def test_oversized_response_terminates_helper(self):
        engine = self.helper('import sys\nsys.stdin.readline()\nprint("x"*8192,flush=True)\nsys.stdin.read()')
        with patch.object(gpu_engine, "MAX_RESPONSE", 100):
            with self.assertRaisesRegex(OSError, "too large"):
                engine.request("status")

    def test_rejection_is_not_transport_failure(self):
        engine = self.helper('import sys,json\nfor line in sys.stdin:\n print(json.dumps({"ok":False,"error":"monitor connected"}),flush=True)')
        with self.assertRaisesRegex(OSError, "monitor connected"):
            engine.request("eco")
        self.assertIsNone(engine.process.poll())

class ServiceRollbackTests(unittest.TestCase):
    def test_start_limit_is_reset_and_retried_once(self):
        engine = gpu_engine.Engine()
        engine.powerd_stopped = True
        responses = [Mock(returncode=1), Mock(returncode=0, stdout="start-limit-hit\n"),
                     Mock(returncode=0), Mock(returncode=0)]
        with patch.object(gpu_engine.subprocess, "run", side_effect=responses) as run:
            engine._restore_powerd()
        self.assertFalse(engine.powerd_stopped)
        self.assertEqual([c.args[0][1] for c in run.call_args_list],
                         ["start", "show", "reset-failed", "start"])

    def test_driver_failure_is_not_reset_or_retried(self):
        engine = gpu_engine.Engine()
        engine.powerd_stopped = True
        with patch.object(gpu_engine.subprocess, "run", side_effect=[
                Mock(returncode=1), Mock(returncode=0, stdout="exit-code\n")]) as run:
            with self.assertRaises(OSError):
                engine._restore_powerd()
        self.assertTrue(engine.powerd_stopped)
        self.assertEqual([c.args[0][1] for c in run.call_args_list], ["start", "show"])
        engine.powerd_stopped = False  # atexit must not touch real services in this test

    def test_start_limit_retry_failure_keeps_restore_pending(self):
        engine = gpu_engine.Engine()
        engine.powerd_stopped = True
        with patch.object(gpu_engine.subprocess, "run", side_effect=[
                Mock(returncode=1), Mock(returncode=0, stdout="start-limit-hit\n"),
                Mock(returncode=0), Mock(returncode=1)]) as run:
            with self.assertRaisesRegex(OSError, "after resetting"):
                engine._restore_powerd()
        self.assertTrue(engine.powerd_stopped)
        self.assertEqual(run.call_count, 4)
        engine.powerd_stopped = False

    def test_start_limit_reset_failure_does_not_retry(self):
        engine = gpu_engine.Engine()
        engine.powerd_stopped = True
        with patch.object(gpu_engine.subprocess, "run", side_effect=[
                Mock(returncode=1), Mock(returncode=0, stdout="start-limit-hit\n"),
                Mock(returncode=1)]) as run:
            with self.assertRaisesRegex(OSError, "Could not reset"):
                engine._restore_powerd()
        self.assertTrue(engine.powerd_stopped)
        self.assertEqual(run.call_count, 3)
        engine.powerd_stopped = False

    def test_stop_timeout_restores_previously_active_service(self):
        import subprocess
        engine = gpu_engine.Engine()
        def run(args, **kw):
            if args[1] == "stop":
                raise subprocess.TimeoutExpired(args, 10)
            return Mock(returncode=0)
        with patch.object(gpu_engine.subprocess, "run", side_effect=run) as calls:
            with self.assertRaises(subprocess.TimeoutExpired):
                engine.set_eco(True)
            self.assertIn(unittest.mock.call(["systemctl", "start", "nvidia-powerd.service"], timeout=10), calls.call_args_list)
            self.assertFalse(engine.powerd_stopped)

    def test_failed_eco_resets_existing_policy_before_restore(self):
        engine = gpu_engine.Engine()
        engine.process = Mock()
        engine.process.poll.return_value = None
        engine.request = Mock(side_effect=[OSError("display attached"), {"policy": "standard"}])
        with patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=0)):
            with self.assertRaises(OSError):
                engine.set_eco(True)
            self.assertEqual(engine.request.call_args_list, [unittest.mock.call("eco"), unittest.mock.call("standard")])
            self.assertFalse(engine.powerd_stopped)
        engine.process = None

class BackendSwitchTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import threading
        from boreas import config, daemon
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = daemon.Daemon.__new__(daemon.Daemon)
        self.d.cfg = config.Config(self.tmp.name + "/config.json")
        self.d.lock = threading.RLock()
        self.d.notice = ""
        self.d.apply_gpu_target = Mock(return_value={})
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(gpu_engine.set_config_backend, None)
        self.backend = Mock()
        self.getter = patch.object(gpu_engine, "get", return_value=self.backend)
        self.getter.start()
        self.addCleanup(self.getter.stop)
        self.reset = patch.object(gpu_engine, "reset")
        self.reset_mock = self.reset.start()
        self.addCleanup(self.reset.stop)
        for name, value in (("supported", True),):
            patcher = patch.object(gpu, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(hw, "get_attr", return_value=Mock(current=1))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejected_opt_in_preserves_disk_and_does_not_touch_firmware(self):
        self.d.cfg.save()
        self.backend.request.side_effect = OSError("unsupported kernel")
        with self.assertRaisesRegex(OSError, "unsupported kernel"):
            self.d.handle({"cmd": "set_gpu_backend", "backend": "ebpf"})
        from boreas.config import Config
        self.assertEqual(Config(self.d.cfg.path)["gpu_backend"], "firmware")
        self.assertFalse(gpu_engine.selected())
        self.d.apply_gpu_target.assert_not_called()
        self.reset_mock.assert_called_once()

    def test_failed_policy_application_restores_choice(self):
        self.d.cfg["gpu_target"] = "eco"
        self.d.apply_gpu_target.side_effect = OSError("monitor attached")
        with self.assertRaisesRegex(OSError, "monitor attached"):
            self.d.set_gpu_backend("ebpf")
        self.assertEqual(self.d.cfg["gpu_backend"], "firmware")
        self.assertEqual(self.d.cfg["gpu_target"], "eco")
        self.assertFalse(os.path.exists(self.d.cfg.path))

    def test_on_off_on_releases_and_recreates_child(self):
        self.d.set_gpu_backend("ebpf")
        self.d.set_gpu_backend("firmware")
        self.assertEqual(self.d.cfg["gpu_target"], "standard")
        self.reset_mock.assert_called_once()
        self.d.set_gpu_backend("ebpf")
        self.assertTrue(gpu_engine.selected())
        self.assertEqual(self.backend.request.call_count, 2)

    def test_disable_in_ultimate_never_writes_mux_or_requests_reboot(self):
        self.d.cfg["gpu_backend"] = "ebpf"
        self.d.cfg["gpu_target"] = "ultimate"
        with patch.object(hw, "get_attr", return_value=Mock(current=0)), patch.object(gpu, "set_mux") as mux:
            result = self.d.set_gpu_backend("firmware")
        self.assertEqual(result, {"backend": "firmware"})
        self.assertEqual(self.d.cfg["gpu_target"], "ultimate")
        self.d.apply_gpu_target.assert_not_called()
        mux.assert_not_called()

    def test_disable_preserves_pending_ultimate_request(self):
        self.d.cfg["gpu_backend"] = "ebpf"
        self.d.cfg["gpu_target"] = "ultimate"
        self.d.set_gpu_backend("firmware")
        self.assertEqual(self.d.cfg["gpu_target"], "ultimate")
        self.d.apply_gpu_target.assert_not_called()

    def test_environment_override_is_not_silently_ignored(self):
        with patch.dict(os.environ, BOREAS_GPU_BACKEND="ebpf"):
            with self.assertRaisesRegex(ValueError, "override"):
                self.d.set_gpu_backend("firmware")
        self.assertFalse(os.path.exists(self.d.cfg.path))

    def test_save_failure_restores_runtime_backend(self):
        with patch.object(self.d.cfg, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.d.set_gpu_backend("ebpf")
        self.assertFalse(gpu_engine.selected())
        self.assertEqual(self.d.cfg["gpu_backend"], "firmware")

    def test_firmware_save_failure_restores_previous_ebpf_policy(self):
        self.d.cfg["gpu_backend"] = "ebpf"
        self.d.cfg["gpu_target"] = "eco"
        gpu_engine.set_config_backend("ebpf")
        with patch.object(self.d.cfg, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.d.set_gpu_backend("firmware")
        self.assertTrue(gpu_engine.selected())
        self.d.apply_gpu_target.assert_called_with("eco", explicit=True)


class CompatibilityTests(unittest.TestCase):
    def test_local_helper_takes_priority_even_if_broken(self):
        with patch.object(gpu_engine.os.path, "lexists", return_value=True):
            self.assertEqual(gpu_engine.helper_path(), gpu_engine.LOCAL_HELPER)

    def test_incompatible_helper_rejected_before_spawn(self):
        engine = gpu_engine.Engine()
        with patch.object(gpu_engine.subprocess, "run", side_effect=[
                Mock(returncode=1), Mock(returncode=0, stdout='{"protocol_version":2,"capabilities":[]}')]), \
                patch.object(hw, "get_attr", return_value=None), \
                patch.object(gpu_engine.os, "stat", return_value=Mock(st_uid=0, st_mode=0o755)), \
                patch.object(gpu_engine.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(OSError, "Incompatible"):
                engine.request("status")
            spawn.assert_not_called()

    def test_protocol_requires_version_and_all_commands(self):
        import json
        with patch.object(gpu_engine.os, "stat", return_value=Mock(st_uid=0, st_mode=0o755)):
            for data in ({}, {"protocol_version": True, "capabilities": ["status", "eco", "standard"]},
                         {"protocol_version": 1, "capabilities": ["status"]}):
                with patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(data))):
                    with self.assertRaises(OSError):
                        gpu_engine.check_protocol("/helper")
            with patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(
                    {"protocol_version": 1, "capabilities": ["status", "eco", "standard"]}))):
                gpu_engine.check_protocol("/helper")

    def test_explicit_reset_allows_new_engine_after_close(self):
        old = gpu_engine._instance
        self.addCleanup(setattr, gpu_engine, "_instance", old)
        child = gpu_engine.Engine()
        with patch.object(child, "close") as close:
            gpu_engine._instance = child
            gpu_engine.reset()
            self.assertIsNone(gpu_engine._instance)
            self.assertIsNot(gpu_engine.get(), child)
            close.assert_called_once_with(strict=True)


class FaultRecoveryTests(unittest.TestCase):
    """An explicit GPU mode request replaces a faulted helper; periodic paths never do."""
    def setUp(self):
        import tempfile
        import threading
        from boreas import config, daemon
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = daemon.Daemon.__new__(daemon.Daemon)
        self.d.cfg = config.Config(self.tmp.name + "/config.json")
        self.d.lock = threading.RLock()
        self.d.notice = ""
        self.d.eco_stuck = False
        self.env = patch.dict(os.environ, BOREAS_GPU_BACKEND="ebpf")
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in (("supported", True), ("set_eco", None)):
            patcher = patch.object(gpu, name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.faulted = {"backend_error": "Boreas GPU engine exited", "mode": "unknown", "mux": 1,
                        "has_mux": True, "pending_reboot": False}
        self.healthy = {"backend_error": None, "mode": "eco", "mux": 1, "has_mux": True, "pending_reboot": False}

    def test_explicit_request_resets_faulted_engine_once(self):
        with patch.object(gpu, "state", side_effect=[self.faulted, self.healthy]), \
                patch.object(gpu_engine, "reset") as reset:
            self.d.apply_gpu_target("standard", explicit=True)
        reset.assert_called_once()
        self.set_eco.assert_called_once_with(False)

    def test_explicit_request_fails_if_fresh_engine_also_faults(self):
        with patch.object(gpu, "state", return_value=self.faulted), patch.object(gpu_engine, "reset") as reset:
            with self.assertRaisesRegex(OSError, "exited"):
                self.d.apply_gpu_target("standard", explicit=True)
        reset.assert_called_once()
        self.set_eco.assert_not_called()

    def test_periodic_apply_never_restarts_faulted_engine(self):
        with patch.object(gpu, "state", return_value=self.faulted), patch.object(gpu_engine, "reset") as reset:
            self.assertEqual(self.d.apply_gpu_target("eco"), {})
        reset.assert_not_called()
        self.set_eco.assert_not_called()
        self.assertIn("exited", self.d.notice)

    def test_firmware_backend_error_is_not_reset(self):
        with patch.dict(os.environ, BOREAS_GPU_BACKEND="firmware"), \
                patch.object(gpu, "state", return_value=self.faulted), patch.object(gpu_engine, "reset") as reset:
            with self.assertRaises(OSError):
                self.d.apply_gpu_target("standard", explicit=True)
        reset.assert_not_called()
