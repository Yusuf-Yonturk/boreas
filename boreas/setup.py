"""First use after a package install: hands the laptop controls to Boreas when the user agrees.

A distribution package only copies files. Masking asusd / supergfxd / power-profiles-daemon and
starting boreasd is boreas-setup's job, and it runs only once the user has said yes: from a
terminal (sudo boreas-setup enable) or from the window, which asks first and then calls this.
"""

from __future__ import annotations

import os
import shutil
import subprocess

SETUP = "/usr/bin/boreas-setup"
SWITCHED_OFF = "/etc/boreas/switched-off"  # boreas-setup disable: the user gave the controls back
MASKED = "/etc/boreas/masked-units"        # boreas-setup's record: it counts the mark only next to it
PKEXEC_CANCEL = 126  # the user closed the password dialog
PKEXEC_DENIED = 127  # not authorized: no password dialog (polkit agent) or permission denied


class SetupCancelled(Exception):
    pass


class NoAuthorization(Exception):
    pass


def needed() -> bool:
    """Whether Boreas is installed but was never switched on: the service is not enabled, and
    the user did not switch it off on purpose (boreas-setup disable)."""
    if not os.path.isfile(SETUP) or (os.path.exists(SWITCHED_OFF) and os.path.exists(MASKED)):
        return False
    try:
        r = subprocess.run(["systemctl", "is-enabled", "boreasd.service"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() in ("disabled", "indirect", "static")


def enable() -> str:
    """Runs boreas-setup enable with pkexec; returns the warnings it printed (the wheel group)."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise RuntimeError("pkexec not found. Run in a terminal: sudo boreas-setup enable")
    # Authentication can take any amount of time; the root child cannot be killed by this user.
    r = subprocess.run([pkexec, SETUP, "enable"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    if r.returncode == PKEXEC_CANCEL:
        raise SetupCancelled()
    if r.returncode == PKEXEC_DENIED:
        raise NoAuthorization()
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f"boreas-setup exited with {r.returncode}")
    lines = r.stdout.splitlines()
    warn = [i for i, line in enumerate(lines) if line.startswith("Warning:")]
    return " ".join(x.strip() for x in lines[warn[0]:warn[0] + 2]) if warn else ""
