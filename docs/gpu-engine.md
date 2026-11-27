# Experimental embedded GPU engine

Experimental integration with limited hardware validation (see below). Default: firmware.
Do not enable on an unattended laptop. No kernel/boot settings are changed by the installer.

## Architecture and scope

boreasd owns an inherited-pipe Rust child, `boreas-gpu-engine`. The child owns the
Cardwire-derived eBPF links and uses its own PID exemption to read hidden GPU nodes.
Boreas owns AC/battery switching and nvidia-powerd restoration; the child owns only
GPU visibility. No separate cardwired, GUI or new public service is required.

The blocking itself comes from Cardwire (GPL-3.0), used here as a library: the eBPF
programs and the code that loads them and hands them the device nodes to block. Boreas
adds its own device discovery, safety checks and the integration with the ASUS GPU modes.

Supported layouts, both with exactly two built-in GPUs:

- one non-primary NVIDIA GPU, a second GPU driving an internal eDP panel, and loaded
  NVIDIA/DRM nodes (tested on one laptop, see below);
- one Radeon dGPU next to the processor's integrated GPU (see "Radeon dGPU" below; not
  tested on real hardware).

Desktops, more than two GPUs, Smart/app policies, external GPU and driver hot-reload are
not supported.
The eBPF path never unloads a driver, removes PCI devices or writes dgpu_disable.
Ultimate still uses Boreas's firmware MUX path and needs reboot.

Policy `eco` means new accesses are blocked, NOT that power is off. Read
`gpu.engine.power_state`, `runtime_status` and `users` to diagnose actual sleep.
GPU temperature polling through nvidia-smi is disabled for this backend.
A Radeon dGPU has no GPU temperature reading in Boreas.
Existing processes keep their handles. Connected dGPU monitors cause Eco to be
rejected. Hotplug while already in Eco is NOT automatically handled in this first
version: choose Standard before attaching an external screen. No udev refresh
and no automatic helper restart after a fault: the blocks drop with the helper
and status shows the error until you choose a GPU mode again, which starts a
fresh helper for that request only. If nvidia-powerd could not be started again
after the fault, the fresh helper inherits that pending restore and every
Standard request retries it. Restart boreasd in Standard after changing
GPUs/drivers. No automatic fallback from eBPF to firmware.

## Radeon dGPU

Not tested on a laptop with a Radeon dGPU. The rules below are covered by unit tests on
a fake sysfs tree; the one real check was the integrated-GPU query, which answered
"integrated" for the Ryzen iGPU of the G713PV. Whether amdgpu lets the dGPU sleep once
access is blocked is unknown. Reports are welcome.

Blocking does not depend on the GPU maker: the helper blocks the card and render nodes,
the PCI device and its audio function, the DRM sysfs entries and hwmon. What differs is
finding the dGPU, because the integrated GPU is AMD (vendor 0x1002) too. With no NVIDIA
GPU present the helper accepts the layout only when all of this holds:

- exactly two built-in GPUs, and exactly one of them has a connected internal eDP panel;
- the other one, the target, is AMD on the amdgpu driver and is not the boot display:
  `boot_vga` is 0, or the file is missing and the PCI class is 0x0380 (the kernel
  creates `boot_vga` only for VGA-class devices);
- the GPU with the panel is the processor's integrated GPU: Intel, or AMD on amdgpu
  that answers the kernel's `AMDGPU_INFO_DEV_INFO` query with the `FUSION` (APU) flag.
  Only the panel GPU is asked, so the dGPU is not woken up. On a laptop whose MUX has
  put the panel on the dGPU the answer is "not integrated" and the layout is refused,
  instead of the integrated GPU being taken for the dGPU. A failed query refuses too.

Differences from Cardwire: Cardwire also tells the GPU type through Vulkan and blocks
the PCI bridges above the dGPU. The helper does neither: it runs as a root child
without a Vulkan loader, and the bridges are left alone. `/dev/kfd` (ROCm/OpenCL
compute) is not blocked; its effect on sleep is unknown.

On the Boreas side the firmware backend still refuses Eco and Optimized with a Radeon
dGPU: its Eco writes `dgpu_disable=1`, which can freeze the system while amdgpu is
bound. When the installed helper advertises `amd` and the service finds the same
layout, the eBPF switch is offered; Eco and Optimized appear after it is switched on.
While it is off, a request for them is refused with "Eco and Optimized are offered
here only through the eBPF engine". A helper built before this support does not
advertise `amd`, and nothing changes with it.

## Helper source and reproducible build

The checkout does not carry a prebuilt helper. The `gpu-engine` job in
`.github/workflows/tests.yml` builds `boreas-gpu-engine` from the Rust source on
every push and pull request, and on a `v*` tag the `release` job attaches that
build and its checksum to the GitHub Release:

```
https://github.com/Yusuf-Yonturk/boreas/releases/download/v<version>/boreas-gpu-engine
https://github.com/Yusuf-Yonturk/boreas/releases/download/v<version>/boreas-gpu-engine.sha256
```

The release job also checks that the tag matches `__version__` in `boreas/__init__.py`,
because that is the version `install.sh` asks for. So the helper users run is the
one CI built from the tagged source, and a Rust change can no longer drift from
the binary: there is no binary to forget to rebuild.

`sudo ./install.sh` resolves the helper, in this order, before writing anything:

1. `BOREAS_GPU_ENGINE=/path/to/boreas-gpu-engine` (offline installs and your own builds,
   for example `BOREAS_GPU_ENGINE=gpu-engine/target/release/boreas-gpu-engine`);
2. `data/bin/boreas-gpu-engine` next to `install.sh`, when a tarball or a copied build
   provides it;
3. otherwise it downloads the two release files for the version in `boreas/__init__.py`
   into a temporary directory and compares the sha256.

A checksum mismatch aborts the installation with nothing installed. A failed
download does not: the helper is optional, so Boreas is installed without it
(a helper that is already installed stays), the eBPF backend is unavailable
until `install.sh` runs again with network access, and the warning names the
`BOREAS_GPU_ENGINE` option. The in-app updater installs the source archive of
the release's tag, and only offers a release that carries the helper.
Installing the helper never selects eBPF.

To reproduce the CI build on x86_64 Linux with LLVM 23, a C toolchain and
bpf-linker available in PATH:

```sh
rustup toolchain install 1.95.0 --profile minimal
rustup toolchain install nightly-2026-08-12 --profile minimal --component rust-src
cargo +1.95.0 install bpf-linker --version 0.12.0 --locked
cargo +1.95.0 test --manifest-path gpu-engine/Cargo.toml --locked -p boreas-gpu-engine
RUSTC_WRAPPER="$PWD/tools/rustc-remap.sh" \
    cargo +1.95.0 build --release --manifest-path gpu-engine/Cargo.toml --locked -p boreas-gpu-engine
```

The upstream build.rs uses nightly-2026-08-12 to compile the embedded BPF object.

`tools/rustc-remap.sh` is the `RUSTC_WRAPPER` CI uses. It appends
`--remap-path-prefix` for the home, cargo and rustup directories and the checkout,
so neither the helper nor the embedded BPF object (whose debug info aya-build
compiles with `-Cdebuginfo=2`) carries build-machine directory names. rustc
applies the last matching prefix, so the most specific one is listed last. CI
fails if `strings` on the result still contains `/home/`; check a local build the
same way: `strings gpu-engine/target/release/boreas-gpu-engine | grep /home/`
(expect no output). Compare your build with the release with `sha256sum`.
Do not delete Cargo.lock: newer semver-compatible Aya build dependencies may
require a newer compiler. No Rust toolchain is required at runtime.

The runtime needs BPF LSM enabled, kernel BTF and support for the upstream hooks.
Kernel lockdown can reject optional directory-hiding hooks; upstream logs reduced
functionality. A successful build does not prove the running kernel accepts it.

## Explicit selection and rollback

First leave firmware Eco or Ultimate, select Standard and reboot if required.
Confirm the dGPU is present. Do not run cardwired or another GPU-switching tool
concurrently. Enable **eBPF engine (no-reboot Eco) · Experimental** in the UI's GPU
mode card, or use:

```sh
boreasctl gpu-backend ebpf
boreasctl gpu-backend firmware
```

Selection is saved in `/etc/boreas/config.json` only after the helper starts,
its protocol is accepted and the requested GPU policy succeeds. A rejected opt-in
keeps the previous preference and does not invoke firmware as an automatic fallback.
Explicitly selecting firmware releases the helper into **Standard**, preserving
Ultimate or a pending Ultimate request. Backend selection never changes the MUX
or requests a reboot. Choose firmware Eco separately if desired. Switching off and back on starts a fresh child.
A fault alone never triggers an automatic helper restart.

Older installations may have a systemd override setting `BOREAS_GPU_BACKEND`.
That environment variable takes precedence over the config. Remove just that
setting with `sudo systemctl edit boreasd`, then run `sudo systemctl daemon-reload`
and `sudo systemctl restart boreasd` before using the UI/CLI switch. Commands fail
with an explanation while the override exists, rather than saving an ineffective
preference. A previous `Conflicts=cardwired.service` line may remain.

Inspect `boreasctl status` and the journal after enabling. The UI shows policy,
PCI power state and runtime status separately: `eco` with `D0 / active` does not
mean the GPU is asleep. An `unknown` mode / backend_error is a failure.
Normal stop/EOF releases BPF links. SIGKILL can skip restoration of nvidia-powerd;
check its previous service state and restore it manually if necessary.
The parent rejects an active cardwired before spawning its helper. This integration
does not control an existing cardwired service. Do not launch the internal helper
directly alongside cardwired.

### Locally maintained helper and compatibility

Keep a patched helper outside the package-managed `/usr/lib` path:

```sh
sudo install -d -m755 /etc/boreas
sudo install -o root -g root -m755 /path/to/your/boreas-gpu-engine /etc/boreas/boreas-gpu-engine
```

Boreas automatically prefers `/etc/boreas/boreas-gpu-engine`; the installer does
not overwrite it. To return to the installed release helper, select firmware,
remove or rename that local helper, then enable eBPF again. Already running children keep
their current executable until explicitly switched off or the daemon restarts.
The installer records the checksum of its managed `/usr/lib` helper. If that
file is subsequently modified, the next upgrade preserves it as the local helper
when no local override exists. An existing local override is never replaced.
Unknown legacy binaries without a recorded checksum, or additional modified
copies when an override exists, are backed up under
`/etc/boreas/preserved-gpu-engines/<sha256>` without auto-activation. The installer
prints the backup path. This avoids mistaking an older stock helper for an
intentional override. To use an existing legacy patch, move it to the local path
and add protocol negotiation before enabling eBPF. `/usr/lib` remains managed.

Before loading hooks, the helper must answer `--protocol-info` without touching
the GPU or requiring root:

```json
{"protocol_version":1,"capabilities":["status","eco","standard","amd"]}
```

Boreas requires protocol v1 and the first three capabilities, plus the existing
newline-delimited JSON request/response contract. `amd` is optional: with it the helper
says it can drive a Radeon dGPU, and without it Boreas offers eBPF only with an NVIDIA
dGPU. Custom executables must be
root-owned and not group/world-writable. Missing, legacy or incompatible helpers
fail visibly; Boreas does not silently substitute a different binary. Rebuild
older helpers from the updated source to support negotiation. Protocol agreement
checks the communication contract, not hardware compatibility or actual sleep.

## Required hardware validation before release

On one G713PV with NVIDIA open 615.78.08, Linux 6.18.55-2-cachyos-lts and
NVreg_DynamicPowerManagement=0x02, a bounded Standard/Eco/Standard probe confirmed
blocked device access in Eco, restored access in Standard, and D3cold/suspended
after 60 seconds without GPU queries. Existing KWin handles remained open.
With Linux 7.3.0-5-cachyos and the same driver/parameter, the driver instead
reported RTD3 "Not supported". These observations do not establish the cause of
the kernel difference or a measured battery saving. The initial LTS startup
failed because Silent used `quiet` while the kernel advertised `low-power`.
Silent now selects that advertised alias. Startup also reports other profile
write failures while keeping independent controls available. Explicit
performance-mode requests still return their write errors.

The installed Python daemon/child path was also tested on this laptop: Eco
reached D3cold, Standard restored access, killing the helper in Eco released
the blocks and produced an unknown/error state, and restarting the daemon
restored Standard. On the LTS kernel (2026-11-01) one s2idle suspend/resume
cycle in Eco was also probed: after wake-up the policy was still Eco, the GPU
reported `D3cold / suspended`, RTD3 `Enabled (fine-grained)`, and card0,
renderD129, nvidia0 and nvidiactl all still returned ENOENT. amdgpu logged a
`amdgpu_dm_atomic_commit_tail` WARNING on resume; its relation to Boreas is
unknown. Suspend/resume was not probed on 7.3. This does not validate every
hardware item below.

- Confirm Standard policy without any firmware or driver changes.
- Eco/Standard repeated transitions; record policy AND real D0/D3cold states.
- Existing game/CUDA handles remain functional and are reported.
- Battery discharge under matched brightness/workload before and after.
- Performance and Dynamic Boost after returning to Standard.
- AC/battery bounce; suspend/resume on 7.3 (done once on 6.18 LTS, see above); MUX change and reboot.
- Connected external screen is refused; recovery with Standard works.
- Helper startup failure/crash/EOF; daemon restart; no legacy fallback.
- Kernel lockdown and rejected hooks are accurately reported.
- Repeat on other supported NVIDIA laptops before broad compatibility claims.
- A laptop with a Radeon dGPU: every item above. Nothing has been run on one yet.

Vendored source provenance and license: ../THIRD_PARTY_NOTICES.md.

## Reviewed live installation

After local tests pass, run `sudo sh tools/install-reviewed.sh` (with
`BOREAS_GPU_ENGINE=...` to review your own helper build). It stages and
checks the package, installs the same staged helper it checked, backs up installed runtime files and configuration under
`/var/lib/boreas/backups`, selects Standard using the current service, and restarts
the daemon with the reviewed files. It does not enable eBPF or reboot. The backup
path is also stored in `/var/lib/boreas/last-reviewed-backup`. Then test
Standard → eBPF → Eco → Standard, recording actual power state and restoring
firmware Standard at the end. Test Ultimate separately when a reboot is intended.

### Live validation, 2026-11-02

On this G713PV session with Linux `7.3.0-5-cachyos` and BPF LSM enabled, the
reviewed installed daemon passed firmware Standard → eBPF Standard → Eco →
Standard → firmware Standard, followed by a second enable/disable cycle.
New opens of `/dev/dri/card0`, `/dev/dri/renderD129` and `/dev/nvidia0` succeeded
in Standard, failed in Eco, and succeeded again after returning to Standard.
`dgpu_disable` stayed 0, the MUX stayed 1, and no reboot became pending.
`nvidia-powerd` was active before and after the test.

After 60 seconds without test-client GPU queries, policy was Eco but the GPU
remained `D0 / active`; the driver reported RTD3 `Not supported`. Existing KWin,
Xwayland, systemd and logind handles remained listed. This confirms access
blocking/restoration, not actual GPU sleep or energy savings, and does not
isolate the cause. The test ended in firmware Standard. Machine-readable evidence:
`outputs/gpu-live-20261102-074534/report.json` (local, ignored by Git).

### NVIDIA check of the helper with Radeon support, 2026-11-27

The helper that also accepts a Radeon dGPU was installed with `tools/install-reviewed.sh`
on the same G713PV (Linux `6.18.60-2-cachyos-lts`, NVIDIA 620.70.06,
NVreg_DynamicPowerManagement=0x02) to see that nothing changed for NVIDIA. One
Standard --> Eco --> Standard pass on the eBPF backend: the service reported the device
as `nvidia`; new opens of `/dev/dri/card0`, `/dev/dri/renderD129`, `/dev/nvidia0` and
`/dev/nvidiactl` succeeded in Standard, failed in Eco and succeeded again in Standard;
the iGPU's `renderD128` opened in every step. After 75 seconds in Eco the GPU reported
`D3cold / suspended`. `dgpu_disable` stayed 0, the MUX stayed 1, no reboot became
pending, and nvidia-powerd was stopped in Eco and active again in Standard.
Suspend/resume, AC/battery and Ultimate were not repeated. This says nothing about a
Radeon dGPU.
