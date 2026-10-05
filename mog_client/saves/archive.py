"""Packing the files of a save into a zip and putting an archive back, safely."""

from __future__ import annotations

import os
import stat
import time
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

from mog_client.saves.locations import UnsafeKey, check_key

MAX_MEMBERS = 20000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
_CHUNK = 1024 * 1024


class ArchiveError(ValueError):
    pass


def build_archive(items: Iterable[tuple[str, Path]], dest: Path) -> int:
    """Write the files (archive key, path on disk) to a zip at `dest`; returns the file count."""
    count = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for key, path in items:
            try:
                z.write(path, arcname=check_key(key))
            except OSError:
                continue  # gone or locked since the scan
            count += 1
    return count


def read_keys(archive: Path) -> list[str]:
    try:
        with zipfile.ZipFile(archive) as z:
            return [info.filename for info in z.infolist() if not info.is_dir()]
    except zipfile.BadZipFile as e:
        raise ArchiveError(str(e)) from e


def extract_archive(
    archive: Path,
    target_for: Callable[[str], Path],
    backup: Path | None = None,
) -> list[tuple[str, Path]]:
    """Put each file of the archive where `target_for(key)` says, returning (key, path) for those written.

    A file that is about to be replaced is first copied into the zip `backup`, so a restore can
    be undone. The whole archive is checked before anything is written: a bad member refuses it all."""
    written: list[tuple[str, Path]] = []
    try:
        with zipfile.ZipFile(archive) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
                raise ArchiveError("archive is too large to restore")
            plan = []
            for member in members:
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ArchiveError(f"symbolic link in archive: {member.filename!r}")
                try:
                    plan.append((member, target_for(check_key(member.filename))))
                except UnsafeKey as e:
                    raise ArchiveError(f"unsafe path in archive: {member.filename!r}") from e

            if backup is not None:
                _backup(plan, backup)
            for member, target in plan:
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + ".mog-part")
                with z.open(member) as src, open(part, "wb") as out:
                    while chunk := src.read(_CHUNK):
                        out.write(chunk)
                os.replace(part, target)
                stamp = time.mktime((*member.date_time, 0, 0, -1))
                os.utime(target, (stamp, stamp))
                written.append((member.filename, target))
    except zipfile.BadZipFile as e:
        raise ArchiveError(str(e)) from e
    return written


def _backup(plan: list[tuple[zipfile.ZipInfo, Path]], backup: Path) -> None:
    existing = [(member.filename, target) for member, target in plan if target.is_file()]
    if not existing:
        return
    backup.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
        for key, target in existing:
            z.write(target, arcname=key)
