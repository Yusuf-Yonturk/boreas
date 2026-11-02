"""Private Rust eBPF helper, opt-in through the UI/CLI backend setting.

No public socket and no automatic fallback to firmware on a helper failure.
The child owns BPF links for its whole lifetime; closing stdin releases them.
"""
from __future__ import annotations
import atexit
import json
import os
import select
import stat
import subprocess
import threading
import time

REQUEST_TIMEOUT = 15
MAX_RESPONSE = 1024 * 1024

HELPER = "/usr/lib/boreas/boreas-gpu-engine"
LOCAL_HELPER = "/etc/boreas/boreas-gpu-engine"
PROTOCOL_VERSION = 1
REQUIRED_CAPABILITIES = {"status", "eco", "standard"}


def helper_path():
    # Never overwrite a locally maintained helper during a package upgrade.
    return LOCAL_HELPER if os.path.lexists(LOCAL_HELPER) else HELPER


def check_protocol(path):
    info = os.stat(path)
    if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OSError("GPU engine must be root-owned and not group/world-writable")
    result = subprocess.run([path, "--protocol-info"], capture_output=True, text=True, timeout=5)
    try:
        data = json.loads(result.stdout)
        if (result.returncode or not isinstance(data, dict)
                or type(data.get("protocol_version")) is not int
                or data["protocol_version"] != PROTOCOL_VERSION
                or not isinstance(data.get("capabilities"), list)
                or not all(isinstance(c, str) for c in data["capabilities"])
                or not REQUIRED_CAPABILITIES.issubset(data["capabilities"])):
            raise ValueError()
    except (ValueError, TypeError):
        raise OSError("Incompatible GPU engine; protocol v1 with status/eco/standard is required") from None


class Engine:
    def __init__(self):
        self.process = None
        self.lock = threading.RLock()
        self.buffer = b""
        self.powerd_stopped = False
        atexit.register(self.close)

    def _start(self):
        if self.process is not None:
            if self.process.poll() is not None:
                raise OSError("Boreas GPU engine exited; choose a GPU mode again to start a new one")
            return
        if subprocess.run(["systemctl", "is-active", "--quiet", "cardwired.service"], timeout=5).returncode == 0:
            raise OSError("Stop cardwired before selecting Boreas's embedded eBPF engine")
        from . import hw
        attr = hw.get_attr("dgpu_disable")
        if attr and attr.current == 1:
            raise OSError("Leave firmware Eco and reboot before selecting the eBPF engine")
        path = helper_path()
        check_protocol(path)
        self.process = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None, bufsize=0, close_fds=True)

    def request(self, command):
        with self.lock:
            try:
                self._start()
                self.process.stdin.write((json.dumps({"cmd": command}) + "\n").encode())
                deadline = time.monotonic() + REQUEST_TIMEOUT
                while b"\n" not in self.buffer:
                    left = deadline - time.monotonic()
                    if left <= 0 or not select.select([self.process.stdout], [], [], max(0, left))[0]:
                        raise OSError("Boreas GPU engine timed out")
                    data = os.read(self.process.stdout.fileno(), 65536)
                    if not data:
                        raise OSError("Boreas GPU engine disconnected; see boreasd journal")
                    self.buffer += data
                    if len(self.buffer) > MAX_RESPONSE:
                        raise OSError("Boreas GPU engine response too large")
                line, self.buffer = self.buffer.split(b"\n", 1)
                response = json.loads(line)
                if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                    raise ValueError("Invalid GPU engine response")
                if response["ok"]:
                    status = response.get("status")
                    if not isinstance(status, dict) or status.get("policy") not in ("eco", "standard"):
                        raise ValueError("Invalid GPU engine status")
                    if command in ("eco", "standard") and status["policy"] != command:
                        raise ValueError("GPU engine did not apply the requested policy")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self._disconnect()
                self._restore_powerd()
                raise OSError(str(exc)) from exc
            if not response.get("ok"):
                raise OSError(response.get("error", "GPU engine rejected request"))
            return response["status"]

    def set_eco(self, enabled):
        with self.lock:
            if not enabled:
                self.request("standard")
                self._restore_powerd()
                return
            active = subprocess.run(["systemctl", "is-active", "--quiet", "nvidia-powerd.service"], timeout=5).returncode == 0
            if active:
                # The stop can time out after systemd has already stopped the unit.
                self.powerd_stopped = True
            try:
                if active:
                    r = subprocess.run(["systemctl", "stop", "nvidia-powerd.service"], timeout=10)
                    if r.returncode:
                        raise OSError("Could not stop nvidia-powerd")
                self.request("eco")
            except Exception:
                # A rejected transition may have left the previous Eco policy active.
                try:
                    if self.process is not None and self.process.poll() is None:
                        self.request("standard")
                except OSError:
                    self._disconnect()
                self._restore_powerd()
                raise

    def _restore_powerd(self):
        if self.powerd_stopped:
            r = subprocess.run(["systemctl", "start", "nvidia-powerd.service"], timeout=10)
            if r.returncode:
                # NVIDIA's unit can limit starts over the entire boot, so
                # ordinary Eco/Standard cycles can exhaust its start budget.
                # Retry once only for that specific systemd result. Other
                # failure results are not reset; there is no restart loop.
                result = subprocess.run(["systemctl", "show", "nvidia-powerd.service",
                                         "--property=Result", "--value"],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode or result.stdout.strip() != "start-limit-hit":
                    raise OSError("Could not restore nvidia-powerd")
                reset = subprocess.run(["systemctl", "reset-failed", "nvidia-powerd.service"], timeout=5)
                if reset.returncode:
                    raise OSError("Could not reset nvidia-powerd's start limit")
                retry = subprocess.run(["systemctl", "start", "nvidia-powerd.service"], timeout=10)
                if retry.returncode:
                    raise OSError("Could not restore nvidia-powerd after resetting its start limit")
            self.powerd_stopped = False

    def _disconnect(self):
        if self.process is not None:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            if self.process.stdout and not self.process.stdout.closed:
                self.process.stdout.close()
        # Keep the failed child reference: no implicit restart / reblock after error.

    def close(self, strict=False):
        with self.lock:
            self._disconnect()
            try:
                self._restore_powerd()
            except (OSError, subprocess.SubprocessError):
                if strict:
                    raise


_instance = None
_config_backend: str | None = None


def set_config_backend(value: str | None) -> None:
    global _config_backend
    _config_backend = value


def get():
    global _instance
    if _instance is None:
        _instance = Engine()
    return _instance


def reset():
    """Explicit user transition permits a fresh child; faults never auto-restart.

    Called by the backend switch and by an explicit GPU mode request that finds
    the helper in a fault state. Periodic re-application never calls this.
    """
    global _instance
    if _instance is not None:
        _instance.close(strict=True)
        _instance = None


def selected():
    env = os.environ.get("BOREAS_GPU_BACKEND")
    if env is not None:
        return env == "ebpf"
    return _config_backend == "ebpf"
