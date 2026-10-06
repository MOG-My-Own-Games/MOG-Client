"""What MOG itself wrote into a game's folder, kept apart from what the game wrote.

The server's list of an install's files is the best record of that, but it can be missing or short (a list that
could not be fetched, a cache that was cleared before a second installer ran, a download from the command line).
This is the record that does not depend on it: the folder is looked at before an install begins and again when it
ends, and whatever is new or different is what the install put there. The result is added to the same manifest
the save sync reads (see state.save_install_manifest), so files from any installer are never taken for saves.

The picture of the folder from before is kept on disk until the install finishes, so a pause, a resume or a
crash in between does not move the starting line."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mog_client.config import _read_json, _write_json
from mog_client.launcher import PREFIX_DIR
from mog_client.saves.locations import MOG_FILE_PREFIX
from mog_client.saves.state import baseline_path, load_install_manifest, save_install_manifest

Snapshot = dict[str, tuple[int, int]]  # relative path -> (size, mtime_ns)
_CHUNK = 1024 * 1024


def snapshot(install_dir: Path) -> Snapshot:
    """The regular files in the folder, by relative path, as (size, mtime_ns). Links, the Wine prefix and the
    files MOG writes for its own entries are left out."""
    seen: Snapshot = {}
    if not install_dir.is_dir():
        return seen
    for dirpath, dirnames, filenames in os.walk(install_dir):
        here = Path(dirpath)
        if here == install_dir:
            dirnames[:] = [d for d in dirnames if d != PREFIX_DIR]
        for name in filenames:
            path = here / name
            try:
                if path.is_symlink():
                    continue
                info = path.stat()
            except OSError:
                continue
            rel = path.relative_to(install_dir).as_posix()
            if not rel.startswith(MOG_FILE_PREFIX) and path.is_file():
                seen[rel] = (info.st_size, info.st_mtime_ns)
    return seen


def begin(game_id: int, install_dir: Path) -> Snapshot:
    """The folder as it was before this install began: the one kept from an earlier attempt that did not
    finish, else a fresh look, which is then kept."""
    raw = _read_json(baseline_path(game_id), None)
    if isinstance(raw, dict):
        return {path: (size, mtime) for path, (size, mtime) in raw.items()}
    before = snapshot(install_dir)
    _write_json(baseline_path(game_id), {path: list(sig) for path, sig in before.items()})
    return before


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()  # noqa: S324 - the server's hash for its manifest, not a security measure
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def finish(game_id: int, install_dir: Path, before: Snapshot) -> int:
    """Add to the game's manifest what the install put in the folder (new or changed since `before`), and drop
    the kept starting picture. Returns how many files were added or updated."""
    entries = []
    known = load_install_manifest(game_id) or {}
    for rel, signature in snapshot(install_dir).items():
        if before.get(rel) == signature:
            continue
        try:
            sha1 = _sha1(install_dir / rel)
        except OSError:
            continue
        if known.get(rel) != (signature[0], sha1):
            entries.append({"path": rel, "size_bytes": signature[0], "sha1": sha1})
    if entries:
        save_install_manifest(game_id, entries)
    baseline_path(game_id).unlink(missing_ok=True)
    return len(entries)
