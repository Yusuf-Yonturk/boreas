# CPU temperature ceiling through the SMU (experimental)

Boreas can hand a mode's CPU temperature target to the CPU itself instead of steering the ASUS
power limits in software (see [cpu-temperature.md](cpu-temperature.md)). The CPU then holds the
ceiling within milliseconds, so short boost bursts no longer run past it. G-Helper does the same on
Windows. This page says how, on which CPUs, and what was checked.

## How it is switched on

```bash
sudo BOREAS_SMU=1 ./install.sh   # BOREAS_SMU=0 switches it off again
```

A copy installed as a package (`packaging/aur/boreas`) takes the package
`packaging/aur/boreas-smu-dkms` instead. It carries the same module source, `/etc/boreas/smu` and
`/usr/lib/modules-load.d/boreas-smu.conf`; the `dkms` package's pacman hook builds the module.

The installer writes `/etc/boreas/smu` (the choice, kept by later installs and the in-app update),
builds the kernel module `boreas_smu` from `smu/` with DKMS for every installed kernel that has its
headers, and loads it at boot (`/etc/modules-load.d/boreas-smu.conf`). `boreasd` then uses the
module when all of these hold:

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
under load); it has not been measured here. Only Dragon Range has been run on hardware so far; every
other family is experimental until someone reports it.

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

## Help test it

Only the ROG Strix G713PV (Dragon Range) has been measured. A report from any other Ryzen laptop in
the table above helps, also one where the module does not load. On AC power (Arch and CachyOS; on
another distribution install `dkms`, `stress-ng` and your kernel's headers with its package manager):

```bash
sudo pacman -S --needed dkms stress-ng linux-headers   # the headers of your kernel, e.g. linux-lts-headers
git clone https://github.com/Yusuf-Yonturk/boreas.git && cd boreas
sudo BOREAS_SMU=1 ./install.sh
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
