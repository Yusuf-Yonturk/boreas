"""Built-in display refresh rate (KDE Wayland, kscreen-doctor). Runs in the user session."""

from __future__ import annotations

import json
import shutil
import subprocess


def _outputs() -> list[dict]:
    if not shutil.which("kscreen-doctor"):
        return []
    try:
        r = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout).get("outputs", [])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def internal() -> dict | None:
    """{'name', 'current': Hz, 'rates': {Hz: mode_id}}: the rates at the current resolution."""
    for o in _outputs():
        if not (o.get("enabled") and o["name"].startswith(("eDP", "LVDS"))):
            continue
        modes = {m["id"]: m for m in o.get("modes", [])}
        cur = modes.get(o.get("currentModeId"))
        if not cur:
            continue
        rates = {}
        for m in modes.values():
            if m["size"] == cur["size"]:
                hz = round(m["refreshRate"])
                rates.setdefault(hz, m["id"])
        return {"name": o["name"], "current": round(cur["refreshRate"]), "rates": rates}
    return None


def set_rate(hz: int) -> bool:
    info = internal()
    if not info or not info["rates"]:
        return False
    best = min(info["rates"], key=lambda r: abs(r - hz))
    if best == info["current"]:
        return True
    r = subprocess.run(["kscreen-doctor", f"output.{info['name']}.mode.{info['rates'][best]}"],
                       capture_output=True, timeout=10)
    return r.returncode == 0
