"""Will an install fit on the disk it goes to? (stdlib only)

Asked before an install starts or resumes, so the person hears about a full disk before hours of downloading, not after.
It is an estimate and never a ban: the window shows the result and lets the person go on anyway.

What the install needs is read from the best thing known about it:
- the server's install cache, once it is finalized (its session is `done`): the size of its manifest, which is exactly
  what gets downloaded;
- otherwise (no cache yet, or one still being made) the weight of the installer: the one picked, else what the game's
  folder holds on the server. That is a floor, as the installed game is usually larger, and a cache that is already
  bigger than it wins.
Whatever is already on disk from an earlier attempt is taken off before comparing with the free space.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mog_client import installdirs

BASIS_CACHE = "cache"
BASIS_INSTALLER = "installer"


@dataclass(frozen=True)
class Shortage:
    needed: int  # bytes the install takes in all
    have: int  # of them, already on disk
    free: int  # free bytes of the disk
    basis: str  # BASIS_CACHE or BASIS_INSTALLER: what `needed` was read from

    @property
    def missing(self) -> int:
        return max(0, self.needed - self.have)


PREFIX_FOLDER = "pfx"  # the game's Wine prefix: not downloaded, and thousands of files to look at for nothing


def local_bytes(folder: Path) -> int:
    """Bytes of the files already under `folder` (0 when it is not there): what a resume does not download again."""
    total = 0
    for base, dirs, names in os.walk(folder, followlinks=False):
        if Path(base) == folder:
            dirs[:] = [d for d in dirs if d != PREFIX_FOLDER]
        for name in names:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                continue
    return total


def estimate(session: dict | None, installer_bytes: int | None) -> tuple[int, str]:
    """(bytes the install needs, what that was read from); 0 when nothing is known."""
    cache = int((session or {}).get("bytes_total") or 0)
    if session and session.get("state") == "done" and cache > 0:
        return cache, BASIS_CACHE
    return max(cache, int(installer_bytes or 0)), BASIS_INSTALLER


def shortage(needed: int, basis: str, have: int, free: int) -> Shortage | None:
    """The shortage when what is still to come does not fit in `free`; None when it fits or nothing is known."""
    if needed <= 0:
        return None
    found = Shortage(needed, min(have, needed), free, basis)
    return found if found.missing > free else None


def check(client, game_id: int, folder: Path, installer: dict | None = None) -> Shortage | None:
    """Look at the server's install of the game and at the disk `folder` is on. Blocks (network, disk): not for the
    window's thread. A server that cannot say leaves no shortage."""
    try:
        session = client.get_session(game_id)
    except Exception:  # noqa: BLE001 - no session, or a server that cannot say: the installer weight remains
        session = None
    needed, basis = estimate(session, None)
    if basis != BASIS_CACHE:  # a finished cache says it all; otherwise the installer's weight is asked for
        weight = (installer or {}).get("file_size_bytes") or client.game_size(game_id)
        needed, basis = estimate(session, weight)
    free = installdirs.free_bytes(folder)
    if needed <= free:  # fits even with nothing downloaded yet: the folder is not read at all
        return None
    return shortage(needed, basis, local_bytes(folder), free)
