"""Updates: checks the latest release on GitHub and, on request, downloads and installs it.

The check only asks GitHub for the latest published release; nothing about the system is sent.
A version is offered once its release carries the GPU helper, because install.sh downloads the
helper from that release. Installing happens only when the user presses "Update": the source
archive of that release's tag is downloaded to a temporary folder and install.sh is run with
pkexec, which asks for the administrator password.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from . import __version__

REPO = "Yusuf-Yonturk/boreas"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# what install.sh downloads from the release; a release without them cannot be installed yet
HELPER_ASSETS = {"boreas-gpu-engine", "boreas-gpu-engine.sha256"}
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


def tarball_url(version: str) -> str:
    """The source archive of a release tag: the same commit CI built that release's helper from."""
    if not re.fullmatch(r"\d+(\.\d+)*", version or ""):
        raise ValueError(f"Not a release version: {version}")
    return f"https://codeload.github.com/{REPO}/tar.gz/refs/tags/v{version}"


def parse_release(text: str) -> str | None:
    """The version of a published release that carries the GPU helper; None for anything else."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    assets = data.get("assets")
    names = {a.get("name") for a in assets if isinstance(a, dict)} if isinstance(assets, list) else set()
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+(\.\d+)*", tag) or not HELPER_ASSETS <= names:
        return None
    return tag[1:]


def fetch_latest(timeout: float = 5.0) -> str | None:
    req = urllib.request.Request(RELEASE_URL, headers={"User-Agent": f"boreas/{__version__}",
                                                       "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_release(r.read(1024 * 1024).decode("utf-8", "replace"))


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


def download_source(dest: str, version: str, timeout: float = 60.0) -> str:
    """Downloads and extracts the source archive of a release; returns the project root folder."""
    req = urllib.request.Request(tarball_url(version), headers={"User-Agent": f"boreas/{__version__}"})
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


def run_update(expected: str) -> str:
    """Downloads and installs the release `expected` with pkexec; returns the installed version.
    UpdateCancelled if the password dialog is closed."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise RuntimeError("pkexec not found. Update manually: git pull && sudo ./install.sh")
    # mkdtemp's 0700 stays: root reads it anyway, and no other user may touch what root installs
    tmp = tempfile.mkdtemp(prefix="boreas-update-")
    try:
        root = download_source(tmp, expected)
        got = source_version(root)
        if not got or version_key(got) != version_key(expected):
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
