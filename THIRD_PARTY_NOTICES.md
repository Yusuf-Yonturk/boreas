# Third-party source notices

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

Original per-file notices are retained. The upstream workspace dependency definitions
and lockfile were also used for the GPU engine workspace. The engine workspace
selects only the required crates and adds Boreas's own helper executable.

Integration by Yusuf Yontürk, 2026-11-01: Boreas supplies its own inherited-stdio
protocol, conservative NVIDIA topology discovery, display checks and policy owner.
The vendored crates are otherwise unmodified at this point. No Shiftray source is used.

The Rust helper and its vendored code are distributed under GPL version 3.
Binary distributions must provide their corresponding sources, Cargo.lock,
workspace manifests and build instructions, including the vendored eBPF sources.
Other Cargo dependencies retain their respective licenses; these notices do not
replace those dependencies' applicable license requirements.

## G-Helper (protocol and control-flow reference)

Source: https://github.com/seerge/g-helper
Reference commit: d7347c075d185a2b500458a2ceb468efa6d18982
Original author/maintainer: Serge (seerge) and G-Helper contributors.
License: GNU General Public License, version 3 (GPL-3.0).

No C# source files from G-Helper are vendored in this repository. The single
5-second reapply after a profile change in `boreas/thermal.py`, used by the
experimental opt-in SMU bridge client, follows G-Helper's timing
(`app/Mode/ModeControl.cs`). The CPU temperature command itself is credited to
RyzenAdj below; G-Helper's `app/Pawn/RyzenSmu.cs` uses the same one.

The keyboard question in `boreas/aura.py` (`probe`, `kind_of`) was learned from
G-Helper's `app/USB/AsusHid.cs` and `app/USB/Aura.cs` as of 2026-11-20: the two
wake-up packets, the query, and the byte of the answer that tells a single-zone,
a four-zone and a per-key keyboard apart. Boreas's code for this is its own; the
colors and thresholds of the heatmap, battery and GPU mode effects are Boreas's
own choices.

## asusctl (keyboard protocol reference)

Source: https://github.com/OpenGamingCollective/asusctl
Reference commit: 5346e184d09c5ed46e5dd16cdecedb7608393502
Original authors: Luke Jones, Denis Benato and asusctl contributors.
License: Mozilla Public License, version 2.0 (MPL-2.0).

No source files from asusctl are vendored in this repository. The Aura packet
layout, effect numbers, speed values and zone numbers in `boreas/aura.py` are
the ones in asusctl's `rog-aura/src/builtin_modes.rs`.

Which effects are offered for each kind of keyboard (`modes_for`) is what
asusctl's `rog-aura/data/aura_support.ron` lists for most models of that kind:
all twelve effects on per-key keyboards (43 of its 49 per-key entries), and
Static, Breathe, RainbowCycle, RainbowWave and Pulse on four-zone keyboards
(27 of 28). Its single-zone entries differ among themselves; Boreas offers the
ones without RainbowWave there. G-Helper offers the same sets. asusctl picks a
keyboard's entry by model name; Boreas asks the keyboard instead (see G-Helper
above), because one model can ship with either kind.

## RyzenAdj (CPU temperature command reference)

Source: https://github.com/FlyGoat/RyzenAdj
Reference commit: 44438e1ba2bd5882b7ce68b6d2cfae9599fcf2b8
Original author/maintainer: Jiaxun Yang (FlyGoat) and RyzenAdj contributors.
License: GNU Lesser General Public License, version 3 (LGPL-3.0).

No source files from RyzenAdj are vendored in this repository. The CPU
temperature command of the experimental opt-in SMU bridge client in
`boreas/ryzen.py` (MP1 `0x3F` on Dragon Range and Fire Range) is the one
RyzenAdj's `set_tctl_temp` uses for those families (`lib/api.c`).
