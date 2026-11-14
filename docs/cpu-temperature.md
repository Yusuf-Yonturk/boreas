# CPU temperature control

The daemon uses `boreas/thermal.py` (`thermal.Controller`). Existing per-mode
`cpu_temp` settings select a target (75 to 95 C); None or the UI's 96
sentinel disables control. 96 is not a claim about the CPU's factory limit.
There is no BIOS modification, raw PCI 0xB8/0xBC write or raw-access fallback.

## Backends and scope

- Default (`power-feedback`): Closed-loop ASUS power-attribute feedback
  (`ppt_pl1_spl`, `ppt_pl2_sppt`, `ppt_pl3_fppt`). Mainline Linux `asus-wmi`
  and `asus-armoury` do not expose an ASUS CPU temperature sysfs attribute
  for endpoint `0x0012009E` (and `DSTS(0x0012009E)` returns `ENODEV` on the
  tested G713PV / G713PV.336), so no unverified sysfs temperature attribute
  names are probed.
- Experimental (`smu-bridge`): Research prototype `/dev/boreas-smu-probe`
  (`ryzen.BridgeSmu`), currently restricted in kernel code to `ROG Strix
  G713PV_G713PV` / BIOS `G713PV.336` / family 19h model 61h. It is not a
  general ASUS SMU backend. Limited device probes are recorded below; they do
  not establish general hardware compatibility or physical thermal enforcement.
  Selection requires all of: `BOREAS_SMU_BRIDGE=1`, `/dev/boreas-smu-probe`, and sysfs
  `/sys/module/boreas_smu_probe/parameters/enable_hw` explicitly reading
  `Y` or `1` (missing `enable_hw` returns unavailable). When active, it sends
  `GET_SMU_VERSION` (0x02), `0x3F` (75..96 C), and a single 5 s post-profile
  reapply, restoring 96 C
  on internal stop. Acknowledged SMU replies do not prove conflict-free
  firmware synchronization or physical thermal enforcement (`hard_limit` stays
  `false`). Any bridge error latches for the service lifetime without falling
  back to raw PCI or power caps.

## Power-feedback capability and bounds

- Requires `ppt_pl1_spl`. Also controls exposed `ppt_pl2_sppt` and `ppt_pl3_fppt`.
- Every exposed control must report a positive minimum, maximum and current value
  within that range. Legacy nodes without ranges are not guessed.
- A readable CPU temperature is required before any feedback write.
- Power ceilings are captured after the mode's profile, fan and user power settings.
  Recovery never exceeds those ceilings. Existing user power controls remain usable.
- Models in the existing `fan_required` table use custom or factory fan curves.
  Failure to prepare required curves prevents thermal control.

## Feedback policy

The target is an integer 75 to 95 C; 96 is the UI's Default sentinel and means off.
Each watcher iteration samples the integer Tctl, with at least one second between
adjustments. With `e = temperature - target`:

| Band | Action |
|---|---|
| `e >= 1` (hot) | every cap decreases by `min(6, max(2, 2*e))` W per second, bounded below by its floor |
| `-2 <= e <= 0` (hold) | nothing changes |
| `e <= -3` (cool) | at stage 0 the caps recover by 2 W every 2 s, boost caps first, up to the captured ceilings; while a rung is engaged the caps stay down and the rung is released instead |

The floor of SPPT/FPPT is their advertised minimum. The floor of SPL is the
advertised minimum or the mode's **sustained power floor** (`cpu_temp_floor_w`),
whichever is higher, clamped to the captured ceiling. Once every cap sits at its
floor and the temperature is still above target, status is `limited`. The loop is
not real-time and is not a hardware thermal protection mechanism; firmware
protection remains responsible for that.

## Escalation ladder

The mode's own CPU policy (CPU Boost mode and power profile, written by the daemon
before the target starts) is the **baseline**. The controller never goes above it
and only steps below it, rung by rung, when the caps are at their floor:

- Governor other than `performance`: EPP notches below the baseline
  (`performance > balance_performance > balance_power > power`), then
  `cpufreq/boost` off when the baseline has boost on. A `Disabled` boost mode has
  no rungs: caps only.
- Governor `performance` (the kernel fixes the EPP there): boost off, then the
  governor falls back to the driver default together with EPP
  `balance_performance`, then the remaining EPP notches. Without a governor to
  come back to, only the boost rung exists.

Timing: the first rung needs 10 s hot at the floor (`ESCALATE_GRACE`) and 15 s
since the last rung change (`RUNG_DWELL`); a rung is released after 20 s of
continuous cool (`RELEASE_DWELL`) plus the same dwell, in reverse order.

Fast path: where the firmware does not let the caps bind, the floor does not cool
and only boost off does (measured on a G713PV: 30 W requested, 50 to 60 W drawn,
93 °C until the boost rung arrived 25 s later). With the caps at their floor and
the CPU 5 °C or more over the target (`FAST_OVER`) for 3 s without a break
(`FAST_GRACE`, counted at the floor), the controller skips both waits and applies
every rung up to and including boost off in one step; the log line says `fast`.
Ladders without a boost rung keep the gradual timing.

Relapse hold: an escalation within 60 s of a release (`RELAPSE_WINDOW`) means the
rung was released too early. The next release then also waits for a hold counted
from the escalation, doubled per relapse from 20 s up to 320 s
(`RELEASE_DWELL_MAX`). An escalation later than that window, a new target or floor
and every mode apply put it back to 20 s. The stage
is not persisted: every mode apply, AC change and resume starts at stage 0 with the
new baseline written by the daemon. A failed rung write is logged and that rung is
skipped; it is not a controller fault. `Default`, mode changes and daemon exit
restore the baseline policy exactly (governor, boost, EPP).

## Ownership and lifecycle

Each iteration compares the caps with sysfs. A value that moved inside the
controller's band (between the floor and the captured ceiling) is adopted and
logged (`re-synced`), which covers firmware rounding and the delayed throttle
policy rerun after a profile write. A cap the firmware raised above the ceiling is
re-asserted when it is a user power limit of the mode (`re-asserted`); more than
three re-assertions in 60 s stop the loop with "Power limits changed externally"
rather than fighting another service. A raised cap that is not a user limit
becomes the new ceiling (`re-captured`). Sensor loss, changed capabilities,
rejected writes and readback mismatches latch an error; lowered caps and the
current rung are retained after a failure (the cold side is the safe side).

Saving the mode again, Default or a mode reapply retries after a latched error and
clears it; only an `smu-bridge` fault needs an explicit reapply. Restoration writes
only values that still match the controller's last request; a cap another writer
changed is left alone and logged (`left ... changed externally`). An externally
changed profile discards previous ownership so old values cannot overwrite a new
profile. Resume uses the mode reapply path.

`cpu_temp` status includes `available`, `target`, `state` (`off`, `active`,
`limited`, `error`), `error`, `ceilings_w`, `requested_w`, `floor_w` (the
effective SPL floor), `stage`, `stage_max`, `release_dwell_s` (the current relapse
hold), `ladder`, `effective` (the policy the
controller last wrote), `baseline`, `control` and `hard_limit: false`. The top
level `cpu_policy` reports what sysfs says right now (governor, EPP, boost). With
`power-feedback`, `applied` stays null: no CPU temperature limit has been written.
With the opt-in `smu-bridge`, `applied` is the last 0x3F argument the SMU
acknowledged; that is not proof of physical behavior. Sysfs readback may be a
driver cache, so it is not proof of physical power or temperature behavior either.

## Observed on the G713PV (2026-11-04, on battery)

Dengeli: target 82 C, user PL1 40 W (= the floor, so the caps had no room), CPU
Boost `Max`, power profile `Performance`, `stress-ng --cpu 0` for 150 s, polled
every 2 s. The controller reported `limited` 9 s into the load (peak 91 C during
the first seconds), engaged stage 1 (boost off, `cpufreq/boost` read 0) 4 s later,
temperature fell to 77 to 81 C, and the rung was released after 20 s of cool
(`boost` read 1 again). The rest of the load ran at stage 0 between 80 and 82 C
with one momentary `limited`; sysfs governor/EPP/boost matched `effective` at
every sample, PL1 never went below 40 W, and the journal had the two `cpu target:
stage` lines and no `externally` line.

On AC the same evening (kernel 6.18.55 LTS, desktop apps adding about 1.3 cores of
background load, idle Tctl already 78 to 95 C): Dengeli with the default power
profile, CPU Boost `Max`, floor 35 W, `stress-ng --cpu 0` for 180 s. The ladder
stepped `balance_performance`, `balance_power`, `power`, boost off at 15 s
intervals; even at stage 4 the full load held 89 to 94 C, because SPPT/FPPT stay
at their 65 W firmware minimum. After the load the rungs were released in reverse
only once the CPU stayed cool for 20 s; the background spikes to 90 C (2 to 8 s
each) neither re-escalated nor blocked a release for long. sysfs matched
`effective` at every one of 165 samples and PL1 never went below 35 W. While at
stage 2, `boreasctl mode silent` left stage 0 with the Silent baseline in sysfs
(powersave, power, boost 0) and PL1 descending from Silent's own ceiling; `mode
balanced` came back at stage 0 and re-escalated within a minute; `cputemp default`
returned PL1 to 40 W and the policy to the baseline; no `externally` line and no
error in any transition. The AC unplug/plug, resume and game runs are still open.

## Validation

Run `python3 -m unittest discover -s tests -v`. Controller tests cover saturation,
hysteresis, rate limits, gradual recovery, sensor loss, outside writers, missing
ranges, partial failures, ignored writes, restoration and a synthetic thermal plant.
Daemon tests cover mode changes, explicit power ceilings, GUI/API target updates,
fan requirements and that the raw PCI `ryzen.CpuTemp` backend is never constructed.
Bridge tests use a fake ioctl and cover opt-in/`enable_hw` gating, the single 5 s
reapply, restore-on-stop and fault latching; they do not exercise the real module.
Existing isolated raw SMU tests remain as historical transport coverage, not the
active implementation.

No physical temperature test or installation is implied by the unit tests alone.

### Observed G713PV / G713PV.336 hardware probe

On a clean boot (without prior raw PCI `0xB8/0xBC` writes):

- Single `0x02` (`GET_SMU_VERSION`) ioctl with `enable_hw=1` returned `status=0x01`, `result=0x00546900` (`version=84.105.0`), followed by clean module unload.
- `ryzen.BridgeSmu` completed `0x02` version check, `0x3F` (`83 C`), and `0x3F` (`96 C` restore) with `status=0x01` and clean module unload.
- With `BOREAS_SMU_BRIDGE=1` and `enable_hw=Y`, `boreasd` activated `control="smu-bridge"`, completed the 5 s post-profile reapply without fault, and held CPU temperature under multi-core load at `78..79 C` when `target=78 C`, `80..81 C` when `target=80 C`, and `84..86 C` when `target=85 C` while ASUS PPT sysfs limits remained constant.
- Scope remains restricted to `ROG Strix G713PV_G713PV` / BIOS `G713PV.336`; SMM interactions and other BIOS/laptop models are not extrapolated from this test.

## Live boost/EPP smoke check, 2026-11-02

The current mode was briefly changed from Disabled to Balanced CPU boost.
The global boost flag read back as 1 and all exposed EPP nodes as `balance_power`.
The original Disabled setting was restored; the boost flag and every observed
EPP value exactly matched their initial values. No service notice was raised.
This checks boost/EPP application and restoration, not physical thermal ceiling
enforcement or load performance.
