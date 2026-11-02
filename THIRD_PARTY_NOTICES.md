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

No C# source files from G-Helper are vendored in this repository. Boreas's
Python implementation in `boreas/thermal.py` and `boreas/ryzen.py` references
G-Helper's Raphael/Dragon Range CPU temperature command (`0x3F` in
`app/Pawn/RyzenSmu.cs`) and single 5-second post-profile reapply timing
(`app/Mode/ModeControl.cs`) for the experimental opt-in SMU bridge client.
