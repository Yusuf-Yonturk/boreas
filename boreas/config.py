"""Persistent settings (/etc/boreas/config.json)."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile

from . import aura, hw

PATH = os.environ.get("BOREAS_CONFIG", "/etc/boreas/config.json")
MAX_MODES = 20

BUILTIN = {
    hw.SILENT: "Silent",
    hw.BALANCED: "Balanced",
    hw.TURBO: "Turbo",
}
MODE_ORDER = [hw.SILENT, hw.BALANCED, hw.TURBO]


def _mode_entry(base: int, name: str) -> dict:
    return {
        "base": base,
        "name": name,
        "fans_apply": False,
        "fan_cpu": None,
        "fan_gpu": None,
        "fan_mid": None,
        "power_apply": False,
        "ppt": {},
        "nv": {},
        "cpu_temp": None,  # °C software target; None = no feedback power control
        "cpu_boost": "default",  # default | disabled | efficient | balanced | aggressive | max
        "cpu_governor": "default",  # default | powersave | performance (cpufreq governor)
        "cpu_temp_floor_w": None,  # W; the target never requests ppt_pl1_spl below this (None = firmware minimum)
    }


def _int(v) -> bool:
    return type(v) is int  # bool is not a number here


def _curve(v) -> list | None:
    """A stored fan curve: 16 numbers, anything else counts as not saved."""
    return v if isinstance(v, list) and len(v) == 16 and all(_int(x) for x in v) else None


# the only names a mode may carry: each one becomes a sysfs file the service writes as root
POWER_NAMES = {"ppt": hw.PPT_ATTRS, "nv": hw.NV_ATTRS}


def check_mode_fields(data) -> None:
    """Type checks for mode fields arriving over the socket; ValueError names the field.

    What is stored is applied again at every boot, so a wrong type must be refused here."""
    if not isinstance(data, dict):
        raise ValueError("Mode data must be an object")
    for key, value in data.items():
        if key == "name":
            ok = isinstance(value, str)
        elif key in ("fans_apply", "power_apply"):
            ok = isinstance(value, bool)
        elif key in ("ppt", "nv"):
            ok = isinstance(value, dict) and all(k in POWER_NAMES[key] and (v is None or _int(v))
                                                 for k, v in value.items())
        elif key.startswith("fan_"):
            ok = value is None or isinstance(value, list)
        else:
            ok = True  # the CPU fields have their own range checks in the service
        if not ok:
            raise ValueError(f"Invalid value for {key}")


def sanitize_mode(entry: dict) -> dict:
    """Coerce a stored mode entry so a hand-edited or stale value cannot stop the service."""
    if not _int(entry.get("base")) or entry["base"] not in hw.PROFILE_BY_BASE:
        entry["base"] = hw.BALANCED
    if not isinstance(entry.get("name"), str):
        entry["name"] = BUILTIN[entry["base"]]
    t = entry.get("cpu_temp")
    entry["cpu_temp"] = t if type(t) is int and hw.TEMP_MIN <= t < hw.TEMP_OFF else None
    if entry.get("cpu_boost") not in hw.CPU_BOOST_MODES:
        entry["cpu_boost"] = "default"
    if entry.get("cpu_governor") not in hw.CPU_GOVERNORS:
        entry["cpu_governor"] = "default"
    f = entry.get("cpu_temp_floor_w")
    entry["cpu_temp_floor_w"] = f if type(f) is int and f > 0 else None
    for key in ("ppt", "nv"):
        values = entry.get(key) if isinstance(entry.get(key), dict) else {}
        entry[key] = {k: v for k, v in values.items() if k in POWER_NAMES[key] and (v is None or _int(v))}
    for key in ("fan_cpu", "fan_gpu", "fan_mid"):
        entry[key] = _curve(entry.get(key))
    for key in ("fans_apply", "power_apply"):
        entry[key] = bool(entry.get(key))
    return entry


def _color(v) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(_int(x) and 0 <= x <= 255 for x in v)


def _zones(v, needed: bool) -> bool:
    if v is None:
        return not needed
    return isinstance(v, list) and len(v) == aura.ZONE_COUNT and all(_color(c) for c in v)


def sanitize_settings(data: dict) -> None:
    """The settings outside the modes: a value of the wrong shape falls back to its default."""
    for key, value in DEFAULT.items():
        data.setdefault(key, value)
    for key in ("mode", "mode_ac", "mode_battery"):
        if not _int(data[key]) or str(data[key]) not in data["modes"]:
            data[key] = DEFAULT[key]
    if data["gpu_target"] not in ("eco", "standard", "optimized", "ultimate"):
        data["gpu_target"] = None
    if data["gpu_backend"] not in ("firmware", "ebpf"):
        data["gpu_backend"] = "firmware"
    for key in ("charge_limit", "kbd_brightness", "overdrive"):
        if not _int(data[key]):
            data[key] = None
    sanitize_aura(data)


def sanitize_aura(data: dict) -> None:
    a = data["aura"]
    # isinstance first: a list or object is not hashable and cannot be looked up in the tables
    if not (isinstance(a, dict) and isinstance(a.get("mode"), str) and a["mode"] in aura.ALL_MODES
            and _color(a.get("color")) and (a.get("color2") is None or _color(a["color2"]))
            and isinstance(a.get("speed", "normal"), str) and a.get("speed", "normal") in aura.SPEEDS
            and _zones(a.get("zones"), needed=a["mode"] == aura.ZONES)):
        data["aura"] = None


DEFAULT = {
    "mode": hw.BALANCED,
    "mode_ac": hw.BALANCED,
    "mode_battery": hw.SILENT,
    "modes": {str(b): _mode_entry(b, n) for b, n in BUILTIN.items()},
    "gpu_target": None,
    "gpu_backend": "firmware",
    "charge_limit": None,
    "kbd_brightness": None,
    "aura": None,
    "overdrive": None,
}


class Config:
    def __init__(self, path: str = PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULT)
        self.load_error = ""  # why an existing file was not used; the service reports it
        loaded = None
        try:
            with open(path) as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("the top level is not an object")
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            loaded = None
            self.load_error = str(e)
            self._keep_unreadable()
        # An unreadable file starts over like a first run: the service then adopts what the
        # hardware currently holds instead of resetting it.
        self.fresh = loaded is None
        if loaded is None:
            return
        self.data.update({k: v for k, v in loaded.items() if k != "modes"})
        modes = loaded.get("modes")
        for k, v in (modes.items() if isinstance(modes, dict) else ()):
            if not (len(k) <= 2 and k.isascii() and k.isdigit() and int(k) < MAX_MODES
                    and str(int(k)) == k and isinstance(v, dict)):
                continue
            entry = _mode_entry(v.get("base", hw.BALANCED), v.get("name", k))
            entry.update(v)
            if int(k) in BUILTIN:
                entry["base"] = int(k)
            self.data["modes"][k] = sanitize_mode(entry)
        sanitize_settings(self.data)

    def _keep_unreadable(self) -> None:
        """The next save replaces the file; keep what was there for the user to recover."""
        kept = self.path + ".corrupt"
        n = 1
        while os.path.lexists(kept):  # an earlier copy is never replaced
            n += 1
            kept = f"{self.path}.corrupt.{n}"
        try:
            os.replace(self.path, kept)
        except OSError:
            pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # a power cut right after the rename must not leave an empty file
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
            # the rename itself has to reach the disk too, or a power cut brings the old file back
            with contextlib.suppress(OSError):
                dfd = os.open(os.path.dirname(self.path), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def mode(self, mode_id: int) -> dict:
        return self.data["modes"].get(str(mode_id)) or self.data["modes"][str(hw.BALANCED)]

    def has_mode(self, mode_id: int) -> bool:
        return str(mode_id) in self.data["modes"]

    def mode_list(self) -> list[int]:
        custom = sorted(int(k) for k in self.data["modes"] if int(k) not in BUILTIN)
        return MODE_ORDER + custom

    def next_mode(self, current: int, back: bool = False) -> int:
        modes = self.mode_list()
        i = modes.index(current) if current in modes else 0
        return modes[(i + (-1 if back else 1)) % len(modes)]

    def add_mode(self, source: int) -> int:
        for i in range(3, MAX_MODES):
            if str(i) not in self.data["modes"]:
                entry = copy.deepcopy(self.mode(source))
                entry["name"] = f"Custom {i - 2}"
                self.data["modes"][str(i)] = entry
                return i
        raise ValueError("At most 17 custom modes")

    def remove_mode(self, mode_id: int) -> None:
        if mode_id in BUILTIN:
            raise ValueError("A built-in mode cannot be deleted")
        self.data["modes"].pop(str(mode_id), None)
        for key in ("mode", "mode_ac", "mode_battery"):
            if self.data[key] == mode_id:
                self.data[key] = hw.BALANCED
