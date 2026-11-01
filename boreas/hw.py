"""Kernel arayüzleri (sysfs) üzerinden ASUS donanım erişimi.

ASUS ACPI (ATKACPI) DEVS/DSTS çağrılarını Linux'ta asus-wmi / asus-armoury çekirdek
sürücüleri sysfs üzerinden sunar.
BOREAS_SYSFS_ROOT ortam değişkeni testlerde sahte bir kök dizin vermek içindir.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

ROOT = os.environ.get("BOREAS_SYSFS_ROOT", "")

# mod tabanları = ACPI throttle_thermal_policy değerleri
BALANCED, TURBO, SILENT = 0, 1, 2
PROFILE_BY_BASE = {BALANCED: "balanced", TURBO: "performance", SILENT: "quiet"}
BASE_BY_PROFILE = {v: k for k, v in PROFILE_BY_BASE.items()}
# power-profiles-daemon "low-power" yazarsa sessiz kabul et
BASE_BY_PROFILE["low-power"] = SILENT

ARMOURY = "/sys/class/firmware-attributes/asus-armoury/attributes"
NB_WMI = "/sys/devices/platform/asus-nb-wmi"
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
KBD_LED = "/sys/class/leds/asus::kbd_backlight"
DMI_PRODUCT = "/sys/class/dmi/id/product_name"

# asus-nb-wmi altındaki eski adlar (armoury yoksa yedek)
NB_WMI_NAMES = {"panel_overdrive": "panel_od", "ppt_pl3_fppt": "ppt_fppt"}

PPT_ATTRS = ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt", "ppt_apu_sppt", "ppt_platform_sppt")
NV_ATTRS = ("nv_dynamic_boost", "nv_temp_target")

# güç limiti ancak özel fan eğrisiyle tutan modeller
FAN_REQUIRED_MODELS = ("GA402X", "GU604", "G513", "G713R", "G713P", "GU605", "GA605", "G634J",
                       "G834J", "G614J", "G814J", "FX507V", "FX507Z", "FX608", "FA608P", "G614F",
                       "G614R", "G733", "H7606")


def p(path: str) -> str:
    return ROOT + path


def read(path: str) -> str | None:
    try:
        with open(p(path)) as f:
            return f.read().strip()
    except OSError:
        return None


def read_int(path: str) -> int | None:
    v = read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def write(path: str, value) -> None:
    # Tek write() çağrısı: open() + with, hata olunca kapanışta yazmayı ikinci kez dener;
    # yavaş firmware çağrılarında (dgpu_disable) bu bekleme süresini ikiye katlıyordu.
    fd = os.open(p(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, f"{value}\n".encode())
    finally:
        os.close(fd)


def exists(path: str) -> bool:
    return os.path.exists(p(path))


@dataclass
class Attr:
    name: str
    current: int | None
    min: int | None = None
    max: int | None = None
    choices: list[int] | None = None
    default: int | None = None

    def to_dict(self) -> dict:
        return {"current": self.current, "min": self.min, "max": self.max,
                "default": self.default, "choices": self.choices}


def _armoury_dir(name: str) -> str:
    return f"{ARMOURY}/{name}"


def attr_path(name: str) -> str | None:
    if exists(_armoury_dir(name) + "/current_value"):
        return _armoury_dir(name) + "/current_value"
    legacy = f"{NB_WMI}/{NB_WMI_NAMES.get(name, name)}"
    if exists(legacy):
        return legacy
    return None


def get_attr(name: str) -> Attr | None:
    path = attr_path(name)
    if path is None:
        return None
    a = Attr(name, read_int(path))
    d = _armoury_dir(name)
    if exists(d):
        a.min = read_int(d + "/min_value")
        a.max = read_int(d + "/max_value")
        a.default = read_int(d + "/default_value")
        poss = read(d + "/possible_values")
        if poss:
            try:
                a.choices = [int(x) for x in poss.split(";") if x != ""]
            except ValueError:
                pass
    return a


def set_attr(name: str, value: int) -> None:
    a = get_attr(name)
    if a is None:
        raise ValueError(f"{name} is not available on this device")
    if a.min is not None and value < a.min:
        value = a.min
    if a.max is not None and value > a.max:
        value = a.max
    if a.choices and value not in a.choices:
        raise ValueError(f"{name}: invalid value {value}, choices {a.choices}")
    write(attr_path(name), int(value))


def model() -> str:
    """Model adı (ör. "ROG Strix G713PV_G713PV")."""
    return read(DMI_PRODUCT) or ""


def fan_required() -> bool:
    m = model().upper()
    return any(x in m for x in FAN_REQUIRED_MODELS)


# ---- performans profili -------------------------------------------------------

def get_profile() -> str | None:
    return read(PLATFORM_PROFILE)


def get_profile_choices() -> list[str]:
    v = read(PLATFORM_PROFILE + "_choices")
    return v.split() if v else []


def set_profile(name: str) -> None:
    write(PLATFORM_PROFILE, name)


# ---- hwmon --------------------------------------------------------------------

def hwmon_by_name(name: str) -> str | None:
    for d in sorted(glob.glob(p("/sys/class/hwmon/hwmon*"))):
        try:
            with open(d + "/name") as f:
                if f.read().strip() == name:
                    return d[len(ROOT):]
        except OSError:
            continue
    return None


def fan_rpms() -> dict[str, int]:
    d = hwmon_by_name("asus")
    out: dict[str, int] = {}
    if not d:
        return out
    for i in (1, 2, 3):
        rpm = read_int(f"{d}/fan{i}_input")
        if rpm is None:
            continue
        label = read(f"{d}/fan{i}_label") or f"fan{i}"
        out[label.replace("_fan", "")] = rpm
    return out


def temps() -> dict[str, int]:
    out: dict[str, int] = {}
    for name, key in (("k10temp", "cpu"), ("coretemp", "cpu"), ("amdgpu", "igpu")):
        d = hwmon_by_name(name)
        if d and key not in out:
            v = read_int(f"{d}/temp1_input")
            if v is not None:
                out[key] = v // 1000
    return out


# ---- pil ----------------------------------------------------------------------

def battery_dir() -> str | None:
    for d in sorted(glob.glob(p("/sys/class/power_supply/BAT*"))):
        return d[len(ROOT):]
    return None


def ac_online() -> bool:
    for d in glob.glob(p("/sys/class/power_supply/*")):
        try:
            with open(d + "/type") as f:
                if f.read().strip() != "Mains":
                    continue
            with open(d + "/online") as f:
                if f.read().strip() == "1":
                    return True
        except OSError:
            continue
    return False


def battery_info() -> dict:
    d = battery_dir()
    if not d:
        return {}
    power = read_int(d + "/power_now")
    return {
        "capacity": read_int(d + "/capacity"),
        "status": read(d + "/status"),
        "limit": read_int(d + "/charge_control_end_threshold"),
        "power_w": round(power / 1e6, 1) if power else None,
    }


def set_charge_limit(value: int) -> None:
    d = battery_dir()
    if not d:
        raise ValueError("Battery not found")
    write(d + "/charge_control_end_threshold", max(40, min(100, int(value))))


# ---- klavye ışığı -------------------------------------------------------------

def kbd_brightness() -> tuple[int | None, int | None]:
    return read_int(KBD_LED + "/brightness"), read_int(KBD_LED + "/max_brightness")


def set_kbd_brightness(value: int) -> None:
    _, mx = kbd_brightness()
    write(KBD_LED + "/brightness", max(0, min(mx or 3, int(value))))
