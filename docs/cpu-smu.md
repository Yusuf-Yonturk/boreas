# CPU SMU access failures (historical backend)

The active daemon uses `thermal.Controller`: ASUS power sysfs feedback by default,
with an explicit opt-in kernel SMU bridge restricted to G713PV / G713PV.336.
The raw userspace SMU module remains for reference and isolated tests; it is not
selected or used as a fallback. See [active controller, gating and validation
limits](cpu-temperature.md).

## Historical raw PCI transport

The historical Dragon Range / Fire Range backend used the root bridge PCI config
SMN index/data window at 0xB8/0xBC. The Python lock serializes Boreas threads,
not other processes, kernel drivers or firmware. The transport guards described
below belong to that historical backend, not the default controller.

Each data read/write checks the selected SMN address before and after access.
A changed selector, short transfer, PCI I/O error, invalid mailbox response or
command timeout disables further SMU access for that service process. The daemon
stops periodic temperature writes, reports an error, and omits the unavailable
CPU temperature control from status. Other hardware controls remain available.
There is no automatic mailbox reset or retry after a transport fault. A service
restart creates a new instance; restarting does not fix the underlying conflict.

The selector checks are detection, not atomic synchronization. An external actor
can still change the selector between the check and the data access, including
changing it away and back before the next check. A completed write cannot be
undone. This guard reduces further exposure after detected interference; it does
not prove that all writes reached the intended register.

An acknowledged temperature command is not proof of the physical temperature
ceiling. A failed transport also does not reset any previously acknowledged
firmware setting to its default. The stored user target is preserved.

On the tested G713PV / G713PV.336, passive tracing identified AML method
`\M249` and field `\M249.VAR1` writing the same SMN selector through
SystemMemory at physical 0xF00000B8. The decoded SSDT9 saves the selector,
accesses a bank and restores the saved selector under `\_SB.AM00` (or M253
when AM00 is absent). Boreas's Python lock does not participate in that mutex.
A boot trace captured an ACPI selector write between Boreas's selection and
subsequent selector validation. This explains an observed interference path;
it does not attribute every historical 0x20212210 result or exclude other actors.

## Current kernel bridge scope

The kernel bridge is now integrated behind explicit opt-in and the device's
`enable_hw` gate. It is restricted to the documented G713PV / G713PV.336
configuration; it is not a general ASUS SMU solution. Recorded device probes and
remaining validation limits are listed in [the active controller document](cpu-temperature.md).
Do not retry the historical raw userspace sequence or invoke an unbounded firmware
mailbox method as an assumed fix. Kernel-build and mock-IO tests cannot establish
real firmware synchronization, SMM behavior or physical temperature enforcement.
