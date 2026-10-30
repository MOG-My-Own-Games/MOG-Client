"""Putting the saves a game made somewhere else (an old install, another Wine prefix) in place of the fresh ones (Qt-free).

The game has been played from here at least once, so `SaveState.tracked` says where it writes: those folders are the structure.
The same folders are looked for in the other place, and what is there (the player's real progress) can take the place of the
fresh files. Everything replaced goes first into a backup archive (`sync._apply`), and the result is sent up as a new version.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mog_client.saves import prefix as prefixes
from mog_client.saves import sync
from mog_client.saves.archive import build_archive
from mog_client.saves.locations import (
    GAME_KEY,
    SHARED_DIRS,
    USER_KEY,
    USER_SUBDIRS,
    Candidate,
    UnsafeKey,
    is_native_key,
    target_for_key,
    walk,
)
from mog_client.saves.scan import sha256_of
from mog_client.saves.state import SaveState, load_state, saves_dir

NEW, DIFFERS, SAME = "new", "differs", "same"
PREFIX, FOLDER = "prefix", "folder"
_ROOTS = frozenset([f"{USER_KEY}/{sub}" for sub in USER_SUBDIRS] + list(SHARED_DIRS))


@dataclass(frozen=True)
class Match:
    key: str
    source: Path  # the old file
    local: Path  # where the game has it here
    status: str  # NEW: the game has no such file here; DIFFERS: it has one with other content; SAME
    newer: bool  # the old file was written after the one here (only for DIFFERS)


def has_structure(state: SaveState) -> bool:
    """Whether the game has saved from here yet: until it has, there is nothing to compare the old saves with."""
    return bool(state.tracked)


def units(state: SaveState) -> list[str]:
    """The folders (or single files) the game's saves are in, as keys: what the user confirmed, else the game-sized folder of each
    file the game wrote. Native roots are left out: an old install of a native game writes to the same places."""
    confirmed = state.includes or []
    found: set[str] = set()
    for key in state.tracked:
        if is_native_key(key):
            continue
        covering = next((i for i in confirmed if key == i or key.startswith(i + "/")), None)
        if covering is not None:
            found.add(covering)
        elif key.startswith(f"{GAME_KEY}/"):
            parent = key.rpartition("/")[0]
            found.add(key if parent == GAME_KEY else parent)  # a file at the game's root is matched on its own
        else:
            folder = sync._folder_of(key)
            found.add(key if folder in _ROOTS else folder)  # nor is a whole Documents folder one game's
    return sorted(u for u in found if not any(u != o and u.startswith(o + "/") for o in found))


def sources(state: SaveState) -> set[str]:
    """What the old saves can be in: a Wine prefix (the profile folders) and/or the old game folder (`game/...`)."""
    kinds = set()
    for unit in units(state):
        kinds.add(FOLDER if unit == GAME_KEY or unit.startswith(f"{GAME_KEY}/") else PREFIX)
    return kinds


def _files(unit: str, root: Path) -> Iterable[Candidate]:
    if root.is_symlink():
        return
    if root.is_file():
        yield Candidate(unit, root)
    elif root.is_dir():
        yield from walk(root, unit)


def find(ctx: sync.Context, prefix: Path | None = None, folder: Path | None = None) -> list[Match]:
    """The files in the structure of the game that the other place has: in `prefix` (a Wine prefix) for the profile folders, in
    `folder` (the old game folder) for the game's own. Files that are the same as here are left out."""
    rec = ctx.rec
    state = load_state(rec.game_id)
    local_c = sync._drive_c(sync.find_prefix(ctx, state), rec)
    other_c = prefixes.drive_c_of(prefix) if prefix is not None else None
    install = Path(rec.install_dir)
    found: dict[str, Match] = {}
    for unit in units(state):
        inside = unit == GAME_KEY or unit.startswith(f"{GAME_KEY}/")
        if inside and folder is None or not inside and other_c is None:
            continue
        try:
            root = target_for_key(unit, None if inside else other_c, folder if inside else install)
        except UnsafeKey:
            continue
        for candidate in _files(unit, root):
            try:
                local = target_for_key(candidate.key, local_c, install, rec.in_sandbox)
                source = candidate.path.stat()
            except (UnsafeKey, OSError):
                continue
            if not local.is_file():
                found[candidate.key] = Match(candidate.key, candidate.path, local, NEW, True)
                continue
            try:
                here = local.stat()
                same = here.st_size == source.st_size and sha256_of(local) == sha256_of(candidate.path)
            except OSError:
                continue
            status = SAME if same else DIFFERS
            found[candidate.key] = Match(candidate.key, candidate.path, local, status, source.st_mtime_ns > here.st_mtime_ns)
    return [m for _, m in sorted(found.items()) if m.status != SAME]


def only_newer(matches: Iterable[Match]) -> list[Match]:
    """The files the game lacks here, or has an older copy of."""
    return [m for m in matches if m.status == NEW or m.newer]


def apply(ctx: sync.Context, matches: list[Match]) -> sync.RestoreResult:
    """Put the old files where the game has its saves. What they replace is kept first in a backup archive."""
    if not matches:
        raise RuntimeError("no files to put in")
    if sync._drive_c(sync.find_prefix(ctx, load_state(ctx.rec.game_id)), ctx.rec) is None and any(
        not m.key.startswith(f"{GAME_KEY}/") for m in matches
    ):
        raise RuntimeError("the game has no prefix here yet: start it once so it makes one")
    archive = saves_dir(ctx.rec.game_id) / f"external-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    count = build_archive(((m.key, m.source) for m in matches), archive)
    if not count:
        archive.unlink(missing_ok=True)
        raise RuntimeError("the old saves could not be read")
    try:
        result = sync.apply_archive(ctx, archive)
    finally:
        archive.unlink(missing_ok=True)
    if result.status != "restored":
        raise RuntimeError(f"the old saves do not fit this game ({result.status})")
    return result
