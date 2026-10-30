"""Choosing which of the install folders a new game goes into, and whether an installed game's folder is
there at the moment (a drive can be unplugged). Stdlib only."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mog_client.api import fmt_bytes, safe_dirname
from mog_client import config
from mog_client.config import InstalledGame, Settings, _read_json, _write_json

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


def _holds_files(folder: Path) -> bool:
    """Whether anything but plain folders is anywhere under `folder` (a link counts: it is not ours to delete)."""
    for dirpath, dirs, files in os.walk(folder):
        if files or any(os.path.islink(os.path.join(dirpath, d)) for d in dirs):
            return True
    return False


def prune_empty(folder: Path, keep: Iterable[Path]) -> None:
    """Remove `folder` when no file is anywhere under it, then each folder above it that has become empty. Never
    removes a `keep` folder (an install folder, say) or anything outside them."""
    stops = {p.resolve() for p in keep}
    folder = Path(folder)

    def inside() -> bool:
        here = folder.resolve()
        return here not in stops and any(here.is_relative_to(s) for s in stops)

    if not (folder.is_dir() and inside()) or _holds_files(folder):
        return
    try:
        shutil.rmtree(folder)
    except OSError:
        return
    folder = folder.parent
    while inside():
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


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


def rebase(path: str | None, old: str, new: str) -> str | None:
    """`path` with the folder `old` it lies under replaced by `new`; a path that is not under `old` is returned as it is."""
    if not path:
        return path
    try:
        return str(Path(new) / Path(path).relative_to(old))
    except ValueError:
        return path


def find_moved(rec: InstalledGame, roots: Iterable[Path]) -> Path | None:
    """Where an installed game's folder is now, when it is not where it was recorded (the games folder was moved, or the install
    folder changed): a folder of the same name in one of the install folders. When the executable is known it has to be there too,
    so a folder that only has the same name is not taken for the game."""
    old = Path(rec.install_dir)
    for root in roots:
        for name in dict.fromkeys([old.name, safe_dirname(rec.name)]):
            candidate = Path(root) / name
            if candidate != old and misfit(rec, candidate) is None:
                return candidate
    return None


_MOG_FILES = {"pfx", ".mog-icon", ".mog-steam.desktop", ".mog-permissions", ".directory"}


def folder_names(game: dict) -> list[str]:
    """The names a game's folder in an install folder can have: the server's own folder name, and the one MOG gives a new install."""
    return list(dict.fromkeys(safe_dirname(n) for n in (game.get("fs_name") or game["name"], game["name"])))


def holds_game(folder: Path, name: str) -> bool:
    """Whether `folder` has anything of a game in it besides what MOG makes (the prefix, the launch files, the icon): the prefix
    that is kept when a game is uninstalled leaves a folder that is not a game."""
    stem = safe_dirname(name)
    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.name in _MOG_FILES or entry.name in {f"{stem}{ext}" for ext in (".sh", ".cmd", ".desktop", ".lnk")}:
            continue
        if not entry.is_dir() or entry.is_symlink() or _holds_files(entry):
            return True
    return False


def _ignored_path() -> Path:
    return config.data_dir() / "ignored-folders.json"


def ignored_folders() -> set[Path]:
    """The folders in an install folder that MOG must not take for a game again: the ones of games the user took out of MOG."""
    return {Path(p) for p in _read_json(_ignored_path(), [])}


def ignore_folder(folder: Path) -> None:
    _write_json(_ignored_path(), sorted({str(p) for p in ignored_folders() | {Path(folder)}}))


def misfit(rec: InstalledGame | None, folder: Path) -> str | None:
    """Why `folder` cannot be where the game's files are now, or None when it can: it has to be a folder, and hold the game's
    executable at the place it has in the game's own folder. A game with no record here, or still waiting for its executable,
    has none to look for: the folder only has to have something in it."""
    if not folder.is_dir():
        return f"{folder} is not a folder"
    if rec is None or not rec.executable:
        try:
            return None if any(folder.iterdir()) else f"{folder} is empty"
        except OSError as e:
            return f"{folder} cannot be read: {e}"
    try:
        relative = Path(rec.executable).relative_to(rec.install_dir)
    except ValueError:  # the executable is not in the game's folder: there is nothing to look for
        return None
    if not (folder / relative).is_file():
        return f"{relative} is not in {folder}: choose the folder of the game itself, the one with its launch script"
    return None


def folder_size(folder: Path) -> int:
    """Bytes the files under `folder` take (a link is counted as the link, never followed). What cannot be read counts 0."""
    total = 0
    stack = [str(folder)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def by_size(records: Iterable[InstalledGame], sizes: dict[int, int | None]) -> list[tuple[InstalledGame, int | None]]:
    """The games with their size, the biggest first. A game with no known size (its folder is not there) goes last, by name."""
    pairs = [(rec, sizes.get(rec.game_id)) for rec in records]
    return sorted(pairs, key=lambda p: (p[1] is None, -(p[1] or 0), p[0].name.casefold()))


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
