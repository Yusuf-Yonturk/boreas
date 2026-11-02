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

Each watcher iteration samples temperature, with at least one second between
adjustments. At target minus 1 C or hotter, each power cap decreases by 2 to 10 W,
bounded by its advertised minimum. At target minus 4 C or cooler for five seconds,
caps recover by 1 W. Between these bands caps hold. This hysteresis and slow
recovery reduce oscillation; it is not a calibrated model of every laptop.

Boost limits can have higher minimum values than SPL. Once all available caps
reach their minimum and temperature remains above target, status is `limited`.
GPU heat, ambient temperature, firmware and blocking daemon operations can prevent
the target from being met. The loop is not real-time and is not a hardware thermal
protection mechanism. Existing firmware protection remains responsible for that.

## Ownership and lifecycle

The controller compares requested caps with sysfs on each iteration. Unexpected
changes stop the loop rather than fighting another service. Sensor loss, changed
capabilities, rejected writes and readback mismatches also latch an error. Partial
writes are tracked and remaining lowered caps are retained after a failure.
Default or an explicit mode reapply attempts restoration; failed restores retain
their recovery records. A restart after a crash cannot recover in-memory baseline
values, so a fresh profile/firmware reset may be required.

Default, internal mode changes and graceful daemon exit restore values only when
they still match this controller's last request. An externally changed profile
discards previous ownership so old values cannot overwrite a new profile. Resume
uses the existing mode reapply path. Firmware's delayed changes within a profile
can still stop feedback with an external-change error; there is no silent retry.

`cpu_temp` status includes `available`, `target`, `state` (`off`, `active`,
`limited`, `error`), `error`, `ceilings_w`, `requested_w`, `control` and
`hard_limit: false`. With `power-feedback`, `applied` stays null: no CPU
temperature limit has been written. With the opt-in `smu-bridge`, `applied` is the
last 0x3F argument the SMU acknowledged; that is not proof of physical behavior.
Sysfs readback may be a driver cache, so it is not proof of physical power or
temperature behavior either.

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
