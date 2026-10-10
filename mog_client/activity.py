"""What was running when the client last closed: installs and mod downloads, kept in a file so the next start can carry
on with them (stdlib only). A job is added when it starts and removed when it ends or is stopped by the user; one that is
still there at the next start was cut off by the client closing (or crashing)."""

from __future__ import annotations

import threading

from mog_client import logstore
from mog_client.config import _read_json, _write_json, data_dir

_lock = threading.Lock()


def _path():
    return data_dir() / "active.json"


def load() -> dict:
    raw = _read_json(_path(), {})
    installs = [int(g) for g in raw.get("installs", []) if isinstance(g, int)]
    mods = [m for m in raw.get("mods", []) if isinstance(m, dict) and isinstance(m.get("game_id"), int) and isinstance(m.get("mod"), dict)]
    return {"installs": installs, "mods": mods}


def _save(state: dict) -> None:
    """Keeping this list is a convenience for the next start: a full disk or a locked file must never take down the
    install or the download it describes."""
    try:
        _write_json(_path(), state)
    except OSError as e:
        logstore.warning(f"Could not keep the list of what is running: {e}")


def add_install(game_id: int) -> None:
    with _lock:
        state = load()
        if game_id not in state["installs"]:
            state["installs"].append(game_id)
            _save(state)


def remove_install(game_id: int) -> None:
    with _lock:
        state = load()
        if game_id in state["installs"]:
            state["installs"].remove(game_id)
            _save(state)


def add_mod(game_id: int, mod: dict) -> None:
    with _lock:
        state = load()
        if not any(m["game_id"] == game_id and m["mod"].get("name") == mod.get("name") for m in state["mods"]):
            state["mods"].append({"game_id": game_id, "mod": mod})
            _save(state)


def remove_mod(game_id: int, name: str) -> None:
    with _lock:
        state = load()
        kept = [m for m in state["mods"] if not (m["game_id"] == game_id and m["mod"].get("name") == name)]
        if len(kept) != len(state["mods"]):
            state["mods"] = kept
            _save(state)
