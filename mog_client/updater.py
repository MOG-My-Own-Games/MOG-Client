"""Self-update from the latest GitHub release (AppImage and Windows exe builds).
Stdlib only. Enabled by the UPDATE_METHOD build flag (see version.py)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mog_client.version import UPDATE_METHOD, __version__

REPO = "MOG-My-Own-Games/MOG-Client"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
SUMS_NAME = "SHA256SUMS.txt"
ASSET_SUFFIX = {"appimage": ".appimage", "exe": ".exe"}
DISABLE_ENV = "MOG_NO_UPDATE_CHECK"
CHUNK = 1 << 20


class UpdateError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    asset_name: str
    asset_url: str
    sums_url: str | None
    page_url: str


def enabled() -> bool:
    return UPDATE_METHOD in ASSET_SUFFIX and __version__ != "dev" and not os.environ.get(DISABLE_ENV)


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", text)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def _open(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mog-client"}), timeout=30)


def _get_json(url: str) -> dict:
    with _open(url) as resp:
        return json.load(resp)


def check_for_update(
    fetch: Callable[[str], dict] = _get_json,
    current: str | None = None,
    method: str | None = None,
) -> UpdateInfo | None:
    """The latest published release if it is newer and has an asset for this build."""
    current = current or __version__
    suffix = ASSET_SUFFIX.get(method or UPDATE_METHOD)
    if suffix is None:
        return None
    release = fetch(LATEST_URL)
    tag = release.get("tag_name", "")
    if not is_newer(tag, current):
        return None
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    name = next((n for n in assets if n.lower().endswith(suffix)), None)
    if name is None:
        return None
    return UpdateInfo(tag.lstrip("v"), name, assets[name], assets.get(SUMS_NAME), release.get("html_url", ""))


def _expected_sha256(info: UpdateInfo) -> str:
    if not info.sums_url:
        raise UpdateError(f"The release has no {SUMS_NAME}, refusing to install unverified code")
    with _open(info.sums_url) as resp:
        for line in resp.read().decode().splitlines():
            digest, _, name = line.strip().partition("  ")
            if name.strip().lstrip("*") == info.asset_name:
                return digest.lower()
    raise UpdateError(f"{info.asset_name} is not listed in {SUMS_NAME}")


def download(info: UpdateInfo, dest: Path, progress: Callable[[int, int], None] | None = None) -> None:
    """Download the asset to `dest` and verify it against the release checksums."""
    expected = _expected_sha256(info)
    digest = hashlib.sha256()
    written = 0
    try:
        with _open(info.asset_url) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            while chunk := resp.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress:
                    progress(written, total)
        if digest.hexdigest() != expected:
            raise UpdateError("Downloaded file does not match its checksum")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def current_target() -> Path:
    """The file this running build lives in, which an update replaces."""
    method = UPDATE_METHOD
    if method == "appimage":
        path = os.environ.get("APPIMAGE")
        if not path:
            raise UpdateError("Not running from an AppImage")
        return Path(path)
    if method == "exe" and getattr(sys, "frozen", False):
        return Path(sys.executable)
    raise UpdateError("This build cannot update itself")


def apply_update(info: UpdateInfo, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the new build and swap it in for the running one. Returns its path."""
    target = current_target()
    if not os.access(target.parent, os.W_OK):
        raise UpdateError(f"No write access to {target.parent}")
    new = target.with_name(target.name + ".new")
    download(info, new, progress)
    if UPDATE_METHOD == "exe":
        # Windows cannot overwrite a running exe but can rename it.
        old = target.with_name(target.name + ".old")
        old.unlink(missing_ok=True)
        target.rename(old)
        new.rename(target)
    else:
        new.chmod(0o755)
        os.replace(new, target)
    return target


def cleanup_old() -> None:
    """Remove the previous exe left behind by the last update."""
    if UPDATE_METHOD == "exe" and getattr(sys, "frozen", False):
        Path(sys.executable).with_name(Path(sys.executable).name + ".old").unlink(missing_ok=True)


def relaunch(target: Path) -> None:
    if UPDATE_METHOD == "exe":
        # Without this the child would reuse the old onefile temp directory.
        env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        subprocess.Popen([str(target)], env=env, close_fds=True)
        os._exit(0)
    os.execv(str(target), [str(target)])
