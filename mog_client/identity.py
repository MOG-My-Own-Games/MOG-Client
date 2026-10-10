"""A local record belongs to the game it was made for, not to whatever the server now numbers the same.

Records are keyed by the server's game id. When the server's database is rebuilt, ids can be handed out again, and a
record then sits on a different game (a game's page would show another one's folder). Each record keeps the server's
folder name for its game (`server_name`); one that no longer fits is moved to the game it belongs to, or set aside
(stdlib only)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from mog_client.config import InstalledGame, data_dir, load_library, save_library
from mog_client.saves.state import baseline_path, manifest_path, saves_dir

_TAGS = re.compile(r"[\[(].*?[\])]")
_WORDS = re.compile(r"[^a-z0-9]+")


def _words(name: str) -> list[str]:
    return [w for w in _WORDS.split(_TAGS.sub(" ", name).casefold()) if w]


def alike(a: str, b: str) -> bool:
    """Whether two names are of the same game: one starts like the other, or they share a good part of their words (a
    scrape renames a game, it does not make it another)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    short, long = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if long[: len(short)] == short:
        return True
    return len(set(wa) & set(wb)) / len(set(wa) | set(wb)) >= 0.5


def orphans_path() -> Path:
    return data_dir() / "installed.orphans.json"


def _set_aside(rec: InstalledGame) -> None:
    path = orphans_path()
    try:
        kept = json.loads(path.read_text())
    except (OSError, ValueError):
        kept = []
    kept.append(rec.__dict__ | {"was_game_id": rec.game_id})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, indent=1, default=str))


def _move_data(old: int, new: int) -> None:
    """What is kept per game id (saves state, file lists) goes with the record, when the new id has none yet."""
    for source, target in ((saves_dir(old), saves_dir(new)), (manifest_path(old), manifest_path(new)), (baseline_path(old), baseline_path(new))):
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))


def reconcile(games: list[dict], busy: set[int] = frozenset()) -> list[str]:
    """Check every local record against the game the server has under its id. Returns what was done, in words.
    `busy` is the games with an install running: they are left alone."""
    games = [{**g, "fs_name": g.get("fs_name") or g["name"]} for g in games]
    by_id = {g["id"]: g for g in games}
    library = load_library()
    said: list[str] = []
    changed = False
    for gid, rec in list(library.items()):
        game = by_id.get(gid)
        if game is None or gid in busy:
            continue
        if rec.server_name:
            fits = rec.server_name == game["fs_name"]
        else:
            fits = alike(rec.name, game["name"]) or alike(rec.name, game["fs_name"])
            if fits:
                rec.server_name, changed = game["fs_name"], True
        if fits:
            continue
        home = next(
            (g for g in games if (rec.server_name and g["fs_name"] == rec.server_name) or (not rec.server_name and g["name"].casefold() == rec.name.casefold())),
            None,
        )
        del library[gid]
        changed = True
        if home is not None and home["id"] not in library:
            rec.game_id, rec.server_name = home["id"], home["fs_name"]
            library[home["id"]] = rec
            _move_data(gid, home["id"])
            said.append(f"{rec.name} was recorded under another number on the server: it is now matched to {home['name']}.")
        else:
            _set_aside(rec)
            said.append(
                f"The record of {rec.name} here belonged to a game the server no longer has under that number "
                f"(it is {game['name']} now), so it was set aside. The files, if any, are in {rec.install_dir}."
            )
    if changed:
        save_library(library)
    return said
