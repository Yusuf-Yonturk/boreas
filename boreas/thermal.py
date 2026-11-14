"""CPU temperature controller.

Default backend: bounded ASUS power-attribute feedback (power-feedback) with an
escalation ladder below the mode's own CPU policy (EPP, cpufreq boost, governor).
Experimental backend: G713PV/G713PV.336 kernel SMU bridge (/dev/boreas-smu-probe,
smu-bridge), selected only when BOREAS_SMU_BRIDGE=1 and sysfs enable_hw=Y/1 are
both present; applies MP1 0x3F and a single 5 s post-profile reapply (never raw
PCI 0xB8/0xBC).
Neither backend claims verified physical thermal ceiling enforcement.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import hw, ryzen

MIN_TEMP, DEFAULT_TEMP = hw.TEMP_MIN, hw.TEMP_OFF  # 96 is the UI's Off/Default sentinel
POWER_ATTRS = ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt")
REAPPLY_DELAY = 5.0

# power-feedback bands and rates; e = temperature - target (integer Tctl)
HOLD_BAND = 2                 # -2 <= e <= 0: hold
COOL_BAND = 3                 # e <= -3: recover power or release a rung
CUT_MIN_W, CUT_MAX_W = 2, 6   # W per second while hot: -min(6, max(2, 2*e))
RECOVER_STEP_W, RECOVER_PERIOD = 2, 2.0  # +2 W every 2 s while cool at stage 0
ESCALATE_GRACE = 10.0         # seconds hot at the floor before the next rung
RUNG_DWELL = 15.0             # minimum seconds between two rung changes
RELEASE_DWELL = 20.0          # continuous cool seconds before a rung is released
# Fast path: where the firmware does not let the power rails bind (measured on a G713PV: 30 W
# requested, 50-60 W drawn, 93 °C), boost is the only rung that cools. This far over the target
# for this long at the floor, the ladder goes straight to it instead of one rung per dwell.
FAST_OVER = 5                 # °C over the target, the margin of daemon.HOT_OVER_LIMIT
FAST_GRACE = 3.0              # continuous seconds that far over at the floor: the floor gets its chance first
# A rung that is needed again right after its release was released too early: the next release
# waits longer after the escalation. The continuous-cool requirement itself stays RELEASE_DWELL,
# so sensor jitter at the band edge cannot hold a rung for longer than before.
RELAPSE_WINDOW = 60.0         # seconds after a release in which a new escalation counts as a relapse
RELEASE_DWELL_MAX = 320.0     # the hold after an escalation doubles per relapse up to this
BOOST_RUNG = ("boost", False)
RESYNC_MAX, RESYNC_WINDOW = 3, 60.0  # re-assertions of a user cap before giving up

EPP_LADDER = ("performance", "balance_performance", "balance_power", "power")


@dataclass(frozen=True)
class Baseline:
    """The mode's own CPU policy: the ceiling the ladder only ever goes below."""
    boost_mode: str = "default"
    governor: str = "default"
    base: int = hw.BALANCED

    @property
    def epp(self) -> str:
        return hw.epp_for_boost_mode(self.boost_mode, self.base)

    @property
    def boost_on(self) -> bool:
        return self.boost_mode != "disabled"

    def policy(self) -> dict:
        return {"governor": self.governor, "boost": self.boost_on, "epp": self.epp}


def build_ladder(baseline: Baseline) -> list[tuple]:
    """Rungs below the baseline, weakest cut first. ("epp", name) | ("boost", False) | ("governor", "default")."""
    epp_ok = bool(hw.cpu_epp_files())
    boost_ok = baseline.boost_on and bool(hw.cpu_boost_paths())
    rungs: list[tuple] = []
    if baseline.governor != "performance":
        if epp_ok and baseline.epp in EPP_LADDER:
            rungs += [("epp", e) for e in EPP_LADDER[EPP_LADDER.index(baseline.epp) + 1:]]
        if boost_ok:
            rungs.append(("boost", False))
    else:
        # The performance governor fixes the EPP: boost first, then leave the governor,
        # which lands on balance_performance, then the remaining EPP notches.
        if boost_ok:
            rungs.append(("boost", False))
        if hw.cpu_governor_choices():
            rungs.append(("governor", "default"))
            if epp_ok:
                rungs += [("epp", e) for e in EPP_LADDER[2:]]
    return rungs


def rung_text(rung: tuple) -> str:
    kind, value = rung
    if kind == "epp":
        return f"EPP {value}"
    if kind == "boost":
        return "boost off"
    return "power profile default"


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
        self.hot_since: float | None = None
        self.over_since: float | None = None
        self.last_recover = float("-inf")
        self.reapply_at: float | None = None
        self.release_dwell = RELEASE_DWELL
        self.released_at = float("-inf")
        self.escalated_at = float("-inf")
        # per-mode inputs
        self.floor_w: int | None = None
        self.floor_min: int | None = None
        self.user_caps: dict[str, int] = {}
        self.resync_log: list[float] = []
        # escalation ladder below the mode's CPU policy
        self.baseline = Baseline()
        self.ladder: list[tuple] = []
        self.stage = 0
        self.effective: dict = self.baseline.policy()
        self.stage_changed_at = float("-inf")
        self.events: list[str] = []

    def opt_in_warning(self) -> str | None:
        """Startup line when BOREAS_SMU_BRIDGE=1 is set but the bridge cannot be used."""
        if os.environ.get(ryzen.BRIDGE_OPT_IN_ENV) != "1" or self.bridge.available():
            return None
        reason = self.bridge.error or "module not loaded or enable_hw is off"
        return f"SMU bridge requested ({ryzen.BRIDGE_OPT_IN_ENV}=1) but not usable: {reason}; using power-feedback"

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

    def event(self, text: str) -> None:
        self.events.append(text)

    def drain_events(self) -> list[str]:
        out, self.events = self.events, []
        return out

    # ---- ladder ------------------------------------------------------------------

    def set_baseline(self, baseline: Baseline) -> None:
        """The daemon wrote the mode's CPU policy; the ladder restarts below it. Caps untouched."""
        self.baseline = baseline
        self.ladder = build_ladder(baseline)
        self.stage = 0
        self.effective = baseline.policy()
        self.stage_changed_at = time.monotonic()
        self.over_since = None
        self._reset_relapse()

    def policy_for_stage(self, k: int) -> dict:
        policy = self.baseline.policy()
        for kind, value in self.ladder[:k]:
            if kind == "epp":
                policy["epp"] = value
            elif kind == "boost":
                policy["boost"] = False
            else:
                policy["governor"] = "default"
                policy["epp"] = "balance_performance"
        return policy

    def _apply_stage(self, k: int) -> None:
        """Write only what differs from the last written policy; governor first so EPP is writable."""
        target = self.policy_for_stage(k)
        writers = (("governor", hw.set_cpu_governor), ("boost", hw.set_boost_enabled), ("epp", hw.set_epp))
        for key, write in writers:
            if target[key] != self.effective.get(key):
                write(target[key])
                self.effective[key] = target[key]

    def _escalate(self, now: float, temp: int, fast: bool = False) -> None:
        rung = self.ladder[self.stage]
        try:
            self._apply_stage(self.stage + 1)
        except (OSError, ValueError) as e:
            self.event(f"cpu target: {rung_text(rung)} failed ({e}); rung skipped")
            self.ladder.pop(self.stage)
            self.stage_changed_at = now
            return
        self.stage += 1
        self.stage_changed_at = now
        self.escalated_at = now
        if now - self.released_at < RELAPSE_WINDOW:
            self.release_dwell = min(RELEASE_DWELL_MAX, self.release_dwell * 2)
        elif self.released_at != float("-inf"):
            self.release_dwell = RELEASE_DWELL
        self.released_at = float("-inf")  # one relapse per release, however many rungs follow
        pl1 = self.written.get(POWER_ATTRS[0], self.ceilings.get(POWER_ATTRS[0]))
        self.event(f"cpu target: stage {self.stage}/{len(self.ladder)} ({rung_text(rung)}{', fast' if fast else ''}), "
                   f"PL1 {pl1} W (floor), {temp}°C")

    def _reset_relapse(self) -> None:
        self.release_dwell = RELEASE_DWELL
        self.released_at = float("-inf")
        self.escalated_at = float("-inf")

    def _boost_rung_ahead(self) -> bool:
        return BOOST_RUNG in self.ladder[self.stage:]

    def _escalate_to_boost(self, now: float, temp: int) -> None:
        """Every rung up to and including boost off, in one tick. A rung that fails is dropped
        by _escalate, so each pass either advances the stage or shortens the ladder."""
        while self._boost_rung_ahead():
            self._escalate(now, temp, fast=True)

    def _release(self, now: float, temp: int) -> None:
        rung = self.ladder[self.stage - 1]
        try:
            self._apply_stage(self.stage - 1)
        except (OSError, ValueError) as e:
            self.event(f"cpu target: could not release {rung_text(rung)} ({e}); retrying later")
            self.stage_changed_at = now
            return
        self.stage -= 1
        self.stage_changed_at = now
        self.released_at = now
        self.event(f"cpu target: stage {self.stage}/{len(self.ladder)} (released {rung_text(rung)}), {temp}°C")

    # ---- lifecycle ---------------------------------------------------------------

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
                    else:
                        self.event(f"left {name} at {a.current if a else '?'} W (changed externally)")
                    del self.written[name]
                except (OSError, ValueError, AttributeError) as e:
                    errors.append(str(e))
            if self.stage > 0:
                try:
                    self._apply_stage(0)
                except (OSError, ValueError) as e:
                    self.event(f"cpu target: could not restore the CPU policy ({e})")
        else:
            self.written.clear()
        self.stage = 0
        self.target = None
        if self.active_backend != "smu-bridge":
            self.applied = None
        self.cool_since = None
        self.hot_since = None
        self.over_since = None
        self._reset_relapse()
        self.reapply_at = None
        self.resync_log = []
        self.last_tick = float("-inf")
        self.last_recover = float("-inf")
        self.error = "; ".join(errors)
        self.state = "error" if errors else "off"
        if not self.written and not self.error:
            self.ceilings.clear()
            self.floor_min = None
            self.active_backend = None

    def start(self, target: int | None, floor_w: int | None = None, user_caps: dict | None = None) -> None:
        if target is None or target == DEFAULT_TEMP:
            self.stop()
            return
        if type(target) is not int or not MIN_TEMP <= target < DEFAULT_TEMP:
            raise ValueError(f"CPU temperature limit must be {MIN_TEMP}..{DEFAULT_TEMP}")
        if self.error and self.active_backend == "smu-bridge":
            return  # a bridge fault needs an explicit mode reapply/Default
        backend = self.active_backend or self._backend()
        self.active_backend = backend
        if target != self.target or floor_w != self.floor_w:
            self._reset_relapse()  # what was learned belongs to the old target and floor
        self.floor_w = floor_w
        self.user_caps = {n: int(v) for n, v in (user_caps or {}).items() if n in POWER_ATTRS}
        if backend == "smu-bridge":
            self._start_smu_bridge(target)
        else:
            if self.error:
                self.event(f"cpu target: retrying after: {self.error}")
                self.error = ""
                self.state = "off"
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
            self.floor_min = attrs[POWER_ATTRS[0]].min
        if target != self.target:
            self.cool_since = None
            self.hot_since = None
            self.over_since = None
        self.target = target
        self.state = "active"
        self.tick()

    def fail(self, message: str) -> None:
        self.error = message
        self.state = "error"
        self.cool_since = None
        self.hot_since = None
        self.over_since = None
        self.reapply_at = None

    # ---- feedback ----------------------------------------------------------------

    def floor(self, name: str, a) -> int:
        if name == POWER_ATTRS[0] and self.floor_w is not None:
            return max(a.min, min(self.ceilings[name], self.floor_w))
        return a.min

    def effective_floor_w(self) -> int | None:
        if self.floor_min is None:
            return None
        ceiling = self.ceilings.get(POWER_ATTRS[0], self.floor_min)
        return max(self.floor_min, min(ceiling, self.floor_w)) if self.floor_w is not None else self.floor_min

    def _reconcile(self, attrs: dict, now: float) -> bool:
        """Follow small firmware/user drifts instead of latching; re-assert a user cap the
        firmware raised; fail only when another writer keeps fighting."""
        for name, a in attrs.items():
            expected = self.written.get(name, self.ceilings[name])
            cur = a.current
            if cur == expected:
                continue
            if a.min <= cur <= self.ceilings[name]:
                if cur == self.ceilings[name]:
                    self.written.pop(name, None)
                else:
                    self.written[name] = cur
                self.event(f"power limits re-synced: {name} {cur} W (was {expected} W)")
            elif name in self.user_caps:
                self.resync_log = [t for t in self.resync_log if now - t < RESYNC_WINDOW]
                if len(self.resync_log) >= RESYNC_MAX:
                    self.fail("Power limits changed externally; controller stopped")
                    return False
                self.resync_log.append(now)
                hw.set_attr(name, expected)
                if hw.get_attr(name).current != expected:
                    raise OSError(f"{name}: power readback mismatch")
                a.current = expected  # the snapshot feeds this tick's move
                self.event(f"power limit re-asserted: {name} {expected} W (firmware set {cur} W)")
            else:
                self.ceilings[name] = cur
                self.event(f"ceiling re-captured: {name} {cur} W")
        return True

    def _move(self, attrs: dict, delta: int) -> None:
        # Lower sustained first, raise boost first to preserve usual ordering.
        names = list(attrs) if delta <= 0 else list(reversed(attrs))
        for name in names:
            a = attrs[name]
            old = a.current
            value = max(self.floor(name, a), min(self.ceilings[name], old + delta))
            if value == old:
                continue
            # Record intent even if the write partially succeeds then raises.
            self.written[name] = value
            hw.set_attr(name, value)
            back = hw.get_attr(name).current
            if back != value:
                if back == old or not self.floor(name, a) <= back <= self.ceilings[name]:
                    raise OSError(f"{name}: power readback mismatch")
                self.written[name] = back  # firmware rounded: follow it

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
        try:
            if not self._reconcile(attrs, now):
                return
            e = temp - self.target
            if e >= 1:
                self.cool_since = None
                if self.hot_since is None:
                    self.hot_since = now
                self._move(attrs, -min(CUT_MAX_W, max(CUT_MIN_W, 2 * e)))
                at_floor = all(hw.get_attr(n).current <= self.floor(n, a) for n, a in attrs.items())
                if e < FAST_OVER or not at_floor:
                    self.over_since = None
                elif self.over_since is None:
                    self.over_since = now
                self.state = "limited" if at_floor else "active"
                if at_floor and self.stage < len(self.ladder):
                    if (self.over_since is not None and now - self.over_since >= FAST_GRACE
                            and self._boost_rung_ahead()):
                        self._escalate_to_boost(now, temp)
                    elif (now - self.hot_since >= ESCALATE_GRACE
                            and now - self.stage_changed_at >= RUNG_DWELL):
                        self._escalate(now, temp)
            elif e <= -COOL_BAND:
                self.hot_since = None
                self.over_since = None
                if self.cool_since is None:
                    self.cool_since = now
                self.state = "active"
                if self.stage > 0:
                    if (now - self.cool_since >= RELEASE_DWELL and now - self.stage_changed_at >= RUNG_DWELL
                            and now - self.escalated_at >= self.release_dwell):
                        self._release(now, temp)
                elif now - self.last_recover >= RECOVER_PERIOD:
                    self.last_recover = now
                    self._move(attrs, RECOVER_STEP_W)
            else:
                self.hot_since = None
                self.over_since = None
                self.cool_since = None
                self.state = "active"
        except (OSError, ValueError, AttributeError) as e:
            self.fail(str(e))

    def status(self) -> dict:
        control = self.active_backend or self._backend()
        return {"min": MIN_TEMP, "max": DEFAULT_TEMP, "control": control,
                "target": self.target, "applied": self.applied, "state": self.state,
                "error": self.error, "ceilings_w": dict(self.ceilings),
                "requested_w": dict(self.written), "hard_limit": False,
                "floor_w": self.effective_floor_w(),
                "stage": self.stage, "stage_max": len(self.ladder),
                "release_dwell_s": int(self.release_dwell),
                "ladder": [f"{k}:{v}" for k, v in self.ladder],
                "effective": dict(self.effective),
                "baseline": {"governor": self.baseline.governor, "boost_mode": self.baseline.boost_mode,
                             "epp": self.baseline.epp},
                "available": self.supported()}
