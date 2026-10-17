"""Fetching a Proton-GE or UMU-Proton release into the folder Steam, Faugus and umu look builds up in, so a build the game was
given is there whatever launcher runs it (Faugus only checks for the build and shows a message when it is missing; it does not
fetch it). Stdlib only."""

from __future__ import annotations

import hashlib
import re
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

GE_NAME = re.compile(r"GE-Proton\d+-\d+$")
UMU_NAME = re.compile(r"UMU-Proton-\d+\.\d+-\d+(?:\.\d+)?[a-z]?$")  # UMU-Proton-10.0-4, UMU-Proton-9.0-3.2, UMU-Proton-9.0-4e
RELEASES = "https://github.com/GloriousEggroll/proton-ge-custom/releases/download"
UMU_RELEASES = "https://github.com/Open-Wine-Components/umu-proton/releases/download"
CHUNK = 1 << 20


def is_ge(name: str | None) -> bool:
    return bool(name) and GE_NAME.match(name) is not None


def is_umu(name: str | None) -> bool:
    return bool(name) and UMU_NAME.match(name) is not None


def is_fetchable(name: str | None) -> bool:
    """Whether `install` can fetch the build: a Proton-GE or a UMU-Proton release by its name."""
    return is_ge(name) or is_umu(name)


def urls(name: str) -> tuple[str, str]:
    """The release's archive and the file with its SHA-512."""
    base = UMU_RELEASES if is_umu(name) else RELEASES
    return f"{base}/{name}/{name}.tar.gz", f"{base}/{name}/{name}.sha512sum"


def _open(url: str, timeout: float = 60.0):
    from mog_client import net

    return net.urlopen(urllib.request.Request(url, headers={"User-Agent": "mog-client"}), timeout=timeout)


def expected_hash(text: str, file_name: str) -> str | None:
    """The hash `sha512sum` wrote for `file_name`."""
    for line in text.splitlines():
        digest, _, name = line.strip().partition(" ")
        if name.strip().lstrip("*") == file_name and re.fullmatch(r"[0-9a-fA-F]{128}", digest):
            return digest.lower()
    return None


def _safe_extract(archive: Path, into: Path) -> None:
    """Unpack `archive` into `into`, refusing anything that would land outside it (an absolute or `..` path, a link out)."""
    root = into.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"unsafe path in the archive: {member.name}")
            if member.issym() or member.islnk():
                link = (target.parent / member.linkname) if member.issym() else (root / member.linkname)
                if not link.resolve().is_relative_to(root):
                    raise RuntimeError(f"unsafe link in the archive: {member.name}")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise RuntimeError(f"unexpected entry in the archive: {member.name}")
        # Modes are kept (the build's programs must stay executable); every member was checked above.
        kwargs = {"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}
        tar.extractall(root, **kwargs)  # noqa: S202


def install(
    name: str,
    root: Path,
    opener: Callable[..., object] = _open,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download the Proton-GE or UMU-Proton release `name`, check its SHA-512 and unpack it as `root/name`. Raises RuntimeError
    when it cannot (no such release, offline, a hash that does not match); nothing is left half-installed."""
    if not is_fetchable(name):
        raise RuntimeError(f"{name} is not a Proton-GE or UMU-Proton release name")
    target = root / name
    if (target / "proton").is_file():
        return target
    archive_url, hash_url = urls(name)
    try:
        with opener(hash_url) as response:
            wanted = expected_hash(response.read().decode(errors="replace"), f"{name}.tar.gz")
        if wanted is None:
            raise RuntimeError(f"no checksum for {name}")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root, prefix=f".{name}-") as scratch:
            archive, digest = Path(scratch) / "build.tar.gz", hashlib.sha512()
            with opener(archive_url) as response, open(archive, "wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            if digest.hexdigest() != wanted:
                raise RuntimeError(f"the download of {name} is damaged (checksum differs)")
            unpacked = Path(scratch) / "unpacked"
            unpacked.mkdir()
            _safe_extract(archive, unpacked)
            if not (unpacked / name / "proton").is_file():
                raise RuntimeError(f"{name} does not hold a proton script")
            (unpacked / name).rename(target)
    except OSError as e:  # includes the network's errors
        raise RuntimeError(f"could not fetch {name}: {e}") from e
    return target
