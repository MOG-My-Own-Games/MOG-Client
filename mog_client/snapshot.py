"""The last library and user the server sent, kept on disk so the window has something to show at once on the next
start while the real list is fetched (stdlib only). Each file names the server it came from, and is ignored for
another one."""

from __future__ import annotations

import json
from pathlib import Path

from mog_client.config import data_dir


def _library_file() -> Path:
    return data_dir() / "library.json"


def _user_file() -> Path:
    return data_dir() / "user.json"


def _avatar_file() -> Path:
    return data_dir() / "user-avatar.img"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(path)


def _read(path: Path, server: str) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("server") == server else None


def save_library(server: str, games: list[dict], libraries: list[dict]) -> None:
    _write(_library_file(), {"server": server, "games": games, "libraries": libraries})


def load_library(server: str) -> tuple[list[dict], list[dict]]:
    data = _read(_library_file(), server)
    if data is None:
        return [], []
    games, libraries = data.get("games"), data.get("libraries")
    return (games if isinstance(games, list) else []), (libraries if isinstance(libraries, list) else [])


def save_user(server: str, name: str, avatar: bytes | None) -> None:
    _write(_user_file(), {"server": server, "name": name})
    if avatar:
        _avatar_file().write_bytes(avatar)
    else:
        _avatar_file().unlink(missing_ok=True)


def load_user(server: str) -> tuple[str, bytes | None] | None:
    data = _read(_user_file(), server)
    if data is None or not data.get("name"):
        return None
    try:
        avatar = _avatar_file().read_bytes()
    except OSError:
        avatar = None
    return str(data["name"]), avatar


def forget() -> None:
    for path in (_library_file(), _user_file(), _avatar_file()):
        path.unlink(missing_ok=True)
