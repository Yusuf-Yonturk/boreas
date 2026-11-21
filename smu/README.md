# boreas_smu

Single-purpose GPL-2.0-only x86_64 kernel module for the AMD Ryzen SMU MP1
temperature ceiling. Only explicitly supported AMD family/model pairs are
accepted. Every SMN access uses the kernel AMD API, node 0, under the BIOS
`\_SB.AM00` ACPI mutex and a local mutex. Without that ACPI mutex, loading fails.
Acquiring it has a 500 ms timeout; each mailbox poll has a 200 ms timeout.
Initialization logs the initial response and queries the SMU version. Unloading
only deregisters the device and does not change the ceiling.

## ABI version 2

`/dev/boreas-smu` has mode `0600`; every ioctl requires `CAP_SYS_ADMIN`.
The request is 32 bytes, with little-endian 32-bit fields on x86:

```c
#include <linux/types.h>
#include <sys/ioctl.h>

struct boreas_smu_req {
    __u32 abi;
    __u32 op;
    __u32 arg;
    __u32 status;
    __u32 result;
    __u32 reserved[3];
};
#define BOREAS_SMU_IOC _IOWR('B', 0x74, struct boreas_smu_req) /* 0xC0204274 */
```

Set `abi = 2` and all `reserved` fields to zero. Input `status` and `result`
are ignored. The layout also supports 32-bit callers on an x86_64 kernel.

| op | arg | SMU command |
| --- | --- | --- |
| 1, GET_VERSION | 0 | `0x02`, argument0 = 1 |
| 2, SET_TCTL | 75 through 96 degrees C, inclusive | CPU group's tctl command, argument0 = arg |

An ioctl returning 0 fills `status` and `result` (SMU argument0). Check `status`:
`0x01` OK, `0xFF` failed, `0xFE` unknown command, `0xFD` rejected (prerequisite),
`0xFC` busy. A non-OK SMU reply is still returned as data, not errno.
Version decoding: `major = (result >> 16) & 0xff`,
`minor = (result >> 8) & 0xff`, `patch = result & 0xff`.

Transport errors are `EBUSY` (ACPI lock), `ETIMEDOUT` (mailbox poll), and `EIO`
(SMN access). Validation/access errors are `EINVAL`, `EPERM`, `ENOTTY` (unknown
ioctl), and `EFAULT` (user copy). An errno after command submission does not
prove that the SMU left the ceiling unchanged.

## Build

From the repository root, with matching kernel headers and compiler installed:

```sh
make -C smu
make -C smu clean
make -C smu KDIR=/usr/lib/modules/6.18.60-2-cachyos-lts/build
make -C smu clean
make -C smu KDIR=/usr/lib/modules/7.3.4-1-cachyos/build LLVM=1
modinfo smu/boreas_smu.ko | grep -E '^(license|description)'
make -C smu clean
```

For a different installed kernel, set `KDIR=/lib/modules/<release>/build`;
use `LLVM=1` for a Clang kernel. `dkms.conf` describes package `boreas-smu`,
version `1`, restricted to x86_64 kernels with `CONFIG_ACPI`.
