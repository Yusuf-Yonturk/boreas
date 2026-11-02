"""Persistent settings (/etc/boreas/config.json)."""

from __future__ import annotations

import copy
import json
import os
import tempfile

from . import hw

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
    }


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
        self.fresh = not os.path.exists(path)
        try:
            with open(path) as f:
                loaded = json.load(f)
            self.data.update({k: v for k, v in loaded.items() if k != "modes"})
            for k, v in loaded.get("modes", {}).items():
                entry = _mode_entry(v.get("base", hw.BALANCED), v.get("name", k))
                entry.update(v)
                self.data["modes"][k] = entry
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path))
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)

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
