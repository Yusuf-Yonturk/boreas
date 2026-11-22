#!/bin/sh
# smu-check.sh [TABLE_DIR]: can the experimental CPU temperature ceiling (docs/cpu-smu.md) work on
# this laptop? Reads the CPU family and the BIOS's ACPI tables, changes nothing and needs no
# Boreas install. The tables are root-only, so run it with sudo:
#     sudo sh smu-check.sh
# TABLE_DIR (default /sys/firmware/acpi/tables) can point at a copy of the tables instead.
TABLES=${1:-/sys/firmware/acpi/tables}
if ! command -v python3 >/dev/null 2>&1; then
    echo "smu-check.sh needs python3" >&2
    exit 1
fi
exec python3 -I - "$TABLES" <<'EOF'
import glob, os, re, sys

tables = sys.argv[1]

def rd(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return "?"

# (cpu family, model) -> (family, MP1 command address); must match boreas/ryzen.py and
# smu/boreas_smu.c. VERIFIED: run on hardware with Boreas's module.
RAVEN = ("Raven Ridge / Picasso / Dali", 0x03B10528)
RENOIR = ("Renoir / Lucienne / Cezanne", 0x03B10528)
MOBILE = ("Van Gogh / Mendocino / Rembrandt / Phoenix / Hawk Point", 0x03B10528)
DRAGON = ("Dragon Range / Raphael", 0x03B10530)
FIRE = ("Fire Range / Granite Ridge", 0x03B10530)
STRIX = ("Strix Point / Krackan Point / Strix Halo", 0x03B10928)
FAMILIES = {}
FAMILIES.update({(0x17, m): RAVEN for m in (0x11, 0x18, 0x20)})
FAMILIES.update({(0x17, m): RENOIR for m in (0x60, 0x68)})
FAMILIES[(0x19, 0x50)] = RENOIR
FAMILIES.update({(0x17, m): MOBILE for m in (0x90, 0x91, 0xA0)})
FAMILIES.update({(0x19, m): MOBILE for m in (0x40, 0x44, 0x74, 0x75, 0x78, 0x7C)})
FAMILIES[(0x19, 0x61)] = DRAGON
FAMILIES[(0x1A, 0x44)] = FIRE
FAMILIES.update({(0x1A, m): STRIX for m in (0x20, 0x24, 0x60, 0x68, 0x70)})
VERIFIED = {DRAGON}

info = {}
for line in open("/proc/cpuinfo"):
    if not line.strip():
        break
    k, _, v = line.partition(":")
    info[k.strip()] = v.strip()
amd = info.get("vendor_id") == "AuthenticAMD"
try:
    fam_model = (int(info["cpu family"]), int(info["model"]))
except (KeyError, ValueError):
    fam_model = None
family = FAMILIES.get(fam_model) if amd else None

files = [f for f in sorted(glob.glob(os.path.join(tables, "*")))
         if os.path.basename(f).startswith(("DSDT", "SSDT"))]
readable, unreadable = [], 0
for f in files:
    try:
        with open(f, "rb") as fh:
            readable.append((os.path.basename(f), fh.read()))
    except OSError:
        unreadable += 1

# MutexOp (0x5B 0x01), an optional path prefix, then the name AM00
mutex = [n for n, d in readable if re.search(rb"\x5b\x01[\\^._A-Z0-9\x2e\x2f]{0,10}AM00", d)]
mp1 = []
if family:
    addr = family[1].to_bytes(4, "little")
    mp1 = [n for n, d in readable if addr in d]

print("## Boreas SMU check")
print()
print("| | |")
print("|---|---|")
print(f"| Laptop | {rd('/sys/class/dmi/id/product_name')} |")
print(f"| BIOS | {rd('/sys/class/dmi/id/bios_version')} |")
print(f"| CPU | {info.get('model name', '?')} |")
print(f"| Family / model | " + (f"0x{fam_model[0]:x} / 0x{fam_model[1]:x}" if fam_model else "?") + " |")
print(f"| Ryzen family | {family[0] if family else 'not in the list'} |")
print(f"| Kernel | {os.uname().release} |")
if readable:
    print(f"| AM00 lock | {', '.join(mutex) + ' (scope not checked)' if mutex else 'not found'} |")
    if family:
        print(f"| MP1 mailbox 0x{family[1]:08X} | {', '.join(mp1) if mp1 else 'not found'} |")
else:
    print("| AM00 lock | tables not readable, run with sudo |")
print()

if not amd or not family:
    verdict = ("No: this CPU is not one the module knows. Boreas keeps its software target "
               "(power-feedback).")
elif not readable:
    verdict = "Unknown: the ACPI tables could not be read. Run it again with sudo."
elif not mutex:
    verdict = ("No: the BIOS has no AM00 lock, so the module refuses to load. Boreas keeps its "
               "software target (power-feedback).")
elif family in VERIFIED:
    verdict = ("Yes: CPU family and an AM00 lock found; Boreas switches the ceiling on by default, "
               "and this family has been measured with it. The module itself confirms the lock "
               "is \\_SB.AM00 when it loads.")
else:
    verdict = ("Likely yes: CPU family and an AM00 lock found; Boreas switches the ceiling on by "
               "default. The module itself confirms the lock is \\_SB.AM00 when it loads. This "
               "family has not been measured with Boreas yet, so a report from tools/smu-report.sh "
               "is welcome.")
print(f"**Result:** {verdict}")
if unreadable and readable:
    print(f"\n({unreadable} table(s) could not be read.)")
EOF
