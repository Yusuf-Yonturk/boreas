# Experimental embedded GPU engine

Experimental integration with limited hardware validation (see below). Default: firmware.
Do not enable on an unattended laptop. No kernel/boot settings are changed by the installer.

## Architecture and scope

boreasd owns an inherited-pipe Rust child, `boreas-gpu-engine`. The child owns the
Cardwire-derived eBPF links and uses its own PID exemption to read hidden GPU nodes.
Boreas owns AC/battery switching and nvidia-powerd restoration; the child owns only
GPU visibility. No separate cardwired, GUI or new public service is required.

First implementation: one non-primary NVIDIA GPU, a second GPU driving an internal
eDP panel, and loaded NVIDIA/DRM nodes. AMD dGPU, desktops, multiple NVIDIA GPUs,
Smart/app policies, external GPU and driver hot-reload are not supported yet.
The eBPF path never unloads a driver, removes PCI devices or writes dgpu_disable.
Ultimate still uses Boreas's firmware MUX path and needs reboot.

Policy `eco` means new accesses are blocked, NOT that power is off. Read
`gpu.engine.power_state`, `runtime_status` and `users` to diagnose actual sleep.
GPU temperature polling through nvidia-smi is disabled for this backend.
Existing processes keep their handles. Connected dGPU monitors cause Eco to be
rejected. Hotplug while already in Eco is NOT automatically handled in this first
version: choose Standard before attaching an external screen. No udev refresh
and no automatic helper restart after a fault: the blocks drop with the helper
and status shows the error until you choose a GPU mode again, which starts a
fresh helper for that request only. Restart boreasd in Standard after changing
GPUs/drivers. No automatic fallback from eBPF to firmware.

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

A failed download or a checksum mismatch aborts the installation with nothing
installed; the message names the `BOREAS_GPU_ENGINE` option. The in-app updater
installs from the `main` source archive, so a version on `main` needs its
`v<version>` Release before installed copies can update to it. Installing the
helper never selects eBPF.

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
{"protocol_version":1,"capabilities":["status","eco","standard"]}
```

Boreas requires protocol v1 and all three capabilities, plus the existing
newline-delimited JSON request/response contract. Custom executables must be
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
