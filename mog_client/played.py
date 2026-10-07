"""When each game was last started from this client (a JSON file; stdlib only)."""

from __future__ import annotations

import time

from mog_client.config import _read_json, _write_json, data_dir
from mog_client.ordering import iso_to_epoch
from mog_client.saves.state import load_state


def _path():
    return data_dir() / "played.json"


def load() -> dict[int, float]:
    raw = _read_json(_path(), {})
    return {int(k): float(v) for k, v in raw.items() if str(k).isdigit() and isinstance(v, (int, float))}


def record(game_id: int, when: float | None = None) -> None:
    played = load()
    played[game_id] = when if when is not None else time.time()
    _write_json(_path(), {str(k): v for k, v in played.items()})



def history() -> dict[int, float]:
    """When each game was last played here: the times a start was seen, and for games started before those were
    kept (or through a shortcut that did not report), the last time their saves were uploaded, which is a game
    having been played."""
    seen = load()
    root = data_dir() / "saves"
    try:
        folders = [d for d in root.iterdir() if d.name.isdigit()]
    except OSError:
        return seen
    for folder in folders:
        uploaded = iso_to_epoch(load_state(int(folder.name)).last_synced_at)
        if uploaded is not None and uploaded > seen.get(int(folder.name), 0):
            seen[int(folder.name)] = uploaded
    return seen
