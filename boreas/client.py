"""boreasd socket client and the boreasctl command line."""

from __future__ import annotations

import json
import socket
import sys

from .daemon import SOCKET


class DaemonError(Exception):
    pass


def call(cmd: str, timeout: float = 30, **kw) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(SOCKET)
        except OSError as e:
            raise DaemonError(f"Could not connect to boreasd ({e}). Is the service running?") from e
        s.sendall((json.dumps({"cmd": cmd, **kw}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf or b"{}")
    if not resp.pop("ok", False):
        raise DaemonError(resp.get("error", "unknown error"))
    return resp


USAGE = """usage: boreasctl COMMAND
  status                  status (JSON)
  mode silent|balanced|turbo|next|prev|<id>
  gpu eco|standard|optimized|ultimate
  gpu-backend firmware|ebpf
  charge 40..100          charge limit
  kbd 0..3                keyboard backlight
  cputemp 75..95|default  CPU temperature target of the active mode
  reapply [--boot]        re-apply all settings
"""

NAMES = {"sessiz": 2, "silent": 2, "quiet": 2, "dengeli": 0, "balanced": 0, "turbo": 1, "performance": 1}


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    try:
        if not a or a[0] in ("-h", "--help"):
            print(USAGE)
            return 0
        if a[0] == "status":
            print(json.dumps(call("status"), indent=2, ensure_ascii=False))
        elif a[0] == "mode" and len(a) > 1:
            if a[1] in ("next", "prev"):
                r = call("cycle_mode", back=a[1] == "prev")
                print(r["mode"])
            else:
                call("set_mode", mode=NAMES.get(a[1].lower(), a[1]))
        elif a[0] == "gpu" and len(a) > 1:
            r = call("set_gpu", target=a[1], timeout=60)
            if r.get("reboot"):
                print("The GPU mode will take effect after a reboot.")
            if r.get("deferred"):
                print("dGPU in use: " + ", ".join(r["busy"]))
        elif a[0] == "gpu-backend" and len(a) > 1:
            r = call("set_gpu_backend", backend=a[1].lower(), timeout=90)
            print(f"GPU backend: {r.get('backend')}")
        elif a[0] == "charge" and len(a) > 1:
            call("set_charge_limit", value=int(a[1]))
        elif a[0] == "kbd" and len(a) > 1:
            call("set_kbd", brightness=int(a[1]))
        elif a[0] == "cputemp" and len(a) > 1:
            mode = call("status")["mode"]
            temp = None if a[1] == "default" else int(a[1])
            call("update_mode", mode=mode, data={"cpu_temp": temp})
        elif a[0] == "reapply":
            call("reapply", boot="--boot" in a)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except DaemonError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
