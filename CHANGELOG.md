# Changelog

Boreas shows these notes from the **Release notes** link when a new version is available.
Newest version first.

## Unreleased

- CPU temperature target, **fast path**: with the power limits at their floor and the CPU 5 °C or more above the target for 3 s, the controller now goes through the whole escalation ladder at once, up to and including `cpufreq/boost` off, instead of one rung every 15 s after a 10 s grace. On machines where the firmware does not let the sustained limit bind (ROG Strix G713PV: 30 W requested, 50 to 60 W drawn, 93 °C for 25 s) the overshoot now ends a few seconds after the limits reach their floor
- A rung that is needed again within 60 s of its release is held longer the next time: the hold after an escalation doubles per relapse from 20 s up to 320 s and goes back to 20 s after a calm minute, a new target or floor, or a mode change. Before, a sustained load switched boost off and on again about every 40 s

## 0.1.6

- The CPU temperature target gained an **escalation ladder**: when the power limits sit at their floor and the CPU is still above the target, the controller steps below the mode's own CPU policy one rung at a time (EPP one notch down, then `cpufreq/boost` off, then the default governor when the mode uses the Performance power profile) and releases the rungs in reverse order as the CPU cools. The mode's CPU Boost and power profile stay the ceiling; `Default`, mode changes and service exit restore them exactly. The Fans & Power page shows the state on the Power card ("Target 82 °C · PL1 30 W (floor) · EPP lowered") and the main card says "limited" when the target cannot be met
- Per-mode **sustained power floor** under the temperature target (slider, `boreasctl cpufloor <W>|min`): the controller never requests SPL below it and moves on to the ladder instead, so a gaming mode can hold 45 W instead of the firmware's 30 W minimum
- Controller fixes: the cut is 2 to 6 W per second (was up to 10) and starts one degree above the target, where `limited` is also judged; recovery is 2 W every 2 s (was 1 W per 5 s); limits that drifted inside the controller's band are followed instead of latching "changed externally", a user limit the firmware undid is written again, and a raised limit that is not a user setting becomes the new ceiling; saving the mode again retries after a fault; a limit another writer changed is logged instead of silently dropped; a dismissed thermal notice is not raised again for the same fault; stored mode values are sanitised at load so a bad `cpu_temp` cannot stop the service; the controller's fault texts are translated

- The explanations on the Fans & Power page moved from text under the rows to an **info icon** beside each label (hover to read); the Power profile one now says what each choice does. A long slider caption ("Default", "Off") is no longer cut off
- The Power card's status line and the "limited" word on the mode line no longer blink. Around the target the controller's state changes every second and it takes a 2 W recovery step that it cuts back at once; "(floor)" is now shown whenever the sustained limit sits at its floor and bridges that one step for up to 10 s, and "limited" stays for up to 30 s. Neither is carried over to another mode, target or floor, a stopped controller, or limits that are back at their ceiling
- Per-mode **Power profile** selector in Fans & Power, under CPU Boost (`Default` / `Power saver` / `Performance`): the `cpufreq/scaling_governor` that the desktop's power-profile menu switched before Boreas masked `power-profiles-daemon`. `Default` restores the driver's own governor (`schedutil`, else `powersave`); the selector is hidden when the kernel offers no governor to come back to. Under the `performance` governor the kernel fixes the EPP, so the CPU Boost list shrinks to `Performance` / `Disabled` while that profile is selected, and the EPP write is skipped instead of reported as a failure

## 0.1.5

- With the eBPF GPU backend the dGPU temperature is shown again: the service asks `nvidia-smi` when the helper reports the standard policy with the device awake, never in eco and never to wake it. Before, the app's GPU line only showed the runtime state ("active")
- The CPU temperature target's hint says why short overshoots happen: the firmware sets a floor on the short-term power limits, so a sudden load can still run a few degrees above the target

## 0.1.4

Battery-mode fixes and a cleaner hand-over from the desktop's power-profile daemon.

- `nv_dynamic_boost` is skipped while the firmware offers no adjustable range (on battery it reports 0..0 and rejects every write); the value is written again when the mode is re-applied on AC. No more "Could not write nv_dynamic_boost" notice on every battery switch
- `install.sh` also masks `power-profiles-daemon` (it wrote the same `platform_profile` and `cpufreq/boost` files behind Boreas's back); `uninstall.sh` brings it back. The desktop's power-profile menu is empty while Boreas is installed
- `boreasd.service` no longer orders itself after `power-profiles-daemon.service`, which produced a systemd stop-order cycle at shutdown
- The GPU status line of the app shows a count of the processes using the dGPU (system services excluded) instead of the whole list; the full `command(PID)` list is in its tooltip, so the window no longer grows with it
- Startup logs a warning when `BOREAS_SMU_BRIDGE=1` is set but the bridge module is not usable, instead of silently falling back to power-feedback

## 0.1.3

CPU temperature targets, per-mode CPU boost controls and an optional experimental GPU backend.

- Per-mode **CPU Boost** selector in Fans & Power (`Default`, `Disabled`, `Efficient`, `Balanced`, `Aggressive`, `Max`) using `cpufreq/boost` and `energy_performance_preference` (EPP)
- Bounded closed-loop **CPU temperature target** (`power-feedback`) via `asus-armoury` power limits (`ppt_pl1_spl`, `ppt_pl2_sppt`, `ppt_pl3_fppt`) without raw userspace PCI `0xB8/0xBC` SMU access, plus opt-in kernel SMU bridge (`smu-bridge`) support
- Selecting or applying a mode on the Fans & Power page now immediately activates that mode
- Battery charge limit is re-applied to the embedded controller whenever AC power is connected
- Silent mode now supports kernels that advertise `low-power` instead of `quiet` in `platform_profile`
- Optional Rust/eBPF GPU engine (`boreas-gpu-engine`), selectable from the GPU mode card or `boreasctl gpu-backend firmware|ebpf`; firmware remains the default
- Backend changes validate helper protocol v1 and apply the requested policy before saving; rejected activation preserves the previous preference, and repeated enable/disable starts a fresh helper
- Returning to firmware releases GPU access blocks and restores nvidia-powerd; it preserves Ultimate and pending Ultimate requests without changing the MUX or requesting a reboot
- Separate Eco policy and real GPU power/runtime status in the UI; Eco access blocking alone is not evidence of GPU sleep
- Custom helper override at `/etc/boreas/boreas-gpu-engine`; upgrades preserve checksum-detected edits and back up unknown legacy helpers instead of silently discarding them
- CPU Boost/EPP writes report failures and verify readback rather than silently accepting partial application
- Added staged upgrade regression tests, a Rust CI build/test job, a backup-based local installer and repeatable live GPU/CPU boost probes
- Choosing a GPU mode after a helper fault (timeout, crash, bad response) starts a fresh helper for that request; periodic re-application still never restarts it
- `cpufreq/boost` writes verify readback (global or per-policy amd-pstate files); `Disabled` reports when no boost control exists instead of silently applying EPP only
- `tools/install-reviewed.sh` also protocol-checks a `/etc/boreas/boreas-gpu-engine` override, since that is the helper boreasd runs
- The helper caches its open-handle scan of `/proc` for 5 seconds between status polls
- The GPU helper is no longer committed to the repository: CI builds it from the Rust source with build paths remapped, fails if the result still contains build-machine paths, and attaches `boreas-gpu-engine` and its sha256 to the GitHub Release of each `v*` tag
- `install.sh` resolves the helper before writing anything: `BOREAS_GPU_ENGINE=<file>` for offline installs or your own build, else `data/bin/boreas-gpu-engine`, else the Release files for the version being installed, which it checks against their sha256 and refuses on a mismatch
- `tools/install-reviewed.sh` installs the same staged helper it protocol-checked

Local validation on 2026-11-02: 183 Python tests and 11 Rust tests passed; one
root/hardware Rust test was excluded. On the tested G713PV with Linux
`7.3.0-5-cachyos`, Eco blocked new GPU access and Standard restored it, including
a second backend enable/disable cycle. After 60 seconds the GPU remained
`D0 / active` and RTD3 reported `Not supported`: actual sleep and power savings
were not demonstrated on that kernel. On the same laptop with Linux
`6.18.55-2-cachyos-lts` and `NVreg_DynamicPowerManagement=0x02` (2026-11-01),
the same Eco reached `D3cold / suspended` after 60 seconds, so the 7.3 result is
a driver/kernel combination issue, not a Boreas one. One s2idle suspend/resume
cycle on that LTS kernel kept Eco, D3cold and the access block after wake-up;
suspend/resume was not tried on 7.3. CPU Boost/EPP application
and restoration passed. These results do not establish compatibility with other
laptops, BIOS versions or kernels. Details: [GPU validation](docs/gpu-engine.md) and
[CPU control scope](docs/cpu-temperature.md).

## 0.1.1

GPU mode fixes, tested on a ROG Strix G713PV.

- Eco → Standard no longer hangs for 30 seconds and fails: the dGPU's PCIe port is woken up first,
  so the switch now takes a few seconds
- Eco can now be applied at boot: nvidia-powerd is stopped before the NVIDIA driver is unloaded
- The apps using the dGPU (KWin, Xwayland, games) are detected correctly, so the "Eco postponed"
  notice now names them
- A firmware error while changing the GPU mode no longer stops the service at boot

## 0.1.0

First public release.

- Performance modes (Silent, Balanced, Turbo) and custom modes
- Custom fan curves per mode (CPU, GPU and mid fan)
- CPU and GPU power limits, CPU temperature limit (Ryzen)
- GPU modes: Eco, Standard, Optimized and Ultimate (MUX switch)
- Aura keyboard lighting and brightness, battery charge limit
- Built-in display refresh rate and panel overdrive (KDE)
- Automatic mode switching on AC / battery, re-apply after sleep
- Light and dark theme, 11 languages
- Update notice with one-click update
