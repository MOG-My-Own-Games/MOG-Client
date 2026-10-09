"""Choosing which of the install folders a new game goes into, and whether an installed game's folder is
there at the moment (a drive can be unplugged). Stdlib only."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mog_client.api import fmt_bytes
from mog_client.config import InstalledGame, Settings

# Room kept free on top of the game's size: the installer's own temporary files, the prefix growing.
MARGIN_BYTES = 256 * 1024 * 1024


def reachable(root: Path) -> bool:
    """Whether games can be put in `root` now: the folder is there and writable, or it is not there yet
    but its parent is (so it can be created). A folder on a drive that is not connected is neither."""
    if root.is_dir():
        return os.access(root, os.W_OK | os.X_OK)
    return root.parent.is_dir() and os.access(root.parent, os.W_OK | os.X_OK)


def free_bytes(root: Path) -> int:
    """Free space of the disk `root` is (or would be) on."""
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def total_free(
    roots: list[Path],
    usable: Callable[[Path], bool] | None = None,
    free_of: Callable[[Path], int] | None = None,
    disk_of: Callable[[Path], object] | None = None,
) -> int:
    """Free bytes over the connected folders. Folders on the same disk count once."""
    usable, free_of, disk_of = usable or reachable, free_of or free_bytes, disk_of or _disk
    seen, total = set(), 0
    for root in roots:
        if not usable(root):
            continue
        try:
            disk, free = disk_of(root), free_of(root)
        except OSError:
            continue
        if disk not in seen:
            seen.add(disk)
            total += free
    return total


def _disk(root: Path) -> int:
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe.stat().st_dev


@dataclass
class Slot:
    path: Path
    state: str  # "ready" | "full" | "unavailable"
    free: int = 0


@dataclass
class Placement:
    slots: list[Slot] = field(default_factory=list)

    @property
    def ready(self) -> list[Slot]:
        """The folders that can take the game, in order of priority."""
        return [s for s in self.slots if s.state == "ready"]

    @property
    def blocked_by(self) -> Slot | None:
        """The first folder that is connected but has no room, when it comes before one that has: the
        user is asked before the game goes to a later folder. None when the first usable folder takes it."""
        for slot in self.slots:
            if slot.state == "ready":
                return None
            if slot.state == "full":
                return slot
        return None


def choose(
    roots: list[Path],
    needed: int,
    usable: Callable[[Path], bool] | None = None,
    free_of: Callable[[Path], int] | None = None,
) -> Placement:
    """Look at every folder in order: not connected ones are passed over, the others are ready when
    they have room for `needed` bytes (plus a margin) and full when they do not."""
    usable, free_of = usable or reachable, free_of or free_bytes
    placement = Placement()
    for root in roots:
        if not usable(root):
            placement.slots.append(Slot(root, "unavailable"))
            continue
        try:
            free = free_of(root)
        except OSError:
            placement.slots.append(Slot(root, "unavailable"))
            continue
        placement.slots.append(Slot(root, "ready" if free >= needed + MARGIN_BYTES else "full", free))
    return placement


def default_root(settings: Settings) -> Path:
    """The folder a game goes into when nobody is asked (the command line): the first one with room."""
    placement = choose(settings.install_roots, 0)
    return placement.ready[0].path if placement.ready else settings.install_roots[0]


def present(rec: InstalledGame) -> bool:
    """Whether the game's folder is there now."""
    return Path(rec.install_dir).is_dir()


def resumable(rec: InstalledGame) -> bool:
    """Whether a partly downloaded game's folder, or the folder that holds it, can be written to."""
    folder = Path(rec.install_dir)
    return reachable(folder)


def status(folder: Path) -> str:
    """What the folders list says about one: its free space, or that its drive is not connected."""
    if not reachable(folder):
        return "not connected"
    try:
        return f"{fmt_bytes(free_bytes(folder))} free"
    except OSError:
        return "not connected"
