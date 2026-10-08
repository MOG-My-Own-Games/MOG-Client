"""Downloading a game's mods (stdlib only). Mods are only fetched, never installed: each top-level folder or archive of
the game's mods folder on the server is one mod, a folder is zipped by the server first (with progress) and what comes
down is one file in the mods folder of the game's own folder here."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from mog_client.api import MogClient

POLL_SECONDS = 1.0
ZIP_TIMEOUT = 6 * 3600  # seconds a zip may take before it is given up on


class ModError(RuntimeError):
    pass


class ModCancelled(ModError):
    """The person stopped a mod download; what was made of it has been removed."""


def mods_dir(install_dir: Path) -> Path:
    """Where a game's mods go: the mods folder inside the game's own folder."""
    return install_dir / "mods"


def file_name(mod: dict) -> str:
    """What the mod is saved as: an archive or a file under its own name, a folder as <name>.zip."""
    return f"{mod['name']}.zip" if mod.get("kind") == "folder" else mod["name"]


def free_path(folder: Path, name: str) -> Path:
    """`folder/name`, or with " (2)", " (3)"... before the extension when that file is already there."""
    target = folder / name
    stem, suffix = target.stem, target.suffix
    n = 2
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def local_files(folder: Path, mod: dict) -> list[Path]:
    """The files of this mod already in `folder`: the one it is saved as, and the numbered copies `free_path` made
    when it was fetched again."""
    name = Path(file_name(mod))
    numbered = re.compile(rf"{re.escape(name.stem)} \(\d+\){re.escape(name.suffix)}")
    found = [folder / name.name] if (folder / name.name).is_file() else []
    if folder.is_dir():
        found += sorted(p for p in folder.iterdir() if p.is_file() and numbered.fullmatch(p.name))
    return found


def remove_local(folder: Path, mod: dict) -> list[Path]:
    """Delete the mod's files from `folder`, and a half-downloaded one with them. Returns what was removed."""
    removed = []
    for path in local_files(folder, mod):
        path.unlink(missing_ok=True)
        removed.append(path)
    base = folder / file_name(mod)
    for leftover in (base.with_name(f"{base.name}.part"), base.with_name(f"{base.name}.part.id")):
        leftover.unlink(missing_ok=True)
    return removed


def fetch(
    client: MogClient,
    game_id: int,
    mod: dict,
    dest_dir: Path,
    progress: Callable[[str, int], None],
    stopped: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Get one mod: ask the server to prepare it, follow the zipping if there is any, then download it. `progress(stage,
    percent)` is told "Zipping" and "Downloading" as they go. `stopped()` turning true ends it with ModCancelled: the
    server is told to drop the zip, and a half-downloaded file is removed. A download cut short is continued from the
    same half file by the next call. Returns the file saved."""
    name = mod["name"]
    state = client.prepare_mod(game_id, name)
    waited = 0.0
    while state.get("state") != "ready":
        if state.get("state") == "failed":
            raise ModError(f"the server could not zip {name}: {state.get('error') or 'unknown error'}")
        if stopped():
            client.cancel_mod(game_id, name)
            raise ModCancelled(f"stopped while {name} was being zipped")
        if waited > ZIP_TIMEOUT:
            raise ModError(f"{name} took too long to zip")
        total = state.get("bytes_total") or 0
        progress("Zipping", min(99, 100 * (state.get("bytes_done") or 0) // total) if total else 0)
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        state = client.mod_status(game_id, name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = free_path(dest_dir, file_name(mod))
    progress("Downloading", 0)

    def downloaded(written: int, total: int) -> None:
        if stopped():
            raise ModCancelled(f"stopped while {name} was downloading")
        progress("Downloading", min(100, 100 * written // total) if total else 0)

    client.download_mod(game_id, name, target, downloaded)
    return target
