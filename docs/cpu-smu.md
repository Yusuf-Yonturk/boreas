# CPU temperature ceiling through the SMU

Boreas can hand a mode's CPU temperature target to the CPU itself instead of steering the ASUS
power limits in software (see [cpu-temperature.md](cpu-temperature.md)). The CPU then holds the
ceiling itself, so short boost bursts no longer run past it (measurements below). G-Helper does the same on
Windows. This page says how, on which CPUs, and what was checked.

## How it is switched on

With no saved choice, `install.sh` switches the ceiling on by default for every family in the table
below. The command and mailbox are the ones G-Helper, RyzenAdj, PawnIO and ryzen_smu send on these
families; G-Helper and PawnIO send them without the BIOS lock this module holds. Whether the module
loads is decided on the machine (CPU family, `\_SB.AM00`), and any SMU fault falls back to
power-feedback. The module needs `dkms` and your kernel's headers to build; without them the
installer warns and Boreas uses power-feedback.

```bash
sudo BOREAS_SMU=0 ./install.sh # switch it off persistently
sudo BOREAS_SMU=1 ./install.sh # switch it on again
```

`/etc/boreas/smu` means enabled; it holds `default` when the installer chose it and `user` after
`BOREAS_SMU=1`, so a later version can take its own default back without overriding a user.
`/etc/boreas/smu-off` records an explicit choice to keep it off.
`BOREAS_SMU=1` creates `smu` and removes `smu-off`; `BOREAS_SMU=0` removes `smu` and creates
`smu-off`. With `BOREAS_SMU` unset or empty, either existing file is left untouched, so later
installs and the in-app update keep the choice even though pkexec does not carry the environment.
An invalid value warns without changing either file. Uninstalling also keeps these choices.

A copy installed as a package (`packaging/aur/boreas`) takes the package
`packaging/aur/boreas-smu-dkms` instead. It carries the same module source, `/etc/boreas/smu` and
`/usr/lib/modules-load.d/boreas-smu.conf`; the `dkms` package's pacman hook builds the module.
Package builds do not select a default based on the build machine's CPU.

When `/etc/boreas/smu` exists, the installer builds the kernel module `boreas_smu` from `smu/`
with DKMS for every installed kernel that has its headers, and loads it at boot
(`/etc/modules-load.d/boreas-smu.conf`). `boreasd` then uses the module when all of these hold:

- `/etc/boreas/smu` exists;
- the CPU family and model are in the table below (`boreas/ryzen.py` and `smu/boreas_smu.c` hold
  the same table; a test keeps them equal);
- `/dev/boreas-smu` exists, that is, the module loaded.

Otherwise, and after any SMU error, the target keeps working through power-feedback. The service
log says which one is in use and why (`cpu target: SMU ceiling unavailable (...)`).

## Supported CPU families

Detection uses `cpu family` and `model` from `/proc/cpuinfo` (in the module: `boot_cpu_data`),
exact pairs only. The values were taken from four sources and cross-checked: G-Helper
(`app/Pawn/RyzenSmu.cs`), PawnIO.Modules (`RyzenSMU.p`), RyzenAdj (`lib/cpuid.c`, `lib/api.c`,
`lib/nb_smu_ops.c`) and ryzen_smu (`smu.c`). See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

| Code names | Family / models | Command | MP1 command / response / argument0 | Re-applied every 30 s |
|---|---|---|---|---|
| Raven Ridge, Picasso, Dali | 0x17 / 0x11, 0x18, 0x20 | `0x1F` | `0x03B10528` / `0x03B10564` / `0x03B10998` | no |
| Renoir, Lucienne, Cezanne, Barcelo | 0x17 / 0x60, 0x68; 0x19 / 0x50 | `0x19` | `0x03B10528` / `0x03B10564` / `0x03B10998` | yes |
| Van Gogh, Mendocino, Rembrandt, Phoenix, Hawk Point | 0x17 / 0x90, 0x91, 0xA0; 0x19 / 0x40, 0x44, 0x74, 0x75, 0x78, 0x7C | `0x19` | `0x03B10528` / `0x03B10578` / `0x03B10998` | yes |
| Dragon Range, Raphael | 0x19 / 0x61 | `0x3F` | `0x03B10530` / `0x03B1057C` / `0x03B109C4` | no |
| Fire Range, Granite Ridge | 0x1A / 0x44 | `0x3F` | `0x03B10530` / `0x03B1057C` / `0x03B109C4` | no |
| Strix Point, Krackan Point, Strix Halo | 0x1A / 0x20, 0x24, 0x60, 0x68, 0x70 | `0x19` | `0x03B10928` / `0x03B10978` / `0x03B10998` | no |

Left out:

- **Matisse and Vermeer:** their command (`0x3E`) appears only in G-Helper. In RyzenAdj's history
  `0x3E` was added as a power limit for another family.
- **Zen 1, server parts and Shimada Peak:** no source has a temperature command for them.
- **Intel:** has no SMU.

The 30-second re-apply follows a G-Helper code comment (those families "silently reset" the limit
under load); it has not been measured here. Only Dragon Range has been run on hardware with this
module so far (`verified` in `boreas/ryzen.py`); the other families rest on the sources above and on
G-Helper reports. The installer defaults every family in the table on; a report from another family
is welcome but not required.

G-Helper re-applies the ceiling 5 s after a mode change on Dragon Range and Fire Range
(`IsReapplyRyzenRequired`), and the same [app/Mode/ModeControl.cs](https://github.com/seerge/g-helper/blob/main/app/Mode/ModeControl.cs)
contains the comment and 30 s re-apply for Cezanne, Rembrandt, Phoenix and Hawk Point.

## Measured on a ROG Strix G713PV (Dragon Range, BIOS 336), 2026-11-21

`stress-ng --cpu 0` (24 threads) on AC, Tctl read every 0.5 s:

| Ceiling | Peak | Last 30 s average |
|---|---|---|
| none (96) | 95.4 °C | 94.2 °C |
| 85 °C | 87.0 °C | 86.2 °C |
| 78 °C | 79.2 °C | 79.1 °C |
| 82 °C, through `boreasd` | 83.9 °C | 82.8 °C |

**What a mode change does.** About 4 s after a mode is applied the firmware puts its own ceiling back for
roughly half a second: the cores went from 3.5 to 4.1 GHz and Tctl from 82 to 90 °C, then took 7 s to cool.
Writing the default ceiling between two modes added another uncapped moment. `boreasd` therefore hands the
ceiling from one mode to the next without the default in between, re-writes it every 0.1 s for 8 s after a
mode is applied and every second for 10 s. With that, under the same load and switching Dengeli, Turbo
(87 °C), Dengeli, Sessiz (80 °C) and back, the temperature stayed within 1 °C of the active ceiling (peaks
87.9, 82.4 and 83.0 °C). Before, each switch into Dengeli or Turbo reached 90-95 °C.

Charger and sleep, same day: unplugging switched to Sessiz with an 80 °C ceiling within 0.6 s (peak
82.1 °C under load) and plugging in back to Dengeli at 82 °C (peak 83.8 °C). After a 38 s s2idle sleep the
ceiling was written again 0.8 s after wake-up and held under load (peak 82.9 °C). At the moment of
wake-up, with the fans still stopped and the desktop waking up, Tctl reached 90 °C for 7 s before the
ceiling was back; the gap is the time between the kernel's resume and `boreasctl reapply`.

No SMU, ACPI or mailbox error appeared in the kernel log in any of these runs.

### In games

PEAK (Proton, Ultra settings, RTX 4060 in Standard mode) on AC, same G713PV, ceiling 88 °C, Tctl and the cores' clocks
read every 0.5 s (2026-11-22):

| Mode | Tctl (average) | Fastest core (average) | Fans cpu / gpu | Frame rate |
|---|---|---|---|---|
| Dengeli | 88.6-89.1 °C | 1.1-1.7 GHz | about 4000 / 4300 rpm | 46-70 fps |
| Turbo | 89.0-89.2 °C | 2.7-2.8 GHz | about 6000 / 5500 rpm | 119 fps |

The ceiling held in both modes. What it cost differed: in Dengeli the CPU dropped to its lowest clocks
to stay at 88 °C and the game became CPU-bound. In Dengeli none of these changed that: power limits of
55/65/65 W or 120/125/125 W, NVIDIA Dynamic Boost 25 W or 5 W, CPU Boost `Max`, the `performance`
power profile.

A second game separated the two things that differ in Turbo, the fan speed and the firmware's profile.
Dying Light (native), ceiling 88 °C, 6 minutes each. Fans, clocks and GPU load are averages of the last
3 minutes of the 2560x1440 logs (2026-11-24 and 2026-11-25). The frame rates were read at 2560x1440 on
Ultra and at 1920x1080 on High:

| Profile | Fan curve | CPU fan | Fastest core | All cores | GPU load | fps 2560x1440 Ultra | fps 1080p High |
|---|---|---|---|---|---|---|---|
| Dengeli | its own | 4000 rpm | 1.5 GHz | 1.0 GHz | 65-72 % | 50-70 | 55-75 |
| Turbo | Dengeli's | 4000 rpm | 1.45 GHz | 0.7 GHz | 57 % | 45-60 | 50-65 |
| Dengeli | in between | 5200 rpm | 1.55 GHz | 1.2 GHz | 92 % | 75-90 | 90-110 |
| Turbo | its own | 5950 rpm | 4.0 GHz | 3.2 GHz | 98 % | 85-100 | 130-160 |

Tctl was 88-89 °C in every row. The fan speed decides, not the profile: Turbo with Dengeli's fan curve
was as slow as Dengeli. Dengeli and Turbo were measured twice, in both orders, and agreed within 5 %;
the other two rows are one run each. Lowering the resolution barely helps where the CPU is the limit
(the first two rows) and helps most where the GPU was (the last row).

A third game added the frame rate to every row, a higher ceiling, and fan curves in between. PAYDAY 3
(Proton, 1920x1080, High), 5 minutes each, averages of the last 3 minutes, frame rate logged by
MangoHud (2026-11-25):

| Profile | Fan curve | Ceiling | CPU fan | Fastest core | GPU load | Frame rate |
|---|---|---|---|---|---|---|
| Dengeli | its own (top 39 %) | 88 °C | 4000 rpm | 1.5-1.9 GHz | 43-51 % | 53-65 fps |
| Turbo | Dengeli's | 88 °C | 4000 rpm | 1.6-1.75 GHz | 45-47 % | 56-59 fps |
| Dengeli | its own | 92 °C | 4000 rpm | 1.8 GHz | 54 % | 71 fps |
| Dengeli | its own | 95 °C | 4000 rpm | 2.05 GHz | 56 % | 74 fps |
| Dengeli | top 50 % | 88 °C | 4600 rpm | 1.95 GHz | 56 % | 72-75 fps |
| Dengeli | top 60 % | 88 °C | 5200 rpm | 2.25-3.1 GHz | 68-75 % | 94-102 fps |
| Dengeli | top 70 % | 88 °C | 5750 rpm | 2.45-3.15 GHz | 77-79 % | 110-111 fps |
| Turbo | its own | 88 °C | 5950 rpm | 3.15-3.45 GHz | 82-88 % | 105-127 fps |

Four sessions: Dengeli was measured seven times, Turbo and the 60 % curve four times each, the 50 % and
70 % curves and Turbo with Dengeli's curve twice each (the fan steps once going up and once going
down), the two higher ceilings once. Tctl stayed within 1 °C of the ceiling in every row. Again the fan
speed decides, not the profile, and the frame rate rises step by step with it: the 70 % curve is level
with Turbo. A higher ceiling with the quiet fans gave less: 74 fps at 95 °C, with Tccd1 at 99.7 °C on
average. So in a game, give a mode with a ceiling a faster fan curve before a higher ceiling, or play
in Turbo.

A fourth game repeated the fan steps. Counter-Strike 2 (native, Vulkan, 2560x1440, Medium settings), ceiling 88 °C, 5 minutes
each, averages of the last 3 minutes, frame rate logged by MangoHud (2026-11-26):

| Profile | Fan curve | CPU fan | Fastest core | All cores | GPU load | Frame rate (average) | Slowest / fastest 10 % |
|---|---|---|---|---|---|---|---|
| Dengeli | top 50 % | 4600 rpm | 1.35 GHz | 1.2 GHz | 53 % | 106 fps | 79 / 137 fps |
| Dengeli | top 60 % | 5150 rpm | 1.65 GHz | 1.45 GHz | 62 % | 135 fps | 103 / 174 fps |
| Dengeli | top 70 % | 5750 rpm | 1.9 GHz | 1.7 GHz | 72 % | 153 fps | 115 / 200 fps |
| Turbo | its own | 5950 rpm | 2.0 GHz | 1.8 GHz | 80 % | 161 fps | 111 / 212 fps |

One run each. Tctl was 88.4-89.5 °C in every row. The GPU was never the limit, so the frame rate
followed the CPU clock, and the CPU clock followed the fan: the 70 % curve reached 95 % of Turbo. The
run with Dengeli's own fan curve was cut short when the game closed, so it is not in the table.

This is not specific to Linux or to Boreas. PEAK on the same laptop under Windows, with
G-Helper in Balanced and its CPU temperature limit at 88 °C (2026-11-24), ran at 30-60 fps, and the
busy cores settled at about 1.2 GHz once the temperature had risen (Tctl 89.9 °C on average).

**Which temperature the ceiling follows.** The CPU applies the ceiling to its own internal reading, which
is not always the `Tctl` that Boreas shows. Under full load and in the game, Tctl stayed about 1 °C above
the ceiling. Under a light desktop load with a few cores boosting, Tctl sat about 2 °C above it while
`Tccd1` averaged within 0.8 °C of it (ceilings 80, 82 and 85 °C, 30 s each). A Tctl a little above the
ceiling is therefore not a fault.

## Help test it

Whether a laptop can use the ceiling at all is a 10-second check that installs nothing (it reads the
CPU family and looks for `\_SB.AM00` in the BIOS tables):

```bash
curl -LO https://raw.githubusercontent.com/Yusuf-Yonturk/boreas/main/tools/smu-check.sh
sudo sh smu-check.sh
```

Only the ROG Strix G713PV (Dragon Range) has been measured. A report from any other Ryzen laptop in
the table above helps, also one where the module does not load. On AC power (Arch and CachyOS; on
another distribution install `dkms`, `stress-ng` and your kernel's headers with its package manager):

```bash
sudo pacman -S --needed dkms stress-ng linux-headers   # the headers of your kernel, e.g. linux-lts-headers
git clone https://github.com/Yusuf-Yonturk/boreas.git && cd boreas
sudo BOREAS_SMU=1 ./install.sh   # also switches it back on if it was switched off before
tools/smu-report.sh        # about 2 minutes, every thread under load for 90 s
```

The script changes no setting. It writes `boreas-smu-report.md`; paste it into a new issue with the
"CPU temperature ceiling (SMU) test" form. To switch the ceiling off again: `sudo BOREAS_SMU=0 ./install.sh`.
To remove Boreas: `sudo ./uninstall.sh`.

## The BIOS shares the mailbox

The BIOS tables of a ROG Strix G713PV (BIOS G713PV.336) were read on 2026-11-21 (DSDT and
SSDT1-23, AML bytes searched directly):

- **SSDT1** (OEM table id `Vortex`, AMD's CPM code) holds exactly the MP1 addresses of this family:
  - command `0x03B10530` (field `A146`);
  - response `0x03B1057C` (`A139`);
  - arguments `0x03B109C4`-`0x03B109D8` (`A140`-`A145`).

  It reaches them with `BankField`s over the ECAM page of `00:00.0` (`AGRB` = `0xF0000000`), index
  `0xA0`, data `0xA4`.
- Its mailbox method `A018` is called from more than 30 places in SSDT1. It works in this order:
  1. `Acquire(\_SB.AM00, 0xFFFF)`;
  2. set the response to 0;
  3. write the six arguments and the command;
  4. wait for a reply **without a time limit**;
  5. `Release(\_SB.AM00)`.
- One method, `A032`, reads the result registers (`A140`, `A141`) after `A018` has released the
  lock. A message of ours in that gap could hand it our argument instead of its result. The window
  is very short; the effect would be a wrong value in that one BIOS query.
- Two root complex windows appear in the tables: `0xA0/0xA4` (SSDT1) and `0xB8/0xBC`
  (DSDT `NAPC`). The `0x60/0x64` window the kernel uses for `amd_smn_read/write` does not appear in
  any table. SMM cannot be inspected.

Hence the module's two rules:

1. It reaches the SMU only through `amd_smn_read/write`, the kernel's own locked SMN path. It never
   touches the `0xB8/0xBC` or `0xA0/0xA4` windows itself.
2. It holds the BIOS's `\_SB.AM00` (`acpi_acquire_mutex`, 500 ms) for every message, so the BIOS and
   Boreas never write the mailbox at the same time. A BIOS without that mutex does not get the
   module at all.

### Which ASUS BIOSes carry the lock

The [linuxhw/ACPI decoded tables](https://github.com/linuxhw/ACPI), surveyed on 2026-11-22,
show `Mutex (AM00)` on these models. CPU families below are inferred from the model names;
the BIOS versions of these dumps are unknown. Finding the lock is not a hardware ceiling test.

| Models | Inferred CPU family | SSDT / MP1 addresses |
|---|---|---|
| ROG Strix G713PV; TUF A16 FA607PV | Dragon Range | `Vortex`; `0x03B10530` |
| TUF A15 FA507XV | Phoenix | `AmdTable`; `0x03B10528` / `0x03B10578` |
| Vivobook S16 M5606UA | Hawk Point / Phoenix | `AmdTable`; `0x03B10528` / `0x03B10578` |
| TUF A15 FA506NF; TUF A16 FA617NSR | Rembrandt-R | `AmdTable`; `0x03B10528` / `0x03B10578` |
| Zenbook S16 UM5606WA | Strix Point | MP1 `0x03B10928` / `0x03B10978` |

`AM00` is absent on ROG Strix G513RM (Rembrandt), ROG Strix G513QY and TUF A17 FA706QR
(Cezanne), TUF A17 FA706IHRB (Renoir), TUF FX505DU and Zephyrus GA502DU (Picasso), and
Zenbook Flip Q508UG (Lucienne). The module does not load on those BIOSes, so power-feedback
remains in use. These older BIOSes access the same MP1 addresses under differently named mutexes
such as `NAPM` and `TMUT`.

The BIOS method using `AM00` is `A018` on Dragon Range and has other names on other families
(for example `A127`). The module checks only the mutex name; the order of operations in those
other families' methods has not been decoded.

## History: the raw userspace transport (removed)

Versions up to 0.1.6 reached the SMU from userspace through the root complex PCI config window
`0xB8/0xBC`. On the G713PV, passive tracing found AML method `\M249` and field `\M249.VAR1` writing the
same SMN selector through SystemMemory at physical `0xF00000B8` under `\_SB.AM00`, and a boot trace
caught an ACPI selector write between Boreas's selection and its check. A Python lock cannot take part
in that mutex, so the transport could not be made safe and was removed. The later opt-in research
bridge (`/dev/boreas-smu-probe`, `BOREAS_SMU_BRIDGE=1`, `enable_hw`) is replaced by `smu/`.

An acknowledged SMU command is not proof of the physical ceiling; the ceiling is measured. A failed
transport does not reset a ceiling the SMU acknowledged earlier. It stays until it is overwritten or
the machine restarts, and it is only ever lower than the default.
