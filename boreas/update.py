"""Updates: checks the latest release on GitHub and, on request, downloads and installs it.

The check only asks GitHub for the latest published release; nothing about the system is sent.
A version is offered once its release carries the GPU helper, because install.sh downloads the
helper from that release. Installing happens only when the user presses "Update": pkexec asks for
the administrator password and starts the installed helper (data/boreas-update), which downloads
the source archive of that release's tag into a folder only root can reach and runs its install.sh.
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
# Written by a distribution package (install.sh, BOREAS_PACKAGED): the package manager updates
# that copy, and installing over it from here would overwrite files the package owns.
PACKAGED_MARK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaged")
PKEXEC_CANCEL = 126  # the user closed the password dialog
PKEXEC_DENIED = 127  # not authorized: no password dialog (polkit agent) or permission denied


class UpdateCancelled(Exception):
    pass


class NoAuthorization(Exception):
    pass


_RX = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)


def packaged() -> str | None:
    """The name of the package this copy belongs to; None for a copy installed by install.sh."""
    try:
        with open(PACKAGED_MARK) as f:
            return f.read().strip() or "package"
    except OSError:
        return None


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
    if not re.fullmatch(r"\d+(\.\d+)*", version or "", re.ASCII):
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


# Started by pkexec. It is the installed copy (root's files, under root's folders), so nothing the
# user account can write runs as root; it downloads the release itself into root's own folder.
HELPER = "/usr/lib/boreas/boreas-update"
PYTHON = "/usr/bin/python3"
PROXY_RX = re.compile(r"https?://[A-Za-z0-9._:@%-]+(:\d+)?/?", re.ASCII)


def _root_only(path: str) -> bool:
    """True when the file and every folder above it belong to root and only root can change them."""
    p = os.path.realpath(path)
    while True:
        try:
            st = os.stat(p)
        except OSError:
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        parent = os.path.dirname(p)
        if parent == p:
            return True
        p = parent


def _root_tree(folder: str) -> bool:
    """_root_only for a folder and everything in it: the code the helper imports."""
    if not _root_only(folder):
        return False
    for top, dirs, files in os.walk(os.path.realpath(folder)):
        for name in dirs + files:
            try:
                st = os.lstat(os.path.join(top, name))
            except OSError:
                return False
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
    return True


def _proxy_args() -> list[str]:
    """pkexec clears the environment, so the user's HTTPS proxy is handed over as an argument.
    It only carries the download: the archive still comes over TLS from GitHub."""
    url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
    return ["--proxy", url] if PROXY_RX.fullmatch(url) else []


def run_update(expected: str) -> str:
    """Installs the release `expected` through pkexec and the root helper; returns that version.
    UpdateCancelled if the password dialog is closed."""
    owner = packaged()
    if owner:
        raise RuntimeError(f"Boreas was installed as a package ({owner}); update it with your package manager.")
    tarball_url(expected)  # ValueError for anything that is not a release version, before any password
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise RuntimeError("pkexec not found. Update manually: git pull && sudo ./install.sh")
    if not (_root_only(HELPER) and _root_only(PYTHON)
            and _root_tree(os.path.join(os.path.dirname(HELPER), "boreas"))):
        raise RuntimeError("The update helper is missing or not protected. "
                           "Update manually: git pull && sudo ./install.sh")
    # -I: no user site folder, no PYTHONPATH and no current folder on the import path.
    # 126 and 127 mean "cancelled" and "not authorized" only when pkexec itself says so:
    # the helper folds its own failures into 1.
    # No timeout here: once the password is given the process is root's and cannot be killed from
    # this side, and the download and install.sh carry their own time limits.
    r = subprocess.run([pkexec, PYTHON, "-I", HELPER, *_proxy_args(), expected],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if r.returncode == PKEXEC_CANCEL:
        raise UpdateCancelled()
    if r.returncode == PKEXEC_DENIED:
        raise NoAuthorization()
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"exit code {r.returncode}"]
        raise RuntimeError(tail[0])
    return expected


def install_release(expected: str) -> None:
    """Runs as root (the helper): downloads the release `expected` into a folder only root can
    reach, checks its version and runs its install.sh."""
    # mkdtemp's 0700 stays: the folder is root's, and no other user may touch what root installs
    tmp = tempfile.mkdtemp(prefix="boreas-update-")
    try:
        root = download_source(tmp, expected)
        got = source_version(root)
        if not got or version_key(got) != version_key(expected):
            raise ValueError(f"Unexpected version in the update archive: {got}")
        r = subprocess.run(["/bin/sh", os.path.join(root, "install.sh")], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"exit code {r.returncode}"]
            raise RuntimeError(tail[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
