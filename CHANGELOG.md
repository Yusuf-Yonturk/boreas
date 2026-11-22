# Changelog

Boreas shows these notes from the **Release notes** link when a new version is available.
Newest version first.

## 0.1.10

- **Security hardening, second part:** the root update helper installs only a release newer than the one installed; the GPU helper's list of dGPU users stops after 2 s instead of delaying its reply past the service's timeout; and in Eco the process names allowed past the block (udev, package managers) count only for processes running as root, since any process can take such a name
- **Security hardening** after an audit of the service, the installer and the GPU helper. `boreasd` takes at most 16 connections at a time (the next one is told so), a request line must arrive within 10 s and be under 64 KiB, and a full file table no longer makes it spin. `install.sh` and `tools/install-reviewed.sh` run Python with `-I`, so a module in the folder they are started from is not loaded as root. `boreas-setup` sets `umask 022` and tightens its records if an earlier run left them writable. The GPU helper must be a regular file that, with every folder above it, is owned by root and not writable by others. Process names that hold the dGPU are cleaned of control characters before they reach the log, the notice or the tooltip, and the tooltip shows them as text
- **The CPU's own temperature ceiling is on by default.** With no saved choice, the installer and the in-app update switch it on for every Ryzen family the module knows (Raven Ridge to Strix Halo); it sends the command G-Helper and RyzenAdj send on those CPUs. The module still loads only on a supported CPU with the BIOS lock `\_SB.AM00`, and any SMU fault falls back to power-feedback. `dkms` and the kernel headers are needed to build it; without them the installer says so, Boreas keeps power-feedback, and the module is built by a later install or update once they are there. A module the CPU or BIOS refuses is no longer loaded at every boot. `/etc/boreas/smu` now says whether it was the default or the user's choice. `BOREAS_SMU=0` saves the off choice in `/etc/boreas/smu-off`, so later installs and updates keep it off. Someone who ran `BOREAS_SMU=0` in 0.1.9 has no saved off choice: the first update switches the ceiling on. To keep it off, run `sudo BOREAS_SMU=0 ./install.sh` once more, or without a copy of the sources `sudo sh -c 'touch /etc/boreas/smu-off; rm -f /etc/boreas/smu; systemctl try-restart boreasd'`
- **Packaging:** the experimental CPU temperature ceiling has its own package, `packaging/aur/boreas-smu-dkms`, for a copy installed as a package. It carries the `smu/` module source for DKMS, the opt-in `/etc/boreas/smu` and the line that loads the module at boot; removing it stops `boreasd` first, so the CPU gets its default ceiling back, and starts it again without the module. The AUR `boreas` PKGBUILD builds 0.1.9
- **Testing the SMU ceiling:** `tools/smu-report.sh` writes a report (laptop, CPU family, module state, a 90 s load test against the mode's target) for the new "CPU temperature ceiling (SMU) test" issue form; docs/cpu-smu.md lists the steps

## 0.1.9

- **Experimental: the CPU's own temperature ceiling.** With `sudo BOREAS_SMU=1 ./install.sh`, a small kernel module (`smu/`, built with DKMS) hands a mode's CPU temperature target to the CPU itself, the way G-Helper does on Windows, so short boost bursts no longer run past it. Boreas recognises the Ryzen family by itself (Raven Ridge to Strix Halo; the list is in docs/cpu-smu.md). The BIOS uses the same SMU mailbox, so the module holds the BIOS's own lock (`\_SB.AM00`) for every message and does not load on a BIOS without it. On an unsupported CPU, without the module, or after any SMU error, the target keeps working through the ASUS power limits as before. The earlier opt-in (`BOREAS_SMU_BRIDGE=1`, `enable_hw`) is gone

## 0.1.8

- **Eco and Optimized only with an NVIDIA dGPU.** Before it writes `dgpu_disable`, Eco stops the NVIDIA services, unloads the NVIDIA driver and removes the dGPU from the PCI bus. On a laptop with a Radeon dGPU (2022 ROG Zephyrus G14, GA402R) none of that applies, so Eco wrote `dgpu_disable=1` with `amdgpu` still bound, which can freeze the system. Eco and Optimized are now offered only when an NVIDIA display device is on the PCI bus, or when the laptop is already in Eco (an NVIDIA dGPU is off the bus then, and leaving Eco stays possible). Elsewhere their buttons and tray entries are hidden, the service and `boreasctl gpu` refuse them, a saved Eco or Optimized mode falls back to Standard with a notice, and the eBPF engine cannot be switched on. An NVIDIA eGPU does not count. Standard and Ultimate work as before, and so does Eco on NVIDIA laptops; if the NVIDIA dGPU has not come back on the bus after leaving Eco, Eco and Optimized are hidden until it does and a saved Optimized is kept. On a laptop without `dgpu_disable` the two buttons are now hidden instead of greyed out, and a saved Eco or Optimized mode falls back to Standard there too
- **Keyboard effects follow the keyboard.** The service asks the keyboard what kind it is (single zone, four zones or per key) and the Effect list shows what that kind has. Per-key keyboards gain Star, Rain, Highlight, Laser, Ripple, Comet and Flash. The same model can ship with either kind of keyboard, so the model name is not used for this (ROG Strix G713PV here: per key)
- **Heatmap, GPU mode and Battery** effects: the keyboard color follows the CPU temperature (blue when idle, green, amber from the mid 70s, red at 90 °C), the GPU mode (Eco green, Standard blue, Optimized amber, Ultimate red) or the battery charge (red when low, green when full). The service rewrites the color when it has visibly changed and does not store it in the keyboard
- **Second color** for Breathe: a second color button next to the first. Until one is picked, Breathe behaves as before
- **Zone colors** on four-zone keyboards: a color for each of the four zones. Not tested on a four-zone keyboard yet; reports are welcome
- Keyboard lighting values sent to the service are checked before they are stored: an unknown effect or a malformed zone list is refused
- **Packaging:** `packaging/aur` holds PKGBUILDs for `boreas` and `boreas-git`. A packaged copy does not update itself: the update check, its tray switch and the in-app update are off, and the package manager updates it. The optional eBPF GPU helper is not part of the packages
- **`boreas-setup`**: handing the laptop controls to Boreas (masking asusd, supergfxd and power-profiles-daemon, starting `boreasd`) and giving them back is its own command now, `sudo boreas-setup enable` / `disable`. `install.sh` and `uninstall.sh` run it as before. After a package install, where nothing is switched on yet, the window asks first: it says what will be masked and how to undo it, and enables Boreas on a yes after the administrator password
- `install.sh`: `BOREAS_GPU_ENGINE=none` installs without the GPU helper and without a download
- **Plug and unplug:** when the firmware refused the profile write at the moment the charger was plugged in or out, the mode for the new power source was never applied until the next plug or unplug. The switch is now tried again every 15 s, with one line in the log, until it lands or another mode is chosen. A profile the firmware sets on its own at that moment is no longer taken for Fn+F5 and stored over the battery or AC mode
- Leaving Eco reports an error when the NVIDIA dGPU does not come back on the PCI bus, instead of showing Standard with no dGPU. Switching from Eco to Ultimate still writes the MUX when the dGPU is only slow to appear, since it comes up at the reboot Ultimate needs. This now works with the NVIDIA driver too, which refuses to load while the dGPU is missing: an installed driver is enough to expect an NVIDIA dGPU (unless a Radeon dGPU is visible), so a saved Eco or Optimized mode is kept and the "need an NVIDIA dGPU" notice no longer shows on an NVIDIA laptop
- **Resume:** when the firmware refused the profile write right after waking up, the mode for the power source was not applied and not tried again, and a profile the firmware set on its own could be stored over the battery or AC mode as if Fn+F5 had been pressed. Resume and startup now use the same retry as plugging in or out, and the GPU step after it still runs
- `uninstall.sh` no longer unmasks and starts services you had masked yourself, also after an earlier `boreas-setup disable` or when every service was already masked before installing: `boreas-setup enable` keeps a record even when it had nothing to mask, `disable` empties it instead of removing it (uninstalling still removes it), and `disable` leaves a mark that Boreas was switched off (the window then no longer offers to enable it). Updating a copy that has masks but no record (made by 0.1.6 or older, also when it went through 0.1.7) takes those masks over into the record before any file is replaced, so `disable` and `uninstall.sh` give them back as before, even after an install that was stopped halfway and run again. This includes a copy that went from 0.1.6 through 0.1.7, whose record lists only part of them; a service you masked yourself before installing 0.1.7 or older is then taken for one of Boreas's too and comes back at uninstall. A mask that lands but fails to stop its service is recorded too. `boreas-setup` refuses options it does not know. An update after `boreas-setup disable` restarts `boreasd` only if it was turned back on by hand. Removing the package (`pacman -R`) no longer leaves that mark behind, and a package upgrade reloads the service unit before restarting it
- An update (in-app or `sudo ./install.sh`) keeps Boreas off after `sudo boreas-setup disable`: the files are updated, but asusd, supergfxd and power-profiles-daemon are not masked again and `boreasd` is not started. `sudo boreas-setup enable` turns it back on
- `install.sh` refuses to install over a copy a package owns (`sudo ./install.sh` after an AUR install): it would take files the package owns and switch the in-app update back on. Update with the package manager, or remove the package first
- **Security, in-app update:** the release was downloaded and unpacked in a folder of the user account, and its `install.sh` was then run as root. Any program running under that account could change the installer while the password dialog was open. pkexec now starts a fixed helper that is installed with Boreas and belongs to root (`/usr/lib/boreas/boreas-update`); it downloads the release into a folder only root can reach, checks its version and runs its installer. The password dialog names that helper and the version instead of `/bin/sh`, and an HTTPS proxy set in the session is handed to the helper, since pkexec clears the environment. The update from 0.1.7 itself still goes the old way, since 0.1.7 does not have the helper yet
- **Plug and unplug, continued:** a pending switch to the battery or AC mode is no longer cancelled when the running mode is saved again (a fan curve edit) or put back after a firmware curve read (opening the fan page). The startup notice about a refused performance mode goes away only when that mode really lands
- CPU temperature target: the 10 s before the next rung of the ladder are now counted from the moment every power limit is at its floor, as documented, not from the first hot second; a user change of governor, boost or EPP no longer races the fallback controller
- GPU: an NVIDIA eGPU no longer counts as the built-in dGPU (entering Eco leaves it on the bus, and it no longer hides a built-in dGPU that did not come back). Choosing Standard again looks for a dGPU that came back too late. A process name that is not valid UTF-8 no longer stops Eco with an error. On a laptop with only an integrated GPU, a saved Eco or Optimized mode falls back to Standard again even when the NVIDIA driver is installed. The NVIDIA driver is also found after a kernel update before the reboot
- In-app update: an HTTPS proxy with a user name and password is handed to the root helper on its input instead of its command line, where every local user could read it. A proxy without `http://`, with an upper-case scheme or with an IPv6 address now works; one Boreas cannot use (SOCKS) is reported before the password is asked instead of being dropped silently
- The window reads the display (`kscreen-doctor`) in the background, so a slow KScreen at login no longer freezes it, and an unexpected answer no longer stops it from starting. "Enable Boreas" no longer fails with a timeout when the password dialog stays open for two minutes. Some setup and update error messages were not translated
- `uninstall.sh` and `tools/install-reviewed.sh` refuse a copy a package owns before they change anything. The packages depend on `qt6-svg`, which the window needs for its icons; `install.sh` names the missing package. The language file is readable for every user even when the installer runs with a strict umask
- Third-party notices: the keyboard protocol is credited to asusctl, the question that tells the keyboard kinds apart to G-Helper, and the CPU temperature command of the experimental SMU bridge to RyzenAdj

## 0.1.7

- **Security:** a mode's power limit tables accepted any name, and the service used that name as part of the path of a file it writes as root. A member of the `wheel` group could reach files outside the attribute folders with `../`, and the stored entry was applied again at every boot. Only the known limit names are accepted now, and unknown names in a stored config are dropped at load
- **Fan safety floor:** the floor followed each point's own temperature, so a curve with every point under 70 °C switched it off. The last point now carries the floor of 100 °C (36 %), because the firmware holds its speed for everything above it. A saved custom curve whose last point is lower is raised to 36 % the next time it is written, and the editor no longer lets that point go lower; the built-in curves are not affected. Fan percentages are rounded up on the way to the hardware, so 12 % no longer arrives as 11 %
- The CPU temperature target keeps working while the service is busy: once a GPU switch or a hung `systemctl` has held the service for 3 s, a fallback steps the controller every second until it is free again (ROG Strix G713PV, service held for 14 s under full load: the sustained limit used to stay at 100 W, now it comes down to 40 W). While the CPU is over the target, a limit that sits below the configured floor is no longer raised to the floor. A power limit that cannot be read stops the controller with an error instead of an exception every second. A mode change whose profile write fails no longer leaves the running mode without its target
- Fan curves: reading a firmware curve switches every custom curve off, including one written a moment earlier, so all firmware curves a mode needs are now read before any curve is written. A curve with an unreadable point is treated as unavailable instead of reading that point as 0 %
- GPU helper (eBPF): a failed `nvidia-powerd` restore is tried once per fault instead of on every status poll and no longer replaces the fault in the message; a `systemctl` timeout during the restore no longer breaks the status; a rejected Standard request still brings `nvidia-powerd` back; a helper that died on its own is cleaned up
- Dragging the charge limit or keyboard brightness slider writes once, when the drag ends. It used to write to the service on every step
- Fans & Power: "Saved" is not shown for a change made while a request was on its way; the page follows a mode change made elsewhere (tray, Fn key) unless it has unsaved edits, so Apply does not quietly switch back; the previous mode's curve is not saved while the selected mode's default curve is still loading; points lying on top of each other can be pulled apart again
- The refresh-rate row no longer stays hidden when the app starts with the session before KScreen answers; the app starts when `~/.cache/boreas` is not writable; a USB-C PD charger counts as AC
- `boreasctl` and the UI show a message instead of a traceback when the service times out, resets the connection or stops in the middle of its answer. A connection that sends nothing is closed after 30 s. A stop during the boot application also gives the power limits back
- In-app update: an installer that fails with exit status 126 or 127 is reported as a failure instead of "cancelled" or "not authorized"
- `install.sh`: a failed helper download is a warning and the rest is installed (a checksum mismatch still aborts with nothing installed); the units it masks are recorded with their previous state and `uninstall.sh` restores only those; it warns when the `wheel` group is missing or the installing user is not in it; `DESTDIR` without `--files-only` and a plain `http://` helper address are refused
- `boreasd.service` runs with `NoNewPrivileges`, `ProtectHome` and `PrivateTmp`
- README: the `wheel` requirement, and a working command to hand the power profile back to the desktop
- CPU temperature target, **fast path**: with the power limits at their floor and the CPU 5 °C or more above the target for 3 s, the controller now goes through the whole escalation ladder at once, up to and including `cpufreq/boost` off, instead of one rung every 15 s after a 10 s grace. On machines where the firmware does not let the sustained limit bind (ROG Strix G713PV: 30 W requested, 50 to 60 W drawn, 93 °C for 25 s) the overshoot now ends a few seconds after the limits reach their floor
- A rung that is needed again within 60 s of its release is held longer the next time: the hold after an escalation doubles per relapse from 20 s up to 320 s and goes back to 20 s after a calm minute, a new target or floor, or a mode change. Before, a sustained load switched boost off and on again about every 40 s
- The service starts on machines with a GPU MUX but no `dgpu_disable`: Standard asked for a write that does not exist there, which stopped `boreasd` at every boot and kept such a machine out of Ultimate. Any other GPU failure at boot (a hung `systemctl`, an unsupported Eco) is now a notice instead of a stopped service, and a postponed Eco that fails for a reason other than a busy dGPU is reported once instead of raising every 15 s
- A damaged or hand-edited `/etc/boreas/config.json` can no longer stop the service: every stored value is checked at load and a wrong one falls back to its default. A file that cannot be read at all is kept as `config.json.corrupt`, a notice says so, and the service starts from what the hardware currently holds instead of silently resetting. The file is flushed to disk before it replaces the old one
- Values sent to the service are checked before they are stored: a malformed mode update is refused instead of being saved and breaking the next boot, an unknown mode number is an error instead of quietly editing Balanced, and a charge limit or keyboard level outside the range is stored as the value that was written. `boreasctl` answers a non-numeric argument with a message instead of a traceback
- UI: a choice whose request failed while the service was unreachable no longer stays selected forever; the tray menu's check marks follow the real mode after a click; cancelling the Ultimate question before the first status no longer raises an error; on Fans & Power a slow reply for a mode you already left is dropped and Apply waits for the selected mode's values, so one mode's settings cannot be saved onto another; a failed rename or delete shows its error
- The raw PCI SMU transport (`ryzen.Smu`, `ryzen.CpuTemp`) is gone from the code. The service had not used it since the temperature target moved to the ASUS power limits and the opt-in kernel bridge; it only remained as reference code with root-level PCI config writes in it
- In-app update follows **published releases**: the check asks GitHub for the latest release and offers it only once the GPU helper is attached, and **Update** installs the source of that release's tag. It used to read the version from `main` and install whatever `main` held, so between a version bump and the end of the release build the update failed because the helper was not there yet, and afterwards the Python code could be newer than the helper it was installed with
- Opening Fans & Power no longer applies the whole mode again every time. Reading a fan's firmware curve makes the kernel reset every custom curve and the power limits, so the mode has to be put back afterwards: fans rewritten, limits rewritten, the temperature target stopped and started, once per fan without a saved curve. The firmware curve is now read once per profile and fan and remembered until the service restarts, so only the first opening does this
- `boreasd` gets 90 s to start instead of 30: waiting for `asus-wmi` can take 20 s and the GPU mode change at boot can take longer than the rest, and a start that systemd cut off left the dGPU half switched
- Errors from the eBPF GPU engine and the backend switch are translated, and the engine's fault line on the GPU card too. Qt's own dialog texts (Yes/No, OK/Cancel, the colour picker) follow the app language where Qt ships a catalog (not Hindi). In Arabic the mode buttons on Fans & Power fill from the right and the switches are mirrored
- `uninstall.sh` says it needs root instead of failing halfway; `install.sh` stops on Ctrl+C instead of carrying on after cleaning up its download; a request line over 1 MB is refused by the service
- In-app update: the folder the new version is downloaded to stays private to your user until the installer has run. It was readable by everyone, and with a permissive umask another local user could have changed the files before they were installed as root

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
