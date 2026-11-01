"""Updates: checks the version on GitHub and, on request, downloads and installs the latest one.

The check only downloads boreas/__init__.py; nothing about the system is sent. Installing
happens only when the user presses "Update": the source archive is downloaded to a temporary
folder and install.sh is run with pkexec, which asks for the administrator password.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from . import __version__

REPO = "Yusuf-Yonturk/boreas"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/main/boreas/__init__.py"
TARBALL_URL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/main"
CHANGELOG_URL = f"https://github.com/{REPO}/blob/main/CHANGELOG.md"
PKEXEC_CANCEL = 126  # the user closed the password dialog
PKEXEC_DENIED = 127  # not authorized: no password dialog (polkit agent) or permission denied


class UpdateCancelled(Exception):
    pass


class NoAuthorization(Exception):
    pass


_RX = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)


def parse_version(text: str) -> str | None:
    m = _RX.search(text or "")
    return m.group(1).strip() if m else None


def version_key(v: str) -> tuple:
    """'0.10.0' → (0, 10); trailing zeros are dropped, non-numeric parts count as 0."""
    parts = []
    for p in re.split(r"[.+-]", str(v).strip().lstrip("vV")):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(remote: str | None, local: str = __version__) -> bool:
    return bool(remote) and version_key(remote) > version_key(local)


def fetch_latest(timeout: float = 5.0) -> str | None:
    req = urllib.request.Request(VERSION_URL, headers={"User-Agent": f"boreas/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_version(r.read(65536).decode("utf-8", "replace"))


def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    """Regular files and folders only; absolute paths and paths leading outside are rejected."""
    root = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if not (m.isfile() or m.isdir()) or os.path.isabs(m.name) or \
                os.path.commonpath([root, target]) != root:
            raise ValueError(f"Unsafe file in the update archive: {m.name}")
    for m in tar.getmembers():
        tar.extract(m, dest, set_attrs=False)
        if m.isfile() and m.mode & 0o111:  # keep install.sh etc. executable
            os.chmod(os.path.join(dest, m.name), 0o755)


def download_source(dest: str, timeout: float = 60.0) -> str:
    """Downloads and extracts the latest source archive; returns the project root folder."""
    req = urllib.request.Request(TARBALL_URL, headers={"User-Agent": f"boreas/{__version__}"})
    path = os.path.join(dest, "boreas.tar.gz")
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    with tarfile.open(path, "r:gz") as tar:
        _safe_extract(tar, dest)
    roots = [d for d in os.listdir(dest) if os.path.isfile(os.path.join(dest, d, "install.sh"))]
    if len(roots) != 1:
        raise ValueError("The update archive does not contain install.sh")
    return os.path.join(dest, roots[0])


def source_version(root: str) -> str | None:
    with open(os.path.join(root, "boreas", "__init__.py"), encoding="utf-8") as f:
        return parse_version(f.read())


def run_update(expected: str | None = None) -> str:
    """Downloads and installs with pkexec; returns the installed version. UpdateCancelled if the password dialog is closed."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise RuntimeError("pkexec not found. Update manually: git pull && sudo ./install.sh")
    tmp = tempfile.mkdtemp(prefix="boreas-update-")
    try:
        os.chmod(tmp, 0o755)  # root must be able to read it too
        root = download_source(tmp)
        got = source_version(root)
        if not got or (expected and is_newer(expected, got)):
            raise ValueError(f"Unexpected version in the update archive: {got}")
        r = subprocess.run([pkexec, "/bin/sh", os.path.join(root, "install.sh")],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        if r.returncode == PKEXEC_CANCEL:
            raise UpdateCancelled()
        if r.returncode == PKEXEC_DENIED:
            raise NoAuthorization()
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"exit code {r.returncode}"]
            raise RuntimeError(tail[0])
        return got
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
