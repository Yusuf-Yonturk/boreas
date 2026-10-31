![Boreas: ASUS laptop control for Linux](docs/banner.jpg)

**Fan, CPU, GPU and battery control for ASUS ROG & TUF laptops on Linux.**

**English** · [Türkçe](README.tr.md)

Boreas is an open-source Armoury Crate alternative for Linux. It manages performance modes, custom fan
curves, CPU and GPU power limits, GPU modes (Eco, Optimized and the Ultimate MUX switch), Aura keyboard
lighting and the battery charge limit on ASUS ROG and TUF laptops.

It builds on the work of the [asus-linux.org](https://asus-linux.org) community: all the hardware support
comes from their kernel drivers (asus-wmi, asus-armoury). Boreas replaces the userspace side (asusd,
supergfxd, rog-control-center) with a single small system service (`boreasd`) and a tray app (`boreas`).

![Boreas: light theme, dark theme, fans and power page](docs/screenshots.png)

Developed and tested on a ROG Strix G713PV with CachyOS (kernel 7.3, asus-armoury); other models and
distributions may behave differently.

**Languages:** English, Türkçe, 简体中文, हिन्दी, Español, العربية, Français, Português (Brasil), Русский,
Deutsch, 日本語.

## Features

| Feature | Boreas | Linux interface |
|---|---|---|
| Silent / Balanced / Turbo | ✓ | `platform_profile` |
| Custom modes (Custom 1, 2…) | ✓ | bound to a built-in mode on the same base |
| Per-mode fan curves | ✓ (CPU, GPU, mid) | `asus_custom_fan_curve` hwmon |
| Power limits (SPL/SPPT/FPPT) | ✓ | asus-armoury `ppt_*` |
| NVIDIA Dynamic Boost / temp target | ✓ | asus-armoury `nv_*` |
| CPU temperature limit (75-95 °C, per mode) | ✓ (Dragon Range, Fire Range) | Ryzen SMU tctl; kernel lockdown must be off |
| Auto mode on battery / AC | ✓ | `power_supply` |
| GPU: Eco / Standard / Optimized / Ultimate | ✓ | `dgpu_disable`, `gpu_mux_mode` |
| Display 60 Hz / max / Auto | ✓ (KDE) | `kscreen-doctor` |
| Panel overdrive | ✓ | `panel_overdrive` |
| Keyboard Aura (static, breathe, cycle, rainbow, strobe) | ✓ | hidraw, report 0x5D (output report) |
| Keyboard brightness | ✓ | `asus::kbd_backlight` |
| Charge limit | ✓ | `charge_control_end_threshold` |
| Fn+F5 / KDE power profile | ✓ | the service watches profile changes and re-applies mode settings |
| Re-apply after sleep | ✓ | systemd-sleep hook |

Not yet: AniMe/Slash matrix, M4/ROG key binding (bind `boreasctl mode next` to a KDE shortcut),
GPU clock offsets, Ryzen undervolt (Curve Optimizer), peripherals (mouse, headset), fan hysteresis,
keyboard light sleep/shutdown settings (Aura power), separate keyboard brightness for AC/battery,
one-time 100 % charge, boot sound (`boot_sound`), USB-C charging mode, Aura second color.

## Install

Requirements: a systemd-based distribution, a kernel with the asus-wmi / asus-armoury drivers,
Python 3.11+, PySide6 (for the UI), `kscreen-doctor` for refresh-rate control on KDE, and Qt's Wayland
plugin for a sharp UI on Wayland. Install Python and PySide6 with your distribution's package manager
(on Arch / CachyOS: `sudo pacman -S --needed python pyside6 qt6-wayland`).

```bash
git clone https://github.com/Yusuf-Yonturk/boreas.git
cd boreas
sudo ./install.sh
```

The installer asks for the interface language (Enter = English). To skip the question:
`sudo BOREAS_LANG=de ./install.sh`. Each user can change the language later with the globe button in
the window. Chinese and Japanese need `noto-fonts-cjk`; Hindi and Arabic need `noto-fonts`.

The script masks `asusd`, `asus-shutdown` and `supergfxd` (they write the same sysfs files;
`disable` is not enough because asusd is started by a udev rule on every boot) and enables
`boreasd`. On first start the service adopts the current charge limit, keyboard brightness,
mode and GPU mode; it writes nothing to the hardware.

Uninstall: `sudo ./uninstall.sh` (removes the masks and brings asusd / supergfxd back).

If KDE → Settings → Power Management uses different power profiles for AC and battery, it conflicts
with Boreas's "auto mode on battery / AC"; turn one of them off.

## Usage

```bash
boreas                 # UI (tray)
boreasctl status
boreasctl mode turbo   # silent | balanced | turbo | next | prev
boreasctl gpu eco      # eco | standard | optimized | ultimate
boreasctl charge 80
boreasctl cputemp 85   # active mode; default = firmware default
```

The UI follows the system light/dark theme; the button in the header switches between auto, light
and dark. `BOREAS_THEME=light boreas` (or `dark`) forces a theme, `BOREAS_SCALE=1` changes the UI
size (default `0.78`), `BOREAS_LANG=ja boreas` forces a language. The **Fans and power** button in the top right opens fan curves, power limits
and battery settings in the same window; press it again to go back.

## Updating

Boreas checks GitHub 10 seconds after it starts and then once a day; each check only downloads the
small file that holds the version number. When a newer version is available, the window shows
**Update · Release notes · Hide**. **Update** downloads the latest version from GitHub, installs it
after asking for your administrator password (pkexec) and restarts the app. **Release notes** opens
[CHANGELOG.md](CHANGELOG.md).

To update by hand instead:

```bash
cd boreas
git pull
sudo ./install.sh
```

Then press **Quit** in the window and start `boreas` again. The check can be turned off from the tray
menu (**Check for updates**) or with `BOREAS_NO_UPDATE_CHECK=1`.

## CPU temperature limit

Only the limit of the active mode is maintained. On Dragon Range the MP1 0x23 query is tried
([ZenStates-Core sources](https://github.com/irusanov/ZenStates-Core/blob/869b7e82572337e5a673e92230edc92bddf0e44b/Hardware/Smu/Commands/GetSystemConfiguredPowerLimit.cs)).
The readback is trusted only after it has been seen to follow a write from the old value to the new
target, because some firmware reports the fixed BIOS limit. Once verified, the service queries
about once a second and rewrites only if the limit changed.

If the readback cannot be verified or fails, the limit is re-sent based on temperature: every 3 s
when less than 2 °C above target, 2 s at 2-4 °C, 1 s at 5 °C or more; 3 s if the sensor can't be
read. This is not a guarantee that the temperature is pinned. Choosing "default" stops the writes
and restores 96 °C.

`cpu_temp` fields in `boreasctl status`:
- `applied`: last write accepted by the SMU (not a value read back from hardware).
- `reported_limit`: value reported by the last query; `null` without a valid reading.
- `readback_verified`: has the value been seen following a limit change?
- `control`: `readback` (verified) or `adaptive` (3/2/1 s refresh).
- `readback_error`: query error. Without query support, temperature writes still work.
- `conflict`: pids of ryzenadj processes writing to the SMU at the same time (scanned every ~30 s).

`temp_peak` in `status` gives the highest CPU temperature since the service started (with time and
mode). When the CPU goes above 95 °C, or 5 °C above a lowered limit, start and end lines are
logged: `journalctl -u boreasd | grep "high temperature"`.

Writing a profile on mode change resets the firmware temperature limit, so the limit is sent right
after the profile (before fan/power writes) and once more at the end. On the G713PV the firmware
resets it again ~3-4 s later, so for 10 s after a mode change the limit is rewritten every 0.25 s.

**Do not use together with ryzenadj.** Both use the same SMU mailbox without an inter-process
lock: they overwrite each other's limit and interleaved writes can send a wrong command to the SMU.
Disable any service running a `ryzenadj --tctl-temp` loop; Boreas shows a warning when it sees one.

The readback method has not yet been verified live on the 7845HX; it is not enabled for Fire Range.

## GPU modes

- **Ultimate** ↔ others switches the MUX and requires a reboot.
- **Eco** can freeze the system if `dgpu_disable` is written while the dGPU driver is bound. The
  service first unloads the NVIDIA modules and removes the PCI device. If the dGPU is in use
  (KWin, games, CUDA), Eco is postponed: the service retries every 15 s and applies it before the
  display manager on boot. If the NVIDIA module is never released, it stops retrying and Eco is
  applied on the next reboot.

## Development

```bash
python3 -m unittest discover -s tests   # fake sysfs tree, no root needed
python3 -m boreas.gui.app               # UI (the service must be running)
```

Settings: `/etc/boreas/config.json`. Socket: `/run/boreas.sock` (`wheel` group).

To publish a new version, raise `__version__` in `boreas/__init__.py`, add a section to
[CHANGELOG.md](CHANGELOG.md) and push to `main`; installed copies show the update note within a day.

## Contributing

Feel free to contribute! Bug reports, translations and pull requests are all welcome, especially if
you own an ASUS laptop other than the ROG Strix G713PV. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
workflow, tests and how to add a language.

## Acknowledgements

- Serge and the [G-Helper](https://github.com/seerge/g-helper) community, for their open-source work on
  ASUS hardware.
- The [asus-linux.org](https://asus-linux.org) community, for the Linux kernel drivers (asus-wmi,
  asus-armoury) and asusctl.
- [RyzenAdj](https://github.com/FlyGoat/RyzenAdj) and [ZenStates-Core](https://github.com/irusanov/ZenStates-Core),
  for their research on the AMD SMU.

## License

Copyright (C) 2026 Yusuf Yontürk. Licensed under [GPL-3.0](LICENSE).

ASUS, ROG, TUF and Armoury Crate are trademarks of ASUSTeK Computer Inc.; this project is not affiliated with ASUS.
