"""Where a Windows game keeps its saves, inside a Wine prefix or the real Windows profile.

Only a short list of places is looked at (the user's Documents, Saved Games and AppData, plus
ProgramData and Public) and anything that is clearly cache, log or system noise is skipped. A
file is named in archives by a key that does not depend on this machine's user name:

    users/USER/<rest>   a file in the user's profile
    ProgramData/<rest>  and  users/Public/<rest>   as they are
    game/<rest>         a file inside the game's install folder that the installer did not write
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

USER_KEY = "users/USER"
GAME_KEY = "game"
PUBLIC_USER = "Public"
# Files MOG itself keeps in a game's install folder (the icon), never the game's.
MOG_FILE_PREFIX = ".mog-"
# Where a Proton or umu prefix names the Windows user, before any other.
PREFERRED_USER = "steamuser"

USER_SUBDIRS = ("Documents", "Saved Games", "AppData/Roaming", "AppData/Local", "AppData/LocalLow")
SHARED_DIRS = ("ProgramData", "users/Public")

# A folder with one of these names (any case), anywhere below a root, is never a save.
DENY_DIRS = frozenset(
    {
        "temp", "tmp", "microsoft", "packages", "crashdumps", "crashes", "d3dscache", "dxcache",
        "glcache", "shadercache", "cache", "code cache", "gpucache", "logs", "wine",
    }
)  # fmt: skip
DENY_SUFFIXES = (".log", ".tmp", ".dmp", ".etl")


class UnsafeKey(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    key: str
    path: Path


def is_denied(parts: tuple[str, ...]) -> bool:
    """Whether a path (as parts below a root) is cache, log or system noise."""
    if any(part.casefold() in DENY_DIRS for part in parts[:-1]):
        return True
    name = parts[-1].casefold()
    return name.endswith(DENY_SUFFIXES) or name in DENY_DIRS


def real_dir(path: Path) -> bool:
    """A directory that is not a symbolic link. Wine links folders such as 'My Documents', and a
    prefix may link Documents into the real home; following those would archive the wrong tree."""
    return path.is_dir() and not path.is_symlink()


def user_dirs(drive_c: Path) -> list[Path]:
    users = drive_c / "users"
    if not real_dir(users):
        return []
    return sorted(p for p in users.iterdir() if p.name != PUBLIC_USER and real_dir(p))


def user_dir_name(drive_c: Path) -> str:
    """The profile folder saves are put back into: steamuser if there is one, else the newest."""
    dirs = user_dirs(drive_c)
    if not dirs:
        return os.environ.get("USERNAME") or os.environ.get("USER") or PREFERRED_USER
    for d in dirs:
        if d.name == PREFERRED_USER:
            return d.name
    return max(dirs, key=lambda d: d.stat().st_mtime).name


def _walk(root: Path, key_prefix: str) -> Iterator[Candidate]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root)
        dirnames[:] = sorted(
            d for d in dirnames if d.casefold() not in DENY_DIRS and not (here / d).is_symlink()
        )
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = (*rel_dir.parts, name)
            if is_denied(rel):
                continue
            yield Candidate(str(PurePosixPath(key_prefix, *rel)), path)


def profile_candidates(drive_c: Path) -> Iterator[Candidate]:
    """Every file in the profile and shared folders worth considering, with its archive key."""
    for user in user_dirs(drive_c):
        for sub in USER_SUBDIRS:
            root = user / sub
            if real_dir(root):
                yield from _walk(root, f"{USER_KEY}/{sub}")
    for shared in SHARED_DIRS:
        root = drive_c / shared
        if real_dir(root):
            yield from _walk(root, shared)


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()  # noqa: S324 - matches the server's manifest, not a security use
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install_candidates(install_dir: Path, manifest: dict[str, tuple[int, str]] | None) -> Iterator[Candidate]:
    """Files in the game's own folder that the installer did not put there. Without the install
    manifest there is no telling the game's files from its saves, so nothing is offered."""
    if manifest is None or not real_dir(install_dir):
        return
    for candidate in _walk(install_dir, GAME_KEY):
        rel = candidate.key[len(GAME_KEY) + 1 :]
        if rel.startswith(MOG_FILE_PREFIX):
            continue
        known = manifest.get(rel)
        if known is not None:
            try:
                if candidate.path.stat().st_size == known[0] and _sha1(candidate.path) == known[1]:
                    continue
            except OSError:
                continue
        yield candidate


def check_key(key: str) -> str:
    """A key from an archive, validated: relative, no `..`, no drive letter or backslash."""
    path = PurePosixPath(key)
    if not key or "\\" in key or "\0" in key or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise UnsafeKey(key)
    return str(path)


def target_for_key(key: str, drive_c: Path | None, install_dir: Path) -> Path:
    """Where a key goes on this machine."""
    key = check_key(key)
    if key == GAME_KEY or key.startswith(f"{GAME_KEY}/"):
        base, rest = install_dir, key[len(GAME_KEY) + 1 :]
    elif drive_c is None:
        raise UnsafeKey(f"{key}: no prefix to put it in")
    elif key.startswith(f"{USER_KEY}/"):
        base, rest = drive_c / "users" / user_dir_name(drive_c), key[len(USER_KEY) + 1 :]
    else:
        base, rest = drive_c, key
    target = base / rest
    if base.resolve() not in target.resolve().parents:
        raise UnsafeKey(key)
    return target
