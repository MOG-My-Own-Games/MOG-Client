"""Downloading a game's mods (stdlib only). Mods are only fetched, never installed: each top-level folder or archive of
the game's mods folder on the server is one mod, a folder is zipped by the server first (with progress) and what comes
down is one file in the user's Downloads folder."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from mog_client.api import MogClient, safe_dirname

POLL_SECONDS = 1.0
ZIP_TIMEOUT = 6 * 3600  # seconds a zip may take before it is given up on


class ModError(RuntimeError):
    pass


def download_dir(game_name: str, home: Path | None = None) -> Path:
    """Where a game's mods go: Downloads/MOG/<game>/mods (the home folder when there is no Downloads)."""
    home = home or Path.home()
    base = home / "Downloads" if (home / "Downloads").is_dir() else home
    return base / "MOG" / safe_dirname(game_name) / "mods"


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
    percent)` is told "Zipping" and "Downloading" as they go. Returns the file saved."""
    name = mod["name"]
    state = client.prepare_mod(game_id, name)
    waited = 0.0
    while state.get("state") != "ready":
        if state.get("state") == "failed":
            raise ModError(f"the server could not zip {name}: {state.get('error') or 'unknown error'}")
        if stopped() or waited > ZIP_TIMEOUT:
            raise ModError(f"stopped while {name} was being zipped")
        total = state.get("bytes_total") or 0
        progress("Zipping", min(99, 100 * (state.get("bytes_done") or 0) // total) if total else 0)
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        state = client.mod_status(game_id, name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = free_path(dest_dir, file_name(mod))
    progress("Downloading", 0)
    client.download_mod(
        game_id, name, target, lambda written, total: progress("Downloading", min(100, 100 * written // total) if total else 0)
    )
    return target
