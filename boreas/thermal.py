"""CPU temperature controller.

Default backend: bounded ASUS power-attribute feedback (power-feedback).
Experimental backend: G713PV/G713PV.336 kernel SMU bridge (/dev/boreas-smu-probe,
smu-bridge), selected only when BOREAS_SMU_BRIDGE=1 and sysfs enable_hw=Y/1 are
both present; applies MP1 0x3F and a single 5 s post-profile reapply (never raw
PCI 0xB8/0xBC).
Neither backend claims verified physical thermal ceiling enforcement.
"""
from __future__ import annotations

import time

from . import hw, ryzen

MIN_TEMP, DEFAULT_TEMP = 75, 96  # 96 is the UI's Off/Default sentinel
POWER_ATTRS = ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt")
REAPPLY_DELAY = 5.0


class Controller:
    def __init__(self, bridge: ryzen.BridgeSmu | None = None):
        self.bridge = bridge or ryzen.BridgeSmu()
        self.active_backend: str | None = None
        self.target: int | None = None
        self.applied: int | None = None
        self.ceilings: dict[str, int] = {}
        self.written: dict[str, int] = {}
        self.error = ""
        self.state = "off"
        self.last_tick = float("-inf")
        self.cool_since: float | None = None
        self.reapply_at: float | None = None

    @staticmethod
    def attributes() -> dict:
        attrs = {}
        for name in POWER_ATTRS:
            a = hw.get_attr(name)
            if a is None:
                continue
            # Never guess writable ranges for legacy sysfs or uninitialized firmware.
            if (a.current is None or a.min is None or a.max is None or
                    not 0 < a.min <= a.current <= a.max or a.choices):
                return {}
            attrs[name] = a
        return attrs if POWER_ATTRS[0] in attrs else {}

    def _backend(self) -> str:
        if self.bridge.available():
            return "smu-bridge"
        return "power-feedback"

    def supported(self) -> bool:
        if self.bridge.error and self.active_backend == "smu-bridge":
            return False
        return bool(self.bridge.available() or self.attributes())

    def stop(self, restore: bool = True) -> None:
        # Restore only values we still own; leave external changes alone.
        errors = []
        if self.active_backend == "smu-bridge":
            self.reapply_at = None
            if self.bridge.error:
                errors.append(self.error or f"SMU bridge disabled: {self.bridge.error}")
            elif restore and self.applied is not None and self.applied != DEFAULT_TEMP:
                try:
                    self.bridge.set(DEFAULT_TEMP)
                except (OSError, ValueError, ryzen.SmuError) as e:
                    errors.append(str(e))
            self.applied = None
        elif restore:
            for name, value in list(self.written.items()):
                try:
                    a = hw.get_attr(name)
                    if a and a.current == value:
                        hw.set_attr(name, self.ceilings[name])
                        if hw.get_attr(name).current != self.ceilings[name]:
                            raise OSError(f"{name}: restore readback mismatch")
                    del self.written[name]
                except (OSError, ValueError, AttributeError) as e:
                    errors.append(str(e))
        else:
            self.written.clear()
        self.target = None
        if self.active_backend != "smu-bridge":
            self.applied = None
        self.cool_since = None
        self.reapply_at = None
        self.last_tick = float("-inf")
        self.error = "; ".join(errors)
        self.state = "error" if errors else "off"
        if not self.written and not self.error:
            self.ceilings.clear()
            self.active_backend = None

    def start(self, target: int | None) -> None:
        if target is None or target == DEFAULT_TEMP:
            self.stop()
            return
        if type(target) is not int or not MIN_TEMP <= target < DEFAULT_TEMP:
            raise ValueError(f"CPU temperature limit must be {MIN_TEMP}..{DEFAULT_TEMP}")
        if self.error:
            return  # explicit mode reapply/Default is required after a fault
        backend = self.active_backend or self._backend()
        self.active_backend = backend
        if backend == "smu-bridge":
            self._start_smu_bridge(target)
        else:
            self._start_power_feedback(target)

    def _start_smu_bridge(self, target: int) -> None:
        try:
            self.bridge.set(target)
            self.target = target
            self.applied = target
            self.state = "active"
            now = time.monotonic()
            self.last_tick = now
            self.reapply_at = now + REAPPLY_DELAY
        except (OSError, ValueError, ryzen.SmuError) as e:
            self.applied = None
            self.reapply_at = None
            self.fail(str(e))

    def _start_power_feedback(self, target: int) -> None:
        if not self.ceilings:
            attrs = self.attributes()
            if not attrs:
                self.fail("ASUS power limits with valid ranges are unavailable")
                return
            self.ceilings = {name: a.current for name, a in attrs.items()}
        if target != self.target:
            self.cool_since = None
        self.target = target
        self.tick()

    def fail(self, message: str) -> None:
        self.error = message
        self.state = "error"
        self.cool_since = None
        self.reapply_at = None

    def tick(self) -> None:
        if self.target is None or self.error:
            return
        now = time.monotonic()
        if self.active_backend == "smu-bridge":
            if self.reapply_at is not None and now >= self.reapply_at:
                self.reapply_at = None
                self.last_tick = now
                try:
                    self.bridge.set(self.target)
                    self.applied = self.target
                    self.state = "active"
                except (OSError, ValueError, ryzen.SmuError) as e:
                    self.applied = None
                    self.fail(str(e))
            return
        if now - self.last_tick < 1:
            return
        self.last_tick = now
        temp = hw.temps().get("cpu")
        if temp is None or not 0 < temp <= 125:
            self.fail("CPU temperature sensor unavailable; power caps retained")
            return
        attrs = self.attributes()
        if set(attrs) != set(self.ceilings):
            self.fail("ASUS power controls changed; power caps retained")
            return
        # Do not fight firmware/another tuning service or adopt its higher ceiling.
        if any(a.current != self.written.get(n, self.ceilings[n]) for n, a in attrs.items()):
            self.fail("Power limits changed externally; controller stopped")
            return
        delta = 0
        if temp >= self.target - 1:
            delta = -min(10, max(2, 2 * (temp - self.target + 2)))
            self.cool_since = None
        elif temp <= self.target - 4:
            if self.cool_since is None:
                self.cool_since = now
            if now - self.cool_since >= 5:
                delta = 1
                self.cool_since = now
        else:
            self.cool_since = None
        try:
            # Lower sustained first, raise boost first to preserve usual ordering.
            names = list(attrs) if delta <= 0 else list(reversed(attrs))
            for name in names:
                a = attrs[name]
                value = max(a.min, min(self.ceilings[name], a.current + delta))
                if value != a.current:
                    # Record intent even if the write partially succeeds then raises.
                    self.written[name] = value
                    hw.set_attr(name, value)
                    if hw.get_attr(name).current != value:
                        raise OSError(f"{name}: power readback mismatch")
            self.state = ("limited" if temp > self.target and all(
                hw.get_attr(n).current == a.min for n, a in attrs.items()) else "active")
        except (OSError, ValueError, AttributeError) as e:
            self.fail(str(e))

    def status(self) -> dict:
        control = self.active_backend or self._backend()
        return {"min": MIN_TEMP, "max": DEFAULT_TEMP, "control": control,
                "target": self.target, "applied": self.applied, "state": self.state,
                "error": self.error, "ceilings_w": dict(self.ceilings),
                "requested_w": dict(self.written), "hard_limit": False,
                "available": self.supported()}
