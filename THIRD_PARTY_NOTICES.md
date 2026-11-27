# Third-party source notices

Boreas's code is its own. The only third-party source in this repository is the vendored Cardwire crates
of the optional eBPF GPU engine (below), plus the list of allowed process names in
`gpu-engine/engine/src/main.rs`, taken from Cardwire's `cardwire-daemon/src/whitelist.rs`. The other
projects are listed because Boreas learned hardware protocol details from them or took hardware tables
from them (named in each section) and checked its own against them; no source files were copied from them.

## Cardwire

Source: https://github.com/OpenGamingCollective/cardwire
Pinned commit: 0b3f2e59174897fac7ec61ea35690725c1ec309f
Original author/maintainer: luytan and Cardwire contributors.
License: GNU General Public License, version 3 (GPL-3.0).
Full license: gpu-engine/vendor/LICENSE.cardwire (also LICENSE in this repository).

The following directories are vendored from that commit:
- gpu-engine/vendor/cardwire-ebpf
- gpu-engine/vendor/cardwire-ebpf-userspace
- gpu-engine/vendor/cardwire-core

The upstream files carry no per-file notices; the crates' Cargo.toml license and author fields are
kept. The allowed process names in `engine/src/main.rs` are those of Cardwire's
`cardwire-daemon/src/whitelist.rs` without `virtnodedevd`. The upstream workspace dependency definitions
and lockfile were also used for the GPU engine workspace. The engine workspace
selects only the required crates and adds Boreas's own helper executable.

Integration by Yusuf Yontürk, 2026-11-01: Boreas supplies its own inherited-stdio
protocol, conservative NVIDIA topology discovery, display checks and policy owner.
The vendored crates are otherwise unmodified at this point. No Shiftray source is used.

Boreas change, 2026-11-22: `cardwire-ebpf/src/helpers.rs` `is_comm_whitelisted()` accepts an
allowed process name only when the process runs as root (uid 0), so an unprivileged process
cannot rename itself past the Eco block.

Boreas change, 2026-11-27: the helper's discovery in `engine/src/main.rs` also accepts a Radeon
dGPU. It follows two ideas of Cardwire's `cardwire-core`: the GPU with a connected internal eDP
panel is the default one, and an amdgpu device with the `AMDGPU_IDS_FLAGS_FUSION` flag is integrated
(`src/gpu/vendor_specific/amd.rs`). The code is written anew in the helper and in `boreas/gpu.py`,
with the ioctl numbers taken from the kernel header `include/uapi/drm/amdgpu_drm.h`. The vendored
crates are not changed by this.

The Rust helper and its vendored code are distributed under GPL version 3.
Binary distributions must provide their corresponding sources, Cargo.lock,
workspace manifests and build instructions, including the vendored eBPF sources.
Other Cargo dependencies retain their respective licenses; these notices do not
replace those dependencies' applicable license requirements.

## G-Helper (CPU temperature and keyboard reference)

Source: https://github.com/seerge/g-helper
Reference commit: aa0037c5326d002ecee316873dcf17de61fac228
Original author/maintainer: Serge (seerge) and G-Helper contributors.
License: GNU General Public License, version 3 (GPL-3.0).

No C# source files from G-Helper are in this repository and no G-Helper code is
copied; Boreas's code is its own.

CPU temperature ceiling: G-Helper's `app/Pawn/RyzenSmu.cs` is one of four sources
the per-family table in `smu/boreas_smu.c` was taken from and checked against; the
others are RyzenAdj, PawnIO.Modules and ryzen_smu (below). The table holds which CPU
families have a temperature command, its number (`SetThm`) and the MP1 mailbox
addresses (`GetMp1Addrs`); the order in which `boreas_smu_xfer()` uses the mailbox,
and its 200 ms idle wait, follow `MailboxRaw`. How Boreas uses them is its own: it
goes through the kernel's locked SMN path, holds the BIOS's own lock (`\_SB.AM00`)
for every message, re-applies the ceiling on a schedule measured on a G713PV (on
Dragon Range, Raphael and Granite Ridge / Fire Range G-Helper re-applies once, 5
seconds after it applies a mode's power limits, `app/Mode/ModeControl.cs`,
`IsReapplyRyzenRequired`), and falls back to the power limits on any fault. The
30-second re-apply on the families G-Helper marks as dropping the ceiling under
load is taken from G-Helper.

Keyboard: these hardware facts in `boreas/aura.py` are taken from G-Helper's
`app/USB/AsusHid.cs` and `app/USB/Aura.cs` (values, not code): the list of Aura
keyboard product IDs (`PIDS`), the meaning of byte 9 of the effect packet (random
color), the order of the effect menu, and the keyboard query (`probe`, `kind_of`):
the two wake-up packets, the query, and the byte of the answer that tells a
single-zone, a four-zone and a per-key keyboard apart. Boreas's Python code is its
own; the colors and thresholds of the heatmap, battery and GPU mode effects differ
from G-Helper's.

## asusctl (keyboard protocol reference)

Source: https://github.com/OpenGamingCollective/asusctl
Reference commit: 5346e184d09c5ed46e5dd16cdecedb7608393502
Original authors: Luke Jones, Denis Benato and asusctl contributors.
License: Mozilla Public License, version 2.0 (MPL-2.0).

No source files from asusctl are vendored in this repository. The effect
numbers, speed values and zone numbers in `boreas/aura.py` match asusctl's
`rog-aura/src/builtin_modes.rs`, and the set/apply packets its `rog-aura/src/usb.rs`;
the meaning of byte 9 (random color) is taken from G-Helper (above). Boreas's code
for this is its own.

Which effects are offered for each kind of keyboard (`modes_for`) is what
asusctl's `rog-aura/data/aura_support.ron` lists for most models of that kind:
all twelve effects on per-key keyboards (43 of its 49 per-key entries), and
Static, Breathe, RainbowCycle, RainbowWave and Pulse on four-zone keyboards
(27 of 28). Its single-zone entries differ among themselves (34 of 70 list RainbowWave, 21
lack RainbowCycle); Boreas offers Static, Breathe, RainbowCycle and Pulse there, as
G-Helper does. G-Helper offers the same built-in effects and adds its own
direct-color modes. asusctl picks a keyboard's entry by DMI board name; Boreas asks the keyboard instead (see G-Helper
above), because one model can ship with either kind.

## RyzenAdj (CPU temperature command reference)

Source: https://github.com/FlyGoat/RyzenAdj
Reference commit: 44438e1ba2bd5882b7ce68b6d2cfae9599fcf2b8
Original author/maintainer: Jiaxun Yang (FlyGoat) and RyzenAdj contributors.
License: GNU Lesser General Public License, version 3 (LGPL-3.0).

No source files from RyzenAdj are vendored in this repository. The CPU
temperature commands and MP1 mailbox addresses in `smu/boreas_smu.c` were
checked against RyzenAdj's `set_tctl_temp` (`lib/api.c`), `lib/nb_smu_ops.c` and
the CPU family detection in `lib/cpuid.c`.

## PawnIO.Modules (CPU family detection reference)

Source: https://github.com/namazso/PawnIO.Modules
Reference commit: 40a95f0c812b09f78e05b2d5aa4614cbcef67b0a
Original author/maintainer: namazso and contributors.
License: GNU Lesser General Public License, version 2.1 or later (LGPL-2.1-or-later).

No source files are vendored. The CPU family and model numbers per code name in
`smu/boreas_smu.c` and `boreas/ryzen.py` were checked against `RyzenSMU.p`
(`get_code_name`), the module G-Helper uses for SMU access.

## ryzen_smu (cross-check)

Source: https://github.com/amkillam/ryzen_smu
Reference commit: d2983668300dd2a598e5a7dc40e71ce0678cc270
Original author/maintainer: leogx9r (original driver), amkillam (this fork) and contributors.
License: GNU General Public License, version 2 (GPL-2.0).

No source files are vendored. Its CPU detection (`smu.c`) and MP1 mailbox
addresses were used as an independent check of the table in `smu/boreas_smu.c`.

## linuxhw/ACPI (BIOS table survey)

Source: https://github.com/linuxhw/ACPI
Reference commit: d6d30c6ef6f45506567f25a302c64f009d8f5f4e
Original author/maintainer: Linux Hardware Project and the people who sent hardware probes.
License: Creative Commons Attribution 4.0 (CC-BY-4.0).

No tables or other files are included in this repository. Its decoded ACPI tables of
ASUS laptops were searched for the `\_SB.AM00` mutex and the MP1 mailbox addresses on
2026-11-22; the findings are summarised in `docs/cpu-smu.md` ("Which ASUS BIOSes carry
the lock").
